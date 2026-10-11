"""多目标的调度与收尾。第一版顺序运行，复用已有依赖 Tool 执行器。"""

from copy import deepcopy
import logging
from langgraph.types import Command
from backend.agent.contracts import GoalStatus
from backend.agent.goal_policy import (
    active_goal,
    completion_evidence,
    replace_goal,
    accumulated_records,
)
from backend.agent.streaming import emit_progress


def _first_node(intent):
    """目标类型只决定入口；完成与否由执行回执判断。"""
    if intent in {"CHITCHAT", "UNSUPPORTED"}:
        return "finalize_goal"
    if intent == "UNCLEAR":
        return "ask_clarification"
    if intent in {"ADD_PERSON", "UPDATE_PERSON", "ADD_RELATION", "UPDATE_RELATION"}:
        return "extract_info"
    return "plan_tasks"


def validate_goals(state):
    # 草案已在 classify_intent 通过结构及依赖校验。这里保留独立节点便于断点观察。
    return Command(goto="select_next_goal")


def refine_goal(state):
    """仅重新理解当前不明确目标，不重做已成功的其他目标。"""
    from backend.agent.contracts import IntentBatch, StructuredOutputError
    from backend.agent.goal_policy import goal_input, validate_goal_business_fields
    from backend.agent.contracts import ModelBusinessValidationError
    from backend.services.structured_llm import invoke_structured

    def validate_refinement(value):
        if len(value.goals) != 1 or value.goals[0].depends_on:
            raise ModelBusinessValidationError(
                "REFINEMENT_SCOPE_INVALID", "仅明确当前一个目标，不得新增目标或依赖。"
            )
        validate_goal_business_fields(value.goals[0], [])

    result = invoke_structured(
        IntentBatch,
        "根据补充信息明确当前这一个目标，只能返回一个目标。不要增加额外目标或依赖。"
        "人物目标填写 person 中的姓名和属性，关系目标填写 relation 中的双方及类型。",
        [{"role": "user", "content": goal_input(state)}],
        validator=validate_refinement,
    )
    if len(result.goals) != 1:
        raise StructuredOutputError("补充信息不能扩展成多个新目标，请单独发起请求")
    draft = result.goals[0]
    # 补充后重新理解目标时，结构化人物/关系字段也要随目标更新，不能留下旧属性。
    goals = replace_goal(
        state, intent=draft.intent, description=draft.description,
        person=draft.person.model_dump() if draft.person else None,
        relation=draft.relation.model_dump() if draft.relation else None,
    )
    target = _first_node(draft.intent)
    return Command(update={"goals": goals, "intent": draft.intent}, goto=target)


def select_next_goal(state):
    goals = deepcopy(state["goals"])
    by_id = {goal["goal_id"]: goal for goal in goals}
    for goal in goals:
        if goal["status"] != GoalStatus.PENDING:
            continue
        if any(
            by_id[dep]["status"] in {"FAILED", "BLOCKED", "CANCELLED"}
            for dep in goal["depends_on"]
        ):
            goal.update(
                status=GoalStatus.BLOCKED.value, failure_reason="前置目标未成功"
            )
            continue
        if any(
            by_id[dep]["status"] != GoalStatus.SUCCEEDED for dep in goal["depends_on"]
        ):
            continue
        if goal["intent"] == "UNSUPPORTED":
            goal.update(
                status=GoalStatus.FAILED.value, failure_reason="当前工具不支持此目标"
            )
            continue
        goal["status"] = GoalStatus.RUNNING.value
        emit_progress(
            "goal.started",
            goal_id=goal["goal_id"],
            goal_status=goal["status"],
            message=f"开始处理目标 {goals.index(goal) + 1}/{len(goals)}：{goal['description']}",
        )
        # 工具数组属于当前目标；请求级已完成写操作、预算和原始提问不能清空。
        update = {
            "goals": goals,
            "active_goal_id": goal["goal_id"],
            "intent": goal["intent"],
            "plan_revision": goal["plan_revision"],
            "replan_count": 0,
            "replan_feedback": {},
            "tool_calls": [],
            "tool_results": [],
            "execution_errors": [],
            "next_tool_index": 0,
            "extracted_info": {},
            "pending_confirmation": None,
            "pending_reference": None,
            "resolved_reference": state.get("resolved_reference", {})
            if len(goals) == 1
            else {},
            "user_confirmed": False,
            "needs_confirmation": False,
            "execution_decision": "",
            "response": "",
        }
        # 不能把上轮人物焦点直接套给本轮另一个目标。规划器从依赖结果中读取真实 ID。
        target = _first_node(goal["intent"])
        return Command(update=update, goto=target)
    return Command(
        update={"goals": goals, "active_goal_id": None}, goto="generate_response"
    )


