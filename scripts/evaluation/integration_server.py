"""启动独立评测 HTTP 服务，连接测试 schema，保持 .env 原文件不变。

所有产品节点、认证、PG Checkpointer、SSE 和后台任务均真实运行。
本脚本只注入 PostgreSQL 搜索路径及模型调用计量，不模拟工具或模型。
"""
import os
os.environ["LANGFUSE_TRACING_ENABLED"]="false"
from pathlib import Path
import json
import time
from copy import deepcopy
from hashlib import sha256
from threading import Lock
from time import perf_counter
from psycopg.conninfo import make_conninfo
from backend.db import postgres_client
from scripts.evaluation.integration_setup import SCHEMA,ROOT

original_conninfo=postgres_client.build_postgres_conninfo
def isolated_conninfo(database=None):
    base=original_conninfo(database) if database else original_conninfo()
    return make_conninfo(base,options="-c search_path="+SCHEMA)
postgres_client.build_postgres_conninfo=isolated_conninfo

# 在导入 main/graph/session-lock 之前改变连接构造，所有连接池捕获同一隔离入口。
from backend.main import app
from backend.services import llm
from scripts.evaluation.harness import CapturedStream
from unittest.mock import patch
import uvicorn

folder=ROOT/"docs/评测文档/runs/integration-20261014"
rows=[]; lock=Lock()
original_client=llm.get_llm_client
FAULT_FILE=ROOT/".superpowers/sdd/2026-10-14-project-evaluation/service-fault.json"
MARKER_FILE=FAULT_FILE.with_name("service-fault-marker.json")


def fault_tool(original):
    """仅评测启动器注入受控故障；不进入产品目录或真实用户配置。

    必须同时匹配虚构 owner 和工具名。提交后延迟用于在真实业务提交与
    审计完成之间触发取消；超时异常用于观察未知结果如何被保留。
    """
    def execute(name, **args):
        if not FAULT_FILE.exists():
            return original(name, **args)
        fault=json.loads(FAULT_FILE.read_text(encoding="utf-8"))
        if args.get("owner_id") != fault.get("owner_id") or name != fault.get("tool"):
            return original(name, **args)
        FAULT_FILE.unlink()  # 一次性故障，不能影响恢复或其他案例。
        if fault["mode"] == "timeout":
            MARKER_FILE.write_text(json.dumps({"mode":"timeout","tool":name}),encoding="utf-8")
            raise TimeoutError("synthetic tool timeout injection")
        result=original(name, **args)
        MARKER_FILE.write_text(json.dumps({"mode":"after_commit","tool":name,"success":result.get("success")}),encoding="utf-8")
        time.sleep(8)
        return result
    return execute
def measured_client():
    client=original_client(); original=client.chat.completions.create
    def create(*a,**kw):
        with lock:
            if len(rows)>=120: raise RuntimeError("INTEGRATION_MODEL_LIMIT")
            row={"sequence":len(rows)+1,"started_at":perf_counter(),"max_tokens":kw.get("max_tokens"),"stream":bool(kw.get("stream"))}; rows.append(row)
        try:
            result=original(*a,**kw); parsed=result.parse() if hasattr(result,"parse") else result
            if kw.get("stream"): return CapturedStream(parsed,row)
            row["usage"]=parsed.usage.model_dump() if parsed.usage else None
            row["finish_reason"]=parsed.choices[0].finish_reason
            return result
        except Exception as exc: row["error_type"]=type(exc).__name__; raise
        finally:
            row["duration_ms"]=(perf_counter()-row["started_at"])*1000
            with lock: (folder/"provider-usage-live.json").write_text(json.dumps(rows,ensure_ascii=False),encoding="utf-8")
    client.chat.completions.create=create
    return client

@app.get("/__evaluation/usage")
def usage():
    # 仅绑定 loopback；不暴露凭据、提示词或业务原文。
    return {"requests":len(rows),"records":rows}

if __name__=="__main__":
    stack=[]
    from backend.agent.strategies.catalog import ToolCatalog, CatalogSnapshot
    original_snapshot=ToolCatalog.snapshot
    def deployment_snapshot(allowed=None):
        snapshot=original_snapshot(allowed)
        if FAULT_FILE.exists() and json.loads(FAULT_FILE.read_text(encoding="utf-8")).get("mode")=="catalog_changed":
            # 模拟运行中的目录部署变化；不改检查点，不改变原 interrupt_id。
            tools=deepcopy(snapshot.tools)
            for item in tools.values():
                item["description"] += " 评测模拟的新目录版本。"
            digest=sha256(json.dumps(tools,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
            return CatalogSnapshot(tools,digest)
        return snapshot
    handle=patch("backend.agent.strategies.catalog.ToolCatalog.snapshot",side_effect=deployment_snapshot)
    handle.start(); stack.append(handle)
    from backend.agent.nodes import execute_tools as executor_module
    handle=patch("backend.agent.nodes.execute_tools.execute_tool",side_effect=fault_tool(executor_module.execute_tool))
    handle.start(); stack.append(handle)
    for module in ["backend.services.llm","backend.services.structured_llm","backend.services.tool_calling_llm",
                   "backend.agent.nodes.generate_response","backend.agent.nodes.classify_intent","backend.agent.nodes.extract_info"]:
        handle=patch(module+".get_llm_client",side_effect=measured_client); handle.start(); stack.append(handle)
    try: uvicorn.run(app,host="127.0.0.1",port=18082,log_level="warning")
    finally:
        for h in reversed(stack): h.stop()
