"""PostgreSQL 记忆维护任务仓储。

工作者采用短事务抢占任务，然后在数据库事务外调用 LLM/Neo4j。完成更新必须携带
``execution_token``，防止超时任务被重新抢占后，旧工作者覆盖新工作者的状态。
"""

import json
from uuid import NAMESPACE_URL, uuid4, uuid5

from psycopg.types.json import Jsonb

from backend.agent.memory.models import MemoryJob, MemoryJobType
from backend.db.postgres_client import get_postgres_connection


class MemoryJobConflictError(ValueError):
    """同一个任务幂等键出现了不同载荷。"""


class PostgresMemoryJobStore:
    persistent = True

    def enqueue(
        self,
        *,
        owner_id: str,
        conversation_id: str,
        request_id: str,
        job_type: MemoryJobType,
        payload: dict,
    ) -> str:
        """幂等登记任务，重复的 request/job_type 返回同一个 job_id。"""
        if not owner_id or not conversation_id or not request_id:
            raise ValueError("记忆任务缺少 owner_id、conversation_id 或 request_id")
        if not isinstance(payload, dict):
            raise TypeError("记忆任务 payload 必须是 dict")

        job_id = _stable_job_id(
            owner_id=owner_id,
            conversation_id=conversation_id,
            request_id=request_id,
            job_type=job_type,
        )
        with get_postgres_connection() as connection:
            row = connection.execute(
                """
                INSERT INTO memory_job
                    (job_id, owner_id, conversation_id, request_id, job_type, payload)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (owner_id, job_id) DO NOTHING
                RETURNING job_id
                """,
                (job_id, owner_id, conversation_id, request_id,
                 job_type.value, Jsonb(payload)),
            ).fetchone()
            if row is not None:
                return row["job_id"]

            existing = connection.execute(
                """
                SELECT conversation_id, request_id, job_type, payload
                FROM memory_job WHERE owner_id = %s AND job_id = %s
                """,
                (owner_id, job_id),
            ).fetchone()
            if (
                existing is None
                or existing["conversation_id"] != conversation_id
                or existing["request_id"] != request_id
                or existing["job_type"] != job_type.value
                or existing["payload"] != payload
            ):
                raise MemoryJobConflictError("相同记忆任务 ID 对应了不同载荷")
        return job_id

    def claim_next(self, *, stale_after_seconds: int) -> MemoryJob | None:
        """抢占一个可运行任务；``SKIP LOCKED`` 允许多个工作者并发消费。"""
        if not 1 <= stale_after_seconds <= 86400:
            raise ValueError("stale_after_seconds 必须在 1 到 86400 之间")
        execution_token = str(uuid4())
        with get_postgres_connection() as connection:
            # 最后一次尝试期间进程崩溃时，不会有旧工作者回来写 failed。先把这类
            # 过期租约收敛为终态，避免它永远停在 running，也避免无限次重领。
            connection.execute(
                """
                UPDATE memory_job
                SET status = 'failed', execution_token = NULL,
                    last_error = COALESCE(last_error, '任务租约超时且已耗尽重试次数'),
                    update_time = CURRENT_TIMESTAMP
                WHERE status = 'running' AND attempts >= max_attempts
                  AND update_time <= CURRENT_TIMESTAMP
                      - (%s * INTERVAL '1 second')
                """,
                (stale_after_seconds,),
            )
            row = connection.execute(
                """
                WITH candidate AS (
                    SELECT id
                    FROM memory_job
                    WHERE (
                        status IN ('pending', 'failed')
                        AND attempts < max_attempts
                        AND available_at <= CURRENT_TIMESTAMP
                    ) OR (
                        status = 'running'
                        AND attempts < max_attempts
                        AND update_time <= CURRENT_TIMESTAMP
                            - (%s * INTERVAL '1 second')
                    )
                    ORDER BY available_at ASC, id ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE memory_job AS job
                SET status = 'running',
                    -- 首次执行、失败重试、崩溃恢复都算一次新的执行尝试。
                    attempts = job.attempts + 1,
                    execution_token = %s, last_error = NULL,
                    update_time = CURRENT_TIMESTAMP
                FROM candidate
                WHERE job.id = candidate.id
                RETURNING job.job_id, job.owner_id, job.conversation_id,
                          job.request_id, job.job_type, job.payload,
                          job.attempts, job.max_attempts, job.execution_token
                """,
                (stale_after_seconds, execution_token),
            ).fetchone()
        if row is None:
            return None
        return MemoryJob(
            job_id=row["job_id"],
            owner_id=row["owner_id"],
            conversation_id=row["conversation_id"],
            request_id=row["request_id"],
            job_type=MemoryJobType(row["job_type"]),
            payload=row["payload"],
            attempts=row["attempts"],
            max_attempts=row["max_attempts"],
            execution_token=row["execution_token"],
        )

    def mark_succeeded(
        self,
        *,
        owner_id: str,
        job_id: str,
        execution_token: str,
    ) -> bool:
        return self._finish(
            owner_id=owner_id,
            job_id=job_id,
            execution_token=execution_token,
            status="succeeded",
            error_message=None,
            retry_after_seconds=0,
        )

    def mark_failed(
        self,
        *,
        owner_id: str,
        job_id: str,
        execution_token: str,
        error_message: str,
        retry_after_seconds: int,
    ) -> bool:
        if not 0 <= retry_after_seconds <= 86400:
            raise ValueError("retry_after_seconds 必须在 0 到 86400 之间")
        return self._finish(
            owner_id=owner_id,
            job_id=job_id,
            execution_token=execution_token,
            status="failed",
            error_message=error_message[:2000],
            retry_after_seconds=retry_after_seconds,
        )

    @staticmethod
    def _finish(
        *,
        owner_id: str,
        job_id: str,
        execution_token: str,
        status: str,
        error_message: str | None,
        retry_after_seconds: int,
    ) -> bool:
        with get_postgres_connection() as connection:
            cursor = connection.execute(
                """
                UPDATE memory_job
                SET status = %s, last_error = %s,
                    available_at = CURRENT_TIMESTAMP
                        + (%s * INTERVAL '1 second'),
                    update_time = CURRENT_TIMESTAMP
                -- owner_id 也必须参与更新条件。任务 ID 虽由 owner_id 派生且碰撞
                -- 概率极低，但租户边界不应依赖 UUID 的统计唯一性。
                WHERE owner_id = %s AND job_id = %s AND status = 'running'
                  AND execution_token = %s
                """,
                (status, error_message, retry_after_seconds,
                 owner_id, job_id, execution_token),
            )
            return cursor.rowcount == 1


def _stable_job_id(
    *,
    owner_id: str,
    conversation_id: str,
    request_id: str,
    job_type: MemoryJobType,
) -> str:
    identity = json.dumps(
        [owner_id, conversation_id, request_id, job_type.value],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return str(uuid5(NAMESPACE_URL, f"relationship-memory-job:{identity}"))
