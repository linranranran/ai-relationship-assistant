# ============================================================
# Agent 对话路由
# 前端通过这个接口和 Agent 交互
# ============================================================

import logging
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from langgraph.types import Command
from starlette.concurrency import run_in_threadpool
from backend.agent.request_recovery import RecoveryConflict, run_or_recover, interrupted_state
from backend.agent.tracing import request_trace, trace_event
from backend.db.agent_session_lock import lock_agent_session, SessionBusy
from backend.db.agent_interaction_store import get_interaction, save_interaction
from backend.db.agent_request_store import has_tool_executions

from backend.auth import get_current_user
from backend.models.schemas import (
    CancelAgentRequest,
    ChatRequest,
    ChatResponse,
    ClarificationPrompt,
    ConfirmationPrompt,
    ResumeAgentRequest,
)
from backend.models.context import RequestContext
from backend.agent.state import AgentState
from backend.agent.graph import get_agent_graph
from backend.agent.request_idempotency import (
    AgentRequestAction,
    claim_agent_request,
    claim_agent_resume,
    load_agent_request,
    mark_agent_request_failed,
    save_agent_response,
)
from backend.agent.session import (
    DEFAULT_CONVERSATION_ID,
    derive_thread_id,
)

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger(__name__)

# 兼容单元测试直接替换 graph；正常运行时通过 get_agent_graph() 取得 lifespan
# 已初始化的进程级实例。
agent_graph = None


def _runtime_graph():
    return agent_graph or get_agent_graph()


def _graph_config(owner_id: str) -> dict:
    """生成 LangGraph 检查点定位信息。

    ``thread_id`` 定位用户会话，其生成规则已包含状态结构版本。根图使用
    LangGraph 默认的空 checkpoint_ns；非空值会被 get_state 解释为子图命名空间。
    当前每个用户只有一个默认会话；支持多会话时还要校验 owner 归属。
    """
    return {
        "configurable": {
            "thread_id": derive_thread_id(owner_id),
        }
    }


def _extract_interrupt(result: dict) -> dict | None:
    """取出 LangGraph 的 interrupt_id 与展示 payload。"""
    interrupts = result.get("__interrupt__", ())
    if not interrupts:
        return None

    current_interrupt = interrupts[0]
    value = getattr(current_interrupt, "value", None)
    interrupt_id = getattr(current_interrupt, "id", None)
    if not isinstance(value, dict) or not isinstance(interrupt_id, str):
        return None

    # ID 必须取自 Interrupt 对象，不能相信 payload 中的同名字段。
    return {**value, "interrupt_id": interrupt_id}


def _pending_interrupt_payloads(snapshot) -> dict[str, dict]:
    """按 interrupt_id 索引当前等待中的结构化交互 payload。"""
    return {
        interrupt.id: interrupt.value
        for task in (snapshot.tasks or ())
        for interrupt in (getattr(task, "interrupts", ()) or ())
        if isinstance(getattr(interrupt, "id", None), str)
        and isinstance(getattr(interrupt, "value", None), dict)
    }


def _to_chat_response(final_state: dict) -> ChatResponse:
    """把普通完成状态和暂停状态统一转换成 HTTP 响应。"""
    interaction = _extract_interrupt(final_state)
    tool_names = [
        item.get("tool", "")
        for item in final_state.get("tool_calls", [])
        if isinstance(item, dict)
    ]

    if interaction and interaction.get("interaction_type") == "CLARIFICATION":
        prompt = ClarificationPrompt.model_validate(interaction)
        return ChatResponse(
            request_id=final_state["request_id"],
            response=prompt.question,
            intent=final_state.get("intent"),
            tool_calls=tool_names,
            status="CLARIFICATION_REQUIRED",
            clarification=prompt,
        )

    if interaction:
        # 兼容旧检查点：没有 interaction_type 的历史 interrupt 仍按确认处理。
        prompt = ConfirmationPrompt.model_validate(interaction)
        return ChatResponse(
            request_id=final_state["request_id"],
            response=f"{prompt.summary}。是否继续？",
            intent=final_state.get("intent"),
            tool_calls=tool_names,
            status="CONFIRMATION_REQUIRED",
            confirmation=prompt,
        )

    if final_state.get("execution_decision") == "CANCELLED":
        return ChatResponse(
            request_id=final_state["request_id"],
            response=final_state.get("response") or "已取消本次任务。",
            intent=final_state.get("intent"),
            tool_calls=tool_names,
            status="CANCELLED",
        )

    if final_state.get("execution_decision") == "WAIT":
        return ChatResponse(
            request_id=final_state["request_id"],
            response=final_state.get("response") or "任务仍在执行，请稍后重试。",
            intent=final_state.get("intent"),
            tool_calls=tool_names,
            status="RUNNING",
        )

    return ChatResponse(
        request_id=final_state["request_id"],
        response=final_state.get("response") or "操作已完成。",
        intent=final_state.get("intent"),
        tool_calls=tool_names,
        status="COMPLETED",
    )


