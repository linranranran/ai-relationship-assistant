"""销售数据的共同契约。先读本文件，再读导入、检索和生成逻辑。

输入来自三种不同信任边界：浏览器提交模拟聊天；数据库提供证据；模型只
提供分析草稿。故意不把 owner_id 放入请求，不让模型提交来源正文或元数据。
"""

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime, BaseModel, ConfigDict, Field, StrictStr,
    StringConstraints, field_validator, model_validator,
)

Identifier = Annotated[StrictStr, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
ShortText = Annotated[StrictStr, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class SalesContract(BaseModel):
    """拒绝额外字段，防止客户端伪造 owner，或模型添加未经定义的事实来源。"""

    model_config = ConfigDict(extra="forbid")


class SimulatedChatMessage(SalesContract):
    """一条模拟原文；source_message_id 是来源身份，不是 Agent call_id。"""

    source_message_id: Identifier
    speaker: Literal["customer", "sales"]
    sent_at: AwareDatetime
    text: Annotated[StrictStr, StringConstraints(min_length=1, max_length=2000)]

    @field_validator("text")
    @classmethod
    def non_blank_original(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("聊天原文不能为空白")
        # 仅判断空白，不 strip 原文。引用检查需要原始空格、标点和换行。
        return value

    @field_validator("sent_at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        # 同一时刻 +08:00 与 Z 的导入摘要应一致；不接受无时区的猜测。
        return value.astimezone(timezone.utc)


class SimulatedChatPayload(SalesContract):
    """文件内容不含 Person/owner；选中的客户由导入请求另行绑定。"""

    source_conversation_id: Identifier
    messages: list[SimulatedChatMessage] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_batch(self):
        ids = [message.source_message_id for message in self.messages]
        if len(ids) != len(set(ids)):
            raise ValueError("一批聊天不能包含重复的 source_message_id")
        if sum(len(message.text) for message in self.messages) > 50000:
            raise ValueError("一批聊天原文总长度不能超过 50000 字符")
        return self


class ChatImportRequest(SalesContract):
    person_id: Identifier
    payload: SimulatedChatPayload


class ChatImportResult(SalesContract):
    total_count: int = Field(ge=1)
    inserted_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)


class StoredChatMessage(SalesContract):
    """可向前端返回的原文。owner/content_hash 等内部字段不出仓储。"""

    message_id: str
    person_id: str
    source_type: Literal["simulated_chat"] = "simulated_chat"
    source_conversation_id: str
    source_message_id: str
    speaker: Literal["customer", "sales"]
    sent_at: AwareDatetime
    text: str
    created_at: AwareDatetime


class EvidenceSource(SalesContract):
    """服务端授权提供的证据片段；模型只能用 ID 引用，不能捏造正文。"""

    source_id: str
    source_type: Literal["simulated_chat", "profile_snapshot"]
    message_id: str | None = None
    source_conversation_id: str | None = None
    occurred_at: AwareDatetime
    speaker: Literal["customer", "sales", "profile"]
    excerpt: str = Field(min_length=1)
    truncated: bool = False


class EvidenceRef(SalesContract):
    source_id: str = Field(min_length=1, max_length=256)
    quote: Annotated[StrictStr, StringConstraints(min_length=1, max_length=300)]

    @field_validator("quote")
    @classmethod
    def non_blank_quote(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("引用原文不能为空白")
        return value


class FactItem(SalesContract):
    """记录中的说法，不代表独立核实；每个事实必须有至少一条证据。"""

    text: ShortText
    evidence_refs: list[EvidenceRef] = Field(min_length=1, max_length=3)


class CommitmentItem(FactItem):
    # 没有后续记录不是未完成：只能使用 UNKNOWN，不能擅自生成待办。
    completion_state: Literal["OPEN", "DONE", "UNKNOWN"]
    # 约定的证据与状态的证据分开，避免只引用“明天发方案”就报告已完成。
    # 非空引用仍不能自动证明状态语义；生成提示词和人工验收负责这层判断。
    status_evidence_refs: list[EvidenceRef] = Field(default_factory=list, max_length=3)


class RecommendationItem(SalesContract):
    """一般提问允许无依据；个性化建议应引用来源，不能变成客户事实。"""

    text: ShortText
    basis_refs: list[EvidenceRef] = Field(default_factory=list, max_length=3)


class EvidenceCoverage(SalesContract):
    candidate_count: int = Field(ge=0)
    selected_source_ids: list[str]
    chat_source_count: int = Field(ge=0)
    from_time: AwareDatetime | None = None
    to_time: AwareDatetime | None = None
    estimated_tokens: int = Field(ge=0)
    truncated: bool
    candidate_limit_reached: bool
    limitations: list[str]


class SalesPreparationContext(SalesContract):
    person_id: str
    customer_name: str
    meeting_goal: ShortText
    as_of: AwareDatetime
    sources: list[EvidenceSource]
    coverage: EvidenceCoverage


class SalesPreparationBrief(SalesContract):
    """仅让模型输出此草稿；sources、版本和检索范围由后台补充。"""

    person_id: str
    customer_summary: list[FactItem] = Field(default_factory=list, max_length=4)
    recent_communications: list[FactItem] = Field(default_factory=list, max_length=5)
    customer_needs: list[FactItem] = Field(default_factory=list, max_length=5)
    previous_commitments: list[CommitmentItem] = Field(default_factory=list, max_length=4)
    suggested_questions: list[RecommendationItem] = Field(default_factory=list, max_length=5)
    suggested_opening: RecommendationItem | None = None
    missing_information: list[ShortText] = Field(default_factory=list, max_length=5)
    limitations: list[ShortText] = Field(default_factory=list, max_length=5)


class SalesPreparationResult(SalesContract):
    """最终可保存/展示的结果；保留来源快照，刷新不能用当前档案替换旧证据。"""

    brief: SalesPreparationBrief
    sources: list[EvidenceSource]
    as_of: AwareDatetime
    meeting_goal: str
    coverage: EvidenceCoverage
    schema_version: Literal[1] = 1
    prompt_version: Literal["sales-prep.v1"] = "sales-prep.v1"
