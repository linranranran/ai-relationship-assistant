"""流式任务仓储：短事务受理、执行租约以及可回放的产品事件。

阅读重点：任务和执行批次同事务；最终结果与终止事件同事务。Web 进程直接
运行 LangGraph，PostgreSQL 保存恢复所需记录。所有读取都带 owner_id。
"""

from pathlib import Path
from uuid import uuid4

from psycopg.types.json import Jsonb

from backend.agent.request_idempotency import hash_request_message
from backend.config import AGENT_RUN_LEASE_SECONDS, AGENT_QUEUE_LIMIT, AGENT_EVENT_RETENTION
from backend.db.postgres_client import get_postgres_connection


class TaskConflict(ValueError):
    """重复请求内容不一致，或者用户仍有未结束的任务。"""


class RunLeaseLost(RuntimeError):
    """旧工作者失去执行权，禁止继续写进度或覆盖新工作者的结果。"""


def ensure_stream_schema():
    sql = Path(__file__).with_name("migrations").joinpath("007_agent_stream.sql").read_text(encoding="utf-8")
    with get_postgres_connection() as connection:
        connection.execute(sql, prepare=False)


def _latest(connection, owner_id, request_id):
    return connection.execute(
        "SELECT t.*, r.status, r.response, r.run_id, r.action, r.interrupt_id, "
        "r.execution_token, r.queued_at AS run_queued_at FROM agent_task t JOIN agent_run r ON r.run_id = t.current_run_id "
        "WHERE t.owner_id = %s AND t.request_id = %s", (owner_id, request_id)).fetchone()


def load_task(owner_id, request_id):
    with get_postgres_connection() as connection:
        row = _latest(connection, owner_id, request_id)
    return dict(row) if row else None


def _event(connection, owner_id, request_id, run_id, event_type, **data):
    # 对同一个请求的 seq 行加短锁，实现跨进程有序；不以时间戳充当事件游标。
    seq = connection.execute(
        "UPDATE agent_task SET seq = seq + 1 WHERE owner_id = %s AND request_id = %s "
        "RETURNING seq", (owner_id, request_id)).fetchone()["seq"]
    event = {"schema_version": 1, "request_id": request_id, "run_id": run_id,
             "seq": seq, "type": event_type, **data}
    connection.execute(
        "INSERT INTO agent_event (owner_id, request_id, seq, run_id, event) VALUES (%s,%s,%s,%s,%s)",
        (owner_id, request_id, seq, run_id, Jsonb(event)))
    # 有界保留。旧游标被淘汰后，SSE 会发送权威快照，客户端不用猜丢失了什么。
    connection.execute("DELETE FROM agent_event WHERE owner_id = %s AND request_id = %s AND seq <= %s",
                       (owner_id, request_id, seq - AGENT_EVENT_RETENTION))
    return event


def _create_run(connection, owner_id, request_id, action, interrupt_id=None):
    run_id = str(uuid4())
    connection.execute(
        "INSERT INTO agent_run (run_id, owner_id, request_id, action, interrupt_id) VALUES (%s,%s,%s,%s,%s)",
        (run_id, owner_id, request_id, action, interrupt_id))
    connection.execute("UPDATE agent_task SET current_run_id = %s WHERE owner_id = %s AND request_id = %s",
                       (run_id, owner_id, request_id))
    _event(connection, owner_id, request_id, run_id, "request.accepted", message="任务已接收，等待执行")
    return dict(_latest(connection, owner_id, request_id))