def _normalize_request_id(raw_request_id: str) -> str:
    """只接受标准 UUID，避免无限长度或格式混乱的幂等键进入数据库。"""
    try:
        return str(UUID(raw_request_id))
    except (ValueError, AttributeError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="Idempotency-Key 必须是 UUID") from exc


def _saved_chat_response(response_data: dict | None) -> ChatResponse:
    if not isinstance(response_data, dict):
        raise HTTPException(status_code=500, detail="已保存的 Agent 响应格式错误")
    return ChatResponse.model_validate(response_data)


@router.post("", response_model=ChatResponse)
async def chat_endpoint(
    request: ChatRequest,
    http_response: Response,
    http_request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    current_user: dict = Depends(get_current_user),
):
    """只受理并持久化，不在 HTTP 连接里运行图；进度另行 GET SSE 订阅。"""
    from backend.db.agent_stream_store import accept_chat, TaskConflict
    from backend.routers.stream import task_response
    request_id = _normalize_request_id(idempotency_key)
    # 身份只来自鉴权结果，不能把 token、密码或整个用户对象写入任务表。
    user_context = {key: current_user[key] for key in ("user_id", "self_person_id", "phone") if key in current_user}
    try:
        task = await run_in_threadpool(accept_chat, current_user["user_id"], request_id,
                                      derive_thread_id(current_user["user_id"]), request.message, user_context)
    except TaskConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    if task["status"] in {"queued", "running"}:
        http_response.status_code = 202
    from backend.agent.stream_runtime import schedule_accepted
    schedule_accepted(http_request, task)
    return task_response(task)


async def chat(
    request: ChatRequest,
    current_user: dict,
    http_response: Response | None = None,
    idempotency_key: str | None = None,
):
    # 图/数据库/模型均为同步调用，放入有界线程池，避免阻塞整个 ASGI 事件循环。
    # 内部调用保留原有不落库的兼容入口；HTTP 路由始终要求 request_id。
    def execute():
        if idempotency_key is None:
            return _chat_sync(request, current_user, http_response, None)
        request_id = _normalize_request_id(idempotency_key)
        thread_id = derive_thread_id(current_user["user_id"])
        try:
            with request_trace(request_id, thread_id), lock_agent_session(thread_id):
                trace_event("request.start")
                return _chat_sync(request, current_user, http_response, request_id)
        except SessionBusy as exc:
            # 新 request_id 可能尚未入库，不能伪造 RUNNING 让浏览器查询不存在的记录。
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await run_in_threadpool(execute)


