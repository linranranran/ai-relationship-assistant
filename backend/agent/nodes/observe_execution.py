"""观察 Tool 执行事实，并把 Agent 路由到可靠的下一步。"""

from langgraph.graph import END
from langgraph.types import Command

from backend.agent.execution_policy import (
    ExecutionDecision,
    ExecutionObservation,
    observe_tool_results,
)
from backend.agent.state import AgentState


def _observe(state: AgentState) -> ExecutionObservation:
    """使用确定性策略解释执行结果，不调用大模型。"""
    return observe_tool_results(
        records=state.get("tool_results", []),
        execution_errors=state.get("execution_errors", []),
        replan_count=state.get("replan_count", 0),
    )


def _observation_update(
    state: AgentState,
    observation: ExecutionObservation,
) -> dict:
    """生成可持久化、可审计，也能交给重规划器使用的反馈。"""
    return {
        "execution_decision": observation.decision.value,
        "replan_feedback": {
            **observation.as_feedback(),
            # Tool 结果可能包含外部文本，因此规划 Prompt 会把这里整体标记为
            # 不可信数据，只允许模型分析，不允许它执行夹带的指令。
            "previous_tool_results": state.get("tool_results", []),
            "execution_errors": state.get("execution_errors", []),
        },
    }


def _route_decision(
    state: AgentState,
    observation: ExecutionObservation,
    update: dict,
) -> Command:
    """把观察结论映射成 LangGraph 路由。

    * COMPLETE：所有步骤成功，生成最终回复；
    * REPLAN：没有危险副作用且仍有次数，带反馈重新规划；
    * WAIT：其他进程仍在执行或遇到临时故障，本轮结束；
    * ASK_USER：进入澄清节点。执行期歧义会在那里 ``interrupt``。
    """
    if observation.decision == ExecutionDecision.COMPLETE:
        return Command(update=update, goto="generate_response")

    if observation.decision == ExecutionDecision.REPLAN:
        return Command(
            update={
                **update,
                "replan_count": state.get("replan_count", 0) + 1,
            },
            goto="plan_tasks",
        )

    if observation.decision == ExecutionDecision.WAIT:
        return Command(
            update={
                **update,
                "response": "任务仍在执行或暂时不可用，请稍后使用原请求重试。",
            },
            goto=END,
        )

    return Command(update=update, goto="ask_clarification")


def observe_execution(state: AgentState) -> Command:
    """执行 Observe 与 Decide 两个阶段。

    ``execute_tools`` 只说明每一步发生了什么；本节点才决定整项任务接下来
    应该做什么。把两者分开后，重试限制、写操作保护和人工介入都能用普通
    Python 规则审计，不需要依赖模型对错误文案的临场理解。

    建议按以下顺序学习这段链路：

    1. ``find_person`` 返回 ``NOT_FOUND``，观察是否安全重规划一次；
    2. 返回 ``AMBIGUOUS``，观察是否进入结构化澄清中断；
    3. 返回 ``IN_PROGRESS`` 或 ``TRANSIENT``，观察是否结束并提示稍后重试。
    """
    observation = _observe(state)
    update = _observation_update(state, observation)
    return _route_decision(state, observation, update)
