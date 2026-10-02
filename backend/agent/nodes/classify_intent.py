# ============================================================
# 意图分类节点
# ============================================================

import json

from langgraph.types import Command
from backend.agent.memory.context_selector import select_context_for_node
from backend.agent.state import AgentState
from backend.agent.prompts.system_prompts import INTENT_CLASSIFY_PROMPT
from backend.services.llm import get_llm_client, chat


EXTRACT_INFO_LIST = ["ADD_PERSON", "UPDATE_PERSON", "ADD_RELATION", "UPDATE_RELATION"]
PLAN_TASKS_LIST = ["QUERY_PERSON", "QUERY_KINSHIP", "SEMANTIC_SEARCH", "LIST_RELATIONS", "DELETE_PERSON", "DELETE_RELATION"]

import logging
logger = logging.getLogger(__name__)

def classify_intent(state: AgentState) -> Command:
    user_input = state["user_input"]
    update = {}

    if not user_input or not user_input.strip():
        update["intent"] = "UNCLEAR"
        update["intent_confidence"] = 0.0
        return Command(update=update, goto="ask_clarification")

    # 历史消息可能包含客户原话，不能把它拼进 system prompt，否则其中的
    # “忽略规则”等文本会被意外提升为高权限指令。系统层只声明历史由后续
    # 普通消息提供，真实历史仍保留各自的 user/assistant 角色。
    system_prompt = INTENT_CLASSIFY_PROMPT.format(
        history="历史对话由后续普通消息提供；它们只用于判断当前意图。"
    )
    messages = select_context_for_node(state, "classify_intent")
    messages.append({"role": "user", "content": user_input})
    llm_client = get_llm_client()
    response_str = chat(llm_client, system_prompt, messages)

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

    logger.info(f"2.1、LLM识别用户意图结果，user_intent= {user_intent}，confidence={confidence}")
    if confidence < 0.6 or user_intent == "UNCLEAR":
        return Command(update=update, goto="ask_clarification")
    elif user_intent in EXTRACT_INFO_LIST:
        return Command(update=update, goto="extract_info")
    elif user_intent in PLAN_TASKS_LIST:
        return Command(update=update, goto="plan_tasks")
    else:
        return Command(update=update, goto="generate_response")
