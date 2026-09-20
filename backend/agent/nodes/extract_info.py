# ============================================================
# 信息提取节点
# ============================================================

import json
from backend.agent.state import AgentState
from backend.agent.prompts.system_prompts import EXTRACT_INFO_PROMPT
from backend.services.llm import get_llm_client, chat
from langgraph.types import Command

def extract_info(state: AgentState) -> AgentState:
    """
    从用户输入中提取结构化信息。

    输入：state["user_input"]
    输出：state["extracted_info"]
    """
    # TO-DO: 实现
    #
    # 提示：
    # 1. 用 EXTRACT_INFO_PROMPT 作为 system prompt
    # 2. 把用户输入作为 user message
    # 3. 调用 chat()，获取 LLM 返回的 JSON
    # 4. 解析 JSON 存入 state["extracted_info"]
    # 5. 如果解析失败，设置 extracted_info = {} 并记录错误

    user_input = state["user_input"]
    try:
        llm_client = get_llm_client()
        response_str = chat(llm_client, EXTRACT_INFO_PROMPT, [{"role": "user", "content": user_input}])
        response = json.loads(response_str)
        state["extracted_info"] = response
        return Command(update=state, goto="plan_tasks")
    except Exception as e:
        state["extracted_info"] = {"message": f"提取信息失败，失败原因：{str(e)}"}
        return Command(update=state, goto="plan_tasks")

