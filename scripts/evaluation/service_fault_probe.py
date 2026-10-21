"""真实服务中的一次性故障与目录变化。注入位置和局限随结果保留。"""
import json
from time import perf_counter, sleep
from pathlib import Path
import psycopg
from psycopg.types.json import Jsonb
from langgraph.checkpoint.postgres import PostgresSaver
from scripts.evaluation.integration_probe import PRIVATE, FOLDER, client, submit, poll, write
from backend.db import postgres_client
from backend.db.neo4j_client import get_neo4j_driver
from backend.agent.graph import build_agent_graph
from backend.agent.session import derive_thread_id

FAULT = PRIVATE.with_name("service-fault.json")
MARKER = PRIVATE.with_name("service-fault-marker.json")


def prepare_fault(a, tool, mode):
    if MARKER.exists():
        MARKER.unlink()
    FAULT.write_text(json.dumps({"owner_id": a["user_id"], "tool": tool, "mode": mode}), encoding="utf-8")


def note(a):
    with get_neo4j_driver() as driver, driver.session() as s:
        return s.run("MATCH (p:Person {owner_id:$owner,id:$id}) RETURN p.note AS note",
                     owner=a["user_id"], id=a["person_id"]).single()["note"]


def main():
    accounts = json.loads(PRIVATE.read_text(encoding="utf-8"))
    a, b = accounts[2:4]
    results = []
    # 明确注入 TimeoutError，而非声称模拟出了真实网络故障。
    with client(a) as c:
        prepare_fault(a, "get_person_detail", "timeout")
        rid, _, _ = submit(c, "获取杨鑫的完整人物资料")
        final = poll(c, rid)
        with postgres_client.get_postgres_connection() as connection:
            records = [dict(row) for row in connection.execute(
                "SELECT tool_name,status FROM tool_execution WHERE owner_id=%s AND request_id=%s", (a["user_id"], rid)).fetchall()]
        results.append({"case_id": "E05", "layer": "real_http_injected_tool_exception", "pass":
                        MARKER.exists() and final["status"] == "FAILED", "final": final, "tool_receipts": records,
                        "note": "故障由评测适配器注入；未知结果保留running，不能盲目重新规划写入。未证明可强制终止永久卡住的工具。"})
        c.post(f"/api/chat/requests/{rid}/cancel")
        poll(c, rid)
        rid, _, _ = submit(c, "把杨鑫的备注改为：取消时已经提交的虚构备注")
        waiting = poll(c, rid)
        confirmation = waiting.get("confirmation")
        if confirmation:
            prepare_fault(a, "update_person", "after_commit")
            c.post("/api/chat/resume", json={"interrupt_id": confirmation["interrupt_id"], "confirmed": True}).raise_for_status()
            start = perf_counter()
            while not MARKER.exists() and perf_counter()-start < 120:
                sleep(.2)
            marker_seen = MARKER.exists()
            cancellation = c.post(f"/api/chat/requests/{rid}/cancel")
            cancellation.raise_for_status()
            final = poll(c, rid)
            committed_note = note(a)
            results.append({"case_id": "E07", "layer": "real_http_cancel_after_business_commit", "pass": marker_seen
                            and final["status"] == "CANCELLED" and committed_note == "取消时已经提交的虚构备注",
                            "final": final, "actual_note": committed_note,
                            "note": "取消在工具提交后的8秒受控延迟中发起；已提交写入保留，后续模型步骤停止。"})
        else:
            results.append({"case_id": "E07", "pass": False, "reason": "No confirmation generated", "final": waiting})
    with client(b) as c:
        rid, _, _ = submit(c, "把杨鑫的备注改为：目录变化后不应提交的内容")
        waiting = poll(c, rid)
        confirmation = waiting.get("confirmation")
        prior_note = note(b)
        if confirmation:
            # 像部署改变 Schema 一样，让持久化的待执行任务持有过期目录摘要。
            with psycopg.connect(postgres_client.build_postgres_conninfo(), autocommit=True) as connection:
                graph = build_agent_graph(PostgresSaver(connection))
                graph.update_state({"configurable": {"thread_id": derive_thread_id(b["user_id"])}},
                                   {"catalog_schema_hash": "0"*64})
            resumed = c.post("/api/chat/resume", json={"interrupt_id": confirmation["interrupt_id"], "confirmed": True})
            final = poll(c, rid) if resumed.status_code == 202 else resumed.json()
            unchanged = prior_note == note(b)
            results.append({"case_id": "E08", "layer": "real_pg_checkpoint_catalog_fault", "pass":
                            unchanged and final.get("status") == "FAILED", "final": final, "person_unchanged": unchanged,
                            "note": "真实PG检查点摘要改为失效值，恢复必须拒绝执行过期Schema。"})
            c.post(f"/api/chat/requests/{rid}/cancel")
            poll(c, rid)
        else:
            results.append({"case_id": "E08", "pass": False, "reason": "No confirmation generated", "final": waiting})
    write("service-fault-results.json", results)
    print(json.dumps({"checks":len(results),"passed":sum(r["pass"] for r in results)}),flush=True)


if __name__ == "__main__":
    main()
