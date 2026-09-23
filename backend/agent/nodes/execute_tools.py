"""按依赖顺序执行 Tool，并在运行时解析前置步骤结果。"""

import logging
from copy import deepcopy
from time import perf_counter

from langgraph.types import Command

from backend.agent.confirmation import HIGH_RISK_TOOLS, build_confirmation_request
from backend.agent.execution_enums import ExecutionClaimAction, ToolStepStatus
from backend.agent.state import AgentState
from backend.agent.tool_dependencies import resolve_result_references
from backend.agent.tool_execution_repository import (
    claim_execution,
    mark_failed,
    mark_success,
)
from backend.tools.registry import TOOL_ARGUMENT_MODELS, execute_tool


logger = logging.getLogger(__name__)


def _successful_results_by_step(records: list[dict]) -> dict[str, dict]:
    """从可持久化的执行记录恢复引用索引，支持 interrupt 后继续执行。"""
    return {
        record["step_id"]: record["result"]
        for record in records
        if record.get("status") == ToolStepStatus.SUCCESS
        and isinstance(record.get("step_id"), str)
        and isinstance(record.get("result"), dict)
    }


def _validate_concrete_args(tool_name: str, args: dict) -> dict:
    """$ref 解析后再次执行完整 Pydantic 校验。"""
    confirmed = args.pop("confirmed", None)
    validated = TOOL_ARGUMENT_MODELS[tool_name].model_validate(args)
    concrete = validated.model_dump(exclude_none=True)
    if confirmed is True:
        concrete["confirmed"] = True
    return concrete


def _record(
    call: dict,
    status: ToolStepStatus,
    result: dict | None = None,
    error: str | None = None,
) -> dict:
    return {
        "step_id": call["step_id"],
        "call_id": call["call_id"],
        "tool": call["tool"],
        "status": status.value,
        "result": result,
        "error": error,
    }


