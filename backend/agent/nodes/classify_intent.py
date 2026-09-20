# ============================================================
# 意图分类节点
# ============================================================

import json
from langgraph.types import Command
from backend.agent.state import AgentState
from backend.agent.prompts.system_prompts import INTENT_CLASSIFY_PROMPT
from backend.services.llm import get_llm_client, chat


EXTRACT_INFO_LIST = ["ADD_PERSON","UPDATE_PERSON","ADD_RELATION","UPDATE_RELATION"]
PLAN_TASKS_LIST = ["QUERY_PERSON", "QUERY_KINSHIP", "SEMANTIC_SEARCH","LIST_RELATIONS", "DELETE_PERSON", "DELETE_RELATION"]
def classify_intent(state: AgentState) -> Command:
    """
    分析用户输入，识别意图并决定下一步路由。

    输入：state["user_input"] + state["history"]
    输出：Command(update=state, goto="下一个节点")
    """
    #
    # 提示：
    # 1. 格式化 INTENT_CLASSIFY_PROMPT，填入对话历史
    # 2. 调用 chat() 发送给 LLM
    # 3. 解析 LLM 返回的 JSON：{"intent": "...", "confidence": 0.95, "reasoning": "..."}
    # 4. 填入 state["intent"] 和 state["intent_confidence"]
    # 5. 根据意图决定跳转：
    #    - confidence < 0.6 或 UNCLEAR → ask_clarification
    #    - CHITCHAT → generate_response
    #    - ADD_PERSON / UPDATE_PERSON / ADD_RELATION / UPDATE_RELATION → extract_info
    #    - 其他（QUERY_* / DELETE_* / SEMANTIC_SEARCH / LIST_RELATIONS）→ plan_tasks

    history = state["history"]
    user_input = state["user_input"]
    if user_input is None or user_input == "":
        state["intent"] = "UNCLEAR"
        state["intent_confidence"] = 0.0
        return Command(update=state, goto="ask_clarification")
    system_prompt = INTENT_CLASSIFY_PROMPT.format(history=history)
    llm_client = get_llm_client()
    response_str = chat(llm_client, system_prompt, [{"role": "user", "content": user_input}])
    try:
        response = json.loads(response_str)
    except json.JSONDecodeError:
        state["intent"] = "UNCLEAR"
        state["intent_confidence"] = 0.0
        return Command(update=state, goto="ask_clarification")
    user_intent = response["intent"]
    confidence = response["confidence"]
    state["intent"] = user_intent
    state["intent_confidence"] = float(confidence)
    if float(confidence) < 0.6:
        return Command(update=state, goto="ask_clarification")
    elif user_intent == "UNCLEAR":
        return Command(update=state, goto="ask_clarification")
    elif user_intent in EXTRACT_INFO_LIST:
        return Command(update=state, goto="extract_info")
    elif user_intent in PLAN_TASKS_LIST:
        return Command(update=state, goto="plan_tasks")
    else:
        return Command(update=state, goto="generate_response")