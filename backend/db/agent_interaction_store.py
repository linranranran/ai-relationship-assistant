"""人工答案回执：先保存答案，再交给 LangGraph，覆盖 resume 中途崩溃窗口。"""

import json
from hashlib import sha256
from pathlib import Path

from psycopg.types.json import Jsonb
from backend.db.postgres_client import get_postgres_connection


def ensure_interaction_schema():
    sql = Path(__file__).with_name("migrations").joinpath(
        "006_agent_interaction.sql").read_text(encoding="utf-8")
    with get_postgres_connection() as connection:
        connection.execute(sql, prepare=False)


def get_interaction(owner_id: str, interrupt_id: str) -> dict | None:
    with get_postgres_connection() as connection:
        row = connection.execute(
            "SELECT request_id, answer, answer_hash FROM agent_interaction "
            "WHERE owner_id = %s AND interrupt_id = %s", (owner_id, interrupt_id)
        ).fetchone()
    return dict(row) if row else None


def save_interaction(owner_id: str, request_id: str, interrupt_id: str, answer: dict):
    """相同中断只接受同一答案；超时重试不能把确认改成取消或换一个候选。"""
    answer_hash = sha256(json.dumps(answer, sort_keys=True, ensure_ascii=False,
                                   separators=(",", ":")).encode("utf-8")).hexdigest()
    with get_postgres_connection() as connection:
        connection.execute(
            "INSERT INTO agent_interaction (owner_id, request_id, interrupt_id, answer, answer_hash) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (owner_id, interrupt_id) DO NOTHING",
            (owner_id, request_id, interrupt_id, Jsonb(answer), answer_hash))
        row = connection.execute(
            "SELECT request_id, answer_hash FROM agent_interaction "
            "WHERE owner_id = %s AND interrupt_id = %s", (owner_id, interrupt_id)).fetchone()
        if row["request_id"] != request_id or row["answer_hash"] != answer_hash:
            raise ValueError("该中断已提交过不同答案，请查询原请求结果")
