# ============================================================
# Agent 对话路由
# 前端通过这个接口和 Agent 交互
# ============================================================

from fastapi import APIRouter, Depends, HTTPException
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
from backend.agent.session import derive_thread_id

router = APIRouter(prefix="/api/chat", tags=["chat"])


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
            response=f"{prompt.summary}。是否继续？",
            intent=final_state.get("intent"),
            tool_calls=tool_names,
            status="CONFIRMATION_REQUIRED",
            confirmation=prompt,
        )

    return ChatResponse(
        response=final_state.get("response") or "操作已完成。",
        intent=final_state.get("intent"),
        tool_calls=tool_names,
        status="COMPLETED",
    )


@router.post("", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    主对话接口。

    接收用户的消息，运行 Agent 状态机，返回 Agent 的回复。

    Request:
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
    # TO-DO: 实现
    #
    # 提示：
    # 1. 构建 AgentState 初始状态
    # 2. 调用 agent_graph.invoke(initial_state)
    # 3. 从最终状态中提取 response
    # 4. 返回 ChatResponse
    #
    # 伪代码：
    # initial_state = AgentState(
    #     user_input=request.message,
    #     user_id=request.user_id,
    #     user_name=request.user_name,
    #     history=request.history,
    #     tool_results=[],
    #     execution_errors=[],
    #     retry_count=0,
    # )
    # final_state = agent_graph.invoke(initial_state)
    # return ChatResponse(
    #     response=final_state["response"],
    #     intent=final_state.get("intent"),
    #     tool_calls=[tc["tool"] for tc in final_state.get("tool_calls", [])]
    # )

    context = RequestContext.from_current_user(current_user)
    thread_id = derive_thread_id(context.owner_id)
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
    final_state = agent_graph.invoke(
        initial_state,
        config=_graph_config(context.owner_id),
    )
    return _to_chat_response(final_state)


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
    context = RequestContext.from_current_user(current_user)
    config = _graph_config(context.owner_id)

    # thread_id 先定位会话，再用 LangGraph 生成的 interrupt_id 定位该会话中
    # 具体的暂停点。旧弹窗、重复点击和错误 ID 都不能恢复其他中断。
    snapshot = agent_graph.get_state(config)
    pending = (snapshot.values or {}).get("pending_confirmation")
    if not pending:
        raise HTTPException(status_code=409, detail="当前没有等待确认的操作")
    if request.interrupt_id not in _pending_interrupt_ids(snapshot):
        raise HTTPException(status_code=409, detail="确认信息已失效，请重新发起操作")

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
    return _to_chat_response(final_state)
