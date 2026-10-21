"""真实图/真实模型，替换基础设施端口；与真实服务集成层严格区分。

模型、提示词、执行节点、授权、证据校验和上下文选择均沿用产品实现。
替换范围：业务仓储、预算仓储、原文仓储及 Checkpointer 存储介质。
ContextVar 隔离并行试验；不得用一个全局人物字典共享所有案例。
"""
import json
import os
from contextlib import ExitStack
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4, uuid5, NAMESPACE_URL
from threading import Lock

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from backend.agent.graph import build_agent_graph
from backend.agent.budget import BudgetExceeded
from backend.agent.execution_enums import ExecutionClaimAction
from backend.agent.tool_execution_repository import ExecutionClaim
from backend.agent.memory.runtime import MemoryRuntime, get_memory_runtime
from backend.agent.memory.ports import NoOpLongTermMemoryStore, NoOpMemoryJobStore
from backend.agent.memory.models import MemoryMessage, ConversationSummary
from backend.agent.strategies.catalog import ToolCatalog
from backend.agent.strategies.contracts import current_strategy_version
from backend.sales.contracts import StoredChatMessage, SimulatedChatPayload

CURRENT = ContextVar("evaluation_trial", default=None)
CALL_ID = ContextVar("evaluation_call_id", default=None)
WRITES = {"add_person","update_person","delete_person","add_relation","update_relation","delete_relation"}


def current():
    value = CURRENT.get()
    if value is None: raise RuntimeError("评测依赖在试验上下文外被调用，禁止退回真实业务库")
    return value


class MemoryPorts:
    persistent = True
    def __init__(self, owner): self.owner, self.messages, self.summary = owner, [], None
    def load_recent(self, *, owner_id, conversation_id, limit):
        return tuple(m for m in self.messages if m.owner_id==owner_id and m.conversation_id==conversation_id)[-limit:]
    def save_turn(self, *, owner_id, conversation_id, user_message, assistant_message):
        assert owner_id==self.owner
        saved = {m.message_id:m for m in self.messages}
        for m in (user_message,assistant_message): saved[m.message_id]=m
        self.messages=list(saved.values())
    def load(self, *, owner_id, conversation_id):
        s=self.summary
        return s if s and s.owner_id==owner_id and s.conversation_id==conversation_id else None
    def load_after_cursor(self, **kwargs): return ()


