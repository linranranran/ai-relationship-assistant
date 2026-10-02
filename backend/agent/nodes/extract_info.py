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
    except json.JSONDecodeError:
        return Command(update={"execution_errors": ["提取结果格式不正确，请补充更明确的信息"]},
                       goto="ask_clarification")
    # 模型/网络等系统异常继续抛出，以便从 extract_info 原节点恢复。

    return Command(update=update, goto="plan_tasks")
