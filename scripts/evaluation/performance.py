"""真实 HTTP/SSE 小样本性能探针；不能据此宣称生产并发容量。

四个独立 owner 同时请求，每个执行两次，只记录八个样本。
首进度排除 request.accepted/snapshot/心跳；首内容记录真实 response.delta。
"""
import json
import argparse
from concurrent.futures import ThreadPoolExecutor
from time import perf_counter
from scripts.evaluation.integration_probe import PRIVATE, FOLDER, client, submit, poll, write


def one(a):
    results = []
    with client(a) as c:
        for iteration in range(2):
            rid, start, _ = submit(c, "查询杨鑫的出生年份和爱好")
            first_progress = first_content = None
            event_types = []
            with c.stream("GET", f"/api/chat/requests/{rid}/events") as stream:
                stream.raise_for_status()
                for line in stream.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    event = json.loads(line[6:])
                    typ = event["type"]
                    elapsed = (perf_counter()-start)*1000
                    event_types.append(typ)
                    if first_progress is None and typ in {"stage.started", "tool.started", "strategy.deciding"}:
                        first_progress = elapsed
                    if first_content is None and typ == "response.delta":
                        first_content = elapsed
            final = poll(c, rid)
            elapsed = (perf_counter()-start)*1000
            response = final.get("response", "")
            results.append({"owner_id": a["user_id"], "request_id": rid, "iteration": iteration,
                "status": final["status"], "first_effective_progress_ms": first_progress,
                "first_answer_content_ms": first_content, "full_response_ms": elapsed,
                "active_seconds": (final.get("budget") or {}).get("active_seconds"),
                "timeout": elapsed > 300000 or final["status"] == "EVALUATION_TIMEOUT",
                "business_correct": "1998" in response and "阅读" in response,
                "event_types": event_types, "final": final})
            if final["status"] != "COMPLETED":
                c.post(f"/api/chat/requests/{rid}/cancel")
                poll(c, rid)
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='performance.json')
    args = parser.parse_args()
    accounts = json.loads(PRIVATE.read_text(encoding="utf-8"))
    with ThreadPoolExecutor(max_workers=4) as executor:
        rows = [r for group in executor.map(one, accounts) for r in group]
    # closest-rank；明确 N=8，不能将样本 P95 宣称为稳定的生产 SLA。
    def p95(key):
        values = sorted(r[key] for r in rows if r[key] is not None)
        return values[-1] if values else None
    result = {"layer": "real_http_sse_pg_neo4j", "concurrency": 4, "samples": len(rows),
              "first_progress_p95_ms": p95("first_effective_progress_ms"),
              "first_content_p95_ms": p95("first_answer_content_ms"),
              "full_response_p95_ms": p95("full_response_ms"), "timeouts": sum(r["timeout"] for r in rows),
              "business_correct": sum(r["business_correct"] for r in rows), "rows": rows,
              "limitation": "eight synthetic requests on shared infrastructure; not load capacity certification"}
    from backend.config import LLM_MODEL, LLM_ENABLE_THINKING
    result.update(model=LLM_MODEL, enable_thinking=LLM_ENABLE_THINKING)
    write(args.output, result)
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}), flush=True)


if __name__ == "__main__":
    main()
