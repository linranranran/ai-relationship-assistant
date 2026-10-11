"""目标完成策略是确定性代码；LLM 提出目标，不能自行宣布业务已经完成。"""

from copy import deepcopy
from uuid import NAMESPACE_URL, uuid5
import json
import re
from backend.agent.contracts import GoalStatus, ModelBusinessValidationError
from backend.agent.budget import BudgetLimits, current_budget


# 每个意图最多允许这些工具；查询目标不能夹带新增/删除。
READ_TOOLS = {
    "find_person",
    "get_person_detail",
    "list_relations",
    "query_relation_path",
    "get_kinship_title",
}
ALLOWED_TOOLS = {
    "ADD_PERSON": {"find_person", "add_person", "update_person", "get_person_detail"},
    "UPDATE_PERSON": {"find_person", "update_person", "get_person_detail"},
    "DELETE_PERSON": {"find_person", "delete_person", "get_person_detail"},
    "ADD_RELATION": READ_TOOLS | {"add_person", "add_relation"},
    "UPDATE_RELATION": READ_TOOLS | {"update_relation"},
    "DELETE_RELATION": READ_TOOLS | {"delete_relation"},
    "QUERY_PERSON": {"find_person", "get_person_detail", "query_relation_path"},
    "QUERY_KINSHIP": {"find_person", "query_relation_path", "get_kinship_title"},
    "SEMANTIC_SEARCH": {"find_person"},
    "LIST_RELATIONS": {"find_person", "list_relations"},
    "CHITCHAT": set(),
    "UNCLEAR": set(),
    "UNSUPPORTED": set(),
}
REQUIRED_TOOLS = {
    "ADD_PERSON": {"add_person", "update_person"},
    "UPDATE_PERSON": {"update_person"},
    "DELETE_PERSON": {"delete_person"},
    "ADD_RELATION": {"add_relation"},
    "UPDATE_RELATION": {"update_relation"},
    "DELETE_RELATION": {"delete_relation"},
    "QUERY_PERSON": {"get_person_detail", "query_relation_path"},
    "QUERY_KINSHIP": {"get_kinship_title"},
    "SEMANTIC_SEARCH": {"find_person"},
    "LIST_RELATIONS": {"list_relations"},
}


def build_goals(batch, state, *, require_business_fields=False):
    """验证目标草案；不按姓名自动合并，也不替模型补造用户未要求的操作。

    新分类结果必须包含人物/关系字段。旧 Checkpoint 的读取不走这一要求。
    这些校验只能覆盖明确的结构和拆分矛盾，不能证明模型理解了所有自然语言。
    """
    drafts = batch.goals
    scope = current_budget()
    limit = (
        scope["store"].snapshot()["limits"]["goals"] if scope else BudgetLimits().goals
    )
    if len(drafts) > limit:
        raise ModelBusinessValidationError("GOAL_LIMIT", "请求包含过多目标，请拆分提问。")
    goals = []
    seen = set()
    for index, draft in enumerate(drafts):
        if require_business_fields:
            validate_goal_business_fields(draft, drafts)
        fingerprint = (draft.intent, draft.description.strip())
        if fingerprint in seen:
            raise ModelBusinessValidationError("GOAL_DUPLICATE", "同一业务目标重复出现，请删除重复目标。")
        seen.add(fingerprint)
        if draft.source_quote not in state["user_input"]:
            raise ModelBusinessValidationError("GOAL_QUOTE_INVALID", "source_quote 必须是当前用户输入的连续原文。")
        if any(
            isinstance(dep, bool) or dep < 0 or dep >= index for dep in draft.depends_on
        ):
            raise ModelBusinessValidationError("GOAL_DEPENDENCY_INVALID", "目标只能依赖更早目标，不允许循环或未知依赖。")
        goal_id = (
            "goal_"
            + uuid5(
                NAMESPACE_URL, f"{state['user_id']}:{state['request_id']}:{index}"
            ).hex
        )
        goals.append(
            {
                **draft.model_dump(),
                "goal_id": goal_id,
                "depends_on": [goals[dep]["goal_id"] for dep in draft.depends_on],
                "status": GoalStatus.PENDING.value,
                "plan_revision": 0,
                "auto_replans": 0,
                "tool_calls": [],
                "tool_results": [],
                "result": [],
                "completion_evidence": [],
                "failure_reason": None,
                "clarifications": [],
            }
        )
    return goals


