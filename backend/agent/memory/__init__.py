"""Agent 三层记忆的公共领域契约。

这里刻意不导出 PostgreSQL Repository 或具体向量库实现。业务节点只依赖这些
稳定的数据类型，底层存储以后从 PostgreSQL 换成其他实现时不需要修改图结构。
"""

from backend.agent.memory.models import (
    ContextBundle,
    ContextStats,
    ConversationSummary,
    MemoryFact,
    MemoryFactStatus,
    MemoryJob,
    MemoryJobStatus,
    MemoryJobType,
    MemoryMessage,
    MemoryRole,
    MemorySettings,
    MemoryWriteCandidate,
)

__all__ = [
    "ContextBundle",
    "ContextStats",
    "ConversationSummary",
    "MemoryFact",
    "MemoryFactStatus",
    "MemoryJob",
    "MemoryJobStatus",
    "MemoryJobType",
    "MemoryMessage",
    "MemoryRole",
    "MemorySettings",
    "MemoryWriteCandidate",
]
