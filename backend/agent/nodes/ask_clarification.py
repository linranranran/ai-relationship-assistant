"""澄清入口：收集业务信息，不批准写入，也不让用户修复内部工具参数。

先看 ask_clarification 的候选/指代/自由文本分流，再看对应恢复函数。
_resume_after_free_text 会区分 Direct 请求、当前 goal 和旧单目标检查点。
候选选择使用中断时的真实候选回填原步骤，不重新调用模型猜人物 ID。
"""

from typing import Any

from langgraph.graph import END
from langgraph.types import Command, interrupt

from backend.agent.execution_enums import ToolStepStatus
from backend.agent.execution_policy import ToolErrorCode
from backend.agent.state import AgentState


MAX_CLARIFICATION_LENGTH = 4000


def _ambiguous_record(state: AgentState) -> dict | None:
    """找到需要用户选择候选项的 Tool 结果。"""
    for record in reversed(state.get("tool_results", [])):
        if record.get("error_code") != ToolErrorCode.AMBIGUOUS:
            continue
        result = record.get("result")
        data = result.get("data") if isinstance(result, dict) else None
        candidates = data.get("candidates") if isinstance(data, dict) else None
        if isinstance(candidates, list) and candidates:
            return record
    return None


def _candidate_id(candidate: dict) -> str | None:
    """兼容人物 Tool 常用的 ID 字段；最终仍会与原始候选列表核对。"""
    value = candidate.get("id") or candidate.get("person_id")
    return str(value) if value is not None else None


def _public_candidate(candidate: dict) -> dict:
    """只把用户作选择所需的信息发给前端，不泄露内部审计字段。"""
    candidate_id = _candidate_id(candidate)
    label = candidate.get("name") or candidate.get("label") or candidate_id or "未知人物"
    details = [
        candidate.get("occupation"),
        candidate.get("relation_type"),
        candidate.get("note"),
    ]
    return {
        "id": candidate_id,
        "label": str(label),
        "description": " · ".join(str(item) for item in details if item),
    }


def _build_clarification_payload(state: AgentState) -> tuple[dict, dict | None]:
    """优先生成候选选择；没有结构化候选时退化为自由文本补充。"""
    pending_reference = state.get("pending_reference")
    if isinstance(pending_reference, dict):
        candidates = pending_reference.get("candidates", [])
        if isinstance(candidates, list) and candidates:
            return ({
                "interaction_type": "CLARIFICATION",
                "clarification_type": "CANDIDATE_SELECTION",
                "question": "你说的‘他/她’是哪位人物？",
                "candidates": [
                    {"id": item["person_id"], "label": item["name"], "description": ""}
                    for item in candidates if isinstance(item, dict)
                    and item.get("person_id") and item.get("name")
                ],
            }, None)
        return ({
            "interaction_type": "CLARIFICATION",
            "clarification_type": "FREE_TEXT",
            "question": "你说的‘他/她’是谁？请提供姓名或更具体的信息。",
            "candidates": [],
        }, None)
    ambiguous = _ambiguous_record(state)
    if ambiguous:
        candidates = ambiguous["result"]["data"]["candidates"]
        public_candidates = [
            _public_candidate(candidate)
            for candidate in candidates
            if isinstance(candidate, dict) and _candidate_id(candidate)
        ]
        if public_candidates:
            return (
                {
                    "interaction_type": "CLARIFICATION",
                    "clarification_type": "CANDIDATE_SELECTION",
                    "question": "找到了多个符合条件的人物，请选择你指的是哪一个。",
                    "candidates": public_candidates,
                },
                ambiguous,
            )

    errors = state.get("execution_errors", [])
    reason = state.get("clarification_question") or (errors[-1] if errors else "当前信息不足，无法确定下一步操作")
    return (
        {
            "interaction_type": "CLARIFICATION",
            "clarification_type": "FREE_TEXT",
            "question": f"{reason}。请补充更具体的信息。",
            "candidates": [],
        },
        None,
    )


