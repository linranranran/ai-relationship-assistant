"""Direct 阅读入口：先看四个公开节点，再看各节点调用的私有辅助函数。

direct_decide   —— 问模型下一步；只保存建议，不执行工具。
direct_validate —— 校验建议；合法动作才进入公共 execute_tools。
direct_observe  —— 合并真实回执，按错误/进展判断继续、澄清、等待或停止。
direct_finish   —— 区分模型正常结束与系统强制结束，保留整次请求事实。

state 中的 tool_calls/tool_results 是「当前轮」，direct_tool_* 是「整次请求」。
切换到下一轮前必须归档当前轮；否则最后回复和记忆只会看到最后一个工具。
普通执行经 observe_execution 内部调用 direct_observe；拒绝确认则直接调度
direct_observe 节点。辅助函数不新增图节点，也不建立隐藏的 goal。
"""
import json
from hashlib import sha256

from langgraph.graph import END
from langgraph.types import Command

from backend.agent.budget import reserve, settle
from backend.agent.confirmation import MODEL_FORBIDDEN_ARGUMENTS, sanitize_model_tool_calls
from backend.agent.contracts import ModelBusinessValidationError
from backend.agent.direct_runtime import (
    DIRECT_WRITE_TOOLS, PERSON_ID_FIELDS, direct_calls, direct_records, validate_direct_call,
)
from backend.agent.state import AgentState
from backend.agent.strategies.tool_protocol import append_tool_result_messages
from backend.agent.strategies.catalog import require_current_catalog
from backend.agent.strategies.contracts import DecisionKind
from backend.agent.strategies.factory import get_strategy
from backend.agent.streaming import emit_progress
from backend.config import AGENT_MAX_NO_PROGRESS_ROUNDS

# 顺序就是观察路由的优先级：致命错误 > 等待 > 澄清 > 无进展 > 普通继续。
UNRECOVERABLE_ERRORS = frozenset({"PERMISSION_DENIED", "INTERNAL", "UNKNOWN"})
WAIT_ERRORS = frozenset({"IN_PROGRESS", "TRANSIENT"})
CLARIFICATION_ERRORS = frozenset({"AMBIGUOUS", "CONFLICT"})
ARGUMENT_REPAIR_ERRORS = frozenset({"INVALID_ARGUMENT", "DEPENDENCY_FAILED"})


def _decision_id(state):
    # 请求 + 动作序号；provider ID 只负责 OpenAI 协议内配对。
    return f"request:{state['request_id']}:{state.get('decision_seq', 0)}"


def _reject(state, exc, message=None):
    """没有执行工具的协议错误：反馈模型一次，连续第二次错误就结束。

    这是修复「模型建议」，不是重试「已经启动的工具」，因此使用 replan 预算。
    不合法建议也补齐 assistant/tool 消息配对，避免下一次发出破损协议。
    """
    count = state.get("protocol_repair_count", 0) + 1
    seq = state.get("decision_seq", 0) + 1
    feedback = {
        "success": False,
        "error_code": getattr(exc, "code", "INVALID_DECISION"),
        "message": getattr(exc, "feedback", "工具参数未通过校验，未执行。"),
    }
    update = {
        "decision_seq": seq,
        "protocol_repair_count": count,
        "pending_decision": {},
        "replan_feedback": feedback,
        "strategy_messages": append_tool_result_messages(
            state.get("strategy_messages", []), message or getattr(exc, "message", {}), feedback,
            decision_id=_decision_id(state) if message is not None else f"request:{state['request_id']}:{seq}",
        ),
    }
    if count > 1:
        return Command(update={**update, "stop_reason": "MODEL_PROTOCOL_INVALID"}, goto="direct_finish")
    attempt = reserve("replan", f"direct-protocol:{state['request_id']}:{seq}")
    settle(attempt, result={"repair": count})
    return Command(update=update, goto="direct_decide")


