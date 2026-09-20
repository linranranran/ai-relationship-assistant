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
    """
    根据意图和提取的信息，规划 Tool 调用序列。

    输入：state["intent"], state["extracted_info"], state["user_id"], state["user_name"]
    输出：state["tool_calls"], state["needs_confirmation"]
    """
    #
    # 提示：
    # 1. 把 ALL_TOOL_DEFINITIONS 转成 JSON 字符串，嵌入 PLAN_TASKS_PROMPT
    # 2. 填入 intent, extracted_info, user_id, user_name
    # 3. 调用 chat() 获取规划结果
    # 4. 解析 JSON，填入 state["tool_calls"] 和 state["needs_confirmation"]
    # 5. 注意：tool_calls 中的 args 需要可直接传给 execute_tool()
    # 6. 自动注入 user_id 到需要它的 Tool args 中
    user_id = state["user_id"]
    user_name = state["user_name"]
    intent = state["intent"]
    extracted_info = state["extracted_info"]
    tool_descriptions = json.dumps(ALL_TOOL_DEFINITIONS)
    system_prompt = PLAN_TASKS_PROMPT.format(tool_descriptions=tool_descriptions,user_name=user_name,user_id=user_id,intent=intent,extracted_info=extracted_info)
    try:
        llm_client = get_llm_client()
        response_str = chat(llm_client, system_prompt, [{"role": "user", "content": state["user_input"]}])
        response = json.loads(response_str)

        tool_calls = response.get("tool_calls")
        needs_confirmation = response.get("needs_confirmation")
        state["tool_calls"] = tool_calls
        state["needs_confirmation"] = needs_confirmation
        # state["reasoning"] = response.get("reasoning")
        return Command(update=state, goto="execute_tools")
    except Exception as e:
        state["response"] = f"无法确定确认任务需求，请补充信息。失败原因：{str(e)}"
        return Command(update=state, goto="ask_clarification")