def _select_original_candidate(record: dict, resume_value: Any) -> dict | None:
    """验证前端选择必须来自 interrupt 展示的原始 Tool 候选集合。"""
    if not isinstance(resume_value, dict):
        return None
    selected_id = resume_value.get("candidate_id")
    if not isinstance(selected_id, str) or not selected_id.strip():
        return None

    candidates = record["result"]["data"]["candidates"]
    return next(
        (
            candidate
            for candidate in candidates
            if isinstance(candidate, dict) and _candidate_id(candidate) == selected_id
        ),
        None,
    )


def _resume_after_candidate(
    state: AgentState,
    ambiguous_record: dict,
    selected: dict,
) -> Command:
    """把歧义查询改写为成功结果，从它的下一步继续执行。

    后续步骤之前因为依赖失败可能已被标记为 SKIPPED，因此这里保留歧义步骤
    之前的执行记录，写入用户选择后的成功结果，并让执行器重新处理后续步骤。
    已经真正成功过的独立步骤即使再次经过执行器，也会由 call_id 幂等记录
    直接复用，不会重复产生业务副作用。
    """
    step_id = ambiguous_record["step_id"]
    tool_calls = state.get("tool_calls", [])
    call_index = next(
        (index for index, call in enumerate(tool_calls) if call.get("step_id") == step_id),
        None,
    )
    if call_index is None:
        return Command(
            update={"response": "原执行步骤已经失效，请重新发起请求。"},
            goto=END,
        )

    earlier_step_ids = {
        call.get("step_id") for call in tool_calls[:call_index] if call.get("step_id")
    }
    preserved_records = [
        record
        for record in state.get("tool_results", [])
        if record.get("step_id") in earlier_step_ids
    ]
    selected_result = {
        "success": True,
        "message": "用户已从候选人物中完成选择",
        # 与规划 Prompt 的 data.id 引用保持一致，后续步骤无需让模型重新猜 ID。
        "data": selected,
    }
    preserved_records.append(
        {
            "step_id": step_id,
            "call_id": ambiguous_record["call_id"],
            "tool": ambiguous_record["tool"],
            "status": ToolStepStatus.SUCCESS.value,
            "result": selected_result,
            "error": None,
            "error_code": None,
        }
    )
    return Command(
        update={
            "tool_results": preserved_records,
            "execution_errors": [],
            "next_tool_index": call_index + 1,
            "execution_decision": "",
            "response": "",
        },
        goto="execute_tools",
    )


def _resume_after_free_text(state: AgentState, resume_value: Any) -> Command:
    """校验补充文本，然后按请求的既定编排方式恢复。

    Direct：补充加入请求账本，重建上下文，继续整次请求的决策。
    目标模式：只补当前 goal，不重新拆整句，不丢其他目标的结果。
    旧单目标：兼容原问题追加补充文本的行为。
    """
    answer = resume_value.get("answer") if isinstance(resume_value, dict) else None
    if not isinstance(answer, str) or not answer.strip():
        return Command(
            update={"response": "没有收到有效的补充信息，本次任务已结束。"},
            goto=END,
        )
    normalized = answer.strip()
    if len(normalized) > MAX_CLARIFICATION_LENGTH:
        return Command(
            update={"response": "补充信息过长，请精简后重新发起请求。"},
            goto=END,
        )

    from backend.agent.strategies.contracts import is_request_direct
    if is_request_direct(state):
        return _resume_direct_text(state, normalized)
    if state.get("execution_version") == 2 and state.get("active_goal_id"):
        return _resume_goal_text(state, normalized)
    return _resume_legacy_text(state, normalized)


