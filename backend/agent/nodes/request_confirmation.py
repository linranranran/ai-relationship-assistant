"""写入授权入口：先暂停，再按「失效 / 格式错误 / 取消 / 拒绝 / 批准」处理。

阅读 request_confirmation 即可看到路由；下面的私有函数只整理各分支的状态。
Direct 确认修改、删除及存疑新增；明确新增可免确认。Plan、Discovery 保留原策略。
拒绝单个操作和取消整次请求是两种不同输入，不能合并成一个分支。
"""

from langgraph.graph import END
from langgraph.types import Command, interrupt

from backend.agent.confirmation import authorize_high_risk_calls
from backend.agent.state import AgentState
from backend.agent.strategies.contracts import is_request_direct
from backend.agent.direct_runtime import DIRECT_WRITE_TOOLS, mutation_fingerprint


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

    # 明确标注交互类型，让 HTTP 层和前端使用确认控件，而不是澄清输入框。
    payload = {**pending, "interaction_type": "CONFIRMATION"}
    resume_value = interrupt(payload)

    # 必须先核对操作内容，再接受任何恢复输入，不能批准一份已经变更的操作。
    if is_request_direct(state) and _operation_changed(state, pending):
        return Command(
            update={"stop_reason": "CONFIRMATION_CHANGED", "needs_confirmation": False,
                    "pending_confirmation": None},
            goto="direct_finish",
        )

    # interrupt_id 的匹配由 LangGraph 负责。进入到这里说明当前 interrupt
    # 已经收到了与其 ID 对应的恢复值；节点只校验业务输入的结构和类型。
    if not isinstance(resume_value, dict):
        return _invalid_confirmation(state)
    if resume_value.get("cancelled") is True:
        return _cancel_request(state)
    # 使用 ``is True``，字符串 "true"、数字 1 都不能代表真实授权。
    if resume_value.get("confirmed") is not True:
        return _reject_operation(state)
    return _approve_operation(state)


def _operation_changed(state, pending) -> bool:
    """确认只绑定原 call_id + 完整参数指纹，不是一次请求的通行证。"""
    index = state.get("next_tool_index", 0)
    calls = state.get("tool_calls", [])
    return (
        index >= len(calls)
        or pending.get("call_id") != calls[index].get("call_id")
        or pending.get("mutation_fingerprint") != mutation_fingerprint(calls[index])
    )


def _clear_confirmation() -> dict:
    """返回状态更新，不原地修改 state；旧授权不能泄漏到下一次写入。"""
    return {"needs_confirmation": False, "pending_confirmation": None, "user_confirmed": False}


def _invalid_confirmation(state) -> Command:
    update = {**_clear_confirmation(), "response": "确认数据格式错误，请重新发起操作。"}
    if is_request_direct(state):
        update["stop_reason"] = "CONFIRMATION_INVALID"
    return Command(update=update, goto="direct_finish" if is_request_direct(state) else END)


def _cancel_request(state) -> Command:
    """停止整次请求；已经提交的业务操作不会回滚。"""
    return Command(
        update={**_clear_confirmation(), "execution_decision": "CANCELLED", "response": "已取消本次任务。"},
        goto="direct_finish" if is_request_direct(state) else END,
    )


def _reject_operation(state) -> Command:
    """Direct 反馈拒绝并继续其他要求；目标模式取消当前目标后继续调度。"""
    if is_request_direct(state):
        call = state["tool_calls"][state.get("next_tool_index", 0)]
        # 保存操作内容指纹，防止模型换 call_id 再提出同一笔被拒绝的写入。
        record = {
            **call, "status": "SKIPPED",
            "result": {"success": False, "message": "用户拒绝该操作"},
            "error": "用户拒绝该操作", "error_code": "USER_REJECTED",
        }
        return Command(
            update={
                **_clear_confirmation(),
                "direct_rejected_mutations": [*state.get("direct_rejected_mutations", []), mutation_fingerprint(call)],
                "tool_results": [record],
                "next_tool_index": len(state["tool_calls"]),
            },
            goto="direct_observe",
        )
    if state.get("execution_version") == 2 and state.get("active_goal_id"):
        from backend.agent.goal_policy import replace_goal
        return Command(
            update={
                **_clear_confirmation(),
                "goals": replace_goal(
                    state, status="CANCELLED", failure_reason="用户拒绝该操作",
                    tool_calls=state.get("tool_calls", []), tool_results=state.get("tool_results", []),
                ),
            },
            goto="select_next_goal",
        )
    return Command(update={**_clear_confirmation(), "response": "已取消本次操作。"}, goto=END)


def _approve_operation(state) -> Command:
    """服务端只给当前索引的操作加 confirmed；后续动作需要自己的确认。"""
    return Command(
        update={
            "tool_calls": authorize_high_risk_calls(
                state.get("tool_calls", []),
                only_index=state.get("next_tool_index", 0),
                **({"confirmation_tools": DIRECT_WRITE_TOOLS}
                   if is_request_direct(state) else {}),
            ),
            "needs_confirmation": False,
            "pending_confirmation": None,
            "user_confirmed": True,
        },
        goto="execute_tools",
    )
