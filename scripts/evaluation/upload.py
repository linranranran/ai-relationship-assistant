"""经授权把已完成的本地实验归档到 Langfuse，不重新调用模型。

这是历史结果导入，云上创建观察的时间不代表模型原始运行时间。
保留实际 usage/latency 和 source_run_id；避免把导入延迟当成业务性能。
"""
import os
os.environ["LANGFUSE_TRACING_ENABLED"] = "true"
# SDK 读 API 与 OTLP exporter 使用不同客户端，均绕过本机失效的代理。
os.environ["NO_PROXY"] = os.environ.get("NO_PROXY", "") + ",cloud.langfuse.com"
import argparse
import json
import httpx
from pathlib import Path
from time import sleep
from datetime import datetime, timedelta, timezone
from langfuse import Langfuse, Evaluation
from backend import config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("folder")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    folder = Path(args.folder).resolve()
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in (folder / "trials").glob("*.json")]
    # 防止把未结束的实验作为完整评测发布，也不读取 .env 的内容上传。
    assert len(rows) == manifest["planned_trials"], "Wait until all planned trials are persisted"
    assert config.LANGFUSE_BASE_URL.rstrip("/") == "https://cloud.langfuse.com"
    lookup = {r["trial_id"]: r for r in rows}
    cases = {c["case_id"]: c for c in manifest["cases"]}
    client = Langfuse(public_key=config.LANGFUSE_PUBLIC_KEY, secret_key=config.LANGFUSE_SECRET_KEY,
                     base_url=config.LANGFUSE_BASE_URL, tracing_enabled=True, environment="evaluation", timeout=20,
                     httpx_client=httpx.Client(trust_env=False, timeout=20))
    if not client.auth_check():
        raise RuntimeError("Langfuse authentication failed; local results retained")
    traces = []

    def verify(trace_ids):
        # Cloud v4 已移除 /traces/{id}，返回410不能解读为记录未上传。
        # 使用官方 v2 observations，以 traceId + 有界时间范围读回。
        now = datetime.now(timezone.utc)
        verified_ids = set()
        with httpx.Client(trust_env=False, timeout=20,
                          auth=(config.LANGFUSE_PUBLIC_KEY, config.LANGFUSE_SECRET_KEY)) as reader:
            cursor = None
            for page in range(10):
                # 批量检索仅本次已授权归档的 task 观察，最多十页，避免逐trace请求180次。
                params={"fields":"core,basic", "limit":1000,
                    "fromStartTime":(now-timedelta(days=1)).isoformat(), "toStartTime":(now+timedelta(minutes=1)).isoformat(),
                    "filter":json.dumps([
                        {"type":"string","column":"name","operator":"=","value":"experiment-item-task"},
                        {"type":"stringObject","column":"metadata","key":"source_run_id","operator":"=","value":manifest["run_id"]}])}
                if cursor:
                    params["cursor"]=cursor
                response = reader.get(config.LANGFUSE_BASE_URL + "/api/public/v2/observations", params=params)
                if response.status_code == 429:
                    sleep(min(30,max(2,int(response.headers.get("Retry-After","30")))))
                    response=reader.get(config.LANGFUSE_BASE_URL + "/api/public/v2/observations",params=params)
                response.raise_for_status()
                body=response.json()
                verified_ids.update(o.get("traceId") for o in body.get("data",[]) if o.get("traceId") in trace_ids)
                cursor=(body.get("meta") or {}).get("cursor")
                if not cursor:
                    break
        return list(verified_ids)

    if args.verify_only:
        archive_path = folder / "langfuse-upload.json"
        archive = json.loads(archive_path.read_text(encoding="utf-8"))
        trace_ids = [url.rsplit("/",1)[1] for url in archive["trace_urls"]]
        checked = verify(trace_ids)
        archive.update(readback_verified=len(checked)==len(trace_ids), verified_traces=len(checked),
                       readback_api="v2/observations", readback_scope="task_roots_only")
        archive_path.write_text(json.dumps(archive,ensure_ascii=False,indent=2),encoding="utf-8")
        client.shutdown()
        print(json.dumps({"verified":len(checked),"expected":len(trace_ids)}),flush=True)
        return

    def task(*, item, **kwargs):
        r = lookup[item["input"]["trial_id"]]
        trace_id = client.get_current_trace_id()
        traces.append(trace_id)
        for index, call in enumerate(r["model_calls"]):
            usage = call.get("usage") or {}
            # 记录 approved payload：虚构聊天、内部 prompt/schema 和实际模型结果。
            with client.start_as_current_observation(name="archived-model-" + str(index + 1), as_type="generation",
                    model=manifest["model"], input={"messages": call.get("messages"), "tools": call.get("tools")},
                    output=call.get("raw_message"),
                    usage_details={"input": usage.get("prompt_tokens", 0), "output": usage.get("completion_tokens", 0)},
                    metadata={"archive_import": True, "original_duration_ms": call.get("duration_ms"),
                              "finish_reason": call.get("finish_reason"), "usage_known": bool(usage)}):
                pass
        with client.start_as_current_observation(name="archived-node-trajectory", input=None,
                output=r.get("nodes", r.get("node_results", r.get("trajectory"))),
                metadata={"archive_import": True}):
            pass
        return r

    def evaluator(*, output, **kwargs):
        return [Evaluation(name="rule_pass", value=output["score"]["task_pass"], data_type="BOOLEAN"),
                Evaluation(name="safety_pass", value=output["score"]["safety_pass"], data_type="BOOLEAN")]

    try:
        client.run_experiment(name="relationship-eval-v1", run_name=manifest["run_id"] + "-archive",
            data=[{"input": {"trial_id": r["trial_id"], "case": cases[r["case_id"]], "mode": r["mode"], "repeat": r["repeat"]},
                   "expected_output": cases[r["case_id"]]["expected"]} for r in rows],
            task=task, evaluators=[evaluator], max_concurrency=2,
            metadata={"source_run_id": manifest["run_id"], "dataset_sha256": manifest["dataset_sha256"],
                      "archive_import": "true", "new_model_requests": "0"})
        client.flush()
        # flush 仅表示客户端送出；用读 API 验证至少一条完整 trace 已落在目标项目。
        verified = False
        for attempt in range(6):
            try:
                checked = verify(traces)
                verified = len(checked) == len(traces)
                if verified:
                    break
            except Exception as exc:
                print(json.dumps({"readback_error": type(exc).__name__, "status_code": getattr(getattr(exc,"response",None),"status_code",getattr(exc,"status_code",None))}),flush=True)
            sleep(2)
        result = {"source_run_id": manifest["run_id"], "trials": len(rows), "archive_import": True,
                  "new_model_requests": 0, "readback_verified": verified,
                  "readback_scope":"task_roots_only",
                  "trace_urls": [client.get_trace_url(trace_id=t) for t in traces]}
        (folder / "langfuse-upload.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({k: v for k, v in result.items() if k != "trace_urls"}), flush=True)
    finally:
        client.shutdown()


if __name__ == "__main__":
    main()
