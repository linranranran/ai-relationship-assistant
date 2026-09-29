"""进程级记忆依赖注册。

与 Checkpointer 一样，Repository 是长生命周期依赖，但不能在模块 import 时连接
数据库。当前默认使用 NoOp，实现 PostgreSQL Repository 后在 FastAPI lifespan
调用 ``configure_memory_runtime`` 注入即可。
"""

from dataclasses import dataclass
from threading import Lock

from backend.agent.memory.models import MemorySettings
from backend.agent.memory.ports import (
    ConservativeTokenCounter,
    LongTermMemoryStore,
    MemoryJobStore,
    MessageStore,
    NoOpLongTermMemoryStore,
    NoOpMessageStore,
    NoOpMemoryJobStore,
    NoOpSummaryStore,
    SummaryStore,
    TokenCounter,
)
from backend.config import (
    AGENT_CONTEXT_SAFETY_TOKENS,
    AGENT_MAX_INPUT_TOKENS,
    AGENT_MEMORY_RETRIEVAL_LIMIT,
    AGENT_RECENT_MESSAGE_LIMIT,
    AGENT_RESERVED_OUTPUT_TOKENS,
    AGENT_SUMMARY_TRIGGER_RATIO,
)


@dataclass(frozen=True, slots=True)
class MemoryRuntime:
    message_store: MessageStore
    summary_store: SummaryStore
    long_term_store: LongTermMemoryStore
    job_store: MemoryJobStore
    token_counter: TokenCounter
    settings: MemorySettings


def _default_runtime() -> MemoryRuntime:
    return MemoryRuntime(
        message_store=NoOpMessageStore(),
        summary_store=NoOpSummaryStore(),
        long_term_store=NoOpLongTermMemoryStore(),
        job_store=NoOpMemoryJobStore(),
        token_counter=ConservativeTokenCounter(),
        settings=MemorySettings(
            max_input_tokens=AGENT_MAX_INPUT_TOKENS,
            reserved_output_tokens=AGENT_RESERVED_OUTPUT_TOKENS,
            safety_tokens=AGENT_CONTEXT_SAFETY_TOKENS,
            recent_message_limit=AGENT_RECENT_MESSAGE_LIMIT,
            summary_trigger_ratio=AGENT_SUMMARY_TRIGGER_RATIO,
            memory_retrieval_limit=AGENT_MEMORY_RETRIEVAL_LIMIT,
        ),
    )


_runtime = _default_runtime()
_runtime_lock = Lock()


def get_memory_runtime() -> MemoryRuntime:
    """返回当前进程的记忆依赖，不创建网络连接。"""
    return _runtime


def configure_memory_runtime(runtime: MemoryRuntime) -> None:
    """原子替换运行时依赖，供 FastAPI lifespan 或手工学习脚本调用。"""
    global _runtime
    with _runtime_lock:
        _runtime = runtime