def accept_chat(owner_id, request_id, thread_id, message, user_context):
    """提交完成才返回 202；Web 重启后仍可按原 run_id 从检查点继续。"""
    message_hash = hash_request_message(message)
    with get_postgres_connection() as connection:
        # Admission 短锁只串行化同一会话的受理，不持锁等待模型。
        connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 7))", (thread_id,))
        # 重试/恢复/取消都按 task -> run 的锁顺序；不能分别用不同的锁抢批次。
        connection.execute("SELECT 1 FROM agent_task WHERE owner_id=%s AND request_id=%s FOR UPDATE",
                           (owner_id, request_id))
        existing = _latest(connection, owner_id, request_id)
        if existing:
            if existing["message_hash"] != message_hash:
                raise TaskConflict("request_id 已用于另一条消息")
            if existing["status"] != "failed":
                return dict(existing)
            # 失败重试换 run_id，保留 request_id、原始输入和全部 Tool call_id。
            return _create_run(connection, owner_id, request_id, "CHAT")
        busy = connection.execute(
            "SELECT 1 FROM agent_task t JOIN agent_run r ON r.run_id = t.current_run_id "
            "WHERE t.thread_id = %s AND r.status IN ('queued','running','waiting','failed') LIMIT 1",
            (thread_id,)).fetchone()
        if busy:
            raise TaskConflict("当前会话仍有未结束任务，请先处理、重试或取消原任务")
        # 全局受理上限限制积压。极端并发可能短暂超出，生产还应配网关速率限制。
        queued = connection.execute("SELECT count(*) AS n FROM agent_run WHERE status = 'queued'").fetchone()["n"]
        if queued >= AGENT_QUEUE_LIMIT:
            raise TaskConflict("任务队列繁忙，请稍后重试")
        connection.execute(
            "INSERT INTO agent_task (owner_id, request_id, thread_id, message_hash, input) VALUES (%s,%s,%s,%s,%s)",
            (owner_id, request_id, thread_id, message_hash,
             Jsonb({"message": message, "current_user": user_context})))
        return _create_run(connection, owner_id, request_id, "CHAT")


def accept_resume(owner_id, request_id, interrupt_id, answer):
    """答案不可变回执与恢复任务一起提交，不留下“收了答案却没排队”的窗口。"""
    import json
    from hashlib import sha256
    answer_hash = sha256(json.dumps(answer, sort_keys=True, ensure_ascii=False,
                                   separators=(",", ":")).encode()).hexdigest()
    with get_postgres_connection() as connection:
        connection.execute("SELECT 1 FROM agent_task WHERE owner_id=%s AND request_id=%s FOR UPDATE",
                           (owner_id, request_id))
        current = _latest(connection, owner_id, request_id)
        if not current:
            raise TaskConflict("请求不存在或不属于当前用户")
        connection.execute(
            "INSERT INTO agent_interaction (owner_id, request_id, interrupt_id, answer, answer_hash) "
            "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (owner_id, interrupt_id) DO NOTHING",
            (owner_id, request_id, interrupt_id, Jsonb(answer), answer_hash))
        receipt = connection.execute("SELECT request_id, answer_hash FROM agent_interaction "
                                     "WHERE owner_id=%s AND interrupt_id=%s", (owner_id, interrupt_id)).fetchone()
        if receipt["request_id"] != request_id or receipt["answer_hash"] != answer_hash:
            raise TaskConflict("该中断已提交过不同答案，请查询原请求结果")
        if current["status"] in {"queued", "running", "completed", "cancelled"}:
            return dict(current)
        return _create_run(connection, owner_id, request_id, "RESUME", interrupt_id)


def request_cancel(owner_id, request_id):
    """协作式停止：只阻止后续步骤；已经提交的新增/修改不会自动回滚。"""
    with get_postgres_connection() as connection:
        row = connection.execute("SELECT 1 FROM agent_task WHERE owner_id=%s AND request_id=%s FOR UPDATE",
                                 (owner_id, request_id)).fetchone()
        if not row:
            return None
        current = _latest(connection, owner_id, request_id)
        if current["status"] in {"completed", "cancelled"}:
            return dict(current)
        connection.execute("UPDATE agent_task SET cancel_requested=TRUE WHERE owner_id=%s AND request_id=%s",
                           (owner_id, request_id))
        if current["status"] in {"waiting", "failed"}:
            return _create_run(connection, owner_id, request_id, "STOP")
        return dict(_latest(connection, owner_id, request_id))


def claim_run(run_id):
    """重复调度时，同一 run_id 同时只有一个有效执行租约。"""
    token = str(uuid4())
    with get_postgres_connection() as connection:
        identity = connection.execute("SELECT owner_id, request_id FROM agent_run WHERE run_id=%s",
                                      (run_id,)).fetchone()
        if not identity:
            return None
        task = connection.execute("SELECT current_run_id FROM agent_task WHERE owner_id=%s AND request_id=%s FOR UPDATE",
                                  (identity["owner_id"], identity["request_id"])).fetchone()
        if not task or task["current_run_id"] != run_id:
            return None  # 已被后续批次替代的旧消息不能再次抢占。
        row = connection.execute(
            "UPDATE agent_run SET status='running', execution_token=%s, "
            "lease_until=CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'), "
            "started_at=COALESCE(started_at,CURRENT_TIMESTAMP) "
            "WHERE run_id=%s AND (status='queued' OR (status='running' AND lease_until<CURRENT_TIMESTAMP)) "
            "RETURNING *", (token, AGENT_RUN_LEASE_SECONDS, run_id)).fetchone()
        if not row:
            return None
        current = _latest(connection, row["owner_id"], row["request_id"])
        return {**dict(current), "execution_token": token}


