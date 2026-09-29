"""三层记忆依赖的端口及安全默认实现。

节点只面向 Protocol 编程，不关心数据来自 PostgreSQL、向量库还是其他服务。
默认 NoOp 实现用于逐步开发：读取为空、写入不持久化，并通过 ``persistent=False``
明确告诉调用方当前处于降级模式，绝不能伪造“已经保存成功”。
"""

from typing import Protocol

from backend.agent.memory.models import (
    ConversationSummary,
    MemoryFact,
    MemoryJob,
    MemoryJobType,
    MemoryMessage,
    MemoryWriteCandidate,
)


class MessageStore(Protocol):
    persistent: bool

    def load_recent(
        self,
        *,
        owner_id: str,
        conversation_id: str,
        limit: int,
    ) -> tuple[MemoryMessage, ...]: ...

    def load_after_cursor(
        self,
        *,
        owner_id: str,
        conversation_id: str,
        after_message_id: str | None,
        limit: int,
    ) -> tuple[MemoryMessage, ...]: ...

    def save_turn(
        self,
        *,
        owner_id: str,
        conversation_id: str,
        user_message: MemoryMessage,
        assistant_message: MemoryMessage,
    ) -> None: ...

    def delete_conversation(
        self,
        *,
        owner_id: str,
        conversation_id: str,
    ) -> None: ...


class SummaryStore(Protocol):
    persistent: bool

    def load(
        self,
        *,
        owner_id: str,
        conversation_id: str,
    ) -> ConversationSummary | None: ...

    def save_if_cursor_matches(
        self,
        *,
        summary: ConversationSummary,
        expected_cursor: str | None,
    ) -> bool: ...

    def delete(
        self,
        *,
        owner_id: str,
        conversation_id: str,
    ) -> None: ...


class LongTermMemoryStore(Protocol):
    persistent: bool

    def search(
        self,
        *,
        owner_id: str,
        conversation_id: str,
        query: str,
        limit: int,
    ) -> tuple[MemoryFact, ...]: ...

    def save_candidates(
        self,
        *,
        owner_id: str,
        conversation_id: str,
        request_id: str,
        candidates: tuple[MemoryWriteCandidate, ...],
    ) -> tuple[MemoryWriteCandidate, ...]: ...


class TokenCounter(Protocol):
    def count_messages(self, messages: list[dict]) -> int: ...


class MemoryJobStore(Protocol):
    """摘要和事实持久化使用的可靠任务队列端口。"""

    persistent: bool

    def enqueue(
        self,
        *,
        owner_id: str,
        conversation_id: str,
        request_id: str,
        job_type: MemoryJobType,
        payload: dict,
    ) -> str: ...

    def claim_next(self, *, stale_after_seconds: int) -> MemoryJob | None: ...

    def mark_succeeded(
        self,
        *,
        owner_id: str,
        job_id: str,
        execution_token: str,
    ) -> bool: ...

    def mark_failed(
        self,
        *,
        owner_id: str,
        job_id: str,
        execution_token: str,
        error_message: str,
        retry_after_seconds: int,
    ) -> bool: ...


class NoOpMessageStore:
    """未接 PostgreSQL 时的显式降级实现。"""

    persistent = False

    def load_recent(self, *, owner_id: str, conversation_id: str, limit: int) -> tuple[MemoryMessage, ...]:
        return ()

    def load_after_cursor(
        self,
        *,
        owner_id: str,
        conversation_id: str,
        after_message_id: str | None,
        limit: int,
    ) -> tuple[MemoryMessage, ...]:
        # 开发环境未接业务表时没有可摘要的原始消息，不能凭空产生摘要。
        return ()

    def save_turn(
        self,
        *,
        owner_id: str,
        conversation_id: str,
        user_message: MemoryMessage,
        assistant_message: MemoryMessage,
    ) -> None:
        # 故意不缓存“成功”标记。调用方通过 persistent 判断本轮是否真正落库。
        return None

    def delete_conversation(self, *, owner_id: str, conversation_id: str) -> None:
        return None


class NoOpSummaryStore:
    persistent = False

    def load(self, *, owner_id: str, conversation_id: str) -> ConversationSummary | None:
        return None

    def save_if_cursor_matches(
        self,
        *,
        summary: ConversationSummary,
        expected_cursor: str | None,
    ) -> bool:
        return False

    def delete(self, *, owner_id: str, conversation_id: str) -> None:
        return None


class NoOpLongTermMemoryStore:
    persistent = False

    def search(
        self,
        *,
        owner_id: str,
        conversation_id: str,
        query: str,
        limit: int,
    ) -> tuple[MemoryFact, ...]:
        return ()

    def save_candidates(
        self,
        *,
        owner_id: str,
        conversation_id: str,
        request_id: str,
        candidates: tuple[MemoryWriteCandidate, ...],
    ) -> tuple[MemoryWriteCandidate, ...]:
        return ()


class NoOpMemoryJobStore:
    """无持久队列时的显式降级；不会假装任务已经成功入队。"""

    persistent = False

    def enqueue(
        self,
        *,
        owner_id: str,
        conversation_id: str,
        request_id: str,
        job_type: MemoryJobType,
        payload: dict,
    ) -> str:
        return ""

    def claim_next(self, *, stale_after_seconds: int) -> MemoryJob | None:
        return None

    def mark_succeeded(
        self,
        *,
        owner_id: str,
        job_id: str,
        execution_token: str,
    ) -> bool:
        return False

    def mark_failed(
        self,
        *,
        owner_id: str,
        job_id: str,
        execution_token: str,
        error_message: str,
        retry_after_seconds: int,
    ) -> bool:
        return False


class ConservativeTokenCounter:
    """无需模型 tokenizer 的保守估算器。

    中文字符按 1 Token、ASCII 字符按约 1/4 Token，再为每条消息预留结构开销。
    这是跨 OpenAI-compatible 供应商的安全默认实现；``services.llm`` 会记录供应商
    返回的真实 usage 供校准。更换为模型官方 tokenizer 时只需实现本 Protocol，
    不需要修改图节点。
    """

    def count_messages(self, messages: list[dict]) -> int:
        total = 0.0
        for message in messages:
            content = str(message.get("content", ""))
            for char in content:
                total += 1.0 if ord(char) > 127 else 0.25
            total += 4  # role、消息边界等结构开销
        if messages and total <= 0:
            return 1
        return max(0, int(total + 0.999))