class Fixture:
    def __init__(self, case, trial_id):
        self.case, self.owner, self.trial_id = case, "eval-"+trial_id, trial_id
        self.people={p["id"]:deepcopy(p) for p in case["initial"]["people"]}
        self.relations=deepcopy(case["initial"].get("relations",[]))
        self.receipts, self.calls, self.writes, self.nodes, self.interrupts = {}, [], [], [], []
        self.approved, self.budgets, self.model_calls, self.budget_attempts = set(), {}, [], {}
        self.error, self.final = None, {}
        self.ports=MemoryPorts(self.owner)
        base=get_memory_runtime()
        self.runtime=MemoryRuntime(self.ports,self.ports,NoOpLongTermMemoryStore(),NoOpMemoryJobStore(),base.token_counter,base.settings)
        payload=case["initial"].get("chat")
        self.messages=[]
        if payload:
            value=SimulatedChatPayload.model_validate(payload)
            self.messages=[StoredChatMessage(message_id=str(uuid5(NAMESPACE_URL,trial_id+m.source_message_id)),
                person_id="p_yang",source_conversation_id=value.source_conversation_id, **m.model_dump(),
                created_at=datetime(2026,10,12,tzinfo=timezone.utc)) for m in value.messages]
        self.message_id_map={m.message_id:m.source_message_id for m in self.messages}

    def profile(self, owner_id, person_id):
        if owner_id != self.owner or person_id not in self.people:
            from backend.sales.importer import SalesPersonNotFound
            raise SalesPersonNotFound("客户不存在")
        return deepcopy(self.people[person_id])

    def candidates(self, *, owner_id, person_id, as_of, limit):
        assert owner_id==self.owner
        return sorted([m for m in self.messages if m.person_id==person_id and m.sent_at<=as_of and m.created_at<=as_of],
                      key=lambda m:(m.sent_at,m.message_id),reverse=True)[:limit]

    def execute(self, tool, **args):
        assert args.pop("owner_id")==self.owner, "工具参数跨用户"
        args.pop("idempotency_key",None)
        confirmed=args.pop("confirmed",False)
        # 工具内部可以取出定位ID构造更新属性，但审计必须保留完整实际参数。
        # **kwargs 已经隔离了调用者对象；这个快照修复的是外部评测审计丢ID，
        # 不改变产品节点收到的参数/回执，也不回写旧试验记录。
        submitted_args=deepcopy(args)
        before=deepcopy(list(self.people.values()))
        def ok(data): return {"success":True,"data":data,"msg":"受控虚构工具执行成功"}
        def fail(code): return {"success":False,"data":{},"error_code":code,"retryable":False,"msg":"受控工具未完成:"+code}
        fault=self.case["initial"].get("tool_failure",{})
        if fault.get("tool")==tool and fault.get("person_id")==args.get("person_id"):
            result=fail(fault["error_code"])
        elif tool=="find_person":
            query=args["query"]
            matches=[p for p in self.people.values() if p["name"]==query or (args.get("search_mode")!="exact" and query in p["name"])]
            result=fail("NOT_FOUND") if not matches else ok(matches[0]) if len(matches)==1 else {
                **fail("AMBIGUOUS"),"data":{"candidates":deepcopy(matches)}}
        elif tool=="get_person_detail":
            pid=args["person_id"]
            result=ok({"person":deepcopy(self.people[pid]),"relations":deepcopy([r for r in self.relations if pid in (r["from_id"],r["to_id"])])}) if pid in self.people else fail("NOT_FOUND")
        elif tool=="list_relations":
            pid=args["person_id"]
            result=ok(deepcopy([r for r in self.relations if pid in (r["from_id"],r["to_id"]) and
                              (not args.get("relation_type") or args["relation_type"]==r["relation_type"])]))
        elif tool=="get_sales_preparation_context":
            from backend.tools.sales import get_sales_preparation_context
            result=get_sales_preparation_context(owner_id=self.owner,**args)
        elif tool=="add_person":
            pid="p_new_"+str(len(self.people))
            if any(p["name"]==args["name"] for p in self.people.values()): result=fail("CONFLICT")
            else:
                self.people[pid]={"id":pid,**deepcopy(args)}
                result=ok(deepcopy(self.people[pid]))
        elif tool=="update_person":
            pid=args.pop("person_id")
            if pid not in self.people: result=fail("NOT_FOUND")
            else: self.people[pid].update(deepcopy(args)); result=ok(deepcopy(self.people[pid]))
        elif tool=="delete_person":
            pid=args["person_id"]
            if not confirmed: result=fail("PERMISSION_DENIED")
            elif pid=="p_self": result=fail("INVALID_ARGUMENT")
            elif pid not in self.people: result=fail("NOT_FOUND")
            else:
                person=self.people.pop(pid)
                self.relations=[r for r in self.relations if pid not in (r["from_id"],r["to_id"])]
                result=ok({"id":pid,"name":person["name"],"deleted":True})
        elif tool=="add_relation":
            left,right=args["from_person_id"],args["to_person_id"]
            if left not in self.people or right not in self.people: result=fail("NOT_FOUND")
            else:
                relation={"relation_id":"r_new_"+str(len(self.relations)),"from_id":left,"to_id":right,
                    "from_name":self.people[left]["name"],"to_name":self.people[right]["name"],
                    "relation_type":args["relation_type"],"type":args["relation_type"]}
                self.relations.append(relation); result=ok(deepcopy(relation))
        elif tool in {"update_relation","delete_relation"}:
            relation=next((r for r in self.relations if r["relation_id"]==args["relation_id"]),None)
            if not relation: result=fail("NOT_FOUND")
            elif tool=="delete_relation" and not confirmed: result=fail("PERMISSION_DENIED")
            elif tool=="delete_relation": self.relations.remove(relation); result=ok({**relation,"deleted":True})
            else:
                relation["type"]=relation["relation_type"]=args.get("new_relation_type",relation["relation_type"])
                result=ok(deepcopy(relation))
        else: result=fail("INVALID_ARGUMENT")
        call={"tool":tool,"call_id":CALL_ID.get(),"args":submitted_args,"result":deepcopy(result)}
        self.calls.append(call)
        if tool in WRITES and result["success"]:
            needs_confirmation=tool.startswith(("update_","delete_"))
            self.writes.append({**call,"authorized":not needs_confirmation or CALL_ID.get() in self.approved,
                                "before_people":before})
        return result


