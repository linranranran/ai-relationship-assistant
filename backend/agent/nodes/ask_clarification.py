# ============================================================
# 追问节点
# ============================================================

from backend.agent.state import AgentState
from backend.agent.prompts.system_prompts import ASK_CLARIFICATION_PROMPT
from backend.services.llm import get_llm_client, chat

def ask_clarification(state: AgentState) -> AgentState:
    """
    当意图不明时，生成追问。

    输入：state["user_input"]
    输出：state["response"]
    """
    #
    # 提示：
    # 1. 格式化 ASK_CLARIFICATION_PROMPT，填入 user_input
    # 2. 调用 chat() 生成追问
    # 3. 存入 state["response"]

    user_input = state["user_input"]
    system_prompt = ASK_CLARIFICATION_PROMPT.format(user_input=user_input)
    llm_client = get_llm_client()
    response_str = chat(llm_client, system_prompt, [{"role": "user", "content": user_input}])
    state["response"] = response_str
    return state
