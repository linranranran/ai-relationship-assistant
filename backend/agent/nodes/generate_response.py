# ============================================================
# 回复生成节点
# ============================================================

import json
from backend.agent.state import AgentState
from backend.agent.prompts.system_prompts import GENERATE_RESPONSE_PROMPT
from backend.services.llm import get_llm_client, chat

LLM_CLIENT = get_llm_client()


def generate_response(state: AgentState) -> AgentState:
    """
    根据执行结果生成自然语言回复。

    输入：state["intent"], state["user_input"], state["tool_results"]
    输出：state["response"]
    """

    prompt = GENERATE_RESPONSE_PROMPT.format(
        intent=state["intent"],
        user_input=state["user_input"],
        tool_results=state["tool_results"],
    )
    llm_client = get_llm_client()
    response = chat(llm_client, "", [{"role": "user", "content": prompt}])
    return {"response": response}