class FixtureBudget:
    """镜像预算端口，不替换产品 reserve/settle 调用；真实持久化另做集成实验。"""
    def __init__(self, owner, request):
        self.scope=current(); assert owner==self.scope.owner
        self.request=request
    def ensure(self, limits):
        self.scope.budgets.setdefault(self.request,{"limits":deepcopy(limits),"model_calls":0,"tool_attempts":0,
            "discovery_calls":0,"charged_tokens":0,"replans":0,"clarifications":0,"active_seconds":0.,"exhausted_reason":None})
    def begin(self): self.started=perf_counter()
    def pause(self): self.snapshot()["active_seconds"]+=perf_counter()-getattr(self,"started",perf_counter())
    def snapshot(self): return self.scope.budgets[self.request]
    def reserve(self, kind,key,tokens=0):
        s=self.snapshot(); col={"model":"model_calls","tool":"tool_attempts","replan":"replans","clarification":"clarifications","discovery":"discovery_calls"}[kind]
        if s["exhausted_reason"]: raise BudgetExceeded(s["exhausted_reason"])
        cache=next((a for a in self.scope.budget_attempts.values() if a["request"]==self.request and a["kind"]==kind and a["key"]==key and a.get("result") is not None and kind!="tool"),None)
        if cache: return {"cached":True,"attempt_id":cache["id"],"result":deepcopy(cache["result"])}
        if s[col]>=s["limits"][col] or s["charged_tokens"]+tokens>s["limits"]["tokens"] or s["active_seconds"]>=s["limits"]["active_seconds"]:
            s["exhausted_reason"]="评测试验达到了产品请求预算"; raise BudgetExceeded(s["exhausted_reason"])
        s[col]+=1; s["charged_tokens"]+=tokens
        aid=str(uuid4()); self.scope.budget_attempts[aid]={"id":aid,"request":self.request,"kind":kind,"key":key,"tokens":tokens}
        return {"cached":False,"attempt_id":aid}
    def settle(self, attempt,actual_tokens=None,result=None):
        if attempt["cached"]: return
        row=self.scope.budget_attempts[attempt["attempt_id"]]
        if row.get("settled"): return
        row["settled"]=True; row["result"]=deepcopy(result)
        if actual_tokens is not None: self.snapshot()["charged_tokens"]+=actual_tokens-row["tokens"]


class Meter:
    """物理 SDK 请求计数，包含失败和修复；与图调用数、预算账本分开。"""
    def __init__(self, ceiling): self.ceiling,self.count,self.lock=ceiling,0,Lock()
    def start(self, kwargs):
        with self.lock:
            if self.count>=self.ceiling: raise RuntimeError("EVALUATION_MODEL_CEILING")
            self.count+=1; seq=self.count
        row={"sequence":seq,"started":True,"model":kwargs.get("model"),"temperature":0,"max_tokens":kwargs.get("max_tokens"),
             "messages":kwargs.get("messages"),"tools":kwargs.get("tools"),"extra_body":kwargs.get("extra_body"),
             "stream":bool(kwargs.get("stream")),"started_at":perf_counter()}
        current().model_calls.append(row); return row


