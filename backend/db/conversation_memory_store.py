"""三层记忆的 PostgreSQL 仓储。

消息、摘要和事实共用业务连接池；每个方法只持有短事务。
Neo4j 人物归属检查在 PostgreSQL 事务外完成。

FastAPI lifespan 会构造本类，并通过 configure_memory_runtime 注入。
本模块不会在导入时连接数据库。
"""

import json
import re
from datetime import datetime
from typing import Callable
from uuid import NAMESPACE_URL, uuid5

from psycopg.types.json import Jsonb

from backend.agent.memory.models import (
    ConversationSummary,
    MemoryFact,
    MemoryFactStatus,
    MemoryMessage,
    MemoryRole,
    MemoryWriteCandidate,
)
from backend.config import AGENT_MEMORY_RETRIEVAL_LIMIT, AGENT_RECENT_MESSAGE_LIMIT
from backend.db.postgres_client import get_postgres_connection


class MemoryConflictError(ValueError):
    """同一幂等键对应不同内容，不能静默覆盖第一次执行的结果。"""


class PostgresConversationMemoryStore:
    """同时实现 MessageStore、SummaryStore 和 LongTermMemoryStore。

    可注入人物归属校验器；默认用 Neo4j 按 owner_id + person_id 校验。
    """

    persistent = True

    def __init__(
        self, person_owner_checker: Callable[[str, str], bool] | None = None
    ) -> None:
        self._person_owner_checker = person_owner_checker

    # ---------- 短期原始消息 ----------

    def load_recent(
        self, *, owner_id: str, conversation_id: str, limit: int
    ) -> tuple[MemoryMessage, ...]:
        """取最近 N 条，返回时恢复为真实的对话先后顺序。"""
        _require_identity(owner_id, conversation_id)
        _validate_limit(limit, AGENT_RECENT_MESSAGE_LIMIT)
        if limit == 0:
            return ()
        # 时间可能相同，用自增 id 保证顺序；查询必须先在 SQL 中限定 owner。
        with get_postgres_connection() as connection:
            rows = connection.execute(
                """
                SELECT message_id, owner_id, conversation_id, request_id, role,
                       content, token_count, metadata, create_time
                FROM conversation_message
                WHERE owner_id = %s AND conversation_id = %s
                ORDER BY create_time DESC, id DESC
                LIMIT %s
                """,
                (owner_id, conversation_id, limit),
            ).fetchall()
        return tuple(_message_from_row(row) for row in reversed(rows))

    def load_after_cursor(
        self,
        *,
        owner_id: str,
        conversation_id: str,
        after_message_id: str | None,
        limit: int,
    ) -> tuple[MemoryMessage, ...]:
        """按数据库插入顺序读取下一批待摘要消息。

        ``after_message_id`` 来自 conversation_summary.covered_until_message_id；
        None 代表从第一条开始。
        message_id 是 UUID，不能写 ``message_id > %s``，也不能拿 request_id
        或 create_time 单独做翻页条件。

        提示：save_turn 已按 owner + conversation 加事务级锁，保证同一会话的
        新消息按提交顺序获得递增 id。摘要工作者只处理已经提交的消息。
        """
        _require_identity(owner_id, conversation_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("摘要批次 limit 必须在 1 到 100 之间")
        with get_postgres_connection() as connection:
            cursor_id = 0
            if after_message_id is not None:
                cursor_row = connection.execute(
                    """
                    SELECT id FROM conversation_message
                    WHERE owner_id = %s AND conversation_id = %s
                      AND message_id = %s
                    """,
                    (owner_id, conversation_id, after_message_id),
                ).fetchone()
                if cursor_row is None:
                    # 从头重放会把“旧摘要 + 旧消息”再次合并，可能重复或覆盖事实。
                    # 游标失效必须显式失败，交给运维或修复任务处理。
                    raise ValueError("摘要游标未指向当前用户会话的消息")
                cursor_id = cursor_row["id"]

            rows = connection.execute(
                """
                SELECT message_id, owner_id, conversation_id, request_id, role,
                       content, token_count, metadata, create_time
                FROM conversation_message
                WHERE owner_id = %s AND conversation_id = %s AND id > %s
                ORDER BY id ASC
                LIMIT %s
                """,
                (owner_id, conversation_id, cursor_id, limit),
            ).fetchall()
        return tuple(_message_from_row(row) for row in rows)

    def save_turn(
        self,
        *,
        owner_id: str,
        conversation_id: str,
        user_message: MemoryMessage,
        assistant_message: MemoryMessage,
    ) -> None:
        """一个短事务保存两条消息，按 owner/request/role 做请求级幂等。

        会话已经有旧消息仍须写入新一轮；不能通过“会话是否为空”决定插入。
        第二条 INSERT 失败时，第一条也随事务回滚。
        """
        _require_identity(owner_id, conversation_id)
        if user_message.request_id != assistant_message.request_id:
            raise ValueError("同一轮消息必须使用相同 request_id")
        for message, expected_role in (
            (user_message, MemoryRole.USER),
            (assistant_message, MemoryRole.ASSISTANT),
        ):
            if (
                message.owner_id != owner_id
                or message.conversation_id != conversation_id
                or message.role != expected_role
            ):
                raise ValueError("消息的 owner、conversation 或 role 不匹配")
            if not message.message_id or not message.request_id or not message.content:
                raise ValueError("消息 ID、请求 ID 和内容不能为空")
            if not isinstance(message.metadata, dict) or message.token_count < 0:
                raise ValueError("消息 metadata 或 token_count 不合法")
        with get_postgres_connection() as connection:
            # 同一会话的并发写入串行化：先拿锁，再按 user→assistant 插入。
            # 自增 id 才能可靠地代表此会话的提交顺序。锁只覆盖数据库短事务，
            # 绝不能在持锁期间调用 Agent、LLM 或 Neo4j。
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))",
                (owner_id, conversation_id),
            )
            _insert_message_once(connection, user_message)
            _insert_message_once(connection, assistant_message)

    def delete_conversation(self, *, owner_id: str, conversation_id: str) -> None:
        """删除本会话消息、摘要及引用其证据的长期事实。

        目前没有独立 conversation 表：空会话只能幂等返回，无法查询其归属。
        Checkpointer 的 delete_thread 由会话服务另外执行，不由仓储越层调用。
        """
        _require_identity(owner_id, conversation_id)
        with get_postgres_connection() as connection:
            # 先停止仍未消费的派生任务，避免消息删除后工作者又创建摘要或事实。
            connection.execute(
                "DELETE FROM memory_job WHERE owner_id = %s AND conversation_id = %s",
                (owner_id, conversation_id),
            )
            # 先删事实，否则后面会失去判断“事实引用哪些消息”的依据。
            connection.execute(
                """
                DELETE FROM memory_fact AS fact
                WHERE fact.owner_id = %s
                  AND EXISTS (
                      SELECT 1 FROM conversation_message AS message
                      WHERE message.owner_id = %s
                        AND message.conversation_id = %s
                        AND fact.evidence_message_ids ? message.message_id
                  )
                """,
                (owner_id, owner_id, conversation_id),
            )
            connection.execute(
                "DELETE FROM conversation_summary WHERE owner_id = %s AND conversation_id = %s",
                (owner_id, conversation_id),
            )
            connection.execute(
                "DELETE FROM conversation_message WHERE owner_id = %s AND conversation_id = %s",
                (owner_id, conversation_id),
            )

    # ---------- 滚动摘要 ----------

    def load(
        self, *, owner_id: str, conversation_id: str
    ) -> ConversationSummary | None:
        """读取唯一摘要；不存在时返回 None。"""
        _require_identity(owner_id, conversation_id)
        with get_postgres_connection() as connection:
            row = connection.execute(
                """
                SELECT owner_id, conversation_id, summary, covered_until_message_id,
                       version, update_time
                FROM conversation_summary
                WHERE owner_id = %s AND conversation_id = %s
                """,
                (owner_id, conversation_id),
            ).fetchone()
        if row is None:
            return None
        return ConversationSummary(
            owner_id=row["owner_id"],
            conversation_id=row["conversation_id"],
            content=row["summary"],
            covered_until_message_id=row["covered_until_message_id"],
            version=row["version"],
            updated_at=_iso(row["update_time"]),
        )

    def save_if_cursor_matches(
        self, *, summary: ConversationSummary, expected_cursor: str | None
    ) -> bool:
        """用旧游标和旧版本做乐观锁，防止慢摘要覆盖较新的摘要。

        summary.version 是调用者读取到的旧版本；成功后数据库加一。
        expected_cursor 是旧摘要的游标，不能传新摘要的游标。
        """
        _require_identity(summary.owner_id, summary.conversation_id)
        if not isinstance(summary.content, dict) or summary.version < 0:
            raise ValueError("摘要必须是 dict，版本不能为负")
        with get_postgres_connection() as connection:
            if summary.covered_until_message_id is not None:
                # 不允许摘要声称覆盖了不存在或属于其他用户的消息。
                row = connection.execute(
                    """
                    SELECT 1 FROM conversation_message
                    WHERE owner_id = %s AND conversation_id = %s AND message_id = %s
                    """,
                    (summary.owner_id, summary.conversation_id,
                     summary.covered_until_message_id),
                ).fetchone()
                if row is None:
                    raise ValueError("摘要游标未指向当前用户会话的消息")
            if expected_cursor is None and summary.version == 0:
                row = connection.execute(
                    """
                    INSERT INTO conversation_summary
                        (owner_id, conversation_id, summary, covered_until_message_id, version)
                    VALUES (%s, %s, %s, %s, 1)
                    ON CONFLICT (owner_id, conversation_id) DO NOTHING
                    RETURNING id
                    """,
                    (summary.owner_id, summary.conversation_id, Jsonb(summary.content),
                     summary.covered_until_message_id),
                ).fetchone()
                return row is not None
            row = connection.execute(
                """
                UPDATE conversation_summary
                SET summary = %s, covered_until_message_id = %s,
                    version = version + 1, update_time = CURRENT_TIMESTAMP
                WHERE owner_id = %s AND conversation_id = %s
                  AND covered_until_message_id IS NOT DISTINCT FROM %s
                  AND version = %s
                RETURNING id
                """,
                (Jsonb(summary.content), summary.covered_until_message_id,
                 summary.owner_id, summary.conversation_id,
                 expected_cursor, summary.version),
            ).fetchone()
            return row is not None

    def delete(self, *, owner_id: str, conversation_id: str) -> None:
        """仅删除指定用户会话的摘要；重复删除也是安全的。"""
        _require_identity(owner_id, conversation_id)
        with get_postgres_connection() as connection:
            connection.execute(
                "DELETE FROM conversation_summary WHERE owner_id = %s AND conversation_id = %s",
                (owner_id, conversation_id),
            )

    # ---------- 长期事实 ----------

    def search(
        self, *, owner_id: str, conversation_id: str, query: str, limit: int,
        person_id: str | None = None, category: str | None = None,
    ) -> tuple[MemoryFact, ...]:
        """小数据量关键词检索，只返回已确认、未过期、有完整证据的最新事实。

        以后向量索引只提供候选 fact_id，仍须回查 PostgreSQL 的
        owner/status/version/evidence。当前版本先取 owner 下有资格的有限候选，再用
        中文字符/二元组相关度排序，避免 ``ILIKE '%整句问题%'`` 几乎永远查不到。
        """
        _require_identity(owner_id, conversation_id)
        _validate_limit(limit, AGENT_MEMORY_RETRIEVAL_LIMIT)
        if limit == 0 or (not query.strip() and not person_id):
            return ()
        # SQL 条件只由固定片段拼接；person_id/category 始终是绑定参数。
        person_filter = " AND person_id = %s" if person_id else ""
        # 关系事实的类别带稳定 UUID 后缀（relationship:<uuid>），不是字面值 relationship。
        category_filter = (
            " AND category LIKE %s" if category == "relationship"
            else " AND category = %s" if category else ""
        )
        filters = tuple(value for value in (
            person_id, "relationship:%" if category == "relationship" else category,
        ) if value)
        with get_postgres_connection() as connection:
            rows = connection.execute(
                f"""
                WITH latest AS (
                    SELECT DISTINCT ON (person_id, category)
                           fact_id, owner_id, person_id, category, value, status,
                           version, evidence_message_ids, confidence, valid_from,
                           valid_to, supersedes_fact_id, create_time
                    FROM memory_fact
                    WHERE owner_id = %s AND status = 'active'
                      AND (valid_to IS NULL OR valid_to > CURRENT_TIMESTAMP)
                      {person_filter}{category_filter}
                    ORDER BY person_id, category, version DESC
                )
                SELECT fact_id, owner_id, person_id, category, value, status,
                       version, evidence_message_ids, confidence, valid_from,
                       valid_to, supersedes_fact_id
                FROM latest AS fact
                WHERE jsonb_array_length(evidence_message_ids) > 0
                  AND NOT EXISTS (
                      SELECT 1
                      FROM jsonb_array_elements_text(fact.evidence_message_ids) AS evidence(message_id)
                      WHERE NOT EXISTS (
                          SELECT 1 FROM conversation_message AS message
                          WHERE message.owner_id = fact.owner_id
                            AND message.message_id = evidence.message_id
                      )
                  )
                ORDER BY create_time DESC
                LIMIT %s
                """,
                # 这是关键词降级检索的候选池，不是最终返回数量。数据规模变大后
                # 应由向量/全文索引提供 fact_id，再执行同样的权限回查。
                (owner_id, *filters, max(100, limit * 20)),
            ).fetchall()
        # 人物可能已从 Neo4j 删除；不把失效人物的事实发给模型。
        owned = self._owned_person_ids(owner_id, {row["person_id"] for row in rows})
        if person_id:
            # 人物身份已由会话焦点确定，不能再凭“他的爱好”与其他人的文本打分。
            return tuple(
                _fact_from_row(row) for row in rows
                if row["person_id"] in owned
            )[:limit]
        ranked = sorted(
            (
                (_fact_relevance_score(query, row), row)
                for row in rows
                if row["person_id"] in owned
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        return tuple(
            _fact_from_row(row)
            for score, row in ranked[:limit]
            if score > 0
        )

    def save_candidates(
        self,
        *,
        owner_id: str,
        conversation_id: str,
        request_id: str,
        candidates: tuple[MemoryWriteCandidate, ...],
    ) -> tuple[MemoryWriteCandidate, ...]:
        """校验候选并版本化保存。

        稳定 fact_id 绑定 owner/request/person/category。重试读回相同候选；
        如果同一个幂等键提交了不同内容，则报冲突。

        ``requires_confirmation=False`` 只供确定性成功 Tool 产生的候选使用，经过
        owner、person 和 evidence 校验后直接 active。模型抽取、备注和低置信候选
        必须保持 True，只能保存为 pending_confirmation。
        """
        _require_identity(owner_id, conversation_id)
        if not request_id:
            raise ValueError("request_id 不能为空")
        if not isinstance(candidates, tuple):
            raise TypeError("candidates 必须是 tuple")
        if not candidates:
            return ()
        by_key: dict[tuple[str, str], MemoryWriteCandidate] = {}
        for candidate in candidates:
            if not candidate.person_id or not candidate.category:
                raise ValueError("候选缺少 person_id 或 category")
            if not isinstance(candidate.value, dict) or not candidate.value:
                raise ValueError("候选 value 必须是非空 dict")
            if not candidate.evidence_message_ids:
                raise ValueError("候选必须包含来源消息 ID")
            if candidate.confidence is not None and not 0 <= candidate.confidence <= 1:
                raise ValueError("confidence 必须在 0 到 1 之间")
            key = (candidate.person_id, candidate.category)
            if key in by_key:
                raise MemoryConflictError("同一请求有重复的人物/事实类别候选")
            by_key[key] = candidate

        # 跨库人物归属检查放在 PostgreSQL 事务前。
        person_ids = {candidate.person_id for candidate in candidates}
        if self._owned_person_ids(owner_id, person_ids) != person_ids:
            raise ValueError("候选人物不存在或不属于当前用户")

        accepted: list[MemoryWriteCandidate] = []
        with get_postgres_connection() as connection:
            for person_id, category in sorted(by_key):
                candidate = by_key[(person_id, category)]
                _validate_evidence(
                    connection, owner_id, conversation_id,
                    candidate.evidence_message_ids,
                )
                # 同一人物/类别的并发版本分配必须串行；事务提交自动释放锁。
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))",
                    (owner_id, f"{person_id}:{category}"),
                )
                fact_id = _candidate_fact_id(owner_id, request_id, person_id, category)
                existing = connection.execute(
                    """
                    SELECT value, evidence_message_ids, confidence,
                           requires_confirmation
                    FROM memory_fact WHERE owner_id = %s AND fact_id = %s
                    """,
                    (owner_id, fact_id),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["value"] != candidate.value
                        or existing["evidence_message_ids"] != candidate.evidence_message_ids
                        or _normal_confidence(existing["confidence"])
                        != _normal_confidence(candidate.confidence)
                        or existing["requires_confirmation"]
                        != candidate.requires_confirmation
                    ):
                        raise MemoryConflictError("同一 request_id 的候选内容发生变化")
                    accepted.append(candidate)
                    continue
                next_version = connection.execute(
                    """
                    SELECT COALESCE(MAX(version), 0) + 1 AS next_version
                    FROM memory_fact
                    WHERE owner_id = %s AND person_id = %s AND category = %s
                    """,
                    (owner_id, person_id, category),
                ).fetchone()["next_version"]
                previous_active = None
                if not candidate.requires_confirmation:
                    previous_active = connection.execute(
                        """
                        SELECT fact_id FROM memory_fact
                        WHERE owner_id = %s AND person_id = %s AND category = %s
                          AND status = 'active'
                        ORDER BY version DESC
                        LIMIT 1
                        """,
                        (owner_id, person_id, category),
                    ).fetchone()
                status = (
                    MemoryFactStatus.PENDING_CONFIRMATION.value
                    if candidate.requires_confirmation
                    else MemoryFactStatus.ACTIVE.value
                )
                connection.execute(
                    """
                    INSERT INTO memory_fact
                        (fact_id, owner_id, person_id, category, value, status,
                          version, evidence_message_ids, confidence,
                          requires_confirmation, supersedes_fact_id)
                    VALUES (%s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s)
                    """,
                    (fact_id, owner_id, person_id, category, Jsonb(candidate.value),
                     status, next_version, Jsonb(candidate.evidence_message_ids),
                     candidate.confidence, candidate.requires_confirmation,
                     previous_active["fact_id"] if previous_active else None),
                )
                if previous_active is not None:
                    connection.execute(
                        """
                        UPDATE memory_fact
                        SET status = 'superseded', valid_to = CURRENT_TIMESTAMP,
                            update_time = CURRENT_TIMESTAMP
                        WHERE owner_id = %s AND fact_id = %s AND status = 'active'
                        """,
                        (owner_id, previous_active["fact_id"]),
                    )
                accepted.append(candidate)
        return tuple(accepted)

    def _owned_person_ids(self, owner_id: str, person_ids: set[str]) -> set[str]:
        if not person_ids:
            return set()
        if self._person_owner_checker is not None:
            return {
                person_id for person_id in person_ids
                if self._person_owner_checker(owner_id, person_id)
            }
        # 懒加载且一个批次共用一个驱动；网络异常向上抛，让上层标记降级。
        from backend.db import neo4j_client

        driver = neo4j_client.get_neo4j_driver()
        try:
            return {
                person_id for person_id in person_ids
                if neo4j_client.person_exists(driver, person_id, owner_id)
            }
        finally:
            driver.close()


