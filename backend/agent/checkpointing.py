"""LangGraph Checkpointer 的创建、持有和关闭。

为什么单独做这一层：

1. ``graph.py`` 只描述节点和边，不应该知道 SQLite/PostgreSQL 的连接细节；
2. Checkpointer 的连接必须覆盖整个 FastAPI 进程生命周期，不能在一次请求结束后
   就关闭，否则后续 ``Command(resume=...)`` 无法读取原检查点；
3. 本地 SQLite 和生产 PostgreSQL 应实现同一个创建接口，业务节点无需跟着修改。

当前默认使用 MEMORY，保证尚未完成 SQLite TODO 时项目仍能启动。完成实现后在
``.env`` 设置 ``AGENT_CHECKPOINTER_BACKEND=sqlite`` 即可验证服务重启恢复。
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver


class CheckpointerBackend(StrEnum):
    """项目支持的检查点存储类型。"""

    MEMORY = "memory"  # 仅适合测试和学习；进程退出后全部丢失。
    SQLITE = "sqlite"  # 适合单机开发；可以验证服务重启后恢复 interrupt。
    POSTGRES = "postgres"  # 生产目标；支持多实例共享检查点。


@dataclass(slots=True)
class CheckpointerHandle:
    """同时持有 Saver 和它依赖的底层资源。

    ``saver`` 传给 ``StateGraph.compile``。
    ``close_callback`` 在 FastAPI shutdown 时调用，用于关闭 SQLite 连接或
    PostgreSQL 连接池。MemorySaver 没有外部资源，所以回调为空。
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
    sqlite_path: str,
    postgres_dsn: str,
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

    if selected == CheckpointerBackend.SQLITE:
        return _create_sqlite_handle(sqlite_path)

    return _create_postgres_handle(postgres_dsn)


def _create_sqlite_handle(sqlite_path: str) -> CheckpointerHandle:
    """创建本地持久化 Checkpointer。

    TODO(你来实现，建议按以下顺序)：

    1. 校验 ``sqlite_path`` 非空，并用 ``Path.resolve()`` 得到绝对路径；
    2. ``path.parent.mkdir(parents=True, exist_ok=True)`` 创建目录；
    3. 使用 ``sqlite3.connect(path, check_same_thread=False)`` 创建长连接；
    4. ``SqliteSaver(connection)`` 创建 Saver；
    5. 调用 ``saver.setup()`` 创建 LangGraph 自己需要的表和索引；
    6. 返回 ``CheckpointerHandle(SQLITE, saver, connection.close)``；
    7. 任一步骤失败时关闭已经创建的连接，再把异常抛出，禁止静默退回内存。

    为什么不直接使用 ``SqliteSaver.from_conn_string``：它返回上下文管理器。
    如果在本函数的 ``with`` 结束后把 saver 返回出去，SQLite 连接已经关闭。
    我们需要把连接保存到 ``CheckpointerHandle``，直到 FastAPI shutdown。

    SQLite 只作为单进程学习环境。多个 Uvicorn worker、容器多副本或者高并发
    写入场景应使用 PostgreSQL，不能让每个实例持有各自的本地 SQLite 文件。
    """
    if not sqlite_path.strip():
        raise ValueError("AGENT_CHECKPOINT_SQLITE_PATH 不能为空")

    # 先保留路径解析，让你实现时能直接从这里继续。
    path = Path(sqlite_path).expanduser().resolve()
    raise NotImplementedError(
        "SQLite Checkpointer 骨架已接入。请按照 "
        f"backend/agent/checkpointing.py 中的 TODO 实现连接：{path}"
    )


def _create_postgres_handle(postgres_dsn: str) -> CheckpointerHandle:
    """生产环境 PostgreSQL Checkpointer 扩展点。

    TODO(后续阶段)：

    1. 安装与当前 LangGraph 版本兼容的 ``langgraph-checkpoint-postgres``；
    2. 从 DSN 创建进程级连接池，而不是每个请求创建连接；
    3. 首次部署执行 Saver 的 ``setup``/迁移；
    4. shutdown 时关闭连接池；
    5. 压测同一 thread_id 的并发写入和多个服务实例之间的恢复行为；
    6. 配置检查点保留时间和定期清理任务。

    MySQL 仍然保存业务幂等记录；Checkpointer 保存 Agent 状态。两个存储职责
    不同，不需要为了“统一数据库”自行实现未经验证的 MySQL Saver。
    """
    if not postgres_dsn.strip():
        raise ValueError("使用 postgres Checkpointer 时必须配置 AGENT_CHECKPOINT_POSTGRES_DSN")
    raise NotImplementedError("PostgreSQL Checkpointer 留待生产化阶段实现")