def captured_client(factory,meter):
    client=factory()
    create=client.chat.completions.create
    def wrapped(*args,**kwargs):
        kwargs["temperature"]=0 # 文档要求两种模式固定同一温度；此覆盖记入实验配置。
        if os.environ.get("EVAL_DISABLE_THINKING") == "true":
            # 仅用于单变量根因试验；正式 retest 必须通过产品配置实现此参数。
            kwargs["extra_body"] = {**kwargs.get("extra_body",{}), "enable_thinking": False}
        row=meter.start(kwargs)
        try:
            result=create(*args,**kwargs)
            completion=result.parse() if hasattr(result,"parse") else result
            if kwargs.get("stream"):
                return CapturedStream(completion,row)
            row["usage"]=completion.usage.model_dump() if completion.usage else None
            row["finish_reason"]=completion.choices[0].finish_reason
            row["raw_message"]=completion.choices[0].message.model_dump()
            return result
        except Exception as exc:
            row["error_type"]=type(exc).__name__
            raise
        finally: row["duration_ms"]=(perf_counter()-row["started_at"])*1000
    client.chat.completions.create=wrapped
    return client


class CapturedStream:
    def __init__(self, stream,row): self.stream,self.row,self.parts=stream,row,[]
    def __enter__(self): return self
    def __exit__(self,*exc): self.stream.close()
    def __iter__(self):
        try:
            for chunk in self.stream:
                if chunk.usage: self.row["usage"]=chunk.usage.model_dump()
                if chunk.choices:
                    choice=chunk.choices[0]
                    if choice.delta.content: self.parts.append(choice.delta.content)
                    if choice.finish_reason: self.row["finish_reason"]=choice.finish_reason
                yield chunk
        finally:
            self.row["raw_message"]={"role":"assistant","content":"".join(self.parts)}
            self.row["duration_ms"]=(perf_counter()-self.row["started_at"])*1000


