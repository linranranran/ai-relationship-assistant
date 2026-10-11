# ============================================================
# 信息提取节点
# ============================================================

import json
from backend.agent.state import AgentState
from backend.agent.prompts.system_prompts import EXTRACT_INFO_PROMPT
from backend.services.llm import get_llm_client, chat
from langgraph.types import Command


def _validate_extraction(result, goal):
    """防止目标拆好后，提取节点又扩展操作范围或丢失已明确的属性。"""
    from backend.agent.contracts import ModelBusinessValidationError

    if goal is None:
        return
    if goal["intent"] in {"ADD_PERSON", "UPDATE_PERSON"}:
        if result.relations:
            raise ModelBusinessValidationError(
                "EXTRACTION_SCOPE_VIOLATION", "当前是人物资料目标，relations 必须为空；关系由另一目标处理。"
            )
        # 用户 resume 后可能明确更正原属性。保留旧字段供模型参考，但不能用旧值
        # 否决新的用户补充；最终完成证明仍根据本轮提取值和工具真实回执核验。
        expected = {} if goal.get("clarifications") else (goal.get("person") or {})
        actual = result.primary_person.model_dump()
        missing = [
            key for key, value in expected.items()
            if value is not None and value != [] and not (key == "gender" and value == "未知")
            and actual.get(key) != value
        ]
        if missing:
            raise ModelBusinessValidationError(
                "PERSON_ATTRIBUTE_LOST",
                "提取结果必须保留结构化目标中的明确属性，请检查字段：" + ", ".join(missing),
                fields=missing,
            )
    elif goal.get("relation"):
        if len(result.relations) != 1:
            raise ModelBusinessValidationError(
                "EXTRACTION_SCOPE_VIOLATION", "当前关系目标只能提取一条关系，不得夹带其他关系。"
            )
        # 和人物属性一样，真实用户的补充可以更正关系类型/方向；旧草案不具有
        # 高于新用户输入的权限。补充后的唯一关系仍须经执行器校验人物归属。
        if goal.get("clarifications"):
            return
        expected = {key: value for key, value in goal["relation"].items() if value is not None}
        if not any(
            all(item.model_dump().get(key) == value for key, value in expected.items())
            for item in result.relations
        ):
            raise ModelBusinessValidationError(
                "RELATION_DIRECTION_LOST", "必须保留结构化目标中的关系双方、类型及方向。"
            )


def extract_info(state: AgentState) -> Command:
    if state.get("execution_version") == 2:
        from backend.agent.contracts import ExtractionResult
        from backend.agent.goal_policy import active_goal, goal_input
        from backend.services.structured_llm import invoke_structured

        goal = active_goal(state)
        # V2 已拆分人物与关系目标。旧通用提示词会让人物目标再次提取客户关系，
        # 所以显式覆盖提取范围，并在缓存前验证范围和属性保留情况。
        scope = (
            "当前仅提取人物资料。relations 返回 []。保留 person 中明确给出的姓名、职业、出生年份等属性。若用户最新补充明确更正属性，以最新补充为准。"
            if state["intent"] in {"ADD_PERSON", "UPDATE_PERSON"}
            else "当前仅提取本目标的唯一关系；不要添加其他关系。未被更正的双方和方向以结构化目标 relation 为准；若用户最新补充明确更正类型或方向，以最新补充为准。用户本人统一为当前用户。"
        )
        result = invoke_structured(
            ExtractionResult,
            EXTRACT_INFO_PROMPT + "\n=== 当前目标范围（优先于通用示例） ===\n" + scope,
            [{"role": "user", "content": goal_input(state)}],
            validator=lambda value: _validate_extraction(value, goal),
        )
        return Command(
            update={"extracted_info": result.model_dump(exclude_none=True)},
            goto="plan_tasks",
        )
    user_input = state["user_input"]
    update = {}

    try:
        llm_client = get_llm_client()
        response_str = chat(
            llm_client, EXTRACT_INFO_PROMPT, [{"role": "user", "content": user_input}]
        )
        response = json.loads(response_str)
        update["extracted_info"] = response
    except json.JSONDecodeError:
        return Command(
            update={"execution_errors": ["提取结果格式不正确，请补充更明确的信息"]},
            goto="ask_clarification",
        )
    # 模型/网络等系统异常继续抛出，以便从 extract_info 原节点恢复。

    return Command(update=update, goto="plan_tasks")
