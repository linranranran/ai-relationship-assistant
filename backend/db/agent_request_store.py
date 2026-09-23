"""Agent 请求幂等记录的 MySQL 持久化函数。"""

import json

from backend.db.mysql_client import get_mysql_connection


def try_insert_agent_request(
    *,
    owner_id: str,
    request_id: str,
    thread_id: str,
    message_hash: str,
    execution_token: str,
) -> bool:
    sql = """
        INSERT INTO agent_request
            (owner_id, request_id, thread_id, message_hash, status, execution_token)
        VALUES (%s, %s, %s, %s, 'running', %s)
        ON DUPLICATE KEY UPDATE request_id = request_id
    """
    return _execute_update(
        sql,
        (owner_id, request_id, thread_id, message_hash, execution_token),
        inserted_only=True,
    )


def get_agent_request(owner_id: str, request_id: str) -> dict | None:
    sql = """
        SELECT id, owner_id, request_id, thread_id, message_hash, status,
               response, error_message, execution_token, create_time, update_time
        FROM agent_request
        WHERE owner_id = %s AND request_id = %s
        LIMIT 1
    """
    connection = get_mysql_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, (owner_id, request_id))
            row = cursor.fetchone()
        if row and isinstance(row.get("response"), (str, bytes)):
            try:
                row["response"] = json.loads(row["response"])
            except (json.JSONDecodeError, TypeError):
                pass
        return row
    finally:
        connection.close()


def try_restart_failed_agent_request(
    owner_id: str,
    request_id: str,
    execution_token: str,
) -> bool:
    sql = """
        UPDATE agent_request
        SET status = 'running', response = NULL, error_message = NULL,
            execution_token = %s, update_time = CURRENT_TIMESTAMP(6)
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
            update_time = CURRENT_TIMESTAMP(6)
        WHERE owner_id = %s AND request_id = %s AND status = 'running'
          AND TIMESTAMPDIFF(SECOND, update_time, CURRENT_TIMESTAMP(6)) >= %s
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
    sql = """
        UPDATE agent_request
        SET status = 'running', execution_token = %s,
            update_time = CURRENT_TIMESTAMP(6)
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
    if status not in {"completed", "confirmation_required", "failed"}:
        raise ValueError(f"不支持的 Agent 请求状态：{status}")
    sql = """
        UPDATE agent_request
        SET status = %s, response = %s, error_message = %s,
            update_time = CURRENT_TIMESTAMP(6)
        WHERE owner_id = %s AND request_id = %s AND status = 'running'
          AND execution_token = %s
    """
    return _execute_update(
        sql,
        (
            status,
            json.dumps(response, ensure_ascii=False) if response is not None else None,
            error_message,
            owner_id,
            request_id,
            execution_token,
        ),
    )


def _execute_update(sql: str, params: tuple, inserted_only: bool = False) -> bool:
    connection = get_mysql_connection()
    try:
        with connection.cursor() as cursor:
            affected = cursor.execute(sql, params)
        connection.commit()
        return affected == 1 if inserted_only else affected > 0
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
