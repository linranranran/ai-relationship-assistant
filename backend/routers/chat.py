# ============================================================
# Agent 对话路由
# 前端通过这个接口和 Agent 交互
# ============================================================

import logging
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from langgraph.types import Command

from backend.auth import get_current_user
from backend.models.schemas import (
    ChatRequest,
    ChatResponse,
    ConfirmationPrompt,
    ResumeAgentRequest,
)
from backend.models.context import RequestContext
from backend.agent.state import AgentState
from backend.agent.graph import agent_graph
from backend.agent.request_idempotency import (
    AgentRequestAction,
    claim_agent_request,
    claim_agent_resume,
    load_agent_request,
    mark_agent_request_failed,
    save_agent_response,
)
from backend.agent.session import derive_thread_id

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger(__name__)


def _graph_config(owner_id: str) -> dict:
    """生成 LangGraph 配置；thread_id 永远不接收前端参数。"""
    return {"configurable": {"thread_id": derive_thread_id(owner_id)}}


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


def _pending_interrupt_ids(snapshot) -> set[str]:
    """读取当前检查点里所有待恢复的 LangGraph interrupt_id。"""
    return {
        interrupt.id
        for task in (snapshot.tasks or ())
        for interrupt in (getattr(task, "interrupts", ()) or ())
        if isinstance(getattr(interrupt, "id", None), str)
    }


def _to_chat_response(final_state: dict) -> ChatResponse:
    """把普通完成状态和暂停状态统一转换成 HTTP 响应。"""
    confirmation = _extract_interrupt(final_state)
    tool_names = [
        item.get("tool", "")
        for item in final_state.get("tool_calls", [])
        if isinstance(item, dict)
    ]

    if confirmation:
        prompt = ConfirmationPrompt.model_validate(confirmation)
        return ChatResponse(
            request_id=final_state["request_id"],
            response=f"{prompt.summary}。是否继续？",
            intent=final_state.get("intent"),
            tool_calls=tool_names,
            status="CONFIRMATION_REQUIRED",
            confirmation=prompt,
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
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    current_user: dict = Depends(get_current_user),
):
    """HTTP 入口强制要求前端提供 Idempotency-Key。"""
    return await chat(
        request,
        current_user=current_user,
        http_response=http_response,
        idempotency_key=idempotency_key,
    )


async def chat(
    request: ChatRequest,
    current_user: dict,
    http_response: Response | None = None,
    idempotency_key: str | None = None,
):
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
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("无法获取 Agent 请求执行权")
            raise HTTPException(status_code=503, detail="请求状态服务暂不可用") from exc

        if claim.action == AgentRequestAction.RETURN_RESPONSE:
            return _saved_chat_response(claim.response)
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

    initial_state = AgentState(
        user_input=request.message,
        user_id=context.owner_id,
        self_person_id=context.self_person_id,
        request_id=context.request_id,
        thread_id=thread_id,
        user_name=context.user_name,
        # 对话历史应由你的 Agent checkpoint 按 owner/thread 从服务端恢复。
        history=[],
        intent="",
        intent_confidence=0.0,
        extracted_info={},
        tool_calls=[],
        tool_results=[],
        execution_errors=[],
        retry_count=0,
        next_tool_index=0,
        needs_confirmation=False,
        confirmation_type="",
        user_confirmed=False,
        pending_confirmation=None,
        response="",
    )
    try:
        final_state = agent_graph.invoke(
            initial_state,
            config=_graph_config(context.owner_id),
        )
        final_state.setdefault("request_id", context.request_id)
        chat_response = _to_chat_response(final_state)
        if claim is not None and claim.execution_token:
            save_agent_response(
                owner_id=context.owner_id,
                request_id=context.request_id,
                response=chat_response.model_dump(mode="json"),
                execution_token=claim.execution_token,
            )
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
        raise HTTPException(status_code=500, detail="Agent 执行失败，请使用原请求 ID 重试") from exc


