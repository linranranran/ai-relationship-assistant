"""请求重试的确定性入口：恢复执行位置，而不是重新让模型生成计划。"""

from langgraph.types import Command


class RecoveryConflict(ValueError):
    """会话中另有未结束任务，或者恢复所需的检查点已丢失。"""


def interrupted_state(snapshot) -> dict:
    """get_state 不带 invoke 返回的 __interrupt__，还原 HTTP 展示所需结构。"""
    interrupts = tuple(
        item for task in (snapshot.tasks or ())
        for item in (getattr(task, "interrupts", ()) or ())
    )
    return {**(snapshot.values or {}), "__interrupt__": interrupts}


def run_or_recover(graph, config: dict, initial_state: dict, *, is_retry: bool,
                   find_answer=None, can_rebuild=None) -> dict:
    """根据原请求的检查点选择执行方式。

    1. 首次请求使用 initial_state；失败重试绝不能再次覆盖已有计划和 call_id。
    2. 节点抛异常时，LangGraph 会保存待执行节点；invoke(None) 从那里重放。
    3. 人工答案先持久化，再恢复图。崩溃后若中断仍在，就重放原答案；若已
       消费答案且后续节点失败，就 invoke(None)，避免再提交旧 interrupt_id。
    4. 图已完成但 HTTP/请求表保存失败，直接复用最终状态。
    5. WAIT 是主动结束本次运行，必须显式重入执行节点；保留原计划及成功结果。
    """
    snapshot = graph.get_state(config)
    values = snapshot.values or {}
    request_id = initial_state["request_id"]
    same_request = values.get("request_id") == request_id

    if not same_request:
        if snapshot.next or values.get("execution_decision") == "WAIT":
            raise RecoveryConflict("会话中还有未结束任务，请先重试、补充或取消原任务")
        if is_retry and not (can_rebuild is not None and can_rebuild()):
            # 宁可暴露无法恢复，也不能在旧写操作可能成功的情况下重新规划。
            raise RecoveryConflict("原请求检查点已丢失或被覆盖，无法安全重建原计划")
        # 首次 invoke 前即失败的请求没有执行过任何工具，可安全重新从入口开始。
        return graph.invoke(initial_state, config=config)

    interrupted = interrupted_state(snapshot)
    pending = interrupted["__interrupt__"]
    if pending:
        answers = {}
        if find_answer is not None:
            for item in pending:
                answer = find_answer(item.id)
                if answer is not None:
                    answers[item.id] = answer
        if answers:
            return graph.invoke(Command(resume=answers), config=config)
        return interrupted

    if snapshot.next:
        return graph.invoke(None, config=config)

    if values.get("execution_decision") == "WAIT":
        # 执行节点在恢复时跳过 SUCCESS/RESOLVED；其余步骤使用原 call_id 再抢占。
        succeeded = [item for item in values.get("tool_results", [])
                     if item.get("status") in {"SUCCESS", "RESOLVED"}]
        # 官方 API 中 Command(resume=...) 用于中断恢复；update/goto 是节点输出。
        # 主动 END 后用普通输入更新恢复标志，由 dispatch_request 确定性路由。
        return graph.invoke({
            "tool_results": succeeded,
            "execution_errors": [],
            "next_tool_index": 0,
            "execution_decision": "",
            "response": "",
            "resume_execution": True,
        }, config=config)
    return dict(values)
