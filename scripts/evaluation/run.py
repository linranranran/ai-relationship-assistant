"""入口：冻结版本 → Langfuse SDK 实验 → 逐例不可覆盖结果 → 汇总。

示例：python -m scripts.evaluation.run --phase pilot --ceiling 80
正式基线：--phase baseline --ceiling 2400 --concurrency 3
试验会访问配置模型。绝不把一次场景当成一次 API 请求；Meter 在 SDK 边界计数。
"""
import os
# 业务审计独立关闭，实验根由下方 Langfuse SDK管理，避免两套审计重复采集。
os.environ["LANGFUSE_TRACING_ENABLED"]="false"
import argparse, asyncio, csv, hashlib, importlib.metadata, json, logging, subprocess, zipfile
from collections import Counter, defaultdict
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from threading import Lock
from langfuse import Langfuse, Evaluation
from scripts.evaluation.harness import Meter, install, run_trial
from scripts.evaluation.grading import grade
from backend import config

ROOT=Path(__file__).resolve().parents[2]
DATA=ROOT/"docs/评测文档/data/scenarios-v2.jsonl"


def digest(paths):
    h=hashlib.sha256()
    for p in sorted(paths):
        if "__pycache__" in p.parts: continue
        h.update(p.relative_to(ROOT).as_posix().encode()); h.update(b"\0"); h.update(p.read_bytes()); h.update(b"\0")
    return h.hexdigest()


def fraction(rows,key):
    eligible=[r["score"][key] for r in rows if r.get("started") and r["score"][key] is not None]
    return {"success":sum(bool(x) for x in eligible),"total":len(eligible),
            "rate":sum(bool(x) for x in eligible)/len(eligible) if eligible else None}


def percentile(values,q):
    if not values: return None
    values=sorted(values); return values[min(len(values)-1,max(0,int((len(values)-1)*q+.5)))]


def summarize(rows,manifest):
    groups={}
    for group in sorted({r["category"]+"/"+r["mode"] for r in rows}):
        selected=[r for r in rows if r["category"]+"/"+r["mode"]==group]
        groups[group]={"trials":len(selected),"rule_pass":fraction(selected,"task_pass"),"business_pass":fraction(selected,"business_pass"),
            "structure_pass":fraction(selected,"structure_pass"),"safety_pass":fraction(selected,"safety_pass"),
            "multi_requirement_pass":fraction(selected,"multi_requirement_pass"),"p50_graph_trial_ms":percentile([r["duration_ms"] for r in selected],.5),
            "p95_graph_trial_ms":percentile([r["duration_ms"] for r in selected],.95),
            "model_requests":sum(len(r["model_calls"]) for r in selected),"tool_attempts":sum(len(r["tool_calls"]) for r in selected)}
    calls=[c for r in rows for c in r["model_calls"]]
    stability=[]
    for cid,mode in sorted({(r["case_id"],r["mode"]) for r in rows}):
        selected=[r for r in rows if (r["case_id"],r["mode"])==(cid,mode)]
        if len(selected)==3:
            stability.append({"case_id":cid,"mode":mode,"all_three_rule_pass":all(r["score"]["task_pass"] for r in selected)})
    direct=[r for r in rows if r["mode"]=="direct"]
    total_citations=sum(r["score"]["citations_total"] for r in rows)
    valid_citations=sum(r["score"]["citations_valid"] for r in rows)
    return {"manifest":manifest,"completed_trials":len(rows),"groups":groups,"physical_model_requests":len(calls),
        "started_trials":sum(bool(r.get("started")) for r in rows),
        "not_started_trials":sum(not r.get("started") for r in rows),
        "usage_known_requests":sum(c.get("usage") is not None for c in calls),
        "prompt_tokens":sum((c.get("usage") or {}).get("prompt_tokens",0) for c in calls),
        "completion_tokens":sum((c.get("usage") or {}).get("completion_tokens",0) for c in calls),
        "cost_money":None,"cost_status":"provider_private_endpoint_price_unconfirmed",
        "citations":{"valid":valid_citations,"total":total_citations,"rate":valid_citations/total_citations if total_citations else None},
        "three_repeat_stability":{"all_three_rule_pass":sum(x["all_three_rule_pass"] for x in stability),"eligible_case_mode_groups":len(stability),"items":stability},
        "failures":dict(Counter(code for r in rows for code in r["score"]["failure_codes"])),
        "finish_reasons":dict(Counter(c.get("finish_reason","unknown") for c in calls)),
        "hard_failures_direct":dict(Counter(code for r in direct for code in r["score"]["hard_failures"])),
        "semantic_review":"pending_human","release_gate":"not_accepted_pending_integration_and_human_review"}


