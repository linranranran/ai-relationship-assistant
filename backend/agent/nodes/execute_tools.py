"""按依赖顺序执行 Tool，并把执行事实交给观察节点。"""

import logging
from copy import deepcopy
from time import perf_counter

from langgraph.types import Command

from backend.agent.confirmation import HIGH_RISK_TOOLS, build_confirmation_request
from backend.agent.execution_enums import ExecutionClaimAction, ToolStepStatus
from backend.agent.execution_policy import ToolErrorCode, parse_tool_error_code
from backend.agent.state import AgentState
from backend.agent.tool_dependencies import resolve_result_references
from backend.agent.tool_execution_repository import (
    ExecutionClaim,
    claim_execution,
    mark_failed,
    mark_success,
)
from backend.tools.registry import TOOL_ARGUMENT_MODELS, execute_tool


logger = logging.getLogger(__name__)

# 第一阶段只为新增人物接入业务数据库幂等。add_relation 完成相同的唯一键或
# MERGE 约束后，再把它加入这里。仅有 MySQL 执行记录还不能阻止业务库重复写。
BUSINESS_IDEMPOTENT_TOOLS = frozenset({"add_person"})


def _successful_results_by_step(records: list[dict]) -> dict[str, dict]:
    """恢复 ``step_id -> result`` 索引，供后续步骤解析 ``$ref``。

    这个索引从 state 中重建，因此图经历 confirmation/clarification interrupt
    后仍然能继续解析之前成功步骤的结果。
    """
    return {
        record["step_id"]: record["result"]
        for record in records
        if record.get("status") == ToolStepStatus.SUCCESS
        and isinstance(record.get("step_id"), str)
        and isinstance(record.get("result"), dict)
    }


def _record(
    call: dict,
    status: ToolStepStatus,
    *,
    result: dict | None = None,
    error: str | None = None,
    error_code: ToolErrorCode | None = None,
) -> dict:
    """生成统一步骤记录；Observe 只依赖这个结构，不解析日志文本。"""
    return {
        "step_id": call["step_id"],
        "call_id": call["call_id"],
        "tool": call["tool"],
        "status": status.value,
        "result": result,
        "error": error,
        "error_code": error_code.value if error_code else None,
    }


def _append_problem(
    records: list[dict],
    errors: list[str],
    call: dict,
    *,
    message: str,
    error_code: ToolErrorCode,
    status: ToolStepStatus = ToolStepStatus.FAILED,
    result: dict | None = None,
) -> None:
    """同时写入结构化步骤结果和便于展示、排障的错误列表。"""
    records.append(
        _record(
            call,
            status,
            result=result,
            error=message,
            error_code=error_code,
        )
    )
    errors.append(message)


def _resolve_and_validate_args(call: dict, results_by_step: dict[str, dict]) -> dict:
    """解析前置步骤引用，再用 Pydantic 做最终参数校验。

    规划阶段遇到 ``$ref`` 时还不知道真实值类型，所以完整校验必须放在引用
    被替换之后再做一次。``confirmed`` 是服务端参数，不属于 Tool 参数模型，
    因此单独保留。
    """
    resolved = resolve_result_references(call.get("args", {}), results_by_step)
    concrete_input = dict(resolved)
    confirmed = concrete_input.pop("confirmed", None)
    validated = TOOL_ARGUMENT_MODELS[call["tool"]].model_validate(concrete_input)
    concrete = validated.model_dump(exclude_none=True)
    if confirmed is True:
        concrete["confirmed"] = True
    return concrete


def _runtime_args(state: AgentState, call: dict, resolved_args: dict) -> dict:
    """注入只能由可信后端提供的运行参数。

    owner_id、self_person_id 和业务幂等键绝不能接受模型或浏览器传值，否则
    会造成越权查询或跨用户复用幂等结果。
    """
    runtime = dict(resolved_args)
    runtime["owner_id"] = state["user_id"]
    if call["tool"] in BUSINESS_IDEMPOTENT_TOOLS:
        runtime["idempotency_key"] = f"{state['user_id']}:{call['call_id']}"
    if call["tool"] == "query_relation_path":
        runtime["from_person_id"] = state["self_person_id"]
    return runtime