@router.get("/requests/{request_id}", response_model=ChatResponse)
async def get_chat_request(
    request_id: str,
    http_response: Response,
    current_user: dict = Depends(get_current_user),
):
    """查询一次 Agent 请求；owner_id 始终来自登录态。"""
    normalized_request_id = _normalize_request_id(request_id)
    record = load_agent_request(current_user["user_id"], normalized_request_id)
    if record is None:
        raise HTTPException(status_code=404, detail="请求不存在")

    request_status = record.get("status")
    if request_status in {"completed", "confirmation_required"}:
        return _saved_chat_response(record.get("response"))
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


@router.post("/resume", response_model=ChatResponse)
async def resume_chat(
    request: ResumeAgentRequest,
    current_user: dict = Depends(get_current_user),
):
    """恢复等待人工确认的 Agent。

    前端只提交 interrupt_id 与布尔值 confirmed。owner_id 和 thread_id 都由
    登录态推导，避免恢复到其他用户的检查点。

    前端调用示例：
    ``POST /api/chat/resume``
    ``{"interrupt_id": "响应中的 ID", "confirmed": true}``
    """
    owner_id = current_user["user_id"]
    config = _graph_config(owner_id)

    # thread_id 先定位会话，再用 LangGraph 生成的 interrupt_id 定位该会话中
    # 具体的暂停点。旧弹窗、重复点击和错误 ID 都不能恢复其他中断。
    snapshot = agent_graph.get_state(config)
    pending = (snapshot.values or {}).get("pending_confirmation")
    if not pending:
        raise HTTPException(status_code=409, detail="当前没有等待确认的操作")
    if request.interrupt_id not in _pending_interrupt_ids(snapshot):
        raise HTTPException(status_code=409, detail="确认信息已失效，请重新发起操作")

    request_id = (snapshot.values or {}).get("request_id")
    if not isinstance(request_id, str):
        raise HTTPException(status_code=500, detail="检查点缺少 request_id")
    resume_claim = claim_agent_resume(owner_id=owner_id, request_id=request_id)
    if resume_claim.action == AgentRequestAction.RETURN_RESPONSE:
        return _saved_chat_response(resume_claim.response)
    if resume_claim.action != AgentRequestAction.EXECUTE or not resume_claim.execution_token:
        raise HTTPException(status_code=409, detail="该请求正在恢复，请勿重复确认")

    # langgraph官方文档，处理多个中断，传入interrupt_id来resume。https://docs.langchain.com/oss/python/langgraph/interrupts#handling-multiple-interrupts
    # # Step 1: stream events to drive the run; both parallel nodes hit interrupt() and pause
    # stream = graph.stream_events({"vals": []}, config, version="v3")
    # print(stream.interrupts)
    # # > (Interrupt(value='question_a', id='...'), Interrupt(value='question_b', id='...'))
    # # Step 2: resume all pending interrupts at once
    # resume_map = {
    #     i.id: f"answer for {i.value}" for i in stream.interrupts
    # }
    try:
        final_state = agent_graph.invoke(
            Command(
                resume={
                    request.interrupt_id: {
                        "confirmed": request.confirmed,
                    }
                }
            ),
            config=config,
        )
        chat_response = _to_chat_response(final_state)
        save_agent_response(
            owner_id=owner_id,
            request_id=request_id,
            response=chat_response.model_dump(mode="json"),
            execution_token=resume_claim.execution_token,
        )
        return chat_response
    except Exception as exc:
        logger.exception("Agent 恢复失败: request_id=%s", request_id)
        try:
            mark_agent_request_failed(
                owner_id=owner_id,
                request_id=request_id,
                error_message=str(exc),
                execution_token=resume_claim.execution_token,
            )
        except Exception:
            logger.exception("Agent 恢复 failed 状态保存失败")
        raise HTTPException(status_code=500, detail="Agent 恢复失败") from exc
