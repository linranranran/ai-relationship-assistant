"""只把成功的 Tool 结果作为会话人物焦点，避免让模型猜测代词指向的 ID。"""

import re
from typing import Any


# 只自动处理明确以单数人物代词开头的追问。句中代词可能指新提到的人，
# 例如“李四说他的爱好是钓鱼”，这种情况仍交给常规意图/人物解析。
_LEADING_REFERENCE = re.compile(
    r"^\s*(?:那)?(?:他|她|这个人|那个人|这位|那位)(?:的|是|有|和|与|怎么|什么|呢|吗|[？?\s]|$)"
)
_PERSON_TOOLS = frozenset({"find_person", "get_person_detail", "add_person", "update_person"})


def derive_focus(
    *, owner_id: str, conversation_id: str, request_id: str,
    tool_results: list[dict],
) -> tuple[dict[str, str] | None, list[dict[str, str]]]:
    """从本轮已成功的单人 Tool 结果提取焦点；多人时只保留候选。"""
    people: dict[str, dict[str, str]] = {}
    for record in tool_results:
        if not isinstance(record, dict) or record.get("tool") not in _PERSON_TOOLS:
            continue
        if record.get("status") != "SUCCESS":
            continue
        result = record.get("result")
        if not isinstance(result, dict) or result.get("success") is not True:
            continue
        data = result.get("data")
        if not isinstance(data, dict):
            continue
        person = data.get("person") if isinstance(data.get("person"), dict) else data
        person_id = person.get("id") or person.get("person_id")
        name = person.get("name")
        if isinstance(person_id, str) and person_id and isinstance(name, str) and name:
            people[person_id] = {"person_id": person_id, "name": name}
    candidates = list(people.values())
    if len(candidates) != 1:
        return None, candidates
    return {
        **candidates[0],
        "owner_id": owner_id,
        "conversation_id": conversation_id,
        "source_request_id": request_id,
    }, candidates


def resolve_reference(state: dict[str, Any]) -> dict[str, Any]:
    """返回 SKIP / RESOLVED / CLARIFY；跨用户或跨会话的焦点一律无效。"""
    question = state.get("user_input", "")
    if not isinstance(question, str) or "用户补充：" in question or not _LEADING_REFERENCE.match(question):
        return {"status": "SKIP", "reference": None, "candidates": []}
    owner_id = state.get("user_id")
    conversation_id = state.get("conversation_id", "default")
    focus = state.get("person_focus")
    if isinstance(focus, dict) and (
        focus.get("owner_id") == owner_id
        and focus.get("conversation_id") == conversation_id
        and isinstance(focus.get("person_id"), str)
        and focus.get("person_id")
    ):
        return {"status": "RESOLVED", "reference": focus, "candidates": []}
    candidates = state.get("person_focus_candidates", [])
    if (state.get("person_focus_candidates_owner_id") != owner_id
            or state.get("person_focus_candidates_conversation_id") != conversation_id):
        candidates = []
    candidates = [
        item for item in candidates if isinstance(item, dict)
        and isinstance(item.get("person_id"), str)
        and isinstance(item.get("name"), str)
    ] if isinstance(candidates, list) else []
    return {"status": "CLARIFY", "reference": None, "candidates": candidates[:5]}
