"""聊天页面的只读投影，与给模型选上下文的 Token 预算分开。

页面可以翻阅所有原始问答，但模型仍只读取预算允许的上下文。这里不读取
Checkpoint 的内部状态，也不返回工具参数、长期记忆或服务器身份快照。
"""

from pathlib import Path

from backend.db.postgres_client import get_postgres_connection


class HistoryCursorError(ValueError):
    """翻页锚点不存在，或不属于当前账号的当前会话。"""


def ensure_history_schema() -> None:
    """只补充查询索引，沿用原始消息表，不新建另一套聊天记录。"""
    sql = Path(__file__).with_name("migrations").joinpath("008_chat_history.sql").read_text(encoding="utf-8")
    with get_postgres_connection() as connection:
        connection.execute(sql, prepare=False)


def load_chat_history(*, owner_id: str, conversation_id: str,
                      thread_id: str, before: str | None, limit: int) -> dict:
    """最新页倒序查询、正序展示；后续用最早 message_id 加载更早消息。

    UUID 只负责定位锚点，真正排序用数据库递增 id。每一条 SQL 都约束 owner，
    不能把浏览器传入的游标当作跨用户查询凭证。limit 与记忆层的最近 12 条
    配置无关，HTTP 层限制最多 100 条，避免一次拉取整个聊天记录。
    """
    if not owner_id or not conversation_id or not thread_id:
        raise ValueError("历史消息查询缺少会话身份")
    if not 1 <= limit <= 100:
        raise ValueError("limit 必须在 1 到 100 之间")
    with get_postgres_connection() as connection:
        cursor_id = None
        if before is not None:
            anchor = connection.execute(
                "SELECT id FROM conversation_message "
                "WHERE owner_id=%s AND conversation_id=%s AND message_id=%s "
                "AND role IN ('user','assistant')",
                (owner_id, conversation_id, before),
            ).fetchone()
            if anchor is None:
                raise HistoryCursorError("历史记录游标已失效，请重新加载")
            cursor_id = anchor["id"]
        rows = connection.execute(
            "SELECT id, message_id, request_id, role, content, create_time "
            "FROM conversation_message WHERE owner_id=%s AND conversation_id=%s "
            "AND role IN ('user','assistant') "
            + ("AND id < %s " if cursor_id is not None else "")
            + "ORDER BY id DESC LIMIT %s",
            (owner_id, conversation_id, *([cursor_id] if cursor_id is not None else []), limit + 1),
        ).fetchall()
        # 首屏同步查出尚未结束的任务：换浏览器后没有 localStorage，也能重新订阅。
        # 这里只返回消息与展示所需状态，user_context / execution_token 不出后端。
        pending = None
        if before is None:
            row = connection.execute(
                "SELECT t.request_id, t.input->>'message' AS message, "
                "t.created_at, r.run_id, r.status, r.response "
                "FROM agent_task t JOIN agent_run r ON r.run_id=t.current_run_id "
                "WHERE t.owner_id=%s AND t.thread_id=%s "
                "AND r.status IN ('queued','running','waiting','failed') "
                "ORDER BY t.created_at DESC LIMIT 1", (owner_id, thread_id),
            ).fetchone()
            pending = dict(row) if row else None
    has_more = len(rows) > limit
    visible = rows[:limit]
    return {
        "messages": [
            {"message_id": row["message_id"], "request_id": row["request_id"],
             "role": row["role"], "content": row["content"], "created_at": row["create_time"]}
            for row in reversed(visible)
        ],
        "has_more": has_more,
        "next_cursor": visible[-1]["message_id"] if has_more else None,
        "pending": pending,
    }
