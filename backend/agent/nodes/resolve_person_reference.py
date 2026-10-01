"""在意图识别前处理可确定的单人代词追问。"""

from langgraph.types import Command

from backend.agent.memory.reference_resolution import resolve_reference
from backend.agent.state import AgentState


def resolve_person_reference(state: AgentState) -> Command:
    result = resolve_reference(state)
    if result["status"] == "RESOLVED":
        return Command(
            update={
                "resolved_reference": result["reference"], "pending_reference": None,
                "reference_resolution": {
                    "status": "RESOLVED", "method": "verified_previous_tool_result",
                    "person_id": result["reference"]["person_id"],
                },
            },
            goto="prepare_context",
        )
    if result["status"] == "CLARIFY":
        return Command(
            update={
                "resolved_reference": {},
                "pending_reference": {
                    "owner_id": state["user_id"],
                    "conversation_id": state.get("conversation_id", "default"),
                    "candidates": result["candidates"],
                },
                "execution_errors": ["无法确定代词指的是哪位人物"],
                "reference_resolution": {
                    "status": "CLARIFY",
                    "reason": "multiple_candidates" if len(result["candidates"]) > 1 else "no_verified_focus",
                    "candidate_ids": [item["person_id"] for item in result["candidates"]],
                },
            },
            goto="ask_clarification",
        )
    return Command(
        update={
            "resolved_reference": {}, "pending_reference": None,
            "reference_resolution": {"status": "SKIP", "reason": "not_single_person_pronoun"},
        },
        goto="prepare_context",
    )
