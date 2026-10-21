"""对真实 PG/Neo4j 做受控故障实验；与模型质量数据分开报告。

最关键的实验是真实子进程在 Neo4j 提交后直接退出，留下 running 回执。
另一个进程接管租约并用相同 call_id 重放业务调用，必须只产生一个人物。
"""
import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from time import sleep
from uuid import uuid4
from scripts.evaluation.integration_probe import FOLDER, PRIVATE, write
from backend.db import postgres_client
from backend.db.neo4j_client import create_person, get_neo4j_driver
from backend.agent.tool_execution_repository import claim_execution, mark_success
from backend.agent.budget import BudgetExceeded, BudgetLimits
from backend.db.agent_budget_store import BudgetStore

JOB = PRIVATE.with_name("fault-job.json")


def claim(job):
    return claim_execution(call_id=job["call_id"], owner_id=job["owner_id"], request_id=job["request_id"],
                           thread_id="eval-fault-thread", tool_name="add_person", arguments=job["args"],
                           stale_after_seconds=job.get("stale_after_seconds", 300))


def child(action):
    job = json.loads(JOB.read_text(encoding="utf-8"))
    got = claim(job)
    if action == "claim":
        print(got.action.value, flush=True)
        return
    assert got.action.value == "EXECUTE"
    with get_neo4j_driver() as driver:
        person = create_person(driver, job["args"], job["owner_id"], idempotency_key=job["call_id"])
    if action == "crash":
        # 强制终止，不经过 finally 或正常清理，也不更新 PG 状态。
        os._exit(71)
    mark_success(call_id=job["call_id"], owner_id=job["owner_id"], result={"success": True, "data": person},
                 latency_ms=0, execution_token=got.execution_token)
    print(person["id"], flush=True)


def run_child(action):
    return subprocess.run([sys.executable, "-X", "utf8", "-m", "scripts.evaluation.fault_probe", action],
                          text=True, capture_output=True, timeout=50)


def main():
    account = json.loads(PRIVATE.read_text(encoding="utf-8"))[0]
    owner = account["user_id"]
    job = {"call_id": str(uuid4()), "request_id": str(uuid4()), "owner_id": owner,
           "args": {"name": "崩溃恢复虚构客户"}}
    JOB.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(run_child, ["claim", "claim"]))
    actions = [r.stdout.strip().splitlines()[-1] if r.returncode == 0 else "PROCESS_ERROR" for r in outcomes]
    concurrent = {"case_id": "E02", "layer": "real_database_two_processes", "pass":
                  sorted(actions) == ["EXECUTE", "IN_PROGRESS"], "actions": actions}
    # 新 call_id，上一条并发测试的 running 记录不会污染崩溃恢复实验。
    job["call_id"] = str(uuid4())
    JOB.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    crashed = run_child("crash")
    with postgres_client.get_postgres_connection() as connection:
        prior = dict(connection.execute("SELECT status FROM tool_execution WHERE owner_id=%s AND call_id=%s",
                                        (owner, job["call_id"])).fetchone())
    with get_neo4j_driver() as driver, driver.session() as session:
        first = session.run("MATCH (p:Person {owner_id:$owner,name:$name}) RETURN p.id AS id",
                            owner=owner, name=job["args"]["name"]).single()["id"]
    job["stale_after_seconds"] = 0
    JOB.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    recovered = run_child("recover")
    cached = claim(job)
    with get_neo4j_driver() as driver, driver.session() as session:
        count = session.run("MATCH (p:Person {owner_id:$owner,name:$name}) RETURN count(p) AS n",
                            owner=owner, name=job["args"]["name"]).single()["n"]
    receipt = {"case_id": "E03", "layer": "real_database_process_crash", "pass":
               crashed.returncode == 71 and prior["status"] == "running" and recovered.returncode == 0
               and cached.action.value == "WAIT" and cached.result["data"]["id"] == first and count == 1,
               "crash_exit_code": crashed.returncode, "pg_status_after_crash": prior["status"],
               "recovery_exit_code": recovered.returncode, "replay_action": cached.action.value,
               "person_count": count, "first_person_id": first,
               "note": "测试调用真实业务写入函数；不声称覆盖辅助向量索引的跨库原子性"}
    budget = BudgetStore(owner, str(uuid4()))
    limits = asdict(BudgetLimits(model_calls=1))
    budget.ensure(limits)
    first_attempt = budget.reserve("model", "operation-1", 100)
    budget.settle(first_attempt, 70, {"text": "cached-synthetic-result"})
    cached_attempt = budget.reserve("model", "operation-1", 100)
    exhausted = False
    try:
        budget.reserve("model", "operation-2", 100)
    except BudgetExceeded:
        exhausted = True
    budget.ensure(asdict(BudgetLimits(model_calls=12)))
    snapshot = budget.snapshot()
    limit_result = {"case_id": "E06", "layer": "real_persistent_budget_component", "pass": exhausted
                    and cached_attempt["cached"] and snapshot["model_calls"] == 1 and snapshot["limits"]["model_calls"] == 1
                    and bool(snapshot["exhausted_reason"]), "snapshot": snapshot,
                    "note": "未发付费请求；验证持久化计数、缓存与重试不能充值，非端到端模型耗尽演练"}
    results = [concurrent, receipt, limit_result]
    write("fault-results.json", results)
    print(json.dumps({"checks": len(results), "passed": sum(r["pass"] for r in results)}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["run", "claim", "crash", "recover"])
    args = parser.parse_args()
    main() if args.action == "run" else child(args.action)