def _chat_sync(request, current_user, http_response=None, idempotency_key=None,
               *, graph_override=None, on_claim=None, stale_after_seconds=None):
    """
    主对话接口。

    接收用户的消息，运行 Agent 状态机，返回 Agent 的回复。

    Request:
        Header: Idempotency-Key: <UUID，重试时必须复用>
        {
            "message": "今天见的张三，李四的朋友，做金融的",
        }

    Response:
        {
            "response": "已添加张三 ✨ ...",
            "intent": "ADD_PERSON",
            "tool_calls": ["find_person", "add_person", "add_relation"]
        }
    """
    # ``None`` 只用于 Python 内部调用和已有单元测试；真实 HTTP 入口由
    # chat_endpoint 强制要求 Idempotency-Key。
    request_idempotency_enabled = idempotency_key is not None
    request_id = (
        _normalize_request_id(idempotency_key)
        if idempotency_key is not None
        else str(uuid4())
    )
    context = RequestContext.from_current_user(current_user, request_id=request_id)
    thread_id = derive_thread_id(context.owner_id)

    claim = None
    if request_idempotency_enabled:
        try:
            claim = claim_agent_request(
                owner_id=context.owner_id,
                request_id=context.request_id,
                thread_id=thread_id,
                message=request.message,
                **({"stale_after_seconds": stale_after_seconds} if stale_after_seconds is not None else {}),
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("无法获取 Agent 请求执行权")
            raise HTTPException(status_code=503, detail="请求状态服务暂不可用") from exc

        if claim.action == AgentRequestAction.RETURN_RESPONSE:
            saved = _saved_chat_response(claim.response)
            prompt = saved.confirmation or saved.clarification
            receipt = get_interaction(context.owner_id, prompt.interrupt_id) if prompt else None
            if not receipt or receipt["request_id"] != context.request_id:
                return saved
            # 覆盖“答案已提交、进程却在 claim_resume 之前崩溃”的窗口。
            # 原 /chat 重试也能重放服务端保存的答案，而不是再次弹出旧问题。
            claim = claim_agent_resume(owner_id=context.owner_id, request_id=context.request_id,
                                      **({"stale_after_seconds": stale_after_seconds} if stale_after_seconds is not None else {}))
        if claim.action == AgentRequestAction.IN_PROGRESS:
            if http_response is not None:
                http_response.status_code = status.HTTP_202_ACCEPTED
            return ChatResponse(
                request_id=context.request_id,
                response="请求正在执行中，请稍后查询或重试。",
                status="RUNNING",
            )
        if not claim.execution_token:
            raise HTTPException(status_code=500, detail="请求已获得执行权但缺少执行令牌")
        if on_claim:
            on_claim(claim.execution_token)

    initial_state = AgentState(
        user_input=request.message,
        user_id=context.owner_id,
        self_person_id=context.self_person_id,
        request_id=context.request_id,
        thread_id=thread_id,
        # 当前版本由服务端固定，不能接受浏览器随意传 conversation_id。下一阶段
        # 新建 conversation 表后，改为查询并校验 owner 归属。
        conversation_id=DEFAULT_CONVERSATION_ID,
        user_name=context.user_name,
        intent="",
        intent_confidence=0.0,
        extracted_info={},
        tool_calls=[],
        tool_results=[],
        execution_errors=[],
        retry_count=0,
        next_tool_index=0,
        resume_execution=False,
        completed_mutations=[],
        execution_decision="",
        replan_count=0,
        replan_feedback={},
        needs_confirmation=False,
        confirmation_type="",
        user_confirmed=False,
        pending_confirmation=None,
        # recent_messages/summary 不在这里写空值，避免新一轮请求覆盖 Checkpoint
        # 中已经积累的短期记忆；prepare_context 使用 state.get 兼容首轮和旧状态。
        model_context=[],
        context_stats={},
        retrieved_memories=[],
        # 会话焦点留在 Checkpoint；本轮解析结果和待澄清请求必须重新计算。
        resolved_reference={},
        reference_resolution={},
        pending_reference=None,
        response="",
    )
    try:
        graph = graph_override or _runtime_graph()
        if request_idempotency_enabled:
            def find_answer(interrupt_id):
                receipt = get_interaction(context.owner_id, interrupt_id)
                if receipt and receipt["request_id"] == context.request_id:
                    return receipt["answer"]
                return None
            trace_event("request.recover" if claim.is_retry else "request.execute")
            final_state = run_or_recover(
                graph, _graph_config(context.owner_id), initial_state,
                is_retry=claim.is_retry, find_answer=find_answer,
                can_rebuild=lambda: not has_tool_executions(context.owner_id, context.request_id))
        else:
            final_state = graph.invoke(initial_state, config=_graph_config(context.owner_id))
        final_state.setdefault("request_id", context.request_id)
        chat_response = _to_chat_response(final_state)
        if chat_response.status == "RUNNING":
            if http_response is not None:
                http_response.status_code = status.HTTP_202_ACCEPTED
            if claim is not None and claim.execution_token:
                # WAIT 已主动 END，本 HTTP 工作进程不再执行；释放请求租约，
                # 避免下一次合法重试被 running 状态挡住整整一个过期窗口。
                mark_agent_request_failed(owner_id=context.owner_id,
                                          request_id=context.request_id,
                                          error_message="WAIT", execution_token=claim.execution_token)
        elif claim is not None and claim.execution_token:
            save_agent_response(
                owner_id=context.owner_id,
                request_id=context.request_id,
                response=chat_response.model_dump(mode="json"),
                execution_token=claim.execution_token,
            )
        trace_event("request.finished", status=chat_response.status)
        return chat_response
    except Exception as exc:
        logger.exception("Agent 请求执行失败: request_id=%s", context.request_id)
        if claim is not None and claim.execution_token:
            try:
                mark_agent_request_failed(
                    owner_id=context.owner_id,
                    request_id=context.request_id,
                    error_message=str(exc),
                    execution_token=claim.execution_token,
                )
            except Exception:
                logger.exception("Agent 请求 failed 状态保存失败")
        if isinstance(exc, RecoveryConflict):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise HTTPException(status_code=500, detail="Agent 执行失败，请使用原请求 ID 重试") from exc


@router.get("/requests/{request_id}", response_model=ChatResponse)
def get_chat_request(
    request_id: str,
    http_response: Response,
    current_user: dict = Depends(get_current_user),
):
    """查询一次 Agent 请求；owner_id 始终来自登录态。"""
    normalized_request_id = _normalize_request_id(request_id)
    from backend.db.agent_stream_store import load_task
    from backend.routers.stream import task_response
    task = load_task(current_user["user_id"], normalized_request_id)
    if task:
        if task["status"] in {"queued", "running"}:
            http_response.status_code = 202
        return task_response(task)
    record = load_agent_request(current_user["user_id"], normalized_request_id)
    if record is None:
        raise HTTPException(status_code=404, detail="请求不存在")

    request_status = record.get("status")
    if request_status in {"completed", "confirmation_required"}:
        saved = _saved_chat_response(record.get("response"))
        prompt = saved.confirmation or saved.clarification
        if request_status == "confirmation_required" and prompt:
            receipt = get_interaction(current_user["user_id"], prompt.interrupt_id)
            if receipt and receipt["request_id"] == normalized_request_id:
                # 答案已接收但工作进程尚未取得恢复租约，不能再次显示可改选的旧按钮。
                return ChatResponse(request_id=normalized_request_id, status="FAILED",
                                    response="人工答案已保存，恢复尚未完成，请重试原请求继续执行。")
        return saved
    if request_status == "running":
        http_response.status_code = status.HTTP_202_ACCEPTED
        return ChatResponse(
            request_id=normalized_request_id,
            response="请求正在执行中，请稍后查询。",
            status="RUNNING",
        )
    return ChatResponse(
        request_id=normalized_request_id,
        response="上一次执行失败，可以使用原 request_id 重试。",
        status="FAILED",
    )


def _validate_interaction_answer(pending: dict, answer: dict):
    """答案类型与当前中断一致，不能把澄清当作高风险操作授权。"""
    if answer.get("cancelled") is True:
        return
    interaction_type = pending.get("interaction_type", "CONFIRMATION")
    required = "confirmed" if interaction_type == "CONFIRMATION" else (
        "candidate_id" if pending.get("clarification_type") == "CANDIDATE_SELECTION" else "answer")
    if required not in answer:
        raise HTTPException(status_code=422, detail=f"当前交互需要 {required}")
    if required == "candidate_id":
        candidates = pending.get("candidates", [])
        allowed = {item.get("id") for item in candidates if isinstance(item, dict)}
        if answer[required] not in allowed:
            raise HTTPException(status_code=422, detail="请选择当前展示的候选人物")


def _handle_interaction(owner_id: str, interrupt_id: str, answer: dict, http_response: Response,
                        *, graph_override=None, on_claim=None, stale_after_seconds=None):
    """确认、澄清、取消共享传输恢复机制，业务语义仍由各节点决定。

    人工答案回执先提交，再恢复图。重复点击同一 interrupt_id 必须复用原答案；
    节点失败后从 checkpoint.next 继续，不会再次消费已经完成的中断。
    """
    config = _graph_config(owner_id)
    graph = graph_override or _runtime_graph()
    snapshot = graph.get_state(config)
    pending = _pending_interrupt_payloads(snapshot).get(interrupt_id)
    receipt = get_interaction(owner_id, interrupt_id)
    if pending is None and receipt is None:
        raise HTTPException(status_code=409, detail="交互信息已失效，请重新发起操作")
    request_id = receipt["request_id"] if receipt else (snapshot.values or {}).get("request_id")
    if not isinstance(request_id, str):
        raise HTTPException(status_code=500, detail="检查点缺少 request_id")
    if pending:
        _validate_interaction_answer(pending, answer)
    try:
        save_interaction(owner_id, request_id, interrupt_id, answer)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    with request_trace(request_id, derive_thread_id(owner_id)):
        claim = claim_agent_resume(owner_id=owner_id, request_id=request_id,
                                  **({"stale_after_seconds": stale_after_seconds} if stale_after_seconds is not None else {}))
        if claim.action == AgentRequestAction.RETURN_RESPONSE:
            return _saved_chat_response(claim.response)
        if claim.action != AgentRequestAction.EXECUTE or not claim.execution_token:
            http_response.status_code = status.HTTP_202_ACCEPTED
            return ChatResponse(request_id=request_id, status="RUNNING", response="原请求正在恢复，请稍后查询状态。")
        try:
            if on_claim:
                on_claim(claim.execution_token)
            # 原中断已被消费时即使 next 为空，也直接复用最终状态；绝不重发旧答案。
            values = snapshot.values or {}
            if values.get("request_id") != request_id:
                raise RecoveryConflict("原请求检查点已被覆盖，无法继续该交互")
            def find_answer(pending_id):
                stored = get_interaction(owner_id, pending_id)
                return stored["answer"] if stored and stored["request_id"] == request_id else None
            final_state = run_or_recover(graph, config, values, is_retry=True, find_answer=find_answer)
            response = _to_chat_response(final_state)
            if response.status == "RUNNING":
                http_response.status_code = status.HTTP_202_ACCEPTED
                mark_agent_request_failed(owner_id=owner_id, request_id=request_id,
                                          error_message="WAIT", execution_token=claim.execution_token)
            else:
                save_agent_response(owner_id=owner_id, request_id=request_id,
                                    response=response.model_dump(mode="json"),
                                    execution_token=claim.execution_token)
            trace_event("interaction.finished", interrupt_id=interrupt_id, status=response.status)
            return response
        except Exception as exc:
            trace_event("interaction.failed", interrupt_id=interrupt_id, error_type=type(exc).__name__)
            try:
                mark_agent_request_failed(owner_id=owner_id, request_id=request_id,
                                          error_message=type(exc).__name__, execution_token=claim.execution_token)
            except Exception:
                logger.exception("Agent 交互失败状态保存失败")
            raise HTTPException(status_code=409 if isinstance(exc, RecoveryConflict) else 500,
                                detail=str(exc) if isinstance(exc, RecoveryConflict) else "恢复失败，请重试原交互或原请求") from exc


async def _interaction_in_thread(owner_id, interrupt_id, answer, http_response):
    def execute():
        try:
            with lock_agent_session(derive_thread_id(owner_id)):
                return _handle_interaction(owner_id, interrupt_id, answer, http_response)
        except SessionBusy as exc:
            # 锁未取得时没有改动 checkpoint；浏览器应保留答案后重试。
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await run_in_threadpool(execute)


@router.post("/resume", response_model=ChatResponse)
async def resume_chat(request: ResumeAgentRequest, http_response: Response, http_request: Request,
                      current_user: dict = Depends(get_current_user)):
    answer = request.model_dump(exclude={"interrupt_id"}, exclude_none=True)
    return await _queue_interaction(current_user, request.interrupt_id, answer, http_response, http_request)


@router.post("/cancel", response_model=ChatResponse)
async def cancel_chat(request: CancelAgentRequest, http_response: Response, http_request: Request,
                      current_user: dict = Depends(get_current_user)):
    return await _queue_interaction(current_user, request.interrupt_id, {"cancelled": True}, http_response, http_request)


async def _queue_interaction(current_user, interrupt_id, answer, http_response, http_request):
    """校验中断、保存答案、安排图恢复；等待用户期间执行线程已释放会话锁。"""
    from backend.db.agent_stream_store import accept_chat, accept_resume, load_task, TaskConflict
    from backend.routers.stream import task_response
    owner_id = current_user["user_id"]

    def accept():
        snapshot = _runtime_graph().get_state(_graph_config(owner_id))
        pending = _pending_interrupt_payloads(snapshot).get(interrupt_id)
        receipt = get_interaction(owner_id, interrupt_id)
        if not pending and not receipt:
            raise HTTPException(409, "交互信息已失效，请重新发起操作")
        if pending:
            _validate_interaction_answer(pending, answer)
        request_id = receipt["request_id"] if receipt else snapshot.values["request_id"]
        if not load_task(owner_id, request_id):
            # 兼容升级前等待中的请求：原 user_input 来自检查点，不取浏览器的新输入。
            # 先提交答案回执，再创建可执行批次。否则进程内恢复循环可能在两次
            # 提交之间启动图，误把“尚未读到答案”再次保存成 waiting。
            try:
                save_interaction(owner_id, request_id, interrupt_id, answer)
            except ValueError as exc:
                raise TaskConflict(str(exc)) from exc
            context = {key: current_user[key] for key in ("user_id", "self_person_id", "phone") if key in current_user}
            accept_chat(owner_id, request_id, derive_thread_id(owner_id), snapshot.values["user_input"], context)
        return accept_resume(owner_id, request_id, interrupt_id, answer)

    try:
        task = await run_in_threadpool(accept)
    except TaskConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    if task["status"] in {"queued", "running"}:
        http_response.status_code = 202
    from backend.agent.stream_runtime import schedule_accepted
    schedule_accepted(http_request, task)
    return task_response(task)
