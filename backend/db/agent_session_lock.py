"""防止同一 thread 被不同 HTTP 请求同时覆盖。"""

from contextlib import contextmanager
from hashlib import sha256
from threading import Lock
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from backend.db.postgres_client import build_postgres_conninfo
from backend.config import POSTGRES_POOL_MAX_SIZE, POSTGRES_POOL_TIMEOUT_SECONDS

_pool = None
_pool_guard = Lock()


def _lock_pool():
    """会话锁单独用一个有界池，避免并发请求占满业务池后彼此等待业务连接。"""
    global _pool
    with _pool_guard:
        if _pool is None:
            pool = ConnectionPool(build_postgres_conninfo(), min_size=1,
                                  max_size=POSTGRES_POOL_MAX_SIZE,
                                  timeout=POSTGRES_POOL_TIMEOUT_SECONDS,
                                  kwargs={"autocommit": True, "row_factory": dict_row},
                                  open=False, name="relationship-agent-session-locks")
            try:
                pool.open(wait=True, timeout=POSTGRES_POOL_TIMEOUT_SECONDS)
            except Exception:
                pool.close()
                raise
            _pool = pool
        return _pool


def close_session_lock_pool():
    global _pool
    with _pool_guard:
        if _pool is not None:
            _pool.close()
            _pool = None


class SessionBusy(ValueError):
    pass


@contextmanager
def lock_agent_session(thread_id: str):
    """使用 PostgreSQL 会话锁，不持有长事务或业务行锁。

    request_id 锁只保护同一次请求，两个不同 request_id 仍可能写同一检查点。
    会话锁将同一 thread 串行化；不同用户仍并行。try_lock 立即失败，不排队。
    锁期间占用一条池连接；图调用和模型等待期间数据库事务已经提交。
    进程崩溃/连接断开时 PostgreSQL 自动释放；finally 正常退出时显式解锁。
    """
    lock_id = int.from_bytes(sha256(thread_id.encode()).digest()[:8], "big", signed=True)
    with _lock_pool().connection() as connection:
        with connection.transaction():
            row = connection.execute("SELECT pg_try_advisory_lock(%s) AS acquired", (lock_id,)).fetchone()
        if not row["acquired"]:
            raise SessionBusy("当前会话正在执行，请稍后查询或重试原请求")
        try:
            yield
        finally:
            try:
                with connection.transaction():
                    connection.execute("SELECT pg_advisory_unlock(%s)", (lock_id,))
            except Exception:
                # Session 锁不能带回池给下一个请求；解锁不确定时丢弃连接。
                connection.close()
                raise