def direct_decide(state: AgentState) -> Command:
    """每次看完整请求和实际工具反馈，不调用 classify_intent/extract_info。"""
    emit_progress("strategy.deciding", message="结合完整请求与执行结果选择下一步")
    try:
        decision = get_strategy("direct", "2").propose(state, require_current_catalog(state))
    except ModelBusinessValidationError as exc:
        return _reject(state, exc)
    return Command(
        update={"pending_decision": decision.as_dict(), "decision_seq": state.get("decision_seq", 0) + 1},
        goto="direct_validate",
    )


def direct_validate(state: AgentState) -> Command:
    """模型建议不等于授权：协议、Schema、可信 ID 和拒绝记录都要核对。

    无工具调用只标记 MODEL_STOP，随后依据回执生成回复；不调用 evaluate_goal。
    公共执行器用真实参数判断：明确新增可自动记录，修改/删除和存疑新增确认。
    """
    decision = state.get("pending_decision") or {}
    try:
        catalog = require_current_catalog(state)
        if decision["kind"] == DecisionKind.FINISH_CANDIDATE:
            return Command(
                update={
                    "direct_final_message": decision.get("message", {}),
                    "completion_basis": "MODEL_STOP_WITH_TOOL_RECEIPTS",
                    "requirements_verified": False,
                },
                goto="direct_finish",
            )
        if decision["kind"] == DecisionKind.ASK_CLARIFICATION:
            if state.get("protocol_repair_count", 0):
                raise ModelBusinessValidationError("INTERNAL_ERROR_IS_NOT_CLARIFICATION", "按 Schema 修复内部参数错误，不得要求用户提供内部参数。")
            return Command(update={"clarification_question": decision["arguments"]["question"]}, goto="ask_clarification")
        # 执行动作分成两件事：先得到可信调用，再归档旧轮次并发布新轮次。
        call = _normalize_business_call(state, decision, catalog)
        return _start_tool_round(state, call)
    except (ModelBusinessValidationError, ValueError, KeyError, TypeError) as exc:
        return _reject(state, exc, decision.get("message"))


def _normalize_business_call(state, decision, catalog):
    """校验单个业务调用，服务端生成 call_id；这里没有工具副作用。"""
    calls = decision.get("calls", [])
    if (decision["kind"] != DecisionKind.EXECUTE_BATCH
            or len(calls) != 1 or calls[0]["tool"] not in catalog.tools):
        raise ModelBusinessValidationError("TOOL_SCOPE_VIOLATION", "Direct 每轮仅允许一个注册业务工具。")
    args = calls[0]["args"]
    if any(key in MODEL_FORBIDDEN_ARGUMENTS for key in args):
        raise ModelBusinessValidationError("TRUSTED_ARGUMENT_FORGED", "不能设置服务端身份、确认或幂等参数。")
    args = {key: value for key, value in args.items() if value is not None}
    for key in PERSON_ID_FIELDS:
        if args.get(key) in {"@self", "self_person_id"}:
            args[key] = state["self_person_id"]

    # 无 goal 时 sanitizer 按 request_id + plan_version + 批内序号生成 call_id。
    # 用已保存的 decision_seq 作为版本：新动作不同 ID，恢复同动作仍是原 ID。
    normalized = sanitize_model_tool_calls(
        [{**calls[0], "args": args}], state["request_id"], plan_version=state["decision_seq"],
    )[0]
    validate_direct_call(state, normalized)
    return normalized


def _start_tool_round(state, call) -> Command:
    """归档上一轮事实，清空本轮临时字段；不清空已完成写入和请求总预算。"""
    return Command(
        update={
            "direct_tool_calls": direct_calls(state),
            "direct_tool_results": direct_records(state),
            "tool_calls": [call],
            "tool_results": [],
            "execution_errors": [],
            "next_tool_index": 0,
            "pending_confirmation": None,
            "needs_confirmation": False,
            "user_confirmed": False,
            "protocol_repair_count": 0,
        },
        goto="execute_tools",
    )


