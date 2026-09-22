# ============================================================
# Agent 对话路由
# 前端通过这个接口和 Agent 交互
# ============================================================

from fastapi import APIRouter, Depends
from backend.auth import get_current_user
from backend.models.schemas import ChatRequest, ChatResponse
from backend.models.context import RequestContext
from backend.agent.state import AgentState
from backend.agent.graph import agent_graph

router = APIRouter(prefix="/api/chat", tags=["chat"])


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
    initial_state = AgentState(
        user_input=request.message,
        user_id=context.owner_id,
        self_person_id=context.self_person_id,
        request_id=context.request_id,
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
        needs_confirmation=False,
        confirmation_type="",
        user_confirmed=False,
        response="",
    )
    final_state = agent_graph.invoke(initial_state)
    return ChatResponse(
        response=final_state["response"],
        intent=final_state.get("intent"),
        tool_calls=[tc["tool"] for tc in final_state.get("tool_calls", [])]
    )
