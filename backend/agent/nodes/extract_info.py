# ============================================================
# 信息提取节点
# ============================================================

import json
from backend.agent.state import AgentState
from backend.agent.prompts.system_prompts import EXTRACT_INFO_PROMPT
from backend.services.llm import get_llm_client, chat
from langgraph.types import Command


def extract_info(state: AgentState) -> Command:
    user_input = state["user_input"]
    update = {}

    try:
        llm_client = get_llm_client()
        response_str = chat(llm_client, EXTRACT_INFO_PROMPT, [{"role": "user", "content": user_input}])
        response = json.loads(response_str)
        update["extracted_info"] = response
    except Exception as e:
        update["extracted_info"] = {"message": f"提取信息失败：{str(e)}"}

    return Command(update=update, goto="plan_tasks")