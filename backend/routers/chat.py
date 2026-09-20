# ============================================================
# Agent 对话路由
# 前端通过这个接口和 Agent 交互
# ============================================================

from fastapi import APIRouter, HTTPException
from backend.models.schemas import ChatRequest, ChatResponse
from backend.agent.state import AgentState
from backend.agent.graph import agent_graph

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    主对话接口。

    接收用户的消息，运行 Agent 状态机，返回 Agent 的回复。

    Request:
        {
            "message": "今天见的张三，李四的朋友，做金融的",
            "user_id": "user_001",
            "user_name": "小明",
            "history": []
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

    # TODO history竟然需要前端传值？后续优化使用checkpoint、story持久化
    initial_state = AgentState(
        user_input=request.message,
        user_id=request.user_id,
        user_name=request.user_name,
        history=request.history,
        tool_results=[],
        execution_errors=[],
        retry_count=0,
    )
    final_state = agent_graph.invoke(initial_state)
    return ChatResponse(
        response=final_state["response"],
        intent=final_state.get("intent"),
        tool_calls=[tc["tool"] for tc in final_state.get("tool_calls", [])]
    )