def _claim(state: AgentState, call: dict, resolved_args: dict) -> ExecutionClaim:
    """抢占 Tool 执行权；这里使用短事务，不持有数据库锁执行真实 Tool。

    三套状态不要混淆：

    * MySQL ``running/success/failed``：这次调用在历史上执行到了哪里；
    * Claim ``EXECUTE/WAIT/IN_PROGRESS``：当前进程现在应该采取什么动作；
    * Graph ``SUCCESS/FAILED/SKIPPED``：本轮 Agent 如何记录该步骤。
    """
    return claim_execution(
        call_id=call["call_id"],
        owner_id=state["user_id"],
        request_id=state["request_id"],
        thread_id=state["thread_id"],
        tool_name=call["tool"],
        arguments=resolved_args,
    )


def _invoke_tool_with_retry(tool_name: str, runtime_args: dict) -> tuple[dict, int]:
    """执行 Tool，并返回结果和耗时。

    第一版沿用现有策略，最多执行两次；明显的参数缺失和数据冲突不重试。
    等全部 Tool 接入结构化 ``retryable`` 后，应删除文案判断。
    """
    started_at = perf_counter()
    result: dict = {"success": False, "message": "Tool 未返回结果"}

    for attempt in range(2):
        result = execute_tool(tool_name, **runtime_args)
        if result.get("success"):
            break

        message = result.get("msg") or result.get("message") or "未知 Tool 错误"
        if "缺少" in message or "已存在" in message:
            break
        if attempt == 1:
            logger.error("%s 重试仍失败: %s", tool_name, message)

    latency_ms = max(0, int((perf_counter() - started_at) * 1000))
    return result, latency_ms


def _save_success(
    state: AgentState,
    call: dict,
    claim: ExecutionClaim,
    result: dict,
    latency_ms: int,
    records: list[dict],
    errors: list[str],
    results_by_step: dict[str, dict],
) -> None:
    """保存成功结果；即使审计落库失败，也如实记录 Tool 已产生的效果。"""
    try:
        mark_success(
            call_id=call["call_id"],
            owner_id=state["user_id"],
            result=result,
            latency_ms=latency_ms,
            execution_token=claim.execution_token,
        )
    except Exception as exc:
        # Tool 可能已经产生业务副作用，不能因审计表更新失败就谎称 Tool 失败。
        # 写 Tool 还需要业务库幂等键来覆盖“副作用成功、审计落库失败”的窗口。
        warning = f"步骤 {call['step_id']} 已成功，但幂等结果保存失败：{exc}"
        logger.exception(warning)
        errors.append(warning)

    records.append(_record(call, ToolStepStatus.SUCCESS, result=result))
    results_by_step[call["step_id"]] = result


def _save_failure(
    state: AgentState,
    call: dict,
    claim: ExecutionClaim,
    result: dict,
    latency_ms: int,
    records: list[dict],
    errors: list[str],
) -> None:
    """保存 Tool 失败事实，并把结构化错误码交给 Observe。"""
    message = result.get("msg") or result.get("message") or "Tool 执行失败"
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
        logger.exception("步骤 %s 的 failed 状态保存失败", call["step_id"])

    _append_problem(
        records,
        errors,
        call,
        message=f"步骤 {call['step_id']} 失败：{message}",
        error_code=parse_tool_error_code(result),
        result=result,
    )


def _confirmation_command(
    state: AgentState,
    tool_calls: list[dict],
    records: list[dict],
    errors: list[str],
    index: int,
) -> Command:
    """把已经解析出真实参数的高风险步骤交给确认节点暂停。"""
    pending = build_confirmation_request([tool_calls[index]], state["user_id"])
    pending["interaction_type"] = "CONFIRMATION"
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


