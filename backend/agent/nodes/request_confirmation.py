"""LangGraph 人工确认节点。"""

from langgraph.graph import END
from langgraph.types import Command, interrupt

from backend.agent.confirmation import authorize_high_risk_calls
from backend.agent.state import AgentState


def request_confirmation(state: AgentState) -> Command:
    """暂停图，等待 ``POST /api/chat/resume`` 恢复。

    关键执行顺序：
    1. ``interrupt(payload)`` 保存当前检查点，并把 payload 返回给调用端；
    2. 第一次运行到这里时，函数会在 interrupt 处暂停；
    3. 收到 ``Command(resume=...)`` 后，本节点会从头重新执行，interrupt
       返回前端传来的确认结果；
    4. 只有确认值严格等于 True，服务端才注入 ``confirmed=True``。

    因为恢复时节点会重跑，所以 interrupt 之前不能写数据库、发消息或调用
    其他有副作用的服务。当前节点只读取 state，满足这个约束。
    """
    pending = state.get("pending_confirmation")
    if not pending:
        return Command(
            update={"response": "没有等待确认的操作，请重新发起请求。"},
            goto=END,
        )

    resume_value = interrupt(pending)

    # interrupt_id 的匹配由 LangGraph 负责。进入到这里说明当前 interrupt
    # 已经收到了与其 ID 对应的恢复值；节点只校验业务输入的结构和类型。
    if not isinstance(resume_value, dict):
        return Command(
            update={
                "response": "确认数据格式错误，请重新发起操作。",
                "needs_confirmation": False,
                "pending_confirmation": None,
                "user_confirmed": False,
            },
            goto=END,
        )

    # 使用 ``is True``，字符串 "true"、数字 1 都不能代表真实授权。
    if resume_value.get("confirmed") is not True:
        return Command(
            update={
                "response": "已取消本次操作。",
                "needs_confirmation": False,
                "pending_confirmation": None,
                "user_confirmed": False,
            },
            goto=END,
        )

    return Command(
        update={
            "tool_calls": authorize_high_risk_calls(
                state.get("tool_calls", []),
                only_index=state.get("next_tool_index", 0),
            ),
            "needs_confirmation": False,
            "pending_confirmation": None,
            "user_confirmed": True,
        },
        goto="execute_tools",
    )
