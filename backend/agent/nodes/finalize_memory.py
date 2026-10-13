"""保存最终对话轮次，并登记与业务 Tool 解耦的记忆维护任务。"""

import logging
from dataclasses import asdict
from uuid import NAMESPACE_URL, uuid5

from backend.agent.memory.fact_pipeline import extract_fact_candidates
from backend.agent.memory.models import (
    MemoryJobType,
    MemoryMessage,
    MemoryRole,
    MemoryWriteCandidate,
)
from backend.agent.memory.runtime import MemoryRuntime, get_memory_runtime
from backend.agent.memory.reference_resolution import derive_focus, resolve_reference
from backend.agent.state import AgentState


logger = logging.getLogger(__name__)


def finalize_memory(state: AgentState) -> dict:
    """幂等保存 user/assistant 消息，并登记摘要与事实任务。

    PostgreSQL ``save_turn`` 使用 ``(owner_id, request_id, role)`` 唯一键。这里的
    稳定 message_id 让相同请求重试得到相同消息身份。

    原始消息是后续摘要和长期事实的证据，因此必须先保存。摘要/事实通过
    ``memory_job`` 交给后台工作者执行；它们失败只重试记忆任务，绝不回到
    execute_tools。每一轮都登记摘要任务，因为只看最近消息无法发现更老的未摘要
    积压；工作者会在未达到最小批次时快速结束。
    """
    runtime = get_memory_runtime()
    from backend.agent.strategies.contracts import is_request_direct
    if is_request_direct(state):
        from backend.agent.direct_runtime import direct_calls, direct_records
        state = {**state, "tool_calls": direct_calls(state), "tool_results": direct_records(state)}
    elif state.get("execution_version") == 2:
        state = {
            **state,
            "tool_calls": [
                call
                for goal in state.get("goals", [])
                for call in goal.get("tool_calls", [])
            ],
            "tool_results": [
                record
                for goal in state.get("goals", [])
                for record in goal.get("tool_results", [])
            ],
        }
    owner_id = state["user_id"]
    conversation_id = state.get("conversation_id", "default")
    request_id = state["request_id"]
    question = state["user_input"]
    if is_request_direct(state):
        answers = [f"用户补充：{answer}" for answer in state.get("direct_clarifications", [])]
        if answers:
            question += "\n\n" + "\n".join(answers)
    elif state.get("execution_version") == 2:
        # State 保留原始提问供幂等恢复；对话证据还需包含本任务中的用户补充。
        # 否则“张三，1990 年出生”来自澄清回答，却只引用了“帮我添加一个人”，
        # 长期事实的 evidence_message_id 就无法追溯具体属性来源。
        answers = [f"目标 {index + 1} 用户补充：{answer}"
                   for index, goal in enumerate(state.get("goals", []))
                   for answer in goal.get("clarifications", [])]
        if answers:
            question += "\n\n" + "\n".join(answers)

    user_message = MemoryMessage(
        message_id=_stable_message_id(owner_id, request_id, MemoryRole.USER),
        owner_id=owner_id,
        conversation_id=conversation_id,
        request_id=request_id,
        role=MemoryRole.USER,
        content=question,
        token_count=runtime.token_counter.count_messages(
            [{"role": "user", "content": question}]
        ),
    )
    assistant_message = MemoryMessage(
        message_id=_stable_message_id(owner_id, request_id, MemoryRole.ASSISTANT),
        owner_id=owner_id,
        conversation_id=conversation_id,
        request_id=request_id,
        role=MemoryRole.ASSISTANT,
        content=state.get("response", ""),
        token_count=runtime.token_counter.count_messages(
            [{"role": "assistant", "content": state.get("response", "")}]
        ),
    )

    # 默认 NoOp adapter 不落库；实现 PostgreSQL adapter 后，这里的异常应向上抛出，
    # 避免业务写操作已经发生但对话记录被误报为已保存。
    runtime.message_store.save_turn(
        owner_id=owner_id,
        conversation_id=conversation_id,
        user_message=user_message,
        assistant_message=assistant_message,
    )

    candidates = extract_fact_candidates(
        tool_calls=state.get("tool_calls", []),
        tool_results=state.get("tool_results", []),
        evidence_message_id=user_message.message_id,
    )
    _enqueue_maintenance_jobs(
        runtime=runtime,
        owner_id=owner_id,
        conversation_id=conversation_id,
        request_id=request_id,
        candidates=candidates,
        summary_required=bool(state.get("context_stats", {}).get("summary_required")),
    )

    recent = _append_turn_idempotently(
        state.get("recent_messages", []),
        user_message,
        assistant_message,
        limit=runtime.settings.recent_message_limit,
    )
    focus, focus_candidates = derive_focus(
        owner_id=owner_id,
        conversation_id=conversation_id,
        request_id=request_id,
        tool_results=state.get("tool_results", []),
    )
    # 代词追问未产生新的单人 Tool 结果时可以继续沿用原焦点；显式切换人物
    # 或一次涉及多人时不沿用旧人物，避免下一轮把“他”指回前一个人。
    if not focus_candidates and resolve_reference(state)["status"] == "RESOLVED":
        focus = state.get("person_focus")
        focus_candidates = state.get("person_focus_candidates", [])
    update = {
        "recent_messages": recent,
        "memory_write_candidates": [asdict(candidate) for candidate in candidates],
        "person_focus": focus,
        "person_focus_candidates": focus_candidates,
        "person_focus_candidates_owner_id": owner_id,
        "person_focus_candidates_conversation_id": conversation_id,
    }
    if state.get("execution_version") == 2:
        from backend.db.agent_budget_store import BudgetStore

        update["budget_snapshot"] = BudgetStore(owner_id, request_id).snapshot()
    return update


