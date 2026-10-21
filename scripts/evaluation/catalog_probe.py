"""补跑因Plus额度耗尽未执行到的目录变化实验；保留首次失败报告。"""
import json
import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from scripts.evaluation.service_fault_probe import note, FAULT
from scripts.evaluation.integration_probe import PRIVATE, FOLDER, client, submit, poll, write
from backend.db import postgres_client
from backend.agent.graph import build_agent_graph
from backend.agent.session import derive_thread_id
from backend.config import LLM_MODEL


def main():
    account = json.loads(PRIVATE.read_text(encoding="utf-8"))[3]
    previous = json.loads((FOLDER / "service-fault-results.json").read_text(encoding="utf-8"))[-1]
    with client(account) as c:
        if previous["final"].get("request_id"):
            c.post("/api/chat/requests/" + previous["final"]["request_id"] + "/cancel")
            poll(c, previous["final"]["request_id"])
        prior_attempt = FOLDER / "catalog-flash-result.json"
        if prior_attempt.exists():
            prior_id = json.loads(prior_attempt.read_text(encoding="utf-8")).get("final", {}).get("request_id")
            if prior_id:
                c.post(f"/api/chat/requests/{prior_id}/cancel")
                poll(c, prior_id)
        rid, _, _ = submit(c, "把杨鑫的备注改为：目录变化后不应提交的内容")
        waiting = poll(c, rid)
        prompt = waiting.get("confirmation")
        if not prompt:
            result = {"case_id": "E08", "pass": False, "reason": "no_confirmation", "final": waiting}
        else:
            prior = note(account)
            # 修改目录适配器，模拟部署；update_state 会创建新中断身份，
            # 上一版探针因此只验证了“拒绝过期中断”，没有到达目录比较。
            FAULT.write_text(json.dumps({"mode":"catalog_changed"}),encoding="utf-8")
            resumed = c.post("/api/chat/resume", json={"interrupt_id": prompt["interrupt_id"], "confirmed": True})
            final = poll(c, rid) if resumed.status_code == 202 else resumed.json()
            actual = note(account)
            result = {"case_id": "E08", "layer": "real_http_pg_checkpoint_schema_change", "model": LLM_MODEL,
                      "pass": prior == actual and final.get("status") == "FAILED", "final": final,
                      "person_unchanged": prior == actual, "note": "原始Plus运行未进入目标步骤，单独补测并保留失败。"}
            if FAULT.exists():
                FAULT.unlink()
            c.post(f"/api/chat/requests/{rid}/cancel")
            poll(c, rid)
    write("catalog-final-result.json", result)
    print(json.dumps({"case_id":"E08","pass":result["pass"]}),flush=True)


if __name__ == "__main__":
    main()
