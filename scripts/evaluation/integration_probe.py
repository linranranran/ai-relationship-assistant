"""真实 HTTP/数据库评测驱动。结果只含虚构数据，凭据留在忽略目录。

图级质量实验用 Fixture 消除数据残留；本文件另验证真实认证、持久化和 SSE。
同一 request_id 的重试必须读取原任务，绝不能为了重连再创建任务。
"""
import argparse
import json
import secrets
from pathlib import Path
from time import perf_counter, sleep
from uuid import uuid4
import httpx

from scripts.evaluation.integration_setup import ROOT, SCHEMA
from psycopg.conninfo import make_conninfo
from backend.db import postgres_client

original_conninfo = postgres_client.build_postgres_conninfo
postgres_client.build_postgres_conninfo = lambda: make_conninfo(
    original_conninfo(), options="-c search_path=" + SCHEMA)
from backend.db.neo4j_client import get_neo4j_driver, create_person

FOLDER = ROOT / "docs/评测文档/runs/integration-20261014"
PRIVATE = ROOT / ".superpowers/sdd/2026-10-14-project-evaluation/integration-private.json"
URL = "http://127.0.0.1:18082"
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "BUDGET_EXCEEDED", "PARTIALLY_COMPLETED",
            "CONFIRMATION_REQUIRED", "CLARIFICATION_REQUIRED"}


