# ============================================================
# 任务规划节点
# ============================================================

import json
from backend.agent.state import AgentState
from backend.agent.prompts.system_prompts import PLAN_TASKS_PROMPT
from backend.tools.definitions import ALL_TOOL_DEFINITIONS
from backend.services.llm import get_llm_client, chat
from langgraph.types import Command


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

        return Command(
            update={
                "tool_calls": response.get("tool_calls"),
                "needs_confirmation": response.get("needs_confirmation"),
            },
            goto="execute_tools",
        )
    except Exception as e:
        return Command(update={}, goto="ask_clarification")