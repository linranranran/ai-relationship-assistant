"""观察工具执行事实，再用服务端规则决定下一步。

阅读入口是本文件最后的 ``observe_execution``，不要从辅助函数开始猜流程。

Plan 模式的判断分成两层：
1. 批次层：这批工具有没有失败、能不能安全重规划？由 execution_policy 判断。
2. 目标层：这些实际回执是否满足用户的业务目标？由 goal_completion 判断。

例如“查询张三的爱好”：find_person 成功只证明找到了人物 ID，还需要详情回执；
而“查询是否存在张三”：精确查询返回 NOT_FOUND 也可以完成这个查询目标。
因此，工具步骤成功不等于业务目标成功，工具报错也不一定代表整个目标失败。

本文件不调用模型、不重新执行工具；需要重新规划时才路由到 plan_tasks。
Direct 委托 direct_nodes.direct_observe；Discovery 委托
strategy_nodes.observe_strategy_execution。下面的目标验收只属于 Plan 分支。
"""

from langgraph.graph import END
from langgraph.types import Command

from backend.agent.execution_policy import (
    ExecutionDecision,
    ExecutionObservation,
    observe_tool_results,
)
from backend.agent.state import AgentState

import logging

logger = logging.getLogger(__name__)


def _observe(state: AgentState) -> ExecutionObservation:
    """先判断当前批次的执行状况，不在这里验收整个业务目标。

    输入来自 execute_tools 写入的状态：
    - tool_results：每一步的 step_id、call_id、工具名、步骤状态和实际返回值；
    - execution_errors：执行器发现的额外错误，不只依赖工具回执；
    - replan_count：当前目标已自动重规划的次数，防止无限循环。

    observe_tool_results 的分支顺序（先命中者优先）：
    1. 没有 FAILED / SKIPPED 步骤且没有 execution_errors -> COMPLETE。
       这是“本批次未发现失败”，空批次也可能满足，不能据此宣称目标完成。
    2. 错误码含 IN_PROGRESS / TRANSIENT -> WAIT。
    3. 错误码含 AMBIGUOUS / CONFLICT / PERMISSION_DENIED -> ASK_USER。
    4. 含 NOT_FOUND / INVALID_ARGUMENT，重规划次数未超限，且本批次没有
       SUCCESS 状态的写工具 -> REPLAN；具体写工具集合见 MUTATING_TOOLS。
    5. 其余情况 -> ASK_USER，表示当前策略无法安全自动恢复。

    第 3、5 步的 ASK_USER 是策略层的粗分类；V2 的 _route_decision 会进一步
    区分“用户能澄清的问题”和“应直接结束的内部错误”，不会一律打断用户。
    这里观察当前批次；后面的目标验收还会合并该目标之前各批次的回执。
    """
    return observe_tool_results(
        records=state.get("tool_results", []),
        execution_errors=state.get("execution_errors", []),
        replan_count=state.get("replan_count", 0),
    )


