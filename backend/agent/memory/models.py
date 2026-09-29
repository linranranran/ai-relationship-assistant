"""三层记忆使用的领域模型。

这些对象会在节点边界转换为普通 dict 后写入 LangGraph Checkpointer。因此字段
只使用字符串、数字、布尔、list 和 dict 等可序列化类型，不在状态里保存数据库
连接、LLM 客户端或 Repository 实例。
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class MemoryRole(StrEnum):
    """原始消息的可信角色。角色由服务端确定，不能让导入文本自行声明。"""

    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class MemoryFactStatus(StrEnum):
    """长期事实的生命周期。"""

    ACTIVE = "active"  # 已有充分证据，可以参与检索和建议。
    PENDING_CONFIRMATION = "pending_confirmation"  # 有候选证据，但需要用户确认。
    SUPERSEDED = "superseded"  # 已被更新版本替代，保留用于审计。
    REJECTED = "rejected"  # 用户或规则明确拒绝，不再参与检索。


class MemoryJobType(StrEnum):
    """异步记忆维护任务类型。

    这些任务只维护消息派生数据，失败时绝不能重新执行已经成功的业务 Tool。
    """

    SUMMARY = "summary"
    PERSIST_FACTS = "persist_facts"


class MemoryJobStatus(StrEnum):
    """持久化任务状态；running 使用 execution_token 防止旧工作者覆盖结果。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class MemoryMessage:
    """权威消息记录。

    ``content`` 是保存下来的原始内容；它不等于本轮一定发送给模型的上下文。
    ``request_id + role`` 用于保证 HTTP 重试不会重复写入同一轮消息。
    """

    message_id: str
    owner_id: str
    conversation_id: str
    request_id: str
    role: MemoryRole
    content: str
    token_count: int = 0
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConversationSummary:
    """旧消息的滚动结构化摘要，不是长期事实的权威来源。

    covered_until_message_id 指向最后一条已写入摘要的原始消息。它只是定位键，
    不能与另一个 UUID 大小比较；数据库必须先查出对应的 conversation_message.id。
    None 表示尚未覆盖任何消息。request_id 是一轮请求的幂等键，不适合作游标。
    """

    owner_id: str
    conversation_id: str
    content: dict[str, Any] = field(default_factory=dict)
    covered_until_message_id: str | None = None
    version: int = 0
    updated_at: str = ""


@dataclass(frozen=True, slots=True)
class MemoryFact:
    """经过证据和权限校验后保存的长期业务事实。"""

    fact_id: str
    owner_id: str
    person_id: str
    category: str
    value: dict[str, Any]
    status: MemoryFactStatus
    version: int
    evidence_message_ids: list[str] = field(default_factory=list)
    confidence: float | None = None
    valid_from: str = ""
    valid_to: str | None = None
    supersedes_fact_id: str | None = None


@dataclass(frozen=True, slots=True)
class MemoryWriteCandidate:
    """规则或模型产生、等待 Repository 校验的候选事实。

    模型抽取默认要求确认。只有成功写 Tool 的确定性结果可以设置
    ``requires_confirmation=False``；Repository 仍须校验 owner/person/evidence、
    分配版本并处理冲突后才能写成 ``ACTIVE``。
    """

    person_id: str
    category: str
    value: dict[str, Any]
    evidence_message_ids: list[str] = field(default_factory=list)
    confidence: float | None = None
    requires_confirmation: bool = True
    reason: str = ""


@dataclass(frozen=True, slots=True)
class MemoryJob:
    """后台工作者抢占到的一项记忆维护任务。

    ``payload`` 只能保存可序列化的派生输入，不能保存数据库连接、LLM 客户端或
    LangGraph State 对象。``execution_token`` 是租约 fencing token。
    """

    job_id: str
    owner_id: str
    conversation_id: str
    request_id: str
    job_type: MemoryJobType
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    execution_token: str


@dataclass(frozen=True, slots=True)
class ContextStats:
    """记录本轮为什么选择或丢弃某些上下文，供 Trace 和调优使用。"""

    input_budget: int
    estimated_input_tokens: int
    selected_message_count: int = 0
    dropped_message_count: int = 0
    selected_memory_count: int = 0
    dropped_memory_count: int = 0
    summary_required: bool = False
    memory_degraded: bool = False
    degradation_reasons: list[str] = field(default_factory=list)
    current_input_over_budget: bool = False


@dataclass(frozen=True, slots=True)
class ContextBundle:
    """一次 LLM 调用可使用的受控上下文，而不是完整 Checkpoint。"""

    messages: list[dict[str, Any]]
    summary: dict[str, Any] = field(default_factory=dict)
    memories: list[dict[str, Any]] = field(default_factory=list)
    compact_execution: dict[str, Any] = field(default_factory=dict)
    stats: ContextStats | None = None


@dataclass(frozen=True, slots=True)
class MemorySettings:
    """上下文预算配置。具体校验由 token_budget 模块集中完成。"""

    max_input_tokens: int
    reserved_output_tokens: int
    safety_tokens: int
    recent_message_limit: int
    summary_trigger_ratio: float
    memory_retrieval_limit: int