def save_reports(folder, rows, manifest):
    summary=summarize(rows,manifest)
    (folder/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    lines=["# 模型图评测报告",f"run_id：{manifest['run_id']}","", "真实模型、真实图；业务/预算/记忆仓储及检查点存储为隔离替身。以下延迟为场景图级时间。", "",
           "| 分组 | 试验 | 规则通过 | 正向业务通过 | 准备单生成 | P95 场景毫秒 |", "|---|---:|---|---|---|---:|"]
    for name,g in summary["groups"].items():
        fmt=lambda s: f"{s['success']}/{s['total']}" if s["total"] else "不适用"
        lines.append(f"| {name} | {g['trials']} | {fmt(g['rule_pass'])} | {fmt(g['business_pass'])} | {fmt(g['structure_pass'])} | {g['p95_graph_trial_ms']:.0f} |")
    lines.extend(["",f"物理模型请求 {summary['physical_model_requests']} 次；已知输入 {summary['prompt_tokens']}、输出 {summary['completion_tokens']} Token；金额尚无该私有端点价格依据。",
        f"合法引用 {valid_citation_text(summary)}。这不是事实支持率；人工复核仍待完成。", "",
        "规则失败完整保存在逐例 JSON 和 summary.json，未排除已启动的超时/解析错误。三次重复不是三个独立业务案例。",
        "发布门禁：未验收；需要真实服务恢复/性能验收及人工销售语义复核。"])
    (folder/"report.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    with (folder/"semantic-review.csv").open("w",encoding="utf-8-sig",newline="") as f:
        writer=csv.writer(f); writer.writerow(["trial_id","case_id","field","fact","quotes","gold_required_sources","human_supported","human_coverage","human_reviewer","notes"])
        for r in rows:
            prep=r.get("sales_preparation")
            if not prep: continue
            for field in ["customer_summary","recent_communications","customer_needs","previous_commitments"]:
                for item in prep["brief"].get(field,[]):
                    text=item["text"]; text="'"+text if text.startswith(("=","+","-","@")) else text
                    writer.writerow([r["trial_id"],r["case_id"],field,text,json.dumps(item.get("evidence_refs",[]),ensure_ascii=False),
                        json.dumps(next(c["expected"].get("required_sources",[]) for c in manifest["cases"] if c["case_id"]==r["case_id"])),"pending","pending","",""])
    return summary


def valid_citation_text(summary):
    return f"{summary['citations']['valid']}/{summary['citations']['total']}"


def main():
    p=argparse.ArgumentParser(); p.add_argument("--phase",choices=["pilot","baseline","retest"],default="pilot")
    p.add_argument("--ceiling",type=int,default=80); p.add_argument("--concurrency",type=int,default=1)
    p.add_argument("--ids",default=""); p.add_argument("--publish",action="store_true")
    p.add_argument("--resume-folder",default=""); args=p.parse_args()
    cases=[json.loads(x) for x in DATA.read_text(encoding="utf-8").splitlines()]
    assert len(cases)==60 and sum(c["split"]=="dev" for c in cases)==40
    requested=set(args.ids.split(",")) if args.ids else None
    selected=[c for c in cases if c["category"] in {"people","sales","memory"} and (not requested or c["case_id"] in requested)]
    if args.phase=="pilot" and not requested: selected=[c for c in selected if c["case_id"] in {"P01","P03","S01","S06","M01"}]
    data=[]
    for repeat in range(1,2 if args.phase=="pilot" else 4):
        for c in selected:
            for mode in (["direct","plan"] if c["category"]=="people" else ["direct"]):
                data.append({"input":{"case":c,"mode":mode,"repeat":repeat},"expected_output":c["expected"],
                    "metadata":{"case_id":c["case_id"],"split":c["split"],"category":c["category"],"mode":mode,"repeat":str(repeat)}})
    resume_folder=Path(args.resume_folder).resolve() if args.resume_folder else None
    run_id=resume_folder.name if resume_folder else datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S")+"-"+args.phase
    folder=resume_folder or ROOT/"docs/评测文档/runs"/run_id
    (folder/"trials").mkdir(parents=True,exist_ok=bool(resume_folder))
    manifest={"run_id":run_id,"phase":args.phase,"dataset_sha256":hashlib.sha256(DATA.read_bytes()).hexdigest(),
        "backend_source_sha256":digest((ROOT/"backend").rglob("*.py")),"harness_sha256":digest((ROOT/"scripts/evaluation").glob("*.py")),
        "git_commit":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
        "frameworks":{n:importlib.metadata.version(n) for n in ["langgraph","langchain-openai","langfuse"]},
        "model":config.LLM_MODEL,"temperature":0,"planned_trials":len(data),"model_request_ceiling":args.ceiling,
        "input_tokens":config.AGENT_MAX_INPUT_TOKENS,"output_tokens":config.AGENT_RESERVED_OUTPUT_TOKENS,"concurrency":args.concurrency,
        "evaluation_only_disable_thinking":os.environ.get("EVAL_DISABLE_THINKING")=="true",
        "scoring_version":"v2.rule-proxy",
        "scoring_limit":"word matches and external state; not human-verified full business completion; run rescore before report",
        "enable_thinking":getattr(config,"LLM_ENABLE_THINKING",None),
        "cases":selected,"human_waiting":"scripted responses; waiting excluded; not a real human participant"}
    rows=[]
    if resume_folder:
        prior=json.loads((folder/"manifest.json").read_text(encoding="utf-8"))
        assert prior["model"]==manifest["model"] and prior["backend_source_sha256"]==manifest["backend_source_sha256"]
        assert prior["dataset_sha256"]==manifest["dataset_sha256"], "Different model/source/dataset must use new run_id"
        rows=[json.loads(f.read_text(encoding="utf-8")) for f in (folder/"trials").glob("*.json")]
        done={(r["case_id"],r["mode"],r["repeat"]) for r in rows}
        data=[item for item in data if (item["input"]["case"]["case_id"],item["input"]["mode"],item["input"]["repeat"]) not in done]
        manifest={**prior,"execution_segments":[*prior.get("execution_segments",[]),{
            "resumed_at":datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),"completed_before_resume":len(rows),
            "remaining_trials":len(data),"harness_sha256":manifest["harness_sha256"]}],
            "interruption_note":"prior process stopped; completed trials retained; in-flight outcomes/usage unknown, never counted as passing",
            "unobserved_interrupted_attempts":"unknown; at least8 SDK starts absent from completed trial records"}
        (folder/"manifest-before-resume.json").write_text(json.dumps(prior,ensure_ascii=False,indent=2),encoding="utf-8")
    (folder/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    # 未提交代码不能仅靠 commit 重建，保留源码快照；严格排除 .env、缓存与凭据。
    with zipfile.ZipFile(folder/("source-resume-snapshot.zip" if resume_folder else "source-snapshot.zip"),"w",compression=zipfile.ZIP_DEFLATED) as archive:
        for path in [*(ROOT/"backend").rglob("*.py"),*(ROOT/"scripts/evaluation").glob("*.py")]:
            if "__pycache__" not in path.parts: archive.write(path,path.relative_to(ROOT).as_posix())
    lock=Lock(); meter=Meter(args.ceiling); provider_unavailable=False
    meter.count=sum(len(r["model_calls"]) for r in rows)
    logging.getLogger("agent.trace").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    def evaluate_item(*,item,**kw):
        nonlocal provider_unavailable
        value=item["input"]; case=value["case"]
        if provider_unavailable:
            output={"case_id":case["case_id"],"mode":value["mode"],"repeat":value["repeat"],"category":case["category"],
                "split":case["split"],"trial_id":str(__import__("uuid").uuid4()),"started":False,
                "error_type":"PROVIDER_UNAVAILABLE_NOT_STARTED","people":case["initial"]["people"],
                "relations":case["initial"]["relations"],"model_calls":[],"tool_calls":[],"duration_ms":0}
            output["score"]=grade(case,output)
            with lock:
                rows.append(output)
                (folder/"trials"/(output["trial_id"]+".json")).write_text(json.dumps(output,ensure_ascii=False),encoding="utf-8")
            return output
        # 先写启动回执。进程被强制终止时，下次可明确报告哪些试验未产生结果。
        start_path=folder/"started"/f"{case['case_id']}-{value['mode']}-{value['repeat']}.json"
        start_path.parent.mkdir(exist_ok=True)
        start_path.write_text(json.dumps(value,ensure_ascii=False),encoding="utf-8")
        try:
            output=run_trial(case,value["mode"],value["repeat"])
            output["score"]=grade(case,output)
            if output.get("error_type") in {"PermissionDeniedError","OpenAIPermissionDeniedError"}:
                # 配额/权限已经失败就停止受理新试验；已启动失败保留，未启动单列。
                # 不在某个试验中途偷换模型，也不让180个案例反复消耗无效请求。
                provider_unavailable=True
        except Exception as exc:
            output={"case_id":case["case_id"],"mode":value["mode"],"repeat":value["repeat"],"category":case["category"],
                "split":case["split"],"trial_id":str(__import__("uuid").uuid4()),"started":False,"error_type":type(exc).__name__,
                "people":case["initial"]["people"],"relations":case["initial"]["relations"],"model_calls":[],"tool_calls":[],"duration_ms":0}
            output["score"]=grade(case,output)
        with lock:
            rows.append(output)
            (folder/"trials"/(output["trial_id"]+".json")).write_text(json.dumps(output,ensure_ascii=False,default=str),encoding="utf-8")
            save_reports(folder,rows,manifest)
            print(json.dumps({"completed":len(rows),"planned":manifest["planned_trials"],"case_id":case["case_id"],"mode":value["mode"],
                "rule_pass":output["score"]["task_pass"],"requests":len(output["model_calls"]),"total_requests":meter.count,
                "error":output.get("error_type")}),flush=True)
        return output
    async def task(*,item,**kw):
        # SDK 会 await 协程，但不会自动把同步 task 放在线程池。
        # 显式 to_thread 才能让 max_concurrency 限制真正同时运行的图。
        # asyncio 会复制 ContextVar，因此每例 fixture/计量仍隔离。
        return await asyncio.to_thread(evaluate_item,item=item)
    def evaluator(*,output,**kw):
        return [Evaluation(name="rule_pass",value=output["score"]["task_pass"],data_type="BOOLEAN"),
                Evaluation(name="safety_pass",value=output["score"]["safety_pass"],data_type="BOOLEAN")]
    # SDK 管理 experiment/task/evaluator，JSON 结果额外独立保存。可选上传，不依赖云可用性。
    if args.publish: os.environ["LANGFUSE_TRACING_ENABLED"]="true"
    client=Langfuse(public_key=config.LANGFUSE_PUBLIC_KEY or "local-disabled",secret_key=config.LANGFUSE_SECRET_KEY or "local-disabled",
                   base_url=config.LANGFUSE_BASE_URL,tracing_enabled=args.publish,timeout=10,environment="evaluation")
    manifest["langfuse_published"]=False
    try:
        with install(meter):
            experiment=client.run_experiment(name="relationship-eval-v1",run_name=run_id,data=data,task=task,evaluators=[evaluator],
                max_concurrency=args.concurrency,metadata={"dataset_sha256":manifest["dataset_sha256"],"phase":args.phase})
        client.flush()
        manifest["langfuse_published"]=args.publish
    finally:
        client.shutdown()
        (folder/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
        summary=save_reports(folder,rows,manifest)
    print(json.dumps({"run_id":run_id,"completed":len(rows),"physical_requests":meter.count,"folder":str(folder)},ensure_ascii=True),flush=True)


if __name__=="__main__": main()