def direct_observe(state: AgentState) -> Command:
    """只观察实际结果和循环风险，不做逐项需求验收。

    NOT_FOUND 是普通业务反馈；可能接下来新增，也可能直接答没有记录。
    IN_PROGRESS/TRANSIENT 则必须 WAIT 原动作，不能让模型生成新调用重试写入。
    """
    # 先保存事实，再按固定优先级路由。即使停止，也不能丢掉此前已提交的操作。
    update, codes = _observation_update(state)
    if codes & UNRECOVERABLE_ERRORS:
        return Command(update={**update, "stop_reason": "TOOL_UNRECOVERABLE"}, goto="direct_finish")
    if codes & WAIT_ERRORS:
        # WAIT 不是 interrupt：主动 END 后，HTTP 重试重入原 execute_tools。
        # 丢弃临时协议反馈，保留原 pending_decision/call_id，禁止模型重新造动作。
        return Command(
            update={
                **update,
                "strategy_messages": state.get("strategy_messages", []),
                "execution_decision": "WAIT",
                "response": "工具仍在执行或暂时不可用，请用原请求稍后重试。",
            },
            goto=END,
        )
    if codes & CLARIFICATION_ERRORS:
        return Command(update=update, goto="ask_clarification")
    if update["no_progress_rounds"] >= AGENT_MAX_NO_PROGRESS_ROUNDS:
        return Command(update={**update, "stop_reason": "LOOP_NO_PROGRESS"}, goto="direct_finish")
    if codes & ARGUMENT_REPAIR_ERRORS:
        attempt = reserve("replan", f"direct-tool-repair:{state['request_id']}:{state['decision_seq']}")
        settle(attempt, result={"repair": "tool_arguments"})
    return Command(update={**update, "pending_decision": {}}, goto="direct_decide")


def _observation_update(state) -> tuple[dict, set]:
    """整理模型下一轮需要的事实和执行反馈，不决定图的去向。"""
    records = direct_records(state)
    payload = {"records": state.get("tool_results", []), "execution_feedback": state.get("execution_errors", [])}
    messages = append_tool_result_messages(state.get("strategy_messages", []),
                                           state.get("pending_decision", {}).get("message", {}), payload,
                                           decision_id=_decision_id(state))
    progress = _progress_update(state, records)
    update = {
        "direct_tool_calls": direct_calls(state),
        "direct_tool_results": records,
        "strategy_messages": messages,
        "replan_feedback": payload,
        **progress,
    }
    # 路由只看本轮错误；以前已处理的错误留在账本，不应永远阻塞后续轮次。
    codes = {record.get("error_code") for record in state.get("tool_results", [])
             if record.get("status") in {"FAILED", "SKIPPED"}}
    return update, codes


def _progress_update(state, records) -> dict:
    """比较新旧证据指纹，区分「执行了一次」与「真的获得新信息」。"""
    # 相同工具、业务参数、返回内容只算一次信息进展。“查无此人”也是有效证据：
    # 查三个不同姓名不能误判为三次无进展。写入失败和暂时错误仍不算新事实。
    # call_id/延迟/错误文案不计入，防止换编号或文案绕过。总预算是硬边界。
    fingerprints = []
    for record in records:
        negative_read = (record.get("tool") not in DIRECT_WRITE_TOOLS
                         and record.get("status") == "FAILED"
                         and record.get("error_code") == "NOT_FOUND")
        if record.get("status") not in {"SUCCESS", "RESOLVED"} and not negative_read:
            continue
        evidence_content = [
            record["tool"],
            {key: value for key, value in record.get("args", {}).items() if key != "confirmed"},
            "NOT_FOUND" if negative_read else None,
            (record.get("result") or {}).get("data"),
        ]
        serialized = json.dumps(evidence_content, sort_keys=True, ensure_ascii=False, default=str)
        fingerprints.append(sha256(serialized.encode()).hexdigest())
    evidence = sorted(set(fingerprints))
    progressed = bool(set(evidence) - set(state.get("progress_snapshot", {}).get("evidence", [])))
    rounds = 0 if progressed else state.get("no_progress_rounds", 0) + 1
    return {"progress_snapshot": {"evidence": evidence}, "no_progress_rounds": rounds}


