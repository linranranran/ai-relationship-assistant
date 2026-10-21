"""按客户装配有限证据，不调用模型，不复用 Agent 聊天表作为客户聊天。

先过滤归属和人物，再取最近连续消息；Token 窗口只是入模限制，完整原文仍在
数据库。coverage 用于说明这次看到了什么，而不是证明未检索到的事从未发生。
"""

from datetime import datetime, timezone
import json

from backend.config import SALES_PREP_CANDIDATE_LIMIT, SALES_PREP_MESSAGE_LIMIT, SALES_PREP_EVIDENCE_TOKEN_BUDGET
from backend.db.customer_chat_store import list_candidates
from backend.agent.memory.runtime import get_memory_runtime
from backend.agent.tracing import trace_event
from backend.sales.contracts import EvidenceSource, EvidenceCoverage, SalesPreparationContext
from backend.sales.importer import require_owned_profile

PROFILE_LABELS = {"name": "姓名", "occupation": "职业", "education": "教育背景",
                  "hobbies": "爱好", "birth_year": "出生年份", "note": "档案备注"}


def validate_sales_config() -> None:
    if not (1 <= SALES_PREP_MESSAGE_LIMIT <= SALES_PREP_CANDIDATE_LIMIT <= 1000):
        raise ValueError("销售消息窗口必须满足 1 <= MESSAGE_LIMIT <= CANDIDATE_LIMIT <= 1000")
    if SALES_PREP_EVIDENCE_TOKEN_BUDGET < 256:
        raise ValueError("销售证据 Token 预算至少为 256")


def _tokens(sources: list[EvidenceSource], counter) -> int:
    # 除正文外，来源 ID、时间与说话人也要计费；这里估算整个来源数组。
    content = json.dumps([source.model_dump(mode="json") for source in sources], ensure_ascii=False)
    return counter.count_messages([{"role": "user", "content": content}])


def _fit_source(source: EvidenceSource, selected: list[EvidenceSource], budget: int, counter):
    if _tokens([*selected, source], counter) <= budget:
        return source
    # 单条太长时二分裁剪前缀。绝不添加模型没见过的“补全内容”；truncated
    # 明确告诉模型与用户这不是完整消息，因此不能基于遗漏部分做否定推断。
    low, high, best = 1, len(source.excerpt), None
    while low <= high:
        middle = (low + high) // 2
        candidate = source.model_copy(update={"excerpt": source.excerpt[:middle], "truncated": True})
        if _tokens([*selected, candidate], counter) <= budget:
            best, low = candidate, middle + 1
        else:
            high = middle - 1
    return best


def build_sales_preparation_context(*, owner_id: str, person_id: str, meeting_goal: str) -> SalesPreparationContext:
    validate_sales_config()
    profile = require_owned_profile(owner_id, person_id)
    as_of = datetime.now(timezone.utc)
    candidates = list_candidates(owner_id=owner_id, person_id=person_id, as_of=as_of,
                                 limit=SALES_PREP_CANDIDATE_LIMIT)
    counter = get_memory_runtime().token_counter
    budget = SALES_PREP_EVIDENCE_TOKEN_BUDGET
    selected: list[EvidenceSource] = []

    # 档案只用少量预算；防止巨大的 note 把最近的客户问答挤出证据窗口。
    profile_budget = min(600, budget // 4)
    for field, label in PROFILE_LABELS.items():
        value = profile.get(field)
        if value in (None, "", []):
            continue
        value = "、".join(map(str, value)) if isinstance(value, list) else str(value)
        source = EvidenceSource(source_id=f"profile:{person_id}:{field}",
                                source_type="profile_snapshot", occurred_at=as_of, speaker="profile",
                                excerpt=f"{label}：{value}")
        fitted = _fit_source(source, selected, profile_budget, counter)
        if fitted:
            selected.append(fitted)

    chat_sources = []
    # 仓储返回倒序。选择最近的连续后缀，再正序交给模型；不跳过大消息去挑
    # 很久以前的小消息。每条带 conversation_id，避免把两个会话串成一组对话。
    for row in candidates[:SALES_PREP_MESSAGE_LIMIT]:
        original = row.text
        # 先限制单条片段长度，给邻近发言留下空间；完整原文可通过 message_id 查看。
        source = EvidenceSource(source_id=f"chat:{row.message_id}", source_type="simulated_chat",
                                message_id=row.message_id, source_conversation_id=row.source_conversation_id,
                                occurred_at=row.sent_at, speaker=row.speaker,
                                excerpt=original[:800], truncated=len(original) > 800)
        fitted = _fit_source(source, [*selected, *chat_sources], budget, counter)
        if fitted is None or len(fitted.excerpt.strip()) < min(40, len(source.excerpt.strip())):
            break
        chat_sources.append(fitted)
    chat_sources.reverse()
    selected.extend(chat_sources)
    cut = len(chat_sources) < len(candidates) or any(source.truncated for source in selected)
    limits = ["聊天为模拟数据，不代表真实客户沟通；本次仅按客户和最近时间范围检索。"]
    if not chat_sources:
        limits.append("本次可见范围没有客户聊天，不能推断客户需求或约定。")
    if cut:
        limits.append("部分消息未入模或原文片段被裁剪；不能对全部历史作否定结论。")
    coverage = EvidenceCoverage(candidate_count=len(candidates),
                                selected_source_ids=[source.source_id for source in selected],
                                chat_source_count=len(chat_sources),
                                from_time=min((source.occurred_at for source in chat_sources), default=None),
                                to_time=max((source.occurred_at for source in chat_sources), default=None),
                                estimated_tokens=_tokens(selected, counter), truncated=cut,
                                candidate_limit_reached=len(candidates) == SALES_PREP_CANDIDATE_LIMIT,
                                limitations=limits)
    trace_event("sales.context_selected", person_id=person_id, source_ids=coverage.selected_source_ids,
                candidate_count=len(candidates), source_count=len(selected),
                evidence_tokens=coverage.estimated_tokens, truncated=cut)
    return SalesPreparationContext(person_id=person_id, customer_name=str(profile.get("name", "客户")),
                                   meeting_goal=meeting_goal, as_of=as_of, sources=selected, coverage=coverage)