def validate_goal_business_fields(draft, drafts):
    if draft.intent in {"ADD_PERSON", "UPDATE_PERSON"}:
        if draft.person is None or (
            draft.intent == "ADD_PERSON" and not draft.person.name
        ):
            raise ModelBusinessValidationError(
                "GOAL_PERSON_MISSING", "人物目标必须在 person 中保留姓名及明确属性。"
            )
        # 只检查“同名人物 + 显式前置新增目标 + 没有独立修改指令”的组合。
        # 姓名不是实体 ID，不能把任意同名目标直接合并；这里退回模型重新拆分。
        if draft.intent == "UPDATE_PERSON" and draft.person.name:
            for dep in draft.depends_on:
                if isinstance(dep, bool) or dep < 0 or dep >= len(drafts):
                    continue  # 通用依赖校验会拒绝它，不在这里用错误下标访问数组。
                preceding = drafts[dep]
                same_creation = (
                    preceding.intent == "ADD_PERSON"
                    and preceding.person is not None
                    and preceding.person.name == draft.person.name
                )
                explicit_update = re.search(
                    r"修改|更新|改成|改为|更正|调整|设为|设成|设置|补充|补上|先.+再",
                    draft.source_quote,
                )
                # 局部 source_quote 可能不含整句里的“然后”；不能把“没看到修改词”
                # 当作没有独立动作。只拒绝形如“生日是1998年”的纯属性片段。
                # 这是保守的语义防线，并非通用自然语言解析器。
                attribute_fragment = re.fullmatch(
                    r"(?:他的|她的|其|他|她)?"
                    r"(?:生日|出生年份|出生年|出生于|年龄|职业|爱好|喜好|学历|毕业院校|公司|学校|性格)"
                    r"(?:是|为|在|于)?[^，,。；;！？!?]+[。！？!?]?",
                    draft.source_quote.strip(),
                )
                if same_creation and attribute_fragment and not explicit_update:
                    raise ModelBusinessValidationError(
                        "GOAL_ATTRIBUTE_SPLIT",
                        "依赖新增人物的属性描述不应另拆 UPDATE_PERSON。"
                        "请检查原文：创建时提供的职业、出生年份等放入同一个 ADD_PERSON；"
                        "独立修改指令才保留 UPDATE_PERSON。不要仅凭同名合并不同人物。",
                    )
    if draft.intent in {"ADD_RELATION", "UPDATE_RELATION"} and draft.relation is None:
        raise ModelBusinessValidationError(
            "GOAL_RELATION_MISSING", "关系目标必须在 relation 中保留双方和关系类型。"
        )


def active_goal(state):
    return next(
        (
            goal
            for goal in state.get("goals", [])
            if goal["goal_id"] == state.get("active_goal_id")
        ),
        None,
    )


def goal_input(state):
    goal = active_goal(state)
    if goal is None:
        return state["user_input"]
    # 目标原文也保留，避免模型拆解描述时丢失用户明确指定的爱好、职业等参数。
    return (
        f"当前目标：{goal['description']}\n原文：{goal['source_quote']}\n"
        + "结构化目标字段："
        + json.dumps(
            {key: goal.get(key) for key in ("person", "relation")},
            ensure_ascii=False,
        )
        + "\n"
        + "\n".join(goal.get("clarifications", []))
    )


def goal_state(state):
    """兼容现有单目标节点；只替换其本轮输入，不改最终入库的原始用户消息。"""
    goal = active_goal(state)
    if goal is None:
        return state
    result = {**state, "user_input": goal_input(state)}
    # 原始整段请求不能再次指挥规划器执行其他目标。历史与记忆仍可辅助解析。
    result["model_context"] = [
        item
        for item in state.get("model_context", [])
        if not (
            item.get("role") == "user" and item.get("content") == state["user_input"]
        )
    ]
    return result