def direct_finish(state: AgentState) -> Command:
    """正常模型结束与系统停止都保留整次请求事实；不伪造 goal 成功。

    已取消或系统强制停止时使用确定性文案，不再花一次模型调用解释错误。
    正常结束才去 generate_response，用全部回执生成面向用户的最终答案。
    """
    records = direct_records(state)
    update = {
        "direct_tool_calls": direct_calls(state),
        "direct_tool_results": records,
        "requirements_verified": False,
    }
    if state.get("stop_reason") or state.get("execution_decision") == "CANCELLED":
        return Command(
            update={
                **update,
                "completion_basis": "SYSTEM_STOP",
                "response": _stopped_response(state, records),
                "execution_decision": "CANCELLED" if state.get("execution_decision") == "CANCELLED" else "PARTIALLY_COMPLETED",
            },
            goto="finalize_memory",
        )
    return Command(update={**update, "execution_decision": "AGENT_FINISHED"}, goto="generate_response")


def _successful_tool_labels(records) -> list[str]:
    """只列真实成功的步骤，不能把模型的回答草稿当成成功证据。"""
    from backend.agent.streaming import TOOL_LABELS
    return [
        TOOL_LABELS.get(record["tool"], record["tool"])
        for record in records
        if record.get("status") in {"SUCCESS", "RESOLVED"}
        and (record.get("result") or {}).get("success")
    ]


def _stopped_response(state, records) -> str:
    """取消/系统停止使用确定性文案，不再花模型预算解释内部错误。"""
    reasons = {
        "LOOP_NO_PROGRESS": "连续调用没有获得新信息，已停止后续操作。",
        "MODEL_PROTOCOL_INVALID": "模型工具决策修复后仍不合法，已停止后续操作。",
        "TOOL_UNRECOVERABLE": "工具出现权限或系统错误，已停止后续操作。",
        "CONFIRMATION_INVALID": "确认数据格式错误，已停止后续操作。",
        "CONFIRMATION_CHANGED": "等待确认的操作内容已经变化，已拒绝执行。",
    }
    if state.get("execution_decision") == "CANCELLED":
        response = state.get("response") or "已取消本次任务。"
    else:
        response = reasons.get(state.get("stop_reason"), "执行协议已变化，已停止后续操作，请核对业务数据。")
    successes = _successful_tool_labels(records)
    if successes:
        response += "\n已确认的成功工具步骤：" + "、".join(successes) + "。已经提交的操作不会回滚。"
    return response


def finish_direct_budget(state, snapshot, saved, warnings):
    """共用 finish_budget 的 PG/Neo4j 核对结果，耗尽后不再请求模型。

    saved 是按 owner + request 读取并核对的成功回执，覆盖‘业务已提交，但节点
    尚未写检查点’的窗口。没有 PG success 不能自行断定没有产生业务副作用。
    """
    calls = {c["call_id"]: c for c in direct_calls(state)}
    records = {r["call_id"]: r for r in direct_records(state)}
    for call_id, row in saved.items():
        call = {
            **calls.get(call_id, {}),
            "call_id": call_id,
            "step_id": calls.get(call_id, {}).get("step_id", call_id),
            "tool": row["tool_name"],
            "args": row.get("arguments") or calls.get(call_id, {}).get("args", {}),
        }
        calls[call_id] = call
        records[call_id] = {**call, "status": "SUCCESS", "result": row["result"], "error": None, "error_code": None}
    reason = snapshot.get("exhausted_reason") or state.get("budget_reason") or "任务执行预算已耗尽"
    successes = _successful_tool_labels(records.values())
    lines = [reason + "。已停止后续步骤；不能确认全部需求均已完成。"]
    if successes:
        lines.append("已核对的成功工具步骤：" + "、".join(successes) + "。已提交的操作不会回滚。")
    lines.extend(warnings)
    return Command(
        update={
            "direct_tool_calls": list(calls.values()),
            "direct_tool_results": list(records.values()),
            # 已核对并合并当前轮。清空旧批次，避免旧 TRANSIENT 再次覆盖 SUCCESS。
            "tool_calls": [], "tool_results": [], "execution_errors": [],
            "execution_decision": "BUDGET_EXCEEDED",
            "completion_basis": "SYSTEM_STOP",
            "requirements_verified": False,
            "budget_snapshot": snapshot,
            "response": "\n".join(lines),
        },
        goto="finalize_memory",
    )
