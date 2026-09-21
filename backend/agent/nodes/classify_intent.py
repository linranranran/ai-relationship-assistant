# ============================================================
# 意图分类节点
# ============================================================

import json
from langgraph.types import Command
from backend.agent.state import AgentState
from backend.agent.prompts.system_prompts import INTENT_CLASSIFY_PROMPT
from backend.services.llm import get_llm_client, chat


EXTRACT_INFO_LIST = ["ADD_PERSON", "UPDATE_PERSON", "ADD_RELATION", "UPDATE_RELATION"]
PLAN_TASKS_LIST = ["QUERY_PERSON", "QUERY_KINSHIP", "SEMANTIC_SEARCH", "LIST_RELATIONS", "DELETE_PERSON", "DELETE_RELATION"]


def classify_intent(state: AgentState) -> Command:
    user_input = state["user_input"]
    update = {}

    if not user_input or not user_input.strip():
        update["intent"] = "UNCLEAR"
        update["intent_confidence"] = 0.0
        return Command(update=update, goto="ask_clarification")

    system_prompt = INTENT_CLASSIFY_PROMPT.format(history=state["history"])
    llm_client = get_llm_client()
    response_str = chat(llm_client, system_prompt, [{"role": "user", "content": user_input}])

    try:
        response = json.loads(response_str)
    except json.JSONDecodeError:
        update["intent"] = "UNCLEAR"
        update["intent_confidence"] = 0.0
        return Command(update=update, goto="ask_clarification")

    user_intent = response["intent"]
    confidence = float(response["confidence"])
    update["intent"] = user_intent
    update["intent_confidence"] = confidence

    if confidence < 0.6 or user_intent == "UNCLEAR":
        return Command(update=update, goto="ask_clarification")
    elif user_intent in EXTRACT_INFO_LIST:
        return Command(update=update, goto="extract_info")
    elif user_intent in PLAN_TASKS_LIST:
        return Command(update=update, goto="plan_tasks")
    else:
        return Command(update=update, goto="generate_response")