def finalize_goal(state):
    goal = active_goal(state)
    calls, records = accumulated_records(goal, state)
    ok, evidence = completion_evidence(goal, records, state.get("extracted_info"))
    reason = (
        None
        if ok
        else (
            "；".join(state.get("execution_errors", []))
            or "没有取得满足目标完成条件的工具回执"
        )
    )
    goals = replace_goal(
        state,
        status="SUCCEEDED" if ok else "FAILED",
        tool_calls=calls,
        tool_results=records,
        # 回复必须看见部分成功的操作，而不仅是“证明整个目标成功”的工具。
        # 例如人物已创建但关系失败，不能向用户声称本目标没有任何业务效果。
        result=[
            record["result"]
            for record in records
            if record.get("status") in {"SUCCESS", "RESOLVED"}
            and isinstance(record.get("result"), dict)
            and record["result"].get("success")
        ],
        extracted_info=state.get("extracted_info", {}),
        completion_evidence=[record["call_id"] for record in evidence],
        failure_reason=reason,
    )
    emit_progress(
        "goal.finished",
        goal_id=goal["goal_id"],
        goal_status="SUCCEEDED" if ok else "FAILED",
        message="当前目标已完成" if ok else "当前目标未完成，检查其他独立目标",
    )
    # 保留每个目标的原始执行事实，最终记忆提取不能只看到最后一个目标。
    return Command(
        update={"goals": goals, "active_goal_id": None}, goto="select_next_goal"
    )


