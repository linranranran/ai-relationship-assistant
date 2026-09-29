# ============================================================
# 回复生成节点
# ============================================================

from backend.agent.memory.context_selector import select_context_for_node
from backend.agent.state import AgentState
from backend.agent.prompts.system_prompts import GENERATE_RESPONSE_PROMPT
from backend.services.llm import get_llm_client, chat

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
    messages = select_context_for_node(state, "generate_response")
    messages.append({"role": "user", "content": prompt})
    response = chat(llm_client, "", messages)
    return {"response": response}
