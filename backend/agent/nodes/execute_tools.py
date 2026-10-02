"""按依赖顺序执行 Tool，并把执行事实交给观察节点。"""

import logging
from copy import deepcopy
from datetime import date, datetime, time, timedelta
from time import perf_counter

from langgraph.types import Command
from neo4j.time import Date as Neo4jDate
from neo4j.time import DateTime as Neo4jDateTime
from neo4j.time import Duration as Neo4jDuration
from neo4j.time import Time as Neo4jTime
from neo4j import Record
from backend.agent.tracing import trace_event
from backend.agent.streaming import check_execution, emit_progress, ExecutionCancelled, TOOL_LABELS

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

# 每个写操作都在业务库中原子保存回执；PostgreSQL 只负责租约与执行审计。
BUSINESS_IDEMPOTENT_TOOLS = frozenset({
    "add_person", "update_person", "delete_person",
    "add_relation", "update_relation", "delete_relation",
})
SELF_PERSON_MARKERS = frozenset({"@self", "self_person_id"})


def _serializable_tool_value(value):
    """在 Tool 边界把 Neo4j 时间值转换成可持久化的普通数据。

    Tool 结果同时进入 PostgreSQL JSONB 和 LangGraph Checkpointer。Neo4j 的
    DateTime 不是 Python datetime，也不能被这两套序列化器直接处理。转换
    必须发生在审计落库及写入图状态之前，保证重试读取到相同的 JSON 结构。
    不认识的对象直接报错，避免把任意对象的 repr 悄悄写进业务数据。
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (Neo4jDateTime, Neo4jDate, Neo4jTime, Neo4jDuration)):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, Record):
        # Record 也是 tuple，必须先转换，否则 relation_id 等字段名会丢失。
        return _serializable_tool_value(value.data())
    if isinstance(value, dict):
        return {key: _serializable_tool_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serializable_tool_value(item) for item in value]
    raise TypeError(f"Tool 结果包含不能序列化的类型：{type(value).__name__}")


def _successful_results_by_step(records: list[dict]) -> dict[str, dict]:
    """恢复 ``step_id -> result`` 索引，供后续步骤解析 ``$ref``。

    这个索引从 state 中重建，因此图经历 confirmation/clarification interrupt
    后仍然能继续解析之前成功步骤的结果。
    """
    return {
        record["step_id"]: record["result"]
        for record in records
        if record.get("status") in {ToolStepStatus.SUCCESS, ToolStepStatus.RESOLVED}
        and isinstance(record.get("step_id"), str)
        and isinstance(record.get("result"), dict)
    }


def _mutation_target(tool: str, args: dict, result: dict):
    """去重只适用于仍未被后续写入改变的目标，不能把更新 A→B→A 当作重复。"""
    data = result.get("data", {})
    if not isinstance(data, dict):
        data = {}
    if tool == "add_person":
        return ("person", data.get("id"))
    if tool in {"update_person", "delete_person"}:
        return ("person", args.get("person_id"))
    if tool == "add_relation":
        return ("relation", data.get("relation_id"))
    return ("relation", args.get("relation_id"))


def _remember_mutation(records: list[dict], call: dict, args: dict, result: dict, version: int):
    target = _mutation_target(call["tool"], args, result)
    for previous in records:
        same_target = _mutation_target(previous["tool"], previous["args"], previous["result"]) == target
        # 创建后的属性修改没有撤销“对象已创建”这一事实，ID 仍可继续引用。
        # 只有删除才让创建结果失效；更新结果则会被任何后续同目标写入失效。
        if previous["tool"] in {"add_person", "add_relation"} and call["tool"].startswith("update_"):
            same_target = False
        # 删除人物会同时删除边，旧关系快照不再代表当前业务效果。
        deleted_endpoint = call["tool"] == "delete_person" and "relation" in previous["tool"]
        if same_target or deleted_endpoint:
            previous["reusable"] = False
    records.append({"tool": call["tool"], "call_id": call["call_id"], "args": args,
                    "result": result, "plan_version": version, "reusable": True})


def _missing_person_probe_steps(
    call: dict,
    tool_calls: list[dict],
    records: list[dict],
    missing_dependencies: list[str],
) -> list[str]:
    """识别“查无此人后新增”的预检查，不能把其他失败依赖当作可忽略。

    只允许新增人物依赖同名的精确查询，且查询明确返回 NOT_FOUND。模糊查询、
    网络错误、权限失败以及其他 Tool 的依赖仍须阻断新增。
    """
    if call.get("tool") != "add_person" or not missing_dependencies:
        return []
    name = call.get("args", {}).get("name")
    if not isinstance(name, str) or not name.strip():
        return []
    calls_by_step = {item.get("step_id"): item for item in tool_calls}
    records_by_step = {item.get("step_id"): item for item in records}
    for dependency in missing_dependencies:
        lookup = calls_by_step.get(dependency, {})
        lookup_args = lookup.get("args", {})
        record = records_by_step.get(dependency, {})
        if (
            lookup.get("tool") != "find_person"
            or lookup_args.get("search_mode") != "exact"
            or lookup_args.get("query") != name
            or record.get("status") != ToolStepStatus.FAILED.value
            or record.get("error_code") != ToolErrorCode.NOT_FOUND.value
        ):
            return []
    return missing_dependencies


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


def _resolve_and_validate_args(
    state: AgentState, call: dict, results_by_step: dict[str, dict]
) -> dict:
    """解析前置步骤引用，再用 Pydantic 做最终参数校验。

    规划阶段遇到 ``$ref`` 时还不知道真实值类型，所以完整校验必须放在引用
    被替换之后再做一次。``confirmed`` 是服务端参数，不属于 Tool 参数模型，
    因此单独保留。
    """
    resolved = resolve_result_references(call.get("args", {}), results_by_step)
    concrete_input = dict(resolved)
    confirmed = concrete_input.pop("confirmed", None)
    if call["tool"] == "add_relation":
        # 模型只提供“本人”的标记，真实 Person ID 必须来自已认证的 Agent state。
        # 兼容旧检查点中已经出现的字面值 self_person_id。
        for field in ("from_person_id", "to_person_id"):
            value = concrete_input.get(field)
            if isinstance(value, str) and value in SELF_PERSON_MARKERS:
                concrete_input[field] = state["self_person_id"]

        # 旧规划会把“魏山凯是我的朋友”写成 魏山凯 -> 本人。朋友是对称关系，
        # 统一存为 本人 -> 朋友，避免后续事实提取把本人当成目标人物。
        if (
            concrete_input.get("relation_type") == "朋友"
            and concrete_input.get("to_person_id") == state["self_person_id"]
            and concrete_input.get("from_person_id") != state["self_person_id"]
        ):
            concrete_input["from_person_id"], concrete_input["to_person_id"] = (
                concrete_input["to_person_id"],
                concrete_input["from_person_id"],
            )
        if concrete_input.get("from_person_id") == concrete_input.get("to_person_id"):
            raise ValueError("关系两端不能是同一人物")
    validated = TOOL_ARGUMENT_MODELS[call["tool"]].model_validate(concrete_input)
    concrete = validated.model_dump(exclude_none=True)
    if confirmed is True:
        concrete["confirmed"] = True
    return concrete


def _runtime_args(state: AgentState, call: dict, resolved_args: dict) -> dict:
    """注入只能由可信后端提供的运行参数。

    owner_id、self_person_id 和业务幂等键绝不能接受模型或浏览器传值，否则
    会造成越权查询或跨用户复用幂等结果。
    add_relation 的本人引用已在参数解析阶段转换；这里不能无条件覆盖起点，
    否则“甲是乙的朋友”也会被错误地写成本人与乙的关系。
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

    * PostgreSQL ``running/success/failed``：这次调用在历史上执行到了哪里；
    * Claim ``EXECUTE/WAIT/IN_PROGRESS``：当前进程现在应该采取什么动作；
    * Graph ``SUCCESS/RESOLVED/FAILED/SKIPPED``：本轮 Agent 如何记录该步骤。
    """
    return claim_execution(
        call_id=call["call_id"],
        owner_id=state["user_id"],
        request_id=state["request_id"],
        thread_id=state["thread_id"],
        tool_name=call["tool"],
        arguments=resolved_args,
    )


def _invoke_tool_with_retry(tool_name: str, runtime_args: dict, *,
                            call_id: str | None = None, step_id: str | None = None) -> tuple[dict, int]:
    """执行 Tool，并返回结果和耗时。

    只重试明确标为 retryable 的 TRANSIENT，禁止靠中文错误文案猜测。
    未知错误可能已经产生副作用，不能盲目自动重复。
    """
    trace_event("tool.start", tool=tool_name, call_id=call_id, step_id=step_id)
    emit_progress("tool.started", tool=tool_name, call_id=call_id, step_id=step_id,
                  message=TOOL_LABELS.get(tool_name, "执行工具"))
    started_at = perf_counter()
    result: dict = {"success": False, "message": "Tool 未返回结果"}

    for attempt in range(2):
        if attempt:
            try:
                check_execution(force=True)
            except ExecutionCancelled:
                # 已开始的第一次调用允许完成并保存回执；取消阻止第二次重试。
                # 返回失败事实而不是中途抛出，才能把本节点前面成功的步骤一起
                # 写进 Checkpoint。下一工具/节点边界再结束整个任务。
                result = {"success": False, "message": "用户已停止后续工具重试",
                          "error_code": ToolErrorCode.INTERNAL.value, "retryable": False}
                break
            emit_progress("tool.retrying", tool=tool_name, call_id=call_id, step_id=step_id,
                          attempt=attempt + 1, message="临时故障，正在重试工具")
        result = _serializable_tool_value(execute_tool(tool_name, **runtime_args))
        if result.get("success"):
            break

        if not (result.get("retryable") is True
                and parse_tool_error_code(result) == ToolErrorCode.TRANSIENT):
            break

    latency_ms = max(0, int((perf_counter() - started_at) * 1000))
    trace_event("tool.finished", tool=tool_name, success=result.get("success"),
                call_id=call_id, step_id=step_id,
                error_code=result.get("error_code"), latency_ms=latency_ms)
    emit_progress("tool.finished", tool=tool_name, call_id=call_id, step_id=step_id,
                  success=bool(result.get("success")), latency_ms=latency_ms,
                  message="工具调用已返回" if result.get("success") else "工具调用未成功")
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
    """审计落库失败时抛出系统错误，保留原计划以便重试。

    此时写操作可能已提交，恢复时以原 call_id 读取 Neo4j 回执，不重新写入。
    不能吞掉系统异常再交给模型重规划，也不能误报业务失败。
    """
    mark_success(
        call_id=call["call_id"],
        owner_id=state["user_id"],
        result=result,
        latency_ms=latency_ms,
        execution_token=claim.execution_token,
    )

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
    completed_mutations: list[dict],
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
            "completed_mutations": completed_mutations,
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
    completed_mutations = deepcopy(state.get("completed_mutations", []))

    trace_event("execution.start", step_count=len(tool_calls))
    for index in range(state.get("next_tool_index", 0), len(tool_calls)):
        try:
            check_execution(force=True)
        except ExecutionCancelled:
            # 前几个步骤可能已提交业务数据，把这些事实写回检查点再结束。
            # 取消不是数据库回滚，也不能清空已成功的 Tool 结果。
            return Command(update={"tool_calls": tool_calls, "tool_results": records,
                                   "execution_errors": errors, "completed_mutations": completed_mutations,
                                   "next_tool_index": index, "execution_decision": "CANCELLED",
                                   "response": "已停止后续步骤；已经完成的操作仍然保留。"}, goto="__end__")
        call = tool_calls[index]
        step_id = call["step_id"]

        if step_id in results_by_step:
            # WAIT 重入时保留已成功步骤，后续 $ref 继续引用原结果。
            trace_event("tool.reused", step_id=step_id, call_id=call["call_id"], tool=call["tool"])
            continue
        trace_event("tool.claim", step_id=step_id, call_id=call["call_id"], tool=call["tool"])

        missing_dependencies = [
            dependency
            for dependency in call.get("depends_on", [])
            if dependency not in results_by_step
        ]
        missing_person_probes = _missing_person_probe_steps(
            call, tool_calls, records, missing_dependencies
        )
        if missing_dependencies and not missing_person_probes:
            _append_problem(
                records,
                errors,
                call,
                message=f"步骤 {step_id} 跳过：依赖未成功 {missing_dependencies}",
                error_code=ToolErrorCode.DEPENDENCY_FAILED,
                status=ToolStepStatus.SKIPPED,
            )
            continue

        if call["tool"] == "add_person" and not missing_person_probes:
            # 精确查询已经找到同名人物时，不执行模型仍然规划出的新增步骤。
            # 这使预检查同时覆盖“存在”和“不存在”两个分支。
            found_probes = [
                dependency
                for dependency in call.get("depends_on", [])
                if any(
                    candidate.get("step_id") == dependency
                    and candidate.get("tool") == "find_person"
                    and candidate.get("args", {}).get("search_mode") == "exact"
                    and candidate.get("args", {}).get("query") == call.get("args", {}).get("name")
                    for candidate in tool_calls
                )
                and dependency in results_by_step
            ]
            if found_probes:
                # 这一步的目标是得到一个可引用的人物 ID。精确查询已命中时，
                # 复用查询结果，而不是把“无需新增”误记成依赖失败。
                found_result = results_by_step[found_probes[0]]
                found_person = found_result.get("data")
                if not isinstance(found_person, dict) or not found_person.get("id"):
                    _append_problem(
                        records, errors, call,
                        message=f"步骤 {step_id} 的查询结果缺少人物 ID",
                        error_code=ToolErrorCode.INTERNAL,
                    )
                    continue
                records.append(_record(call, ToolStepStatus.RESOLVED, result=found_result))
                results_by_step[step_id] = found_result
                continue

        try:
            resolved_args = _resolve_and_validate_args(state, call, results_by_step)
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

        # 同一请求在澄清后可能换 plan_version/call_id。精确匹配已经完成的写
        # 操作，复用结果；不是让模型自行记住“不要重复新增”。确认位是权限参数，
        # 不参与业务操作内容比较。新的不同操作仍按正常流程确认和执行。
        business_args = {key: value for key, value in resolved_args.items() if key != "confirmed"}
        previous = next((item for item in completed_mutations
                         if item.get("tool") == call["tool"]
                         and item.get("args") == business_args
                         and item.get("reusable", True)
                         and item.get("plan_version", -1) != state.get("replan_count", 0)), None)
        if call["tool"] in BUSINESS_IDEMPOTENT_TOOLS and previous is not None:
            result = previous["result"]
            records.append(_record(call, ToolStepStatus.RESOLVED, result=result))
            results_by_step[step_id] = result
            trace_event("tool.reused_business_result", call_id=call["call_id"],
                        original_call_id=previous["call_id"], step_id=step_id)
            emit_progress("tool.reused", tool=call["tool"], call_id=call["call_id"],
                          step_id=step_id, message="复用此前已完成的操作结果")
            continue

        if call["tool"] in HIGH_RISK_TOOLS and resolved_args.get("confirmed") is not True:
            try:
                return _confirmation_command(state, tool_calls, records, errors, index, completed_mutations)
            except Exception as exc:
                _append_problem(
                    records,
                    errors,
                    call,
                    message=f"步骤 {step_id} 无法生成确认信息：{exc}",
                    error_code=ToolErrorCode.INTERNAL,
                )
                continue

        # 仓储/租约错误属于系统故障，应由 checkpoint 保存执行位置后交给
        # 原请求重试，不能包装成业务信息不足后让模型或用户重新规划。
        claim = _claim(state, call, resolved_args)

        if claim.action == ExecutionClaimAction.WAIT:
            emit_progress("tool.reused", tool=call["tool"], call_id=call["call_id"],
                          step_id=step_id, message="复用已保存的工具结果")
            records.append(_record(call, ToolStepStatus.SUCCESS, result=claim.result))
            results_by_step[step_id] = claim.result
            if call["tool"] in BUSINESS_IDEMPOTENT_TOOLS:
                _remember_mutation(completed_mutations, call, business_args, claim.result,
                                   state.get("replan_count", 0))
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
            call["tool"], _runtime_args(state, call, resolved_args),
            call_id=call["call_id"], step_id=step_id,
        )
        if result.get("success"):
            _save_success(
                state, call, claim, result, latency_ms, records, errors, results_by_step
            )
            if call["tool"] in BUSINESS_IDEMPOTENT_TOOLS:
                _remember_mutation(completed_mutations, call, business_args, result,
                                   state.get("replan_count", 0))
            if missing_person_probes:
                # 查无此人是新增的预期条件。新增成功后，从本轮观察结果中移除
                # 这条“失败”，但 PostgreSQL tool_execution 仍保存原始查询审计。
                records[:] = [
                    record for record in records
                    if record.get("step_id") not in missing_person_probes
                ]
                errors[:] = [
                    error for error in errors
                    if not any(
                        error.startswith(f"步骤 {dependency} 失败：")
                        for dependency in missing_person_probes
                    )
                ]
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
            "completed_mutations": completed_mutations,
        },
        goto="observe_execution",
    )
