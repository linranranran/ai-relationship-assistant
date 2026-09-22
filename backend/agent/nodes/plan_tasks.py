# ============================================================
# 任务规划节点
# ============================================================

import json

from backend.agent.confirmation import (
    sanitize_model_tool_calls,
)
from backend.agent.state import AgentState
from backend.agent.prompts.system_prompts import PLAN_TASKS_PROMPT
from backend.tools.definitions import ALL_TOOL_DEFINITIONS
from backend.services.llm import get_llm_client, chat
from langgraph.types import Command
from backend.exception.AgentException import ToolPlanValidationError

def plan_tasks(state: AgentState) -> Command:
    user_id = state["user_id"]
    user_name = state["user_name"]
    intent = state["intent"]
    extracted_info = state.get("extracted_info", {})
    tool_descriptions = json.dumps(ALL_TOOL_DEFINITIONS)
    system_prompt = PLAN_TASKS_PROMPT.format(
        tool_descriptions=tool_descriptions,
        user_name=user_name,
        user_id=user_id,
        intent=intent,
        extracted_info=extracted_info,
    )

    try:
        llm_client = get_llm_client()
        response_str = chat(llm_client, system_prompt, [{"role": "user", "content": state["user_input"]}])
        response = json.loads(response_str)

        # 模型负责“提出计划”，但无权决定自己是否已经获得用户授权。
        # 所以这里忽略模型返回的 needs_confirmation / confirmed，统一由代码判断。
        tool_calls = sanitize_model_tool_calls(response.get("tool_calls", []))

        # 高风险判断移到 execute_tools：先执行安全查询并解析出真实 ID，
        # 再生成准确的确认摘要并 interrupt。
        return Command(
            update={
                "tool_calls": tool_calls,
                "tool_results": [],
                "execution_errors": [],
                "next_tool_index": 0,
                "needs_confirmation": False,
                "pending_confirmation": None,
            },
            goto="execute_tools",
        )
    except ToolPlanValidationError as e:
        return Command(
            update={
                "execution_errors": [str(e)],
                "response": "任务规划参数不完整，请补充信息。",
            },
            goto="ask_clarification",
        )
    except Exception as e:
        return Command(update={}, goto="ask_clarification")