def _observation_update(
    state: AgentState,
    observation: ExecutionObservation,
) -> dict:
    """把规则判断转成 LangGraph 状态更新，供审计、恢复和重规划使用。

    execution_decision 保存机器可读的结论，不解析中文错误文案做路由。
    replan_feedback 包含判断理由、失败步骤、错误码以及本批次的原始回执。
    例如 find_person 没找到张三，下一轮规划器可以看到 NOT_FOUND 和查询结果，
    再选择补建人物或调整查询，而不是重新盲猜上一轮到底发生了什么。

    注意：这些反馈帮助模型理解事实，不保证下一轮计划必然正确。
    新计划仍须经过参数、权限、依赖及目标范围校验后才能执行。
    返回的 dict 在 Command(update=...) 后才会合入图状态，便于 Checkpointer 保存。
    """
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

    * COMPLETE：本批次未发现失败。V2 先去 finalize_goal 验收、归档当前目标，
      再调度其他目标；旧版直接去 generate_response。
    * REPLAN：观察策略允许重规划，更新次数与版本后去 plan_tasks。
    * WAIT：结束本轮图运行，提示稍后重试原请求；不是业务目标已完成。
    * ASK_USER：V2 只有 AMBIGUOUS / CONFLICT 才进入 ask_clarification；
      其他无法恢复的情况交给 finalize_goal 保存失败与部分成功结果。

    Command 的 update 和 goto 要一起读：前者保存判断事实，后者选择下一节点。
    goto=END 只是结束本轮执行，不等同于 interrupt 暂停，也不授予重试写入权限。
    """
    logger.info(f"5.1、观察节点反馈结果，decision={observation.decision}")
    if observation.decision == ExecutionDecision.COMPLETE:
        # V2 多目标请求可能“人物新增成功、关系新增失败”，必须分别归档。
        # finalize_goal 会再次核验业务完成条件，所以路由到它不代表直接判成功。
        # 它之后去 select_next_goal；全部目标收尾后才统一生成回复。
        return Command(
            update=update,
            goto="finalize_goal"
            if state.get("execution_version") == 2
            else "generate_response",
        )

    if observation.decision == ExecutionDecision.REPLAN:
        if state.get("execution_version") == 2:
            from backend.agent.budget import reserve, settle
            from backend.agent.goal_policy import replace_goal

            # 一次目标可以有多个计划版本；新版本让审计能区分“最初怎么规划”
            # 和“哪一次根据失败反馈修正”。不是新建用户请求或重置全局预算。
            revision = state.get("plan_revision", 0) + 1
            # 先向请求总预算登记一次 replan，再发布状态更新。
            # active_goal_id + revision 标识这次逻辑动作，供预算账本去重。
            # 如果预算不足，reserve 抛出异常，由外层节点包装器转到 finish_budget；
            # 不会继续调用模型，也不会把它当作可以向用户索取答案的歧义。
            attempt = reserve("replan", f"{state['active_goal_id']}:{revision}")
            settle(attempt, result={"revision": revision})
            # goals 保存当前目标的长期记录；顶层 replan_count 是当前执行工作区。
            # 两处一起更新，目标归档和后续节点才能看到一致的重规划次数。
            update.update(
                plan_revision=revision,
                goals=replace_goal(
                    state,
                    plan_revision=revision,
                    auto_replans=state.get("replan_count", 0) + 1,
                ),
            )
        # 带上 replan_feedback 回到规划节点，而不是直接重跑上一批工具。
        # 新计划会再次校验；成功回执仍须保留，不能因为重规划就抹掉已发生的事实。
        return Command(
            update={
                **update,
                "replan_count": state.get("replan_count", 0) + 1,
            },
            goto="plan_tasks",
        )

    if observation.decision == ExecutionDecision.WAIT:
        # 同一 call_id 可能已由另一个进程执行；也可能外部服务暂时不可用。
        # 此时不立即让模型生成新的调用，避免为同一业务操作造出新 call_id。
        # 调用方应稍后携带原 request_id 重试，由恢复/幂等链路读取既有执行状态。
        # 这里不轮询、不 sleep、不清空已保存的工具计划和结果。
        return Command(
            update={
                **update,
                "response": "任务仍在执行或暂时不可用，请稍后使用原请求重试。",
            },
            goto=END,
        )

    if state.get("execution_version") == 2:
        # 只有用户能解决的歧义/冲突才询问。权限失败和内部错误不能靠补充文本绕过。
        # 具体条件是“错误集合里至少含一个 AMBIGUOUS 或 CONFLICT”。
        # 仅有 PERMISSION_DENIED、INTERNAL、UNKNOWN、依赖失败或超出重规划次数时，
        # 直接收尾当前目标；若多个错误混合出现，按此集合条件判断，不是逐条处理。
        if not set(observation.error_codes) & {"AMBIGUOUS", "CONFLICT"}:
            return Command(update=update, goto="finalize_goal")
    # ask_clarification 会构建问题/候选并调用 interrupt；本节点本身不发起中断。
    # 旧版没有 V2 的细分收尾，剩余 ASK_USER 情况都走这个兼容分支。
    return Command(update=update, goto="ask_clarification")


def _evaluate_current_goal(state):
    """合并当前目标各轮回执，用完成合同验收；不让模型自评是否完成。"""
    from backend.agent.goal_completion import (
        build_completion_spec, evaluate_goal, record_facts, trusted_bindings,
    )
    from backend.agent.goal_policy import active_goal
    goal = active_goal(state)
    records = record_facts(goal, state)
    evaluation = evaluate_goal(
        build_completion_spec(goal, state.get("extracted_info")),
        records,
        trusted_bindings(state, records),
    )
    return evaluation, records


def _can_finalize_goal(evaluation, observation) -> bool:
    """已有目标证据时可以结束，但权限、故障或执行未知状态不能被掩盖。"""
    blocking_errors = {"PERMISSION_DENIED", "INTERNAL", "UNKNOWN", "TRANSIENT", "IN_PROGRESS"}
    return evaluation.status == "SATISFIED" and not set(observation.error_codes) & blocking_errors


def _adjust_incomplete_batch(state, observation, evaluation, records):
    """批次成功却缺目标证据：没有成功写入且次数允许时，补一次规划。

    例如 find_person 成功只得到 ID，尚未取得爱好详情。已有成功写入或写步骤
    RESOLVED 时保持原结论，交给 finalize_goal 保留部分成果并验收为未完成。
    这里不改变已有错误分支，不扩大自动写入，也不重置请求预算。
    """
    if evaluation.status != "PENDING" or observation.decision != ExecutionDecision.COMPLETE:
        return observation
    from backend.agent.execution_policy import MAX_AUTO_REPLANS, MUTATING_TOOLS
    has_write = any(
        record.get("tool") in MUTATING_TOOLS and record.get("status") in {"SUCCESS", "RESOLVED"}
        for record in records
    )
    if not has_write and state.get("replan_count", 0) < MAX_AUTO_REPLANS:
        return ExecutionObservation(ExecutionDecision.REPLAN, "批次结束但查询目标缺少有效回执")
    return observation


def observe_execution(state: AgentState) -> Command:
    """执行 Observe 与 Decide 两个阶段。

    ``execute_tools`` 只说明每一步发生了什么；本节点才决定整项任务接下来
    应该做什么。把两者分开后，重试限制、写操作保护和人工介入都能用普通
    Python 规则审计，不需要依赖模型对错误文案的临场理解。

    按下面五步读函数体：

    1. 判断策略模式：Direct / Discovery 委托给逐轮观察器，Plan 继续往下。
    2. 判断批次结果：_observe 根据步骤状态、错误码、次数得到初步决策。
    3. 核验业务目标：V2 按完成条件检查真实人物 ID、属性或关系操作回执。
    4. 修正初步决策：满足目标可提前收尾；只读批次遗漏所需结果可补规划。
    5. 保存反馈并路由：_route_decision 选择收尾、重规划、等待或澄清。

    对照场景：
    - 查询张三，精确查询 NOT_FOUND -> 可能已完成负向查询；
    - 新增张三，查询 NOT_FOUND -> 尚未完成创建，仍需保存人物的有效回执；
    - 查询张三爱好，仅 find_person 成功 -> 缺详情，可能安全补规划一次；
    - 写人物成功但后续失败 -> 保留部分成功，避免自动规划新的写入；
    - 同名多人 AMBIGUOUS -> 澄清；IN_PROGRESS / TRANSIENT -> 等待原请求重试。
    """

    # 第 1 步：这里是图中共同的业务执行观察入口，但三种模式的循环不同。
    # Plan 一次规划一个批次；Direct / Discovery 每轮返回结果后再决定下一步，
    # 还需回填 Function Calling 消息、判断是否有新进展以及检查循环停止条件。
    # 下面是普通 Python 函数委托，不是 goto="observe_strategy"；不会新增一次
    # 独立图节点执行。Discovery 查询工具目录后的独立观察节点是另一条入口。
    from backend.agent.strategies.contracts import is_request_direct
    if is_request_direct(state):
        from backend.agent.nodes.direct_nodes import direct_observe
        return direct_observe(state)
    if state.get("execution_version") == 2 and state.get("strategy_mode") == "discovery":
        from backend.agent.nodes.strategy_nodes import observe_strategy_execution
        return observe_strategy_execution(state)
    # 第 2 步：获得“批次层”的初步结论，先不把 COMPLETE 当作业务成功。
    observation = _observe(state)
    if state.get("execution_version") == 2:
        # 第 3、4 步：目标验收优先于普通批次失败，再补齐“批次成功但证据不足”。
        # NOT_FOUND 可以证明负向查询已完成，但不能证明新增人物成功。
        evaluation, records = _evaluate_current_goal(state)
        if _can_finalize_goal(evaluation, observation):
            return Command(update={"goal_evaluation": evaluation.as_dict()}, goto="finalize_goal")
        observation = _adjust_incomplete_batch(state, observation, evaluation, records)
    # 第 5 步：将初步/修正后的决策、原始回执和缺失条件保存到状态，再选路由。
    # 旧版没有 V2 的业务完成合同，只使用批次层判断；所以 evaluation 只在 V2 读取。
    update = _observation_update(state, observation)
    if state.get("execution_version") == 2:
        update["goal_evaluation"] = evaluation.as_dict()
        # 规划器不仅知道“失败了”，还知道必须补齐哪些验收证据。
        # 这是下一轮规划的输入提示，不是绕过工具执行校验的授权。
        update["replan_feedback"]["missing_requirements"] = list(evaluation.missing_requirements)
    return _route_decision(state, observation, update)