def _clarification_messages(state, answer, decision_id):
    """把回答配回原模型动作；恢复同一轮时替换协议组，避免重复历史。"""
    from backend.agent.strategies.tool_protocol import append_tool_result_messages
    pending = state.get("pending_decision") or {}
    return append_tool_result_messages(
        state.get("strategy_messages", []), pending.get("message", {}),
        {"user_answer": answer}, decision_id=decision_id,
    )


def _resume_direct_text(state, answer) -> Command:
    """先保存当前轮事实，再清空工作区；原 user_input 和已完成写入不变。"""
    from backend.agent.direct_runtime import direct_calls, direct_records
    messages = _clarification_messages(
        state, answer, f"request:{state['request_id']}:{state.get('decision_seq', 0)}",
    )
    return Command(
        update={
            "direct_tool_calls": direct_calls(state),
            "direct_tool_results": direct_records(state),
            "direct_clarifications": [*state.get("direct_clarifications", []), answer],
            "strategy_messages": messages,
            "pending_decision": {}, "tool_calls": [], "tool_results": [],
            "pending_reference": None, "pending_confirmation": None,
            "clarification_question": "", "execution_errors": [],
            "next_tool_index": 0, "no_progress_rounds": 0, "response": "",
        },
        # 新补充影响上下文相关性和 Token 预算，不能直接复用中断前的提示词。
        goto="prepare_context",
    )


def _goal_resume_target(state, goal) -> str:
    """先按业务类型选入口，再处理 Discovery 的逐步策略分流。"""
    if goal["intent"] == "UNCLEAR":
        return "refine_goal"
    if goal["intent"] in {"ADD_PERSON", "UPDATE_PERSON", "ADD_RELATION", "UPDATE_RELATION"}:
        return "extract_info"
    if state.get("strategy_mode", "plan") == "plan":
        return "plan_tasks"

    query = goal.get("query_spec") or {}
    needs_target = query.get("kind") in {"person_detail", "relation_path", "kinship_title"}
    return "refine_goal" if needs_target and not query.get("target_name") else "strategy_dispatch"


def _resume_goal_text(state, answer) -> Command:
    """仅修订当前目标，先归档旧计划；预算和其他目标始终保留。"""
    from backend.agent.goal_policy import active_goal, replace_goal, archive_current_plan
    goal = active_goal(state)
    revision = state.get("plan_revision", 0) + 1
    archived = {**state, "goals": archive_current_plan(state)}
    goals = replace_goal(
        archived, clarifications=[*goal.get("clarifications", []), answer], plan_revision=revision,
    )
    messages = _clarification_messages(
        state, answer, f"{state.get('active_goal_id')}:{state.get('decision_seq', 0)}",
    )
    return Command(
        update={
            "goals": goals, "plan_revision": revision,
            "tool_calls": [], "tool_results": [], "execution_errors": [],
            "next_tool_index": 0, "pending_confirmation": None, "pending_reference": None,
            "response": "", "strategy_messages": messages, "pending_decision": {},
            "clarification_question": "", "no_progress_rounds": 0,
        },
        goto=_goal_resume_target(state, goal),
    )


def _resume_legacy_text(state, normalized) -> Command:
    """旧检查点兼容分支：追加问题并重建上下文，不迁移到新策略。"""
    return Command(
        update={
            "user_input": f"{state['user_input']}\n用户补充：{normalized}",
            "tool_calls": [],
            "tool_results": [],
            "execution_errors": [],
            "next_tool_index": 0,
            "execution_decision": "",
            # 即使用户补充导致生成新计划，也不能忘掉旧计划已经完成的业务效果。
            # 执行器还会按解析后的工具参数匹配 completed_mutations，防止模型重复写。
            "replan_feedback": {
                "reason": "用户补充信息，请仅规划剩余工作",
                "completed_mutations": state.get("completed_mutations", []),
                "previous_tool_results": state.get("tool_results", []),
            },
            "replan_count": state.get("replan_count", 0) + 1,
            "response": "",
            "pending_reference": None,
            "reference_resolution": {"status": "SKIP", "reason": "user_supplied_new_information"},
        },
        # 用户补充形成了新的输入，必须重新经过上下文预算和记忆检索，不能直接
        # 使用中断前的 model_context。
        goto="resolve_person_reference",
    )


