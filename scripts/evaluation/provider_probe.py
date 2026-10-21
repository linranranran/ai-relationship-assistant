"""备用模型协议先导：一条虚构、强制工具调用，验证权限和原生tools协议。"""
import os
os.environ["LANGFUSE_TRACING_ENABLED"] = "false"
import argparse
import json
import re
from time import perf_counter
from scripts.evaluation.integration_setup import ROOT
from backend.services.llm import get_llm_client


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("model", choices=["qwen3.6-flash", "qwen3.5-27b"])
    parser.add_argument("--max-output",type=int,default=64)
    args = parser.parse_args()
    start = perf_counter()
    result = {"model":args.model,"physical_requests":1,"enable_thinking":False}
    try:
        with get_llm_client().with_options(timeout=20,max_retries=0) as client:
            response = client.chat.completions.create(model=args.model,
                messages=[{"role":"user","content":"协议探针，请调用probe_result，answer填OK。"}], max_tokens=args.max_output,temperature=0,
                extra_body={"enable_thinking":False},
                tools=[{"type":"function","function":{"name":"probe_result","parameters":{"type":"object",
                       "properties":{"answer":{"type":"string"}},"required":["answer"],"additionalProperties":False}}}],
                tool_choice={"type":"function","function":{"name":"probe_result"}})
            calls=response.choices[0].message.tool_calls or []
            result.update(pass_protocol=bool(calls) and json.loads(calls[0].function.arguments).get("answer")=="OK",
                          finish_reason=response.choices[0].finish_reason,
                          usage=response.usage.model_dump() if response.usage else None)
    except Exception as exc:
        result.update(pass_protocol=False,error_type=type(exc).__name__,status_code=getattr(exc,"status_code",None))
        body=getattr(exc,"body",{})
        error=body.get("error",body) if isinstance(body,dict) else {}
        if isinstance(error,dict):
            result["provider_code"]=error.get("code")
            message=str(error.get("message",""))[:300]
            result["provider_message"]=re.sub(r"sk-[A-Za-z0-9_-]+","<redacted>",message)
    result["duration_ms"]=(perf_counter()-start)*1000
    folder=ROOT/"docs/评测文档/runs/provider-preflight"; folder.mkdir(exist_ok=True)
    (folder/(args.model+("-workload" if args.max_output!=64 else "")+".json")).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result),flush=True)


if __name__=="__main__":
    main()