def finish_budget(state):
    """无需额外模型调用即可收尾；耗尽后不能再要求模型生成解释。"""
    from backend.db.agent_budget_store import BudgetStore

    store = BudgetStore(state["user_id"], state["request_id"])
    snapshot = store.snapshot()
    goals = deepcopy(state.get("goals", []))
    # 当前节点可能在写成功、Checkpoint 前失败。以 PG 已持久化工具成功记录补齐事实。
    from backend.db.postgres_client import get_postgres_connection

    with get_postgres_connection() as connection:
        rows = connection.execute(
            "SELECT call_id,tool_name,result,status,arguments FROM tool_execution WHERE owner_id=%s AND request_id=%s",
            (state["user_id"], state["request_id"]),
        ).fetchall()
    saved = {row["call_id"]: row for row in rows if row["status"] == "success"}
    from backend.agent.nodes.execute_tools import BUSINESS_IDEMPOTENT_TOOLS
    from backend.db.neo4j_operations import read_committed_operations

    uncertain = [
        row
        for row in rows
        if row["status"] != "success" and row["tool_name"] in BUSINESS_IDEMPOTENT_TOOLS
    ]
    warnings = []
    try:
        committed = read_committed_operations(
            state["user_id"],
            [
                {"call_id": row["call_id"], "tool": row["tool_name"]}
                for row in uncertain
            ],
        )
    except Exception:
        # 数据库无法核对时必须承认未知，不能把缺少 PG success 解释为未写入。
        logging.getLogger(__name__).warning("预算收尾无法读取业务回执", exc_info=True)
        committed = {}
        warnings.append(
            "部分已启动写操作暂时无法核对，请检查业务数据后再发起新的操作。"
        )
    if committed:
        warnings.append("已核对并保留业务库中的成功写入；关联检索索引可能需要补同步。")
    for row in uncertain:
        if row["call_id"] in committed:
            saved[row["call_id"]] = {**row, "result": committed[row["call_id"]]}
    for goal in goals:
        if goal["status"] in {"PENDING", "RUNNING", "WAITING"}:
            if goal["goal_id"] == state.get("active_goal_id"):
                calls, records = accumulated_records(goal, state)
            else:
                calls, records = (
                    goal.get("tool_calls", []),
                    goal.get("tool_results", []),
                )
            present = {
                record["call_id"]
                for record in records
                if record.get("status") in {"SUCCESS", "RESOLVED"}
            }
            for call in calls:
                if call["call_id"] in saved and call["call_id"] not in present:
                    row = saved[call["call_id"]]
                    call["args"] = row.get("arguments", call["args"])
                    records = [
                        record
                        for record in records
                        if record["call_id"] != call["call_id"]
                    ]
                    records.append(
                        {
                            **call,
                            "args": row.get("arguments", call["args"]),
                            "status": "SUCCESS",
                            "result": row["result"],
                        }
                    )
            # 预查 NOT_FOUND 在新增成功后属于预期分支；恢复补回执也要使用
            # 与执行器相同的规则，否则会把已成功新增仍判成失败。
            successful_creates = {
                dep
                for call in calls
                if call["tool"] == "add_person" and call["call_id"] in saved
                for dep in call.get("depends_on", [])
            }
            records = [
                record
                for record in records
                if not (
                    record.get("step_id") in successful_creates
                    and record.get("tool") == "find_person"
                    and record.get("result", {}).get("error_code") == "NOT_FOUND"
                )
            ]
            extracted = (
                state.get("extracted_info", {})
                if goal["goal_id"] == state.get("active_goal_id")
                else goal.get("extracted_info", {})
            )
            ok, evidence = completion_evidence(goal, records, extracted)
            goal.update(
                status="SUCCEEDED" if ok else "BLOCKED",
                failure_reason=None if ok else "任务预算已耗尽",
                tool_calls=calls,
                tool_results=records,
                result=[
                    record["result"]
                    for record in records
                    if record.get("status") in {"SUCCESS", "RESOLVED"}
                    and isinstance(record.get("result"), dict)
                    and record["result"].get("success")
                ],
                completion_evidence=[record["call_id"] for record in evidence],
            )
    reason = (
        snapshot.get("exhausted_reason")
        or state.get("budget_reason")
        or "任务执行预算已耗尽"
    )
    lines = [reason + "。已确认成功的操作已保留，其余步骤已停止。"]
    labels = {
        "SUCCEEDED": "已完成",
        "FAILED": "未完成",
        "BLOCKED": "未继续执行",
        "CANCELLED": "已取消",
    }
    from backend.agent.streaming import TOOL_LABELS

    for index, goal in enumerate(goals):
        text = f"{index + 1}. {goal['description']}：{labels.get(goal['status'], goal['status'])}"
        if goal["status"] != "SUCCEEDED":
            writes = [
                TOOL_LABELS.get(record["tool"], record["tool"])
                for record in goal.get("tool_results", [])
                if record["tool"] in BUSINESS_IDEMPOTENT_TOOLS
                and record.get("status") == "SUCCESS"
            ]
            if writes:
                text += "（其中已完成：" + "、".join(writes) + "）"
        lines.append(text)
    lines.extend(warnings)
    return Command(
        update={
            "goals": goals,
            "execution_decision": "BUDGET_EXCEEDED",
            "budget_snapshot": snapshot,
            "response": "\n".join(lines),
        },
        goto="finalize_memory",
    )