def _require_identity(owner_id: str, conversation_id: str) -> None:
    if not owner_id or not conversation_id:
        raise ValueError("owner_id 和 conversation_id 不能为空")


def _validate_limit(limit: int, maximum: int) -> None:
    if type(limit) is not int or not 0 <= limit <= maximum:
        raise ValueError(f"limit 必须在 0 到 {maximum} 之间")


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value is not None else ""


def _message_from_row(row: dict) -> MemoryMessage:
    return MemoryMessage(
        message_id=row["message_id"], owner_id=row["owner_id"],
        conversation_id=row["conversation_id"], request_id=row["request_id"],
        role=MemoryRole(row["role"]), content=row["content"],
        token_count=row["token_count"], created_at=_iso(row["create_time"]),
        metadata=row["metadata"],
    )


def _insert_message_once(connection, message: MemoryMessage) -> None:
    row = connection.execute(
        """
        INSERT INTO conversation_message
            (message_id, owner_id, conversation_id, request_id, role,
             content, token_count, metadata)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (owner_id, request_id, role) DO NOTHING
        RETURNING message_id
        """,
        (message.message_id, message.owner_id, message.conversation_id,
         message.request_id, message.role.value, message.content,
         message.token_count, Jsonb(message.metadata)),
    ).fetchone()
    if row is not None:
        return
    existing = connection.execute(
        """
        SELECT message_id, conversation_id, content
        FROM conversation_message
        WHERE owner_id = %s AND request_id = %s AND role = %s
        """,
        (message.owner_id, message.request_id, message.role.value),
    ).fetchone()
    if (
        existing is None
        or existing["message_id"] != message.message_id
        or existing["conversation_id"] != message.conversation_id
        or existing["content"] != message.content
    ):
        raise MemoryConflictError("相同 request_id 和 role 对应了不同消息")


