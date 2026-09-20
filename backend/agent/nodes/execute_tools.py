# ============================================================
# Tool 执行节点
# ============================================================

import logging
from backend.agent.state import AgentState
from backend.tools.registry import execute_tool
from langgraph.types import Command

logger = logging.getLogger(__name__)

def execute_tools(state: AgentState) -> Command:
    """
    按顺序执行 tool_calls，自动注入 owner_id。

    执行后路由：
    - 全部成功 → generate_response
    - 部分成功 → generate_response（附带错误）
    - 全部失败 → ask_clarification（重试耗尽，通知用户）
    """
    tool_calls = list(state["tool_calls"])
    results = []
    execution_errors = []

    for tool_call in tool_calls:
        tool_name = tool_call["tool"]
        args = dict(tool_call["args"])
        args["owner_id"] = state["user_id"]

        # ── 重试逻辑：只对临时性错误重试一次 ──
        for attempt in range(2):
            result = execute_tool(tool_name, **args)

            if result.get("success"):
                results.append(result)
                break

            # 业务错误（缺参数、重复创建）不重试
            msg = result.get("msg", "")
            if "缺少" in msg or "已存在" in msg:
                execution_errors.append(result)
                break

            # 临时错误，第一次失败时重试
            if attempt == 1:
                logger.error(f"{tool_name} 重试仍失败: {msg}")
                execution_errors.append(result)

    state["tool_results"] = results
    state["execution_errors"] = execution_errors

    # ── 路由 ──
    if not results and execution_errors:
        # 全部失败 → 追问
        return Command(update=state, goto="ask_clarification")
    # 全部成功或部分成功 → 生成回复
    return Command(update=state, goto="generate_response")