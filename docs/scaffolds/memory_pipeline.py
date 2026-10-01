"""客户记忆教学骨架：原始消息、事实版本和可重建向量索引。

建议落地：
- backend/memory/models.py：SourceMessage、MemoryFact、EvidenceRef。
- backend/memory/import_service.py：固定 JSON 导入、人物映射、批次幂等。
- backend/memory/fact_service.py：候选事实校验、更正、版本状态。
- backend/memory/retrieval.py：owner/person/time 范围内检索并回查权威事实。
- backend/memory/brief_service.py：生成带证据的沟通准备结果。

本文件只固定契约。不会读取文件、数据库或调用模型。
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal, Mapping, Protocol

from identity_access import RequestContext


class FactStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    PENDING_CONFIRMATION = "pending_confirmation"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ImportMessage:
    external_message_id: str
    speaker_external_id: str
    sent_at: datetime
    text: str


@dataclass(frozen=True)
class ImportBatch:
    source_type: Literal["demo_json"]
    source_id: str
    messages: tuple[ImportMessage, ...]


@dataclass(frozen=True)
class SourceMessage:
    message_id: str
    owner_id: str
    person_id: str
    source_id: str
    external_message_id: str
    speaker_role: Literal["user", "customer"]
    sent_at: datetime
    text: str
    content_hash: str


@dataclass(frozen=True)
class EvidenceRef:
    message_id: str
    quote: str
    sent_at: datetime


@dataclass(frozen=True)
class MemoryFact:
    fact_id: str
    owner_id: str
    person_id: str
    category: str
    value: str
    status: FactStatus
    version: int
    evidence: tuple[EvidenceRef, ...]
    supersedes_fact_id: str | None
    valid_from: datetime
    valid_to: datetime | None
    index_status: Literal["pending", "ready", "failed"]


@dataclass(frozen=True)
class FactCandidate:
    person_id: str
    category: str
    value: str
    evidence_message_ids: tuple[str, ...]
    mode: Literal["assert", "correct"]
    replaces_fact_id: str | None


@dataclass(frozen=True)
class MemoryHit:
    fact: MemoryFact
    score: float | None


@dataclass(frozen=True)
class AdviceItem:
    suggestion: str
    reason: str
    evidence: tuple[EvidenceRef, ...]


@dataclass(frozen=True)
class CommunicationBrief:
    person_id: str
    facts: tuple[MemoryHit, ...]
    advice: tuple[AdviceItem, ...]
    missing_information: tuple[str, ...]


class MemoryRepository(Protocol):
    def save_import(self, *, ctx: RequestContext, person_id: str, batch: ImportBatch) -> tuple[SourceMessage, ...]: ...
    def get_messages(self, *, ctx: RequestContext, message_ids: tuple[str, ...]) -> tuple[SourceMessage, ...]: ...
    def get_active_facts(self, *, ctx: RequestContext, person_id: str) -> tuple[MemoryFact, ...]: ...
    def apply_fact_change(self, *, ctx: RequestContext, candidate: FactCandidate, operation_id: str) -> MemoryFact: ...


class FactExtractor(Protocol):
    def extract(self, *, person_id: str, messages: tuple[SourceMessage, ...]) -> tuple[FactCandidate, ...]: ...


class FactIndex(Protocol):
    def search(self, *, owner_id: str, person_id: str, query: str, limit: int) -> tuple[tuple[str, float], ...]: ...
    def upsert(self, *, fact: MemoryFact) -> None: ...


def import_chat_batch(
    *,
    ctx: RequestContext,
    person_id: str,
    batch: ImportBatch,
    repository: MemoryRepository,
) -> tuple[SourceMessage, ...]:
    # 1. 在导入前用身份服务验证 person_id 属于 ctx.user_id。
    # 2. 仅接受 demo_json；限制消息数、单条长度、总长度和时间范围。
    # 3. 验证 external_message_id 在同 source_id 中唯一，时间带时区且可解析。
    # 4. 说话人必须由导入页面明确映射为 user/customer，不能让模型猜身份。
    # 5. 原文是数据：其中的“忽略规则”“查看其他客户”等句子没有系统权限。
    # 6. 使用 owner+source_id+external_message_id 作为幂等键；同键不同内容报冲突。
    # 7. 完整批次重试返回已有记录；真实的两条相同文本不能因文本相同被去重。
    # 8. repository 在事务中保存；返回稳定 message_id，供事实证据使用。
    raise NotImplementedError("请实现：校验并幂等导入一种聊天 JSON")


def validate_fact_candidate(
    *,
    ctx: RequestContext,
    candidate: FactCandidate,
    repository: MemoryRepository,
) -> FactCandidate:
    # 1. 读取全部证据消息；任何不存在/异主/异人物证据都拒绝。
    # 2. 检查 quote 所需信息确实来自证据；模型给的置信度不能替代证据。
    # 3. 长期偏好、临时状态、背景、近期事项采用不同 category，避免混写。
    # 4. “最近没空钓鱼”不能自动变成“不喜欢钓鱼”。含糊矛盾应待确认。
    # 5. mode=correct 必须明确指定属于同人物的 active fact；不按相似文本猜替换。
    # 6. 过滤控制字符与超长值，保留原始消息不作破坏性清洗。
    raise NotImplementedError("请实现：用原始证据验证候选事实")


def apply_fact_candidate(
    *,
    ctx: RequestContext,
    candidate: FactCandidate,
    operation_id: str,
    repository: MemoryRepository,
) -> MemoryFact:
    # 1. 先调用 validate_fact_candidate。
    # 2. 在权威存储的一个事务中写新版本、失效旧版本、设置 index_status=pending。
    # 3. 明确更正保留旧事实，status=superseded；不物理删除审计证据。
    # 4. operation_id 重复时返回同一版本；并发更新使用版本检查，冲突让用户重试。
    # 5. 索引写入不属于业务事务；本函数提交成功即意味着事实可从权威库查询。
    raise NotImplementedError("请实现：版本化保存事实或明确更正")


def retrieve_active_memories(
    *,
    ctx: RequestContext,
    person_id: str,
    query: str,
    limit: int,
    repository: MemoryRepository,
    index: FactIndex,
) -> tuple[MemoryHit, ...]:
    # 1. 限制 query 和 limit；person_id 必须属于 ctx。
    # 2. 向量查询同时限定 owner 和 person；索引只返回 fact_id+distance/score。
    # 3. 对每个命中回查权威事实，只保留 active、最新版本和合法证据。
    # 4. 已失效事实即使旧向量仍命中也不得返回；重复命中按 fact_id 合并。
    # 5. 索引不可用时退化为结构化 active facts，并在结果元数据说明退化。
    # 6. 稳定排序，记录实际 metric；不要把未知距离公式包装成相似度。
    raise NotImplementedError("请实现：检索并回查当前有效记忆")


def build_communication_brief(
    *,
    ctx: RequestContext,
    person_id: str,
    question: str,
    memories: tuple[MemoryHit, ...],
) -> CommunicationBrief:
    # 1. 构造模型输入时只放允许的有效事实、证据片段和用户问题。
    # 2. 输出结构先校验；每条事实陈述与建议理由必须引用 evidence message_id。
    # 3. 引用 quote 必须是原文连续子串或明确标注为摘要，不能由模型伪造。
    # 4. 建议与事实分栏；“可以问钓鱼近况”是建议，不是客户新事实。
    # 5. 没有足够证据时列 missing_information，仍可给低风险通用开场。
    # 6. 最后再次按 ctx 回查引用消息，防止跨用户 ID 或已删除证据泄露。
    raise NotImplementedError("请实现：生成每条建议都能回到证据的沟通准备")


# 首批验收：重复导入、同文本不同消息、证据异主、明确更正、含糊否定、
# 旧向量命中、索引失败重放、同名人物、引用不存在、模拟提示注入文本。
