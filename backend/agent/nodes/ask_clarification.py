"""收集继续执行所缺少的信息，并从同一 LangGraph 检查点恢复。"""

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
    reason = errors[-1] if errors else "当前信息不足，无法确定下一步操作"
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
    """把用户补充内容加入原问题，重新进行意图识别和规划。

    ``replan_count`` 同时作为 plan_version，递增后会生成新 call_id，避免新计划
    错误复用旧计划中参数不同的 Tool 调用记录。
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

    return Command(
        update={
            "user_input": f"{state['user_input']}\n用户补充：{normalized}",
            "tool_calls": [],
            "tool_results": [],
            "execution_errors": [],
            "next_tool_index": 0,
            "execution_decision": "",
            "replan_feedback": {},
            "replan_count": state.get("replan_count", 0) + 1,
            "response": "",
        },
        goto="classify_intent",
    )


def ask_clarification(state: AgentState) -> Command:
    """暂停图，等待用户补充信息或选择真实候选项。

    它和 ``request_confirmation`` 都使用 LangGraph ``interrupt``，但权限语义
    完全不同：

    * clarification 只补全业务信息，永远不能注入 ``confirmed=True``；
    * confirmation 只授权已经确定的高风险操作，不能偷偷更换操作参数。

    本节点在 ``interrupt`` 之前只读取 state、构造 payload，不执行 Tool 或写
    数据库。LangGraph 恢复节点时会从头重放，所以这一限制非常重要。
    """
    payload, ambiguous = _build_clarification_payload(state)
    resume_value = interrupt(payload)

    if ambiguous:
        selected = _select_original_candidate(ambiguous, resume_value)
        if selected is None:
            return Command(
                update={"response": "候选人物选择无效，请重新发起请求。"},
                goto=END,
            )
        return _resume_after_candidate(state, ambiguous, selected)

    return _resume_after_free_text(state, resume_value)
