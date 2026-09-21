# ============================================================
# Tool 执行节点
# ============================================================

import logging
from backend.agent.state import AgentState
from backend.tools.registry import execute_tool
from langgraph.types import Command

logger = logging.getLogger(__name__)


def execute_tools(state: AgentState) -> Command:
    tool_calls = list(state.get("tool_calls", []))
    results = []
    execution_errors = []

    for tool_call in tool_calls:
        tool_name = tool_call["tool"]
        args = dict(tool_call["args"])
        args["owner_id"] = state["user_id"]

        for attempt in range(2):
            result = execute_tool(tool_name, **args)

            if result.get("success"):
                results.append(result)
                break

            msg = result.get("msg", "")
            if "缺少" in msg or "已存在" in msg:
                execution_errors.append(result)
                break

            if attempt == 1:
                logger.error(f"{tool_name} 重试仍失败: {msg}")
                execution_errors.append(result)

    update = {
        "tool_results": results,
        "execution_errors": execution_errors,
    }

    if not results and execution_errors:
        return Command(update=update, goto="ask_clarification")
    return Command(update=update, goto="generate_response")