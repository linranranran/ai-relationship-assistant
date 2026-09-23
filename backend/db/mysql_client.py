"""MySQL access functions for Tool execution idempotency.

Connections are created per operation. Importing this module must never require the
database to be reachable, otherwise an unavailable audit store would prevent the
FastAPI application from starting.
"""

import json

import pymysql
from pymysql.connections import Connection
from pymysql.cursors import DictCursor

from backend.agent.execution_enums import ToolExecutionStatus
from backend.config import (
    MYSQL_DATABASE,
    MYSQL_HOST,
    MYSQL_PASSWORD,
    MYSQL_PORT,
    MYSQL_USER,
)


def get_mysql_connection() -> Connection:
    """Create one short-lived connection owned by the caller."""
    return pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
        connect_timeout=5,
        read_timeout=10,
        write_timeout=10,
    )


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
    """Atomically claim a new call.

    Returns True only for the request that inserted the row. This depends on the
    database UNIQUE(owner_id, call_id) constraint supplied in the migration.
    """
    sql = """
        INSERT INTO tool_execution
            (request_id, thread_id, tool_name, call_id, owner_id, status,
             arguments, execution_token)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE call_id = call_id
    """
    connection = get_mysql_connection()
    try:
        with connection.cursor() as cursor:
            affected = cursor.execute(
                sql,
                (
                    request_id,
                    thread_id,
                    tool_name,
                    call_id,
                    owner_id,
                    ToolExecutionStatus.RUNNING.value,
                    json.dumps(arguments, ensure_ascii=False),
                    execution_token,
                ),
            )
        connection.commit()
        return affected == 1
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_tool_execution(call_id: str, owner_id: str) -> dict | None:
    sql = """
        SELECT id, request_id, thread_id, tool_name, call_id, owner_id, status,
               arguments, result, error_message, latency_ms, execution_token,
               create_time, update_time
        FROM tool_execution
        WHERE call_id = %s AND owner_id = %s
        LIMIT 1
    """
    connection = get_mysql_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, (call_id, owner_id))
            row = cursor.fetchone()
        return _decode_json(row) if row else None
    finally:
        connection.close()


def try_restart_failed_execution(call_id: str, owner_id: str, execution_token: str) -> bool:
    """Allow exactly one contender to retry a previously failed call."""
    sql = """
        UPDATE tool_execution
        SET status = %s, result = NULL, error_message = NULL,
            latency_ms = NULL, execution_token = %s, update_time = CURRENT_TIMESTAMP(6)
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
    """Reclaim a running row left behind by a crashed worker."""
    sql = """
        UPDATE tool_execution
        SET error_message = NULL, execution_token = %s, update_time = CURRENT_TIMESTAMP(6)
        WHERE call_id = %s AND owner_id = %s AND status = %s
          AND TIMESTAMPDIFF(SECOND, update_time, CURRENT_TIMESTAMP(6)) >= %s
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
    """Finish a claimed execution and persist the result used by downstream refs."""
    if status not in {ToolExecutionStatus.SUCCESS, ToolExecutionStatus.FAILED}:
        raise ValueError(f"unsupported tool execution status: {status}")

    sql = """
        UPDATE tool_execution
        SET status = %s, result = %s, error_message = %s, latency_ms = %s,
            update_time = CURRENT_TIMESTAMP(6)
        WHERE call_id = %s AND owner_id = %s AND status = %s
          AND execution_token = %s
    """
    connection = get_mysql_connection()
    try:
        with connection.cursor() as cursor:
            affected = cursor.execute(
                sql,
                (
                    status.value,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    error_message,
                    latency_ms,
                    call_id,
                    owner_id,
                    ToolExecutionStatus.RUNNING.value,
                    execution_token,
                ),
            )
        connection.commit()
        return affected > 0
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _execute_claim_update(sql: str, params: tuple) -> bool:
    connection = get_mysql_connection()
    try:
        with connection.cursor() as cursor:
            affected = cursor.execute(sql, params)
        connection.commit()
        return affected > 0
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _decode_json(row: dict) -> dict:
    for key in ("arguments", "result"):
        value = row.get(key)
        if isinstance(value, (str, bytes)):
            try:
                row[key] = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                pass
    return row