def write(name, value):
    (FOLDER / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def client(account):
    return httpx.Client(base_url=URL, headers={"Authorization": "Bearer " + account["token"]}, timeout=30, trust_env=False)


def poll(c, request_id, limit=330):
    started = perf_counter()
    while perf_counter() - started < limit:
        response = c.get("/api/chat/requests/" + request_id)
        response.raise_for_status()
        data = response.json()
        if data["status"] in TERMINAL:
            return data
        sleep(0.7)
    return {"request_id": request_id, "status": "EVALUATION_TIMEOUT"}


def submit(c, text, kind="chat"):
    request_id = str(uuid4())
    start = perf_counter()
    response = c.post("/api/chat", json={"message": text, "response_kind": kind},
                      headers={"Idempotency-Key": request_id})
    response.raise_for_status()
    return request_id, start, response.json()


def setup():
    accounts = []
    # 注册真实测试账号。随机手机号只是符合表单格式的虚构标识，不向手机发送信息。
    with httpx.Client(base_url=URL, timeout=30, trust_env=False) as c:
        for index in range(4):
            body = {"phone_number": "199" + str(secrets.randbelow(10**8)).zfill(8),
                    "password": secrets.token_hex(12)}
            c.post("/api/auth/register", json=body).raise_for_status()
            login = c.post("/api/auth/login", json=body)
            login.raise_for_status()
            account = {**body, **login.json()}
            accounts.append(account)
    with get_neo4j_driver() as driver:
        for index, account in enumerate(accounts):
            props = {"name": "评测客户杨鑫" + str(index), "occupation": "采购负责人", "birth_year": 1998,
                     "hobbies": ["阅读"], "note": "纯虚构评测客户"}
            person = create_person(driver, props, account["user_id"], idempotency_key=str(uuid4()))
            account["person_id"] = person["id"]
            account["person_name"] = props["name"]
    PRIVATE.write_text(json.dumps(accounts, ensure_ascii=False), encoding="utf-8")
    write("accounts.json", [{k: a[k] for k in ("user_id", "person_id", "person_name")} for a in accounts])
    print("Created four isolated synthetic accounts", flush=True)


def api():
    accounts = json.loads(PRIVATE.read_text(encoding="utf-8"))
    a, b = accounts[:2]
    results = []
    with client(a) as c, client(b) as other:
        original = {"person_id": a["person_id"], "payload": {
            "source_conversation_id": "integration-synthetic-001", "messages": [
                {"source_message_id": "001", "speaker": "customer", "sent_at": "2026-10-13T10:00:00+08:00",
                 "text": "我们想了解销售数据报表，尤其是权限管理。"},
                {"source_message_id": "002", "speaker": "sales", "sent_at": "2026-10-13T10:01:00+08:00",
                 "text": "我明天发送演示材料。"}]}}
        imported = c.post("/api/sales/chat-imports", json=original)
        imported.raise_for_status()
        repeated = c.post("/api/sales/chat-imports", json=original)
        repeated.raise_for_status()
        alien = other.post("/api/sales/chat-imports", json=original)
        results.append({"check": "import_idempotency_and_owner", "pass":
                        imported.json()["inserted_count"] == 2 and repeated.json()["duplicate_count"] == 2
                        and alien.status_code == 404,
                        "first": imported.json(), "repeated": repeated.json(), "other_status": alien.status_code})
        stale = c.post("/api/chat/resume", json={"interrupt_id": "expired-synthetic-id", "confirmed": True})
        results.append({"case_id": "A05", "check": "stale_interrupt", "pass": stale.status_code == 409,
                        "http_status": stale.status_code, "response": stale.json()})
        rid, start, accepted = submit(c, "查询" + a["person_name"] + "的出生年份和爱好")
        events = []
        # 连上正在运行的 SSE，收到前两个事件便主动断开，再按 seq 重连。
        with c.stream("GET", f"/api/chat/requests/{rid}/events") as stream:
            stream.raise_for_status()
            for line in stream.iter_lines():
                if line.startswith("data: "):
                    events.append({"received_ms": (perf_counter()-start)*1000, **json.loads(line[6:])})
                    if len(events) >= 2:
                        break
        cursor = max(e["seq"] for e in events)
        with c.stream("GET", f"/api/chat/requests/{rid}/events?after={cursor}",
                      headers={"Last-Event-ID": str(cursor)}) as stream:
            stream.raise_for_status()
            for line in stream.iter_lines():
                if line.startswith("data: "):
                    events.append({"received_ms": (perf_counter()-start)*1000, **json.loads(line[6:])})
        final = poll(c, rid)
        duplicate = c.post("/api/chat", json={"message": "查询"+a["person_name"]+"的出生年份和爱好"},
                           headers={"Idempotency-Key": rid})
        unauthorized = other.get(f"/api/chat/requests/{rid}")
        other_events = other.get(f"/api/chat/requests/{rid}/events")
        seqs = [e["seq"] for e in events if e["type"] != "request.snapshot"]
        results.extend([
            {"case_id": "E01", "check": "duplicate_request", "pass": duplicate.json() == final,
             "request_id": rid, "final": final, "duplicate": duplicate.json()},
            {"case_id": "A03", "check": "sse_reconnect", "pass": final["status"] == "COMPLETED"
             and seqs == sorted(set(seqs)), "cursor": cursor, "event_count": len(events)},
            {"case_id": "A07", "check": "request_event_owner", "pass":
             unauthorized.status_code == 404 and other_events.status_code == 404,
             "request_status": unauthorized.status_code, "events_status": other_events.status_code},
        ])
        write("sse-events.json", events)
        history = c.get("/api/chat/history")
        history.raise_for_status()
        results.append({"case_id": "A01", "check": "history_load", "pass":
                        rid in json.dumps(history.json()), "history": history.json()})
        # 在真实 PG 中取得导入后的消息 ID，再经鉴权 HTTP 读取原文。
        with postgres_client.get_postgres_connection() as connection:
            row = connection.execute("SELECT message_id FROM customer_chat_message WHERE owner_id=%s AND person_id=%s LIMIT 1",
                                     (a["user_id"], a["person_id"])).fetchone()
        message_id = str(row["message_id"])
        own = c.get("/api/sales/messages/" + message_id)
        alien = other.get("/api/sales/messages/" + message_id)
        results.append({"case_id": "A08", "check": "evidence_original", "pass":
                        own.status_code == 200 and alien.status_code == 404,
                        "own_message": own.json(), "other_status": alien.status_code})
        rid, start, accepted = submit(c, "我明天要联系" + a["person_name"] + "，帮我准备一下", "sales_preparation")
        final = poll(c, rid)
        results.append({"case_id": "A02", "check": "card_persistence", "pass":
                        bool(final.get("sales_preparation")), "request_id": rid,
                        "duration_ms": (perf_counter()-start)*1000, "final": final})
        history = c.get("/api/chat/history").json()
        write("history-after-preparation.json", history)
        results[-1]["history_contains_card"] = bool(history.get("messages")) and "sales_preparation" in json.dumps(history)
        results[-1]["pass"] = results[-1]["pass"] and results[-1]["history_contains_card"]
        # 如准备失败，主动停止旧任务后再发新问题；把“失败导致会话锁死”独立评测。
        if final["status"] != "COMPLETED":
            c.post(f"/api/chat/requests/{rid}/cancel")
            poll(c, rid)
        next_id, _, accepted = submit(c, "查询" + a["person_name"] + "的爱好")
        next_final = poll(c, next_id)
        results.append({"case_id": "A06", "check": "session_after_failure_or_terminal", "pass":
                        next_final["status"] == "COMPLETED", "previous_status": final["status"], "final": next_final})
    write("api-results.json", results)
    print(json.dumps({"checks": len(results), "passed": sum(r["pass"] for r in results)}, ensure_ascii=False), flush=True)


def restart_prepare():
    a = json.loads(PRIVATE.read_text(encoding="utf-8"))[1]
    with client(a) as c:
        rid, _, _ = submit(c, "把" + a["person_name"] + "的备注改为：已经通过重启恢复评测")
        final = poll(c, rid)
        write("restart-before.json", {"request_id": rid, "owner_id": a["user_id"], "person_id": a["person_id"], "final": final})
        print(json.dumps({"request_id": rid, "status": final["status"]}), flush=True)


def normalize_fixture_names():
    # 原始“评测客户杨鑫0”被模型误解析为“杨鑫0”。保留失败记录，
    # 后续恢复实验使用普通真实姓名形式，避免把实体抽取错误混入恢复验收。
    accounts = json.loads(PRIVATE.read_text(encoding="utf-8"))
    with get_neo4j_driver() as driver, driver.session() as session:
        for a in accounts:
            row = session.run("MATCH (p:Person {owner_id:$owner,id:$id}) SET p.name=$name RETURN p.id AS id",
                              owner=a["user_id"], id=a["person_id"], name="杨鑫").single()
            assert row and row["id"] == a["person_id"]
            a["person_name"] = "杨鑫"
    PRIVATE.write_text(json.dumps(accounts, ensure_ascii=False), encoding="utf-8")
    write("fixture-name-adjustment.json", {"reason": "separate name extraction failure from recovery test",
          "new_name": "杨鑫", "affected_synthetic_owners": [a["user_id"] for a in accounts]})


def restart_resume():
    a = json.loads(PRIVATE.read_text(encoding="utf-8"))[1]
    before = json.loads((FOLDER / "restart-before.json").read_text(encoding="utf-8"))
    prompt = before["final"].get("confirmation")
    if not prompt:
        write("restart-result.json", {"case_id": "E04", "pass": False, "reason": "No resumable confirmation"})
        return
    with client(a) as c:
        c.post("/api/chat/resume", json={"interrupt_id": prompt["interrupt_id"], "confirmed": True}).raise_for_status()
        final = poll(c, before["request_id"])
    with get_neo4j_driver() as driver, driver.session() as session:
        record = session.run("MATCH (p:Person {owner_id:$owner,id:$id}) RETURN p.note AS note",
                             owner=a["user_id"], id=a["person_id"]).single()
        note = record["note"]
    write("restart-result.json", {"case_id": "E04", "pass": final["status"] == "COMPLETED"
          and note == "已经通过重启恢复评测", "note": note, "final": final,
          "experiment": "real service process terminated after PG interrupt persisted, new process resumed same interrupt_id"})
    print(json.dumps({"status": final["status"], "note": note}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["setup", "api", "restart_prepare", "restart_resume", "normalize_fixture_names"])
    args = parser.parse_args()
    globals()[args.phase]()
