"""从已成功的业务 Tool 结果中生成可审计的长期事实候选。

这里故意不用 LLM 猜测 person_id 或 Tool 是否成功。只有执行器记录为 SUCCESS 的
写操作才能产生候选，证据固定指向本轮用户原话。自由文本备注不自动进入 active
事实，避免把敏感或含糊信息直接写入客户画像。
"""

from collections.abc import Mapping
from uuid import NAMESPACE_URL, uuid5

from backend.agent.execution_enums import ToolStepStatus
from backend.agent.memory.models import MemoryWriteCandidate


SAFE_PROFILE_FIELDS = (
    "name",
    "gender",
    "birth_year",
    "occupation",
    "education",
    "hobbies",
    "personality",
    "tags",
)


def extract_fact_candidates(
    *,
    tool_calls: list[dict],
    tool_results: list[dict],
    evidence_message_id: str,
) -> tuple[MemoryWriteCandidate, ...]:
    """将成功写操作转换为确定性候选；查询 Tool 和失败步骤不会产生记忆。"""
    if not evidence_message_id:
        raise ValueError("事实候选必须有用户消息证据")
    calls_by_step = {
        call.get("step_id"): call
        for call in tool_calls
        if isinstance(call, dict) and isinstance(call.get("step_id"), str)
    }
    candidates: list[MemoryWriteCandidate] = []
    seen: set[tuple[str, str]] = set()

    for record in tool_results:
        if (
            not isinstance(record, dict)
            or record.get("status") != ToolStepStatus.SUCCESS.value
        ):
            continue
        call = calls_by_step.get(record.get("step_id"))
        if not call:
            continue
        tool_name = call.get("tool")
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        tool_response = record.get("result")
        if not isinstance(tool_response, dict) or not tool_response.get("success"):
            continue
        data = _as_dict(tool_response.get("data"))

        candidate = None
        if tool_name in {"add_person", "update_person"}:
            candidate = _person_profile_candidate(
                args=args,
                data=data,
                evidence_message_id=evidence_message_id,
            )
        elif tool_name == "add_relation":
            candidate = _relationship_candidate(
                args=args,
                data=data,
                evidence_message_id=evidence_message_id,
            )

        if candidate is None:
            continue
        key = (candidate.person_id, candidate.category)
        if key in seen:
            # 同一轮多次更新同一类别时只保存最后一个成功结果。
            candidates = [
                item
                for item in candidates
                if (item.person_id, item.category) != key
            ]
        seen.add(key)
        candidates.append(candidate)
    return tuple(candidates)


def _person_profile_candidate(
    *, args: dict, data: dict, evidence_message_id: str
) -> MemoryWriteCandidate | None:
    person_id = _first_text(data.get("id"), data.get("person_id"), args.get("person_id"))
    if not person_id:
        return None
    profile = {
        field: data.get(field, args.get(field))
        for field in SAFE_PROFILE_FIELDS
        if data.get(field, args.get(field)) not in (None, "", [], {})
    }
    if not profile:
        return None
    return MemoryWriteCandidate(
        person_id=person_id,
        category="person_profile",
        value=profile,
        evidence_message_ids=[evidence_message_id],
        confidence=1.0,
        # 候选来自用户明确要求且已经成功写入业务库的确定性 Tool 结果；Repository
        # 仍会重新校验 owner/person/evidence 后才允许激活。
        requires_confirmation=False,
        reason="成功的人物写操作产生的确定性资料",
    )


def _relationship_candidate(
    *, args: dict, data: dict, evidence_message_id: str
) -> MemoryWriteCandidate | None:
    person_id = _first_text(args.get("to_person_id"), data.get("to_person_id"))
    relation_id = _first_text(data.get("relation_id"))
    relation_type = _first_text(args.get("relation_type"), data.get("type"))
    from_person_id = _first_text(args.get("from_person_id"), data.get("from_person_id"))
    if not person_id or not relation_type or not from_person_id:
        return None

    # category 列只有 64 个字符，不能直接截断最长 128 字符的 relation_id；截断
    # 会让相同前缀的两条关系互相覆盖。UUID5 既固定长度，又能在重试时稳定复用。
    relationship_identity = relation_id or f"{from_person_id}:{person_id}"
    category = f"relationship:{uuid5(NAMESPACE_URL, relationship_identity)}"
    value = {
        "relation_id": relation_id,
        "from_person_id": from_person_id,
        "to_person_id": person_id,
        "relation_type": relation_type,
    }
    for key in ("from_name", "to_name"):
        if isinstance(data.get(key), str) and data[key]:
            value[key] = data[key]
    return MemoryWriteCandidate(
        person_id=person_id,
        category=category,
        value=value,
        evidence_message_ids=[evidence_message_id],
        confidence=1.0,
        requires_confirmation=False,
        reason="成功的关系写操作产生的确定性关系",
    )


def _as_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, Mapping):
        return dict(value)
    try:
        return dict(value) if value is not None else {}
    except (TypeError, ValueError):
        return {}


def _first_text(*values) -> str:
    return next((value for value in values if isinstance(value, str) and value), "")
