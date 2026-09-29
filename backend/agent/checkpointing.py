"""LangGraph Checkpointer 的创建、持有和关闭。

Checkpointer 保存的是 Agent 短期运行状态，例如当前节点、interrupt 和恢复位置。
它与 ``agent_request``、``tool_execution`` 等业务幂等表职责不同，因此使用独立
连接池。生产环境使用 PostgreSQL；memory 仅供不需要重启恢复的本地调试。
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


class CheckpointerBackend(StrEnum):
    """项目支持的检查点存储类型。"""
    MEMORY = "memory"  # 仅适合测试和学习；进程退出后全部丢失。
    POSTGRES = "postgres"  # 生产目标；支持多实例共享检查点。


@dataclass(slots=True)
class CheckpointerHandle:
    """同时持有 Saver 和它依赖的底层资源。

    ``saver`` 传给 ``StateGraph.compile``。
    ``close_callback`` 在 FastAPI shutdown 时调用，用于关闭 PostgreSQL
    连接池。MemorySaver 没有外部资源，所以回调为空。
    """

    backend: CheckpointerBackend
    saver: Any
    close_callback: Callable[[], None] | None = None
    _closed: bool = False

    def close(self) -> None:
        """幂等关闭资源，避免 reload/shutdown 重复关闭时报错。"""
        if self._closed:
            return
        if self.close_callback is not None:
            self.close_callback()
        self._closed = True


def create_checkpointer_handle(
    *,
    backend: str,
    postgres_dsn: str,
    pool_min_size: int = 1,
    pool_max_size: int = 5,
    pool_timeout_seconds: int = 10,
) -> CheckpointerHandle:
    """根据配置创建一个进程级 Checkpointer。

    注意：这里创建的是长生命周期资源，因此只应在模块初始化或 FastAPI
    lifespan 启动阶段调用一次，不能在每个 HTTP 请求里调用。
    """
    try:
        selected = CheckpointerBackend(backend.strip().lower())
    except ValueError as exc:
        supported = ", ".join(item.value for item in CheckpointerBackend)
        raise ValueError(
            f"未知 AGENT_CHECKPOINTER_BACKEND={backend!r}，可选值：{supported}"
        ) from exc

    if selected == CheckpointerBackend.MEMORY:
        return CheckpointerHandle(backend=selected, saver=InMemorySaver())

    return _create_postgres_handle(
        postgres_dsn,
        pool_min_size=pool_min_size,
        pool_max_size=pool_max_size,
        pool_timeout_seconds=pool_timeout_seconds,
    )


def _create_postgres_handle(
    postgres_dsn: str,
    *,
    pool_min_size: int,
    pool_max_size: int,
    pool_timeout_seconds: int,
) -> CheckpointerHandle:
    """创建 PostgreSQL Saver 及其进程级专用连接池。

    ``PostgresSaver.from_conn_string`` 返回上下文管理器，不能直接当 Saver
    使用。这里显式创建池，既能支持并发请求，也能在应用关闭时可靠释放资源。

    LangGraph 要求连接启用 ``autocommit``、关闭 prepared statement，并使用
    ``dict_row``。这些选项只作用于 Checkpointer 池，不影响业务事务连接池。
    """
    if not postgres_dsn.strip():
        raise ValueError("使用 postgres Checkpointer 时必须配置 PostgreSQL 连接信息")
    if pool_min_size < 1 or pool_max_size < pool_min_size:
        raise ValueError("Checkpointer 连接池大小必须满足 1 <= min_size <= max_size")

    pool = ConnectionPool(
        conninfo=postgres_dsn,
        min_size=pool_min_size,
        max_size=pool_max_size,
        timeout=pool_timeout_seconds,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
        open=False,
        name="relationship-agent-checkpointer",
    )
    try:
        # wait=True：启动阶段就验证网络和凭据，避免第一个用户请求才报错。
        pool.open(wait=True, timeout=pool_timeout_seconds)
        checkpointer = PostgresSaver(pool)
        # setup() 的迁移是幂等的，可在每次应用启动时安全执行。
        checkpointer.setup()
    except Exception:
        pool.close()
        raise

    return CheckpointerHandle(
        backend=CheckpointerBackend.POSTGRES,
        saver=checkpointer,
        close_callback=pool.close,
    )