def install(meter):
    """一次安装端口替身，各线程通过 CURRENT 取得自己的 fixture。"""
    import backend.services.llm as llm
    import backend.agent.tracing as tracing
    import backend.agent.nodes.execute_tools as executor
    stack=ExitStack(); factory=llm.get_llm_client
    for module in ["backend.services.llm","backend.services.structured_llm","backend.services.tool_calling_llm",
                   "backend.agent.nodes.generate_response","backend.agent.nodes.classify_intent","backend.agent.nodes.extract_info"]:
        stack.enter_context(patch(module+".get_llm_client",side_effect=lambda:captured_client(factory,meter)))
    for module in ["backend.agent.memory.runtime","backend.agent.nodes.prepare_context","backend.agent.nodes.finalize_memory","backend.sales.context"]:
        stack.enter_context(patch(module+".get_memory_runtime",side_effect=lambda:current().runtime))
    stack.enter_context(patch("backend.db.agent_budget_store.BudgetStore",FixtureBudget))
    stack.enter_context(patch("backend.sales.context.require_owned_profile",side_effect=lambda owner,pid:current().profile(owner,pid)))
    stack.enter_context(patch("backend.sales.context.list_candidates",side_effect=lambda **kw:current().candidates(**kw)))
    stack.enter_context(patch("backend.agent.nodes.execute_tools.execute_tool",side_effect=lambda tool,**kw:current().execute(tool,**kw)))
    original_invoke=executor._invoke_tool_with_retry
    def invoke(tool,args,**kw):
        token=CALL_ID.set(kw.get("call_id"))
        try: return original_invoke(tool,args,**kw)
        finally: CALL_ID.reset(token)
    stack.enter_context(patch("backend.agent.nodes.execute_tools._invoke_tool_with_retry",invoke))
    def claim(**kw):
        scope=current(); row=scope.receipts.get(kw["call_id"])
        if row and row["status"]=="success": return ExecutionClaim(ExecutionClaimAction.WAIT,result=row["result"])
        scope.receipts[kw["call_id"]]={"call_id":kw["call_id"],"status":"running","tool_name":kw["tool_name"],"arguments":kw["arguments"],"request_id":kw["request_id"]}
        return ExecutionClaim(ExecutionClaimAction.EXECUTE,execution_token="fixture")
    def mark(status,**kw): current().receipts[kw["call_id"]].update(status=status,result=deepcopy(kw.get("result")))
    stack.enter_context(patch("backend.agent.nodes.execute_tools.claim_execution",side_effect=claim))
    stack.enter_context(patch("backend.agent.nodes.execute_tools.mark_success",side_effect=lambda **kw:mark("success",**kw)))
    stack.enter_context(patch("backend.agent.nodes.execute_tools.mark_failed",side_effect=lambda **kw:mark("failed",**kw)))
    stack.enter_context(patch("backend.agent.nodes.goal_nodes._load_verified_receipts",side_effect=lambda state:({k:r for k,r in current().receipts.items() if r["status"]=="success"},[])))
    def record(state,result,**kw):
        name=tracing._trace.get().get("node")
        update=result.update if isinstance(result,Command) else result
        current().nodes.append({"node":name,"goto":getattr(result,"goto",None),"outcome":kw.get("outcome"),
            "update":deepcopy(update),"request_id":state.get("request_id")})
    stack.enter_context(patch("backend.agent.tracing.record_node_result",side_effect=record))
    import backend.agent.graph as graph_module
    original_response=graph_module.generate_response
    def response(state):
        if current().case.get("fault")=="stale_sales_snapshot":
            state={**state,"direct_tool_results":[*state.get("direct_tool_results",[]),{"call_id":"stale-write","tool":"update_person",
                "status":"SUCCESS","args":{"person_id":"p_yang"},"result":{"success":True}}]}
        return original_response(state)
    stack.enter_context(patch("backend.agent.graph.generate_response",response))
    return stack


def initial(scope,case,mode,turn):
    return {"execution_version":2,"goals":[],"active_goal_id":None,"plan_revision":0,"budget_snapshot":{},"budget_reason":"",
        "strategy_mode":mode,"strategy_version":current_strategy_version(mode),"catalog_schema_hash":ToolCatalog.snapshot().schema_hash,
        "decision_seq":0,"pending_decision":{},"strategy_messages":[],"goal_evaluation":{},"progress_snapshot":{},
        "no_progress_rounds":0,"finish_repair_count":0,"protocol_repair_count":0,"loaded_tool_schemas":[],"discovery_result":None,
        "clarification_question":"","stop_reason":"","direct_tool_calls":[],"direct_tool_results":[],"direct_clarifications":[],
        "direct_final_message":{},"direct_rejected_mutations":[],"completion_basis":"","requirements_verified":False,
        "user_input":turn["message"],"response_kind":case.get("response_kind","chat"),"sales_preparation":None,
        "response_error_code":None,"response_retryable":None,"user_id":scope.owner,"self_person_id":"p_self",
        "request_id":str(uuid4()),"thread_id":scope.trial_id,"conversation_id":"default","user_name":"评测用户",
        "intent":"","intent_confidence":0.,"extracted_info":{},"tool_calls":[],"tool_results":[],"execution_errors":[],
        "retry_count":0,"next_tool_index":0,"resume_execution":False,"completed_mutations":[],"execution_decision":"",
        "replan_count":0,"replan_feedback":{},"needs_confirmation":False,"user_confirmed":False,"pending_confirmation":None}