def validate_plan_scope(state, tool_calls):
    goal = active_goal(state)
    if goal is None:
        return
    allowed = ALLOWED_TOOLS[goal["intent"]]
    rejected = sorted({call["tool"] for call in tool_calls if call["tool"] not in allowed})
    if rejected:
        raise ModelBusinessValidationError(
            "TOOL_SCOPE_VIOLATION",
            f"当前意图 {goal['intent']} 不能调用 {', '.join(rejected)}；"
            f"只允许 {', '.join(sorted(allowed))}。请删除越界操作，不要更改当前目标。",
            intent=goal["intent"],
            rejected_tools=rejected,
        )
    if goal["intent"] in {"ADD_RELATION", "UPDATE_RELATION", "DELETE_RELATION"}:
        relation_writes = [
            call for call in tool_calls
            if call["tool"] in {"add_relation", "update_relation", "delete_relation"}
        ]
        # 目标只描述一条关系。即使工具都在白名单里，也不能夹带第二条关系写入。
        if len(relation_writes) > 1:
            raise ModelBusinessValidationError(
                "RELATION_PLAN_SCOPE_VIOLATION", "当前目标只允许一条关系写入，请删除额外关系操作。"
            )
        relations = state.get("extracted_info", {}).get("relations", [])
        if relation_writes and len(relations) == 1:
            requested_type = relations[0].get("type")
            type_field = (
                "new_relation_type"
                if relation_writes[0]["tool"] == "update_relation"
                else "relation_type"
            )
            planned_type = relation_writes[0]["args"].get(type_field)
            # 删除工具没有 relation_type；类型只检查真实字符串，动态引用由执行器解析。
            if isinstance(planned_type, str) and requested_type and planned_type != requested_type:
                raise ModelBusinessValidationError(
                    "RELATION_TYPE_MISMATCH", "关系工具的 relation_type 必须与当前提取的关系类型一致。"
                )


def completion_evidence(goal, records, extracted_info=None):
    """只承认成功工具回执。部分工具成功不能推出整个目标成功。"""
    successes = [
        record
        for record in records
        if record.get("status") in {"SUCCESS", "RESOLVED"}
        and isinstance(record.get("result"), dict)
        and record["result"].get("success")
    ]
    required = REQUIRED_TOOLS.get(goal["intent"], set())
    evidence = [record for record in successes if record["tool"] in required]
    if not required:
        return goal["intent"] == "CHITCHAT", []
    ok = bool(evidence) and not any(
        record.get("status") in {"FAILED", "SKIPPED"} for record in records
    )
    if ok and goal["intent"] in {"ADD_PERSON", "UPDATE_PERSON"} and extracted_info:
        expected = extracted_info.get("primary_person", {})
        # 只取得 ID 不能证明明确的属性已保存；现有人物复用分支也必须核验这些字段。
        attrs = {
            key: value
            for key, value in expected.items()
            if value is not None
            and value != []
            and not (key == "gender" and value == "未知")
        }
        if attrs:
            ok = any(
                isinstance(record["result"].get("data"), dict)
                and all(
                    record["result"]["data"].get(key) == value
                    for key, value in attrs.items()
                )
                for record in evidence
            )
    return ok, evidence


def replace_goal(state, **changes):
    goals = deepcopy(state.get("goals", []))
    for goal in goals:
        if goal["goal_id"] == state.get("active_goal_id"):
            goal.update(changes)
    return goals


def archive_current_plan(state):
    """换计划前归档成功事实；失败步骤不累加到新计划，否则修复后仍会被判失败。"""
    goal = active_goal(state)
    if goal is None:
        return state.get("goals", [])
    calls = {call["call_id"]: call for call in goal.get("previous_tool_calls", [])}
    calls.update({call["call_id"]: call for call in state.get("tool_calls", [])})
    records = {
        record["call_id"]: record for record in goal.get("previous_tool_results", [])
    }
    records.update(
        {
            record["call_id"]: record
            for record in state.get("tool_results", [])
            if record.get("status") in {"SUCCESS", "RESOLVED"}
        }
    )
    return replace_goal(
        state,
        previous_tool_calls=list(calls.values()),
        previous_tool_results=list(records.values()),
    )


def accumulated_records(goal, state):
    calls = {call["call_id"]: call for call in goal.get("previous_tool_calls", [])}
    calls.update({call["call_id"]: call for call in state.get("tool_calls", [])})
    records = {
        record["call_id"]: record for record in goal.get("previous_tool_results", [])
    }
    records.update(
        {record["call_id"]: record for record in state.get("tool_results", [])}
    )
    return list(calls.values()), list(records.values())
