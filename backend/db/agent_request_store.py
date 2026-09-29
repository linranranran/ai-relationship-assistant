"""Agent 请求级幂等记录的 PostgreSQL 持久化函数。"""

from psycopg.types.json import Jsonb

from backend.db.postgres_client import get_postgres_connection


def try_insert_agent_request(
    *,
    owner_id: str,
    request_id: str,
    thread_id: str,
    message_hash: str,
    execution_token: str,
) -> bool:
    """首次请求插入 running 记录；唯一键冲突表示该 request_id 已存在。"""
    sql = """
        INSERT INTO agent_request
            (owner_id, request_id, thread_id, message_hash, status, execution_token)
        VALUES (%s, %s, %s, %s, 'running', %s)
        ON CONFLICT (owner_id, request_id) DO NOTHING
        RETURNING id
    """
    with get_postgres_connection() as connection:
        row = connection.execute(
            sql,
            (owner_id, request_id, thread_id, message_hash, execution_token),
        ).fetchone()
    return row is not None


def get_agent_request(owner_id: str, request_id: str) -> dict | None:
    sql = """
        SELECT id, owner_id, request_id, thread_id, message_hash, status,
               response, error_message, execution_token, create_time, update_time
        FROM agent_request
        WHERE owner_id = %s AND request_id = %s
        LIMIT 1
    """
    with get_postgres_connection() as connection:
        row = connection.execute(sql, (owner_id, request_id)).fetchone()
    return dict(row) if row else None


def try_restart_failed_agent_request(
    owner_id: str,
    request_id: str,
    execution_token: str,
) -> bool:
    sql = """
        UPDATE agent_request
        SET status = 'running', response = NULL, error_message = NULL,
            execution_token = %s, update_time = CURRENT_TIMESTAMP
        WHERE owner_id = %s AND request_id = %s AND status = 'failed'
    """
    return _execute_update(sql, (execution_token, owner_id, request_id))


def try_reclaim_stale_agent_request(
    owner_id: str,
    request_id: str,
    stale_after_seconds: int,
    execution_token: str,
) -> bool:
    sql = """
        UPDATE agent_request
        SET execution_token = %s, error_message = NULL,
            update_time = CURRENT_TIMESTAMP
        WHERE owner_id = %s AND request_id = %s AND status = 'running'
          AND update_time <= CURRENT_TIMESTAMP - (%s * INTERVAL '1 second')
    """
    return _execute_update(
        sql,
        (execution_token, owner_id, request_id, stale_after_seconds),
    )


def try_resume_agent_request(
    owner_id: str,
    request_id: str,
    execution_token: str,
) -> bool:
    """原子地把等待人工交互的请求重新抢占为 running。"""
    sql = """
        UPDATE agent_request
        SET status = 'running', execution_token = %s,
            update_time = CURRENT_TIMESTAMP
        WHERE owner_id = %s AND request_id = %s
          AND status = 'confirmation_required'
    """
    return _execute_update(sql, (execution_token, owner_id, request_id))


def finish_agent_request(
    *,
    owner_id: str,
    request_id: str,
    status: str,
    response: dict | None,
    error_message: str | None,
    execution_token: str,
) -> bool:
    """只有仍持有 execution_token 的工作进程才能完成请求。"""
    if status not in {"completed", "confirmation_required", "failed"}:
        raise ValueError(f"不支持的 Agent 请求状态：{status}")
    sql = """
        UPDATE agent_request
        SET status = %s, response = %s, error_message = %s,
            update_time = CURRENT_TIMESTAMP
        WHERE owner_id = %s AND request_id = %s AND status = 'running'
          AND execution_token = %s
    """
    return _execute_update(
        sql,
        (
            status,
            Jsonb(response) if response is not None else None,
            error_message,
            owner_id,
            request_id,
            execution_token,
        ),
    )


def _execute_update(sql: str, params: tuple) -> bool:
    """执行条件更新；受影响一行代表当前进程成功完成状态转换。"""
    with get_postgres_connection() as connection:
        cursor = connection.execute(sql, params)
        return cursor.rowcount > 0