def _enqueue_maintenance_jobs(
    *,
    runtime: MemoryRuntime,
    owner_id: str,
    conversation_id: str,
    request_id: str,
    candidates: tuple[MemoryWriteCandidate, ...],
    summary_required: bool,
) -> None:
    """登记可靠后台任务；队列故障不能把已成功的 Tool 伪装成失败。

    ``summary_required`` 记录本轮是否已经接近上下文预算，供运维观察。无论它是否
    为 True 都登记任务，以便工作者基于数据库中的未摘要消息数量作最终判断。
    """
    if not runtime.job_store.persistent:
        return
    try:
        runtime.job_store.enqueue(
            owner_id=owner_id,
            conversation_id=conversation_id,
            request_id=request_id,
            job_type=MemoryJobType.SUMMARY,
            payload={"budget_triggered": summary_required},
        )
        if candidates:
            runtime.job_store.enqueue(
                owner_id=owner_id,
                conversation_id=conversation_id,
                request_id=request_id,
                job_type=MemoryJobType.PERSIST_FACTS,
                payload={"candidates": [asdict(candidate) for candidate in candidates]},
            )
    except Exception:
        # 用户请求仍然完成；告警必须进入监控。摘要可由后续轮次追上，事实任务则可
        # 根据 conversation_message 与 tool_execution 审计记录进行人工补偿。
        logger.exception(
            "记忆维护任务登记失败: owner_id=%s request_id=%s",
            owner_id,
            request_id,
        )


def _stable_message_id(owner_id: str, request_id: str, role: MemoryRole) -> str:
    return str(
        uuid5(NAMESPACE_URL, f"relationship-agent:{owner_id}:{request_id}:{role.value}")
    )


def _append_turn_idempotently(
    current: list[dict],
    user_message: MemoryMessage,
    assistant_message: MemoryMessage,
    *,
    limit: int,
) -> list[dict]:
    by_message_id = {
        item.get("message_id"): item
        for item in current
        if isinstance(item, dict) and item.get("message_id")
    }
    for message in (user_message, assistant_message):
        by_message_id[message.message_id] = asdict(message)

    ordered = [
        item
        for item in current
        if isinstance(item, dict) and item.get("message_id") in by_message_id
    ]
    existing_ids = {item.get("message_id") for item in ordered}
    for message in (user_message, assistant_message):
        if message.message_id not in existing_ids:
            ordered.append(by_message_id[message.message_id])
    # limit 表示消息条数而不是对话轮数；0 表示不在 checkpoint 保留原始消息。
    return ordered[-limit:] if limit > 0 else []
