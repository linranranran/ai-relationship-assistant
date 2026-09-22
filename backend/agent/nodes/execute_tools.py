"""按依赖顺序执行 Tool，并在运行时解析前置步骤结果。"""

import logging
from copy import deepcopy

from langgraph.types import Command

from backend.agent.confirmation import HIGH_RISK_TOOLS, build_confirmation_request
from backend.agent.state import AgentState
from backend.agent.tool_dependencies import resolve_result_references
from backend.tools.registry import TOOL_ARGUMENT_MODELS, execute_tool


logger = logging.getLogger(__name__)


def _successful_results_by_step(records: list[dict]) -> dict[str, dict]:
    """从可持久化的执行记录恢复引用索引，支持 interrupt 后继续执行。"""
    return {
        record["step_id"]: record["result"]
        for record in records
        if record.get("status") == "SUCCESS"
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


def _record(call: dict, status: str, result: dict | None = None, error: str | None = None) -> dict:
    return {
        "step_id": call["step_id"],
        "call_id": call["call_id"],
        "tool": call["tool"],
        "status": status,
        "result": result,
        "error": error,
    }


def execute_tools(state: AgentState) -> Command:
    """执行一个顺序 DAG。

    - ``depends_on`` 声明前置步骤；前置失败时当前步骤标记为 SKIPPED。
    - ``$ref`` 从成功结果中取值，解析后再次校验 Tool 参数。
    - 安全查询可以先执行；遇到高风险步骤时，使用已经解析出的真实 ID
      构造确认摘要并暂停，恢复后从 ``next_tool_index`` 继续。

    TODO(下一阶段)：把执行记录落库，并用 call_id 实现跨进程幂等。
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
            records.append(_record(call, "SKIPPED", error=message))
            errors.append(message)
            continue

        try:
            resolved_args = resolve_result_references(call.get("args", {}), results_by_step)
            resolved_args = _validate_concrete_args(call["tool"], dict(resolved_args))
        except Exception as exc:
            message = f"步骤 {step_id} 参数解析失败：{exc}"
            records.append(_record(call, "FAILED", error=message))
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
                records.append(_record(call, "FAILED", error=message))
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

        result: dict | None = None
        for attempt in range(2):
            result = execute_tool(call["tool"], **runtime_args)
            if result.get("success"):
                break

            message = result.get("msg") or result.get("message") or "未知 Tool 错误"
            if "缺少" in message or "已存在" in message:
                break
            if attempt == 1:
                logger.error("%s 重试仍失败: %s", call["tool"], message)

        if result and result.get("success"):
            records.append(_record(call, "SUCCESS", result=result))
            results_by_step[step_id] = result
        else:
            message = (result or {}).get("msg") or (result or {}).get("message") or "Tool 执行失败"
            records.append(_record(call, "FAILED", result=result, error=message))
            errors.append(f"步骤 {step_id} 失败：{message}")

    update = {
        "tool_calls": tool_calls,
        "tool_results": records,
        "execution_errors": errors,
        "next_tool_index": len(tool_calls),
        "needs_confirmation": False,
        "pending_confirmation": None,
    }
    if not any(record.get("status") == "SUCCESS" for record in records) and errors:
        return Command(update=update, goto="ask_clarification")
    return Command(update=update, goto="generate_response")