def _resume_after_reference_choice(state: AgentState, resume_value: Any) -> Command:
    """只能使用中断时展示过的候选，禁止前端提交任意 person_id。"""
    pending = state.get("pending_reference") or {}
    selected_id = resume_value.get("candidate_id") if isinstance(resume_value, dict) else None
    if (pending.get("owner_id") != state["user_id"]
            or pending.get("conversation_id") != state.get("conversation_id", "default")):
        return Command(update={"response": "人物选择已失效，请重新提问。"}, goto=END)
    selected = next(
        (item for item in pending.get("candidates", [])
         if isinstance(item, dict) and item.get("person_id") == selected_id),
        None,
    )
    if selected is None:
        return Command(update={"response": "人物选择无效，请重新提问。"}, goto=END)
    focus = {
        **selected,
        "owner_id": state["user_id"],
        "conversation_id": state.get("conversation_id", "default"),
        "source_request_id": state["request_id"],
    }
    return Command(
        update={
            "person_focus": focus,
            "resolved_reference": focus,
            "pending_reference": None,
            "execution_errors": [],
            "reference_resolution": {
                "status": "RESOLVED", "method": "user_selected_candidate",
                "person_id": selected["person_id"],
            },
        },
        goto="prepare_context",
    )


def ask_clarification(state: AgentState) -> Command:
    """暂停图，等待用户补充信息或选择真实候选项。

    它和 ``request_confirmation`` 都使用 LangGraph ``interrupt``，但权限语义
    完全不同：

    * clarification 只补全业务信息，永远不能注入 ``confirmed=True``；
    * confirmation 只授权已经确定的写入，不能偷偷更换操作参数。

    本节点在 ``interrupt`` 前不执行业务 Tool。唯一的写入是按稳定操作摘要
    幂等登记澄清预算；LangGraph 恢复时从头重放，不会重复扣费。
    """
    payload, ambiguous = _build_clarification_payload(state)
    _register_clarification_budget(state, payload, ambiguous)
    resume_value = interrupt(payload)

    # 取消是用户显式结束本轮，不把“取消执行”当作补充文本继续规划。
    if isinstance(resume_value, dict) and resume_value.get("cancelled") is True:
        from backend.agent.strategies.contracts import is_request_direct
        return Command(
            update={
                "execution_decision": "CANCELLED",
                "response": "已取消本次任务。",
            },
            goto="direct_finish" if is_request_direct(state) else END,
        )

    if ambiguous:
        selected = _select_original_candidate(ambiguous, resume_value)
        if selected is None:
            return Command(
                update={"response": "候选人物选择无效，请重新发起请求。"},
                goto=END,
            )
        return _resume_after_candidate(state, ambiguous, selected)

    if isinstance(state.get("pending_reference"), dict) and state["pending_reference"].get("candidates"):
        return _resume_after_reference_choice(state, resume_value)

    return _resume_after_free_text(state, resume_value)


def _register_clarification_budget(state, payload, ambiguous) -> None:
    """interrupt 会重放本节点：稳定标识确保同一问题只登记一次澄清预算。"""
    if state.get("execution_version") != 2:
        return
    from hashlib import sha256
    import json
    from backend.agent.budget import reserve, settle
    from backend.agent.strategies.contracts import is_request_direct

    identity = [state.get("active_goal_id"), state.get("plan_revision", 0),
                (ambiguous or {}).get("call_id"), payload]
    if is_request_direct(state):
        identity.append(state.get("decision_seq", 0))
    # 旧模式的标识结构保持不变；改变它会让已持久化问题恢复时重复扣预算。
    key = sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    attempt = reserve("clarification", key)
    settle(attempt, result={"registered": True})