def execute_tools(state: AgentState) -> Command:
    """顺序执行带依赖的 Tool 计划。

    主循环刻意只展示一个步骤的生命周期：

    1. 检查依赖是否成功；
    2. 解析 ``$ref`` 并校验真实参数；
    3. 必要时暂停并请求高风险授权；
    4. 抢占幂等执行权；
    5. 执行 Tool 并保存成功或失败结果；
    6. 全部步骤处理完后统一进入 ``observe_execution``。

    执行器只记录事实，不决定应该回复、重规划还是询问用户。这正是 Observe
    节点存在的原因。
    """
    tool_calls = deepcopy(state.get("tool_calls", []))
    records = list(state.get("tool_results", []))
    errors = list(state.get("execution_errors", []))
    results_by_step = _successful_results_by_step(records)

    for index in range(state.get("next_tool_index", 0), len(tool_calls)):
        call = tool_calls[index]
        step_id = call["step_id"]

        missing_dependencies = [
            dependency
            for dependency in call.get("depends_on", [])
            if dependency not in results_by_step
        ]
        if missing_dependencies:
            _append_problem(
                records,
                errors,
                call,
                message=f"步骤 {step_id} 跳过：依赖未成功 {missing_dependencies}",
                error_code=ToolErrorCode.DEPENDENCY_FAILED,
                status=ToolStepStatus.SKIPPED,
            )
            continue

        try:
            resolved_args = _resolve_and_validate_args(call, results_by_step)
        except Exception as exc:
            _append_problem(
                records,
                errors,
                call,
                message=f"步骤 {step_id} 参数解析失败：{exc}",
                error_code=ToolErrorCode.INVALID_ARGUMENT,
            )
            continue

        # 保存解析后的真实参数。发生 interrupt 后，恢复执行看到的是同一份参数，
        # 不需要再次依赖模型推导。
        call["args"] = resolved_args
        tool_calls[index] = call

        if call["tool"] in HIGH_RISK_TOOLS and resolved_args.get("confirmed") is not True:
            try:
                return _confirmation_command(state, tool_calls, records, errors, index)
            except Exception as exc:
                _append_problem(
                    records,
                    errors,
                    call,
                    message=f"步骤 {step_id} 无法生成确认信息：{exc}",
                    error_code=ToolErrorCode.INTERNAL,
                )
                continue

        try:
            claim = _claim(state, call, resolved_args)
        except Exception as exc:
            _append_problem(
                records,
                errors,
                call,
                message=f"步骤 {step_id} 无法获取幂等执行权：{exc}",
                error_code=ToolErrorCode.INTERNAL,
            )
            continue

        if claim.action == ExecutionClaimAction.WAIT:
            records.append(_record(call, ToolStepStatus.SUCCESS, result=claim.result))
            results_by_step[step_id] = claim.result
            continue

        if claim.action == ExecutionClaimAction.IN_PROGRESS:
            _append_problem(
                records,
                errors,
                call,
                message=f"步骤 {step_id} 正由另一个请求执行，请稍后重试",
                error_code=ToolErrorCode.IN_PROGRESS,
                status=ToolStepStatus.SKIPPED,
            )
            continue

        if not claim.execution_token:
            _append_problem(
                records,
                errors,
                call,
                message=f"步骤 {step_id} 已获得执行权但缺少 execution_token",
                error_code=ToolErrorCode.INTERNAL,
            )
            continue

        result, latency_ms = _invoke_tool_with_retry(
            call["tool"], _runtime_args(state, call, resolved_args)
        )
        if result.get("success"):
            _save_success(
                state, call, claim, result, latency_ms, records, errors, results_by_step
            )
        else:
            _save_failure(state, call, claim, result, latency_ms, records, errors)

    # 这里不生成面向用户的回复。Observe 会读取结构化结果，做出稳定、可审计
    # 的 COMPLETE / REPLAN / ASK_USER / WAIT 决策。
    return Command(
        update={
            "tool_calls": tool_calls,
            "tool_results": records,
            "execution_errors": errors,
            "next_tool_index": len(tool_calls),
            "needs_confirmation": False,
            "pending_confirmation": None,
        },
        goto="observe_execution",
    )