def heartbeat(run, request_execution_token=None):
    with get_postgres_connection() as connection:
        cursor = connection.execute(
            "UPDATE agent_run SET lease_until=CURRENT_TIMESTAMP + (%s * INTERVAL '1 second') "
            "WHERE run_id=%s AND execution_token=%s AND status='running'",
            (AGENT_RUN_LEASE_SECONDS, run["run_id"], run["execution_token"]))
        if cursor.rowcount != 1:
            raise RunLeaseLost("执行批次租约已失效")
        if request_execution_token:
            connection.execute("UPDATE agent_request SET update_time=CURRENT_TIMESTAMP "
                               "WHERE owner_id=%s AND request_id=%s AND execution_token=%s AND status='running'",
                               (run["owner_id"], run["request_id"], request_execution_token))
        return connection.execute("SELECT cancel_requested FROM agent_task WHERE owner_id=%s AND request_id=%s",
                                  (run["owner_id"], run["request_id"])).fetchone()["cancel_requested"]


def _fence(connection, run):
    task = connection.execute("SELECT current_run_id FROM agent_task WHERE owner_id=%s AND request_id=%s FOR UPDATE",
                              (run["owner_id"], run["request_id"])).fetchone()
    row = connection.execute("SELECT status, execution_token FROM agent_run WHERE run_id=%s FOR UPDATE",
                             (run["run_id"],)).fetchone()
    if not task or task["current_run_id"] != run["run_id"] or not row or row["status"] != "running" or row["execution_token"] != run["execution_token"]:
        raise RunLeaseLost("旧执行批次不能写入事件")


def append_event(run, event_type, **data):
    with get_postgres_connection() as connection:
        _fence(connection, run)
        return _event(connection, run["owner_id"], run["request_id"], run["run_id"], event_type, **data)


def finish_run(run, response):
    if response["status"] == "RUNNING":
        # WAIT 是图已主动结束的可重试状态，当前批次没有后台任务继续执行。
        response = {**response, "status": "FAILED",
                    "response": "工具暂被其他执行者占用，请稍后使用原请求重试。"}
    status = response["status"]
    run_status, event_type = {
        "COMPLETED": ("completed", "request.completed"),
        "CANCELLED": ("cancelled", "request.cancelled"),
        "CONFIRMATION_REQUIRED": ("waiting", "interaction.required"),
        "CLARIFICATION_REQUIRED": ("waiting", "interaction.required"),
    }.get(status, ("failed", "request.failed"))
    with get_postgres_connection() as connection:
        _fence(connection, run)
        _event(connection, run["owner_id"], run["request_id"], run["run_id"], event_type,
               message=response["response"], payload={"response": response})
        connection.execute("UPDATE agent_run SET status=%s, response=%s, finished_at=CURRENT_TIMESTAMP "
                           "WHERE run_id=%s", (run_status, Jsonb(response), run["run_id"]))


def pending_runs(limit=4, excluded=()):
    """寻找尚未启动/租约已过期的当前批次，不消费消息、不改变状态。

    进程内线程满、受理后崩溃或会话锁争用，都能再次按原批次安排执行。
    waiting 不在范围内：人工答案提交前不会自动恢复一个中断。
    """
    with get_postgres_connection() as connection:
        rows = connection.execute(
            "SELECT r.run_id FROM agent_run r WHERE "
            "EXISTS (SELECT 1 FROM agent_task t WHERE t.owner_id=r.owner_id AND t.request_id=r.request_id AND t.current_run_id=r.run_id) AND "
            "(r.status='queued' OR (r.status='running' AND r.lease_until<CURRENT_TIMESTAMP)) "
            "AND r.run_id <> ALL(%s::varchar[]) "
            "ORDER BY r.queued_at LIMIT %s", (list(excluded), limit)).fetchall()
        return [row["run_id"] for row in rows]


def load_events(owner_id, request_id, after, limit=100):
    with get_postgres_connection() as connection:
        rows = connection.execute("SELECT event FROM agent_event WHERE owner_id=%s AND request_id=%s "
                                  "AND seq>%s ORDER BY seq LIMIT %s", (owner_id, request_id, after, limit)).fetchall()
    return [row["event"] for row in rows]
