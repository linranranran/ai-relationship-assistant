"""不可变客户聊天证据仓储；短事务导入、唯一约束去重、所有读取带 owner。

阅读 save_messages：冲突先交给数据库裁决，再比对内容。任何一条冲突抛异常，
连接上下文回滚整批；不能只 catch 后继续，否则会出现半批成功。
"""

from hashlib import sha256
import json
from pathlib import Path
from datetime import datetime
from uuid import uuid4

from backend.db.postgres_client import get_postgres_connection
from backend.sales.contracts import ChatImportResult, SimulatedChatPayload, StoredChatMessage


class ChatImportConflict(ValueError):
    """来源消息已存在，但原文或人物归属不同；不能自动覆盖历史证据。"""


def ensure_sales_schema() -> None:
    """重复启动只执行幂等 DDL，不删除或回写任何现有业务数据。"""
    ddl = Path(__file__).with_name("migrations").joinpath("012_customer_chat_message.sql").read_text(encoding="utf-8")
    with get_postgres_connection() as connection:
        connection.execute(ddl, prepare=False)


def _message_hash(person_id, conversation_id, message) -> str:
    # 只去重同一来源的同一消息，不按句子去重。时间已在契约中转为 UTC。
    value = {"person_id": person_id, "source_type": "simulated_chat",
             "conversation": conversation_id, **message.model_dump(mode="json")}
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def save_messages(*, owner_id: str, person_id: str, payload: SimulatedChatPayload) -> ChatImportResult:
    if not owner_id or not person_id:
        raise ValueError("保存聊天缺少可信身份")
    inserted = 0
    with get_postgres_connection() as connection:
        # 相同批次即使输入顺序相反，也按稳定来源 ID 获取唯一键锁，降低死锁风险。
        for message in sorted(payload.messages, key=lambda item: item.source_message_id):
            fingerprint = _message_hash(person_id, payload.source_conversation_id, message)
            row = connection.execute(
                "INSERT INTO customer_chat_message (message_id,owner_id,person_id,source_type,"
                "source_conversation_id,source_message_id,speaker,sent_at,text,content_hash) "
                "VALUES (%s,%s,%s,'simulated_chat',%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (owner_id,source_type,source_conversation_id,source_message_id) "
                "DO NOTHING RETURNING message_id",
                (uuid4(), owner_id, person_id, payload.source_conversation_id,
                 message.source_message_id, message.speaker, message.sent_at, message.text, fingerprint),
            ).fetchone()
            if row:
                inserted += 1
                continue
            # READ COMMITTED 下这是新的语句快照：并发赢家提交后，能读取其结果。
            existing = connection.execute(
                "SELECT person_id,content_hash FROM customer_chat_message "
                "WHERE owner_id=%s AND source_type='simulated_chat' "
                "AND source_conversation_id=%s AND source_message_id=%s",
                (owner_id, payload.source_conversation_id, message.source_message_id),
            ).fetchone()
            if not existing or existing["person_id"] != person_id or existing["content_hash"] != fingerprint:
                raise ChatImportConflict("来源消息与已保存内容或客户绑定冲突，请使用新的来源消息标识")
    return ChatImportResult(total_count=len(payload.messages), inserted_count=inserted,
                            duplicate_count=len(payload.messages) - inserted)


def _visible_message(row) -> StoredChatMessage:
    # 明确列出可返回字段；后续增加内部字段也不会意外泄露到 API。
    return StoredChatMessage(**{key: (str(row[key]) if key == "message_id" else row[key])
                               for key in StoredChatMessage.model_fields})


def get_message(*, owner_id: str, message_id: str) -> StoredChatMessage | None:
    with get_postgres_connection() as connection:
        row = connection.execute(
            "SELECT * FROM customer_chat_message WHERE owner_id=%s AND message_id=%s",
            (owner_id, message_id),
        ).fetchone()
    return _visible_message(row) if row else None


def list_candidates(*, owner_id: str, person_id: str, as_of: datetime, limit: int) -> list[StoredChatMessage]:
    if not owner_id or not person_id or not 1 <= limit <= 1000:
        raise ValueError("检索身份或数量不合法")
    with get_postgres_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM customer_chat_message WHERE owner_id=%s AND person_id=%s "
            "AND sent_at<=%s AND created_at<=%s ORDER BY sent_at DESC,message_id DESC LIMIT %s",
            (owner_id, person_id, as_of, as_of, limit),
        ).fetchall()
    return [_visible_message(row) for row in rows]