def _validate_evidence(
    connection, owner_id: str, conversation_id: str, message_ids: list[str]
) -> None:
    if len(set(message_ids)) != len(message_ids) or any(not mid for mid in message_ids):
        raise ValueError("证据消息 ID 不能为空或重复")
    rows = connection.execute(
        """
        SELECT message_id FROM conversation_message
        WHERE owner_id = %s AND conversation_id = %s AND message_id = ANY(%s)
        """,
        (owner_id, conversation_id, message_ids),
    ).fetchall()
    if {row["message_id"] for row in rows} != set(message_ids):
        raise ValueError("证据消息不属于当前用户会话")


def _candidate_fact_id(
    owner_id: str, request_id: str, person_id: str, category: str
) -> str:
    identity = json.dumps(
        [owner_id, request_id, person_id, category],
        ensure_ascii=False, separators=(",", ":"),
    )
    return str(uuid5(NAMESPACE_URL, f"relationship-memory-candidate:{identity}"))


def _normal_confidence(value) -> float | None:
    return None if value is None else round(float(value), 4)


def _fact_relevance_score(query: str, row: dict) -> float:
    """无需分词器的中文友好降级评分；只用于小候选池重排。"""
    document = json.dumps(
        {"category": row["category"], "value": row["value"]},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    normalized_query = _normalize_search_text(query)
    normalized_document = _normalize_search_text(document)
    if not normalized_query or not normalized_document:
        return 0.0
    score = 5.0 if normalized_query in normalized_document else 0.0
    query_chars = set(normalized_query)
    if query_chars:
        score += len(query_chars & set(normalized_document)) / len(query_chars)
    query_bigrams = _bigrams(normalized_query)
    if query_bigrams:
        score += 2.0 * len(query_bigrams & _bigrams(normalized_document)) / len(query_bigrams)
    return score


def _normalize_search_text(value: str) -> str:
    # 去掉标点和高频虚词，降低“的、了、吗”带来的无关命中。
    normalized = "".join(re.findall(r"[0-9a-zA-Z\u4e00-\u9fff]", value.lower()))
    for stop_word in ("的", "了", "吗", "呢", "我", "你", "他", "她", "它"):
        normalized = normalized.replace(stop_word, "")
    return normalized


def _bigrams(value: str) -> set[str]:
    return {value[index:index + 2] for index in range(max(0, len(value) - 1))}


def _fact_from_row(row: dict) -> MemoryFact:
    return MemoryFact(
        fact_id=row["fact_id"], owner_id=row["owner_id"],
        person_id=row["person_id"], category=row["category"],
        value=row["value"], status=MemoryFactStatus(row["status"]),
        version=row["version"], evidence_message_ids=row["evidence_message_ids"],
        confidence=float(row["confidence"]) if row["confidence"] is not None else None,
        valid_from=_iso(row["valid_from"]), valid_to=_iso(row["valid_to"]) or None,
        supersedes_fact_id=row["supersedes_fact_id"],
    )
