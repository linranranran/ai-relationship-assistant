"""PostgreSQL access functions for Tool execution idempotency.

连接池是进程级资源，由 FastAPI lifespan 启动和关闭。每个仓储操作只在短事务
中完成一次 INSERT/SELECT/UPDATE，不会持有数据库锁去执行真实 Tool。
"""

from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock
from typing import Any

from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from backend.agent.execution_enums import ToolExecutionStatus
from backend.config import (
    POSTGRES_DATABASE,
    POSTGRES_HOST,
    POSTGRES_PASSWORD,
    POSTGRES_POOL_MAX_SIZE,
    POSTGRES_POOL_MIN_SIZE,
    POSTGRES_POOL_TIMEOUT_SECONDS,
    POSTGRES_PORT,
    POSTGRES_USER,
)


_pool: ConnectionPool | None = None
_pool_lock = Lock()


def build_postgres_conninfo(database: str = POSTGRES_DATABASE) -> str:
    """从统一配置安全构造连接信息。

    业务连接池和 LangGraph Checkpointer 都调用这里，避免两份 DSN 的用户名、
    密码或数据库名不一致。``make_conninfo`` 会正确处理密码中的特殊字符。
    """
    return make_conninfo(
        "",
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        dbname=database,
        connect_timeout=POSTGRES_POOL_TIMEOUT_SECONDS,
    )


def open_postgres_pool() -> ConnectionPool:
    """创建并打开共享连接池；重复调用返回同一个实例。

    ``open=False`` 避免导入模块时访问远程数据库。FastAPI lifespan 会主动调用
    本函数，实现启动失败时快速暴露配置或网络问题；仓储直接调用时也会懒加载。
    """
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ConnectionPool(
                conninfo=build_postgres_conninfo(),
                min_size=POSTGRES_POOL_MIN_SIZE,
                max_size=POSTGRES_POOL_MAX_SIZE,
                timeout=POSTGRES_POOL_TIMEOUT_SECONDS,
                kwargs={"row_factory": dict_row},
                open=False,
                name="relationship-agent-business",
            )
            _pool.open(wait=True, timeout=POSTGRES_POOL_TIMEOUT_SECONDS)
        return _pool


def close_postgres_pool() -> None:
    """关闭连接池并清空全局引用，兼容 reload 和重复 lifespan。"""
    global _pool
    with _pool_lock:
        if _pool is None:
            return
        _pool.close()
        _pool = None


@contextmanager
def get_postgres_connection() -> Iterator[Any]:
    """借用一个连接并开启短事务。

    正常退出时 psycopg 提交事务，抛出异常时回滚，随后连接归还连接池。调用方
    不能手工关闭该连接，也不能把连接对象保存到函数外。
    """
    pool = open_postgres_pool()
    with pool.connection(timeout=POSTGRES_POOL_TIMEOUT_SECONDS) as connection:
        yield connection


def try_insert_tool_execution(
    *,
    request_id: str,
    thread_id: str,
    tool_name: str,
    call_id: str,
    owner_id: str,
    arguments: dict,
    execution_token: str,
) -> bool:
    """原子抢占新调用；唯一键冲突时返回 False。"""
    sql = """
        INSERT INTO tool_execution
            (request_id, thread_id, tool_name, call_id, owner_id, status,
             arguments, execution_token)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (owner_id, call_id) DO NOTHING
        RETURNING id
    """
    with get_postgres_connection() as connection:
        row = connection.execute(
            sql,
            (
                request_id,
                thread_id,
                tool_name,
                call_id,
                owner_id,
                ToolExecutionStatus.RUNNING.value,
                Jsonb(arguments),
                execution_token,
            ),
        ).fetchone()
    return row is not None


def get_tool_execution(call_id: str, owner_id: str) -> dict | None:
    sql = """
        SELECT id, request_id, thread_id, tool_name, call_id, owner_id, status,
               arguments, result, error_message, latency_ms, execution_token,
               create_time, update_time
        FROM tool_execution
        WHERE call_id = %s AND owner_id = %s
        LIMIT 1
    """
    with get_postgres_connection() as connection:
        row = connection.execute(sql, (call_id, owner_id)).fetchone()
    # JSONB 由 psycopg 自动解码为 dict/list，不需要再 json.loads。
    return dict(row) if row else None


def try_restart_failed_execution(
    call_id: str,
    owner_id: str,
    execution_token: str,
) -> bool:
    """只允许一个竞争者把 failed 调用重新抢占为 running。"""
    sql = """
        UPDATE tool_execution
        SET status = %s, result = NULL, error_message = NULL,
            latency_ms = NULL, execution_token = %s, update_time = CURRENT_TIMESTAMP
        WHERE call_id = %s AND owner_id = %s AND status = %s
    """
    return _execute_claim_update(
        sql,
        (
            ToolExecutionStatus.RUNNING.value,
            execution_token,
            call_id,
            owner_id,
            ToolExecutionStatus.FAILED.value,
        ),
    )


def try_reclaim_stale_execution(
    call_id: str,
    owner_id: str,
    stale_after_seconds: int,
    execution_token: str,
) -> bool:
    """条件更新超时租约；execution_token 防止旧工作进程覆盖新结果。"""
    sql = """
        UPDATE tool_execution
        SET error_message = NULL, execution_token = %s,
            update_time = CURRENT_TIMESTAMP
        WHERE call_id = %s AND owner_id = %s AND status = %s
          AND update_time <= CURRENT_TIMESTAMP - (%s * INTERVAL '1 second')
    """
    return _execute_claim_update(
        sql,
        (
            execution_token,
            call_id,
            owner_id,
            ToolExecutionStatus.RUNNING.value,
            stale_after_seconds,
        ),
    )


def finish_tool_execution(
    *,
    call_id: str,
    owner_id: str,
    status: ToolExecutionStatus,
    result: dict | None,
    error_message: str | None,
    latency_ms: int,
    execution_token: str,
) -> bool:
    """使用 fencing token 完成自己持有的执行租约。"""
    if status not in {ToolExecutionStatus.SUCCESS, ToolExecutionStatus.FAILED}:
        raise ValueError(f"unsupported tool execution status: {status}")

    sql = """
        UPDATE tool_execution
        SET status = %s, result = %s, error_message = %s, latency_ms = %s,
            update_time = CURRENT_TIMESTAMP
        WHERE call_id = %s AND owner_id = %s AND status = %s
          AND execution_token = %s
    """
    with get_postgres_connection() as connection:
        cursor = connection.execute(
            sql,
            (
                status.value,
                Jsonb(result) if result is not None else None,
                error_message,
                latency_ms,
                call_id,
                owner_id,
                ToolExecutionStatus.RUNNING.value,
                execution_token,
            ),
        )
        return cursor.rowcount > 0


def _execute_claim_update(sql: str, params: tuple) -> bool:
    """执行带旧状态条件的原子 UPDATE，受影响一行代表抢占成功。"""
    with get_postgres_connection() as connection:
        cursor = connection.execute(sql, params)
        return cursor.rowcount > 0