def execute_tools(state: AgentState) -> Command:
    """执行一个顺序 DAG。

    - ``depends_on`` 声明前置步骤；前置失败时当前步骤标记为 SKIPPED。
    - ``$ref`` 从成功结果中取值，解析后再次校验 Tool 参数。
    - 安全查询可以先执行；遇到高风险步骤时，使用已经解析出的真实 ID
      构造确认摘要并暂停，恢复后从 ``next_tool_index`` 继续。

    Tool 执行记录通过 ``request_id + call_id`` 关联到原始用户请求。
    """
    tool_calls = deepcopy(state.get("tool_calls", []))
    records = list(state.get("tool_results", []))
    errors = list(state.get("execution_errors", []))
    results_by_step = _successful_results_by_step(records)
    start_index = state.get("next_tool_index", 0)

    for index in range(start_index, len(tool_calls)):
        call = tool_calls[index]
        step_id = call["step_id"]
        dependencies = call.get("depends_on", [])

        failed_dependencies = [
            dependency for dependency in dependencies if dependency not in results_by_step
        ]
        if failed_dependencies:
            message = f"步骤 {step_id} 跳过：依赖未成功 {failed_dependencies}"
            records.append(_record(call, ToolStepStatus.SKIPPED, error=message))
            errors.append(message)
            continue

        try:
            resolved_args = resolve_result_references(call.get("args", {}), results_by_step)
            resolved_args = _validate_concrete_args(call["tool"], dict(resolved_args))
        except Exception as exc:
            message = f"步骤 {step_id} 参数解析失败：{exc}"
            records.append(_record(call, ToolStepStatus.FAILED, error=message))
            errors.append(message)
            continue

        call["args"] = resolved_args
        tool_calls[index] = call

        # 先执行安全依赖，等人物/关系 ID 已经确定后，再暂停询问用户。
        if call["tool"] in HIGH_RISK_TOOLS and resolved_args.get("confirmed") is not True:
            try:
                pending = build_confirmation_request([call], state["user_id"])
            except Exception as exc:
                message = f"步骤 {step_id} 无法生成确认信息：{exc}"
                records.append(_record(call, ToolStepStatus.FAILED, error=message))
                errors.append(message)
                continue

            return Command(
                update={
                    "tool_calls": tool_calls,
                    "tool_results": records,
                    "execution_errors": errors,
                    "next_tool_index": index,
                    "needs_confirmation": True,
                    "confirmation_type": "HIGH_RISK_TOOL",
                    "pending_confirmation": pending,
                    "user_confirmed": False,
                },
                goto="request_confirmation",
            )

        runtime_args = dict(resolved_args)
        runtime_args["owner_id"] = state["user_id"]
        if call["tool"] == "query_relation_path":
            runtime_args["from_person_id"] = state["self_person_id"]

        # 存在 三套“状态”，表达的含义不同。
        # 类型	值	表达什么
        # 数据库执行状态	            running/success/failed	        这次 Tool 历史上执行到哪一步
        # claim_execution 返回动作	EXECUTE/WAIT/IN_PROGRESS	      当前这个进程接下来应该做什么
        # LangGraph 步骤结果	        SUCCESS/FAILED/SKIPPED	        Agent 本轮流程如何记录这个步骤
        try:
            claim = claim_execution(
                call_id=call["call_id"],
                owner_id=state["user_id"],
                request_id=state["request_id"],
                thread_id=state["thread_id"],
                tool_name=call["tool"],
                arguments=resolved_args,
            )
        except Exception as exc:
            message = f"步骤 {step_id} 无法获取幂等执行权：{exc}"
            records.append(_record(call, ToolStepStatus.FAILED, error=message))
            errors.append(message)
            continue

        if claim.action == ExecutionClaimAction.WAIT:
            records.append(_record(call, ToolStepStatus.SUCCESS, result=claim.result))
            results_by_step[step_id] = claim.result
            continue

        if claim.action == ExecutionClaimAction.IN_PROGRESS:
            message = f"步骤 {step_id} 正由另一个请求执行，请稍后重试"
            records.append(_record(call, ToolStepStatus.SKIPPED, error=message))
            errors.append(message)
            continue

        if not claim.execution_token:
            message = f"步骤 {step_id} 已获得执行权但缺少 execution_token"
            records.append(_record(call, ToolStepStatus.FAILED, error=message))
            errors.append(message)
            continue

        result: dict | None = None
        started_at = perf_counter()
        for attempt in range(2):
            result = execute_tool(call["tool"], **runtime_args)
            if result.get("success"):
                break

            message = result.get("msg") or result.get("message") or "未知 Tool 错误"
            if "缺少" in message or "已存在" in message:
                break
            if attempt == 1:
                logger.error("%s 重试仍失败: %s", call["tool"], message)

        latency_ms = max(0, int((perf_counter() - started_at) * 1000))
        if result and result.get("success"):
            try:
                mark_success(
                    call_id=call["call_id"],
                    owner_id=state["user_id"],
                    result=result,
                    latency_ms=latency_ms,
                    execution_token=claim.execution_token,
                )
            except Exception as exc:
                # Tool 已经产生副作用，不能谎称它没有执行；记录严重告警，
                # 后续应为写 Tool 增加同库事务或业务幂等键进一步收紧窗口。
                warning = f"步骤 {step_id} 已成功，但幂等结果保存失败：{exc}"
                logger.exception(warning)
                errors.append(warning)
            records.append(_record(call, ToolStepStatus.SUCCESS, result=result))
            results_by_step[step_id] = result
        else:
            message = (result or {}).get("msg") or (result or {}).get("message") or "Tool 执行失败"
            try:
                mark_failed(
                    call_id=call["call_id"],
                    owner_id=state["user_id"],
                    error_message=message,
                    latency_ms=latency_ms,
                    result=result,
                    execution_token=claim.execution_token,
                )
            except Exception:
                logger.exception("步骤 %s 的 failed 状态保存失败", step_id)
            records.append(
                _record(call, ToolStepStatus.FAILED, result=result, error=message)
            )
            errors.append(f"步骤 {step_id} 失败：{message}")

    update = {
        "tool_calls": tool_calls,
        "tool_results": records,
        "execution_errors": errors,
        "next_tool_index": len(tool_calls),
        "needs_confirmation": False,
        "pending_confirmation": None,
    }
    if not any(
        record.get("status") == ToolStepStatus.SUCCESS for record in records
    ) and errors:
        return Command(update=update, goto="ask_clarification")
    return Command(update=update, goto="generate_response")