def run_trial(case,mode,repeat):
    trial_id=f"{case['case_id']}-{mode}-{repeat}-"+uuid4().hex[:10]
    scope=Fixture(case,trial_id); token=CURRENT.set(scope); started=perf_counter(); turns=[]; graph_invocations=0
    try:
        graph=build_agent_graph(InMemorySaver()); config={"configurable":{"thread_id":trial_id},"recursion_limit":64}
        variant=case.get("memory_variant")
        if variant=="large_summary": scope.ports.summary=ConversationSummary(scope.owner,"default",{"user_goals":[{"text":"旧目标"*20000,"evidence_message_ids":["old"]}]})
        if variant=="duplicate_summary":
            from backend.agent.memory.models import MemoryRole
            scope.ports.messages=[MemoryMessage("memory-u",scope.owner,"default","old",MemoryRole.USER,"杨鑫喜欢阅读。"),
                MemoryMessage("memory-a",scope.owner,"default","old",MemoryRole.ASSISTANT,"杨鑫喜欢阅读。")]
            scope.ports.summary=ConversationSummary(scope.owner,"default",{"confirmed_people":[{"text":"杨鑫喜欢阅读。","evidence_message_ids":["memory-u"]}]})
        for index,turn in enumerate(case["turns"]):
            state=initial(scope,case,mode,turn)
            if variant in {"foreign_owner_focus","foreign_conversation_focus","unverified_focus"}:
                state["person_focus"]={"person_id":"p_zhang","name":"张建国","owner_id":"foreign" if variant=="foreign_owner_focus" else scope.owner,
                    "conversation_id":"other" if variant=="foreign_conversation_focus" else "default"}
                if variant=="unverified_focus": state["person_focus"]=None
            graph_invocations+=1
            result=graph.invoke(state,config=config)
            for _ in range(8):
                pending=result.get("__interrupt__",())
                if not pending: break
                item=pending[0]; payload=item.value; scope.interrupts.append(deepcopy(payload))
                answers=case.get("answers",{})
                if payload.get("interaction_type")=="CONFIRMATION":
                    yes=answers.get("confirmed",True)
                    answer={"confirmed":yes}
                    if yes:
                        pending_call=graph.get_state(config).values["tool_calls"][graph.get_state(config).values.get("next_tool_index",0)]
                        scope.approved.add(pending_call["call_id"])
                elif payload.get("clarification_type")=="CANDIDATE_SELECTION" and answers.get("candidate_id"):
                    answer={"candidate_id":answers["candidate_id"]}
                elif answers.get("text"): answer={"answer":answers["text"]}
                else: raise RuntimeError("UNSCRIPTED_CLARIFICATION")
                graph_invocations+=1
                result=graph.invoke(Command(resume={item.id:answer}),config=config)
            if result.get("__interrupt__"): raise RuntimeError("INTERACTION_LIMIT")
            turns.append({"response":result.get("response",""),"request_id":state["request_id"],"execution_decision":result.get("execution_decision"),
                          "sales_preparation":result.get("sales_preparation"),"context_stats":result.get("context_stats")})
            scope.final=result
    except Exception as exc:
        scope.error=type(exc).__name__
        if str(exc) in {"UNSCRIPTED_CLARIFICATION","INTERACTION_LIMIT","EVALUATION_MODEL_CEILING"}: scope.error=str(exc)
    finally: CURRENT.reset(token)
    return {"trial_id":trial_id,"case_id":case["case_id"],"mode":mode,"repeat":repeat,"split":case["split"],"category":case["category"],
        "started":True,"error_type":scope.error,"duration_ms":(perf_counter()-started)*1000,"graph_invocations":graph_invocations,
        "response":scope.final.get("response",""),"execution_decision":scope.final.get("execution_decision"),
        "sales_preparation":scope.final.get("sales_preparation"),"people":deepcopy(list(scope.people.values())),
        "relations":deepcopy(scope.relations),"interrupts":scope.interrupts,"writes":scope.writes,"tool_calls":scope.calls,
        "model_calls":scope.model_calls,"nodes":scope.nodes,"turns":turns,"message_id_map":scope.message_id_map,
        "layer":"real_model_graph_with_fixture_stores","replaced_dependencies":["business_store","budget_store","message_store","checkpoint_storage"]}
