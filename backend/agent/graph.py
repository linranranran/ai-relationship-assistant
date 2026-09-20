# ============================================================
# Agent 状态图 —— LangGraph 编排
# 这是整个 Agent 的"大脑"，连接所有节点
# ============================================================

from langgraph.graph import StateGraph, END
from backend.agent.state import AgentState
from backend.agent.nodes.classify_intent import classify_intent
from backend.agent.nodes.extract_info import extract_info
from backend.agent.nodes.plan_tasks import plan_tasks
from backend.agent.nodes.execute_tools import execute_tools
from backend.agent.nodes.generate_response import generate_response
from backend.agent.nodes.ask_clarification import ask_clarification


def build_agent_graph() -> StateGraph:
    """
    构建并编译 Agent 状态图。

    路由策略：Command 模式 — 每个节点内部用 Command(update, goto) 决定跳转，
    graph 只定义默认边提供兜底。

    节点流转（默认边 → Command 可覆盖）：
    classify_intent       → extract_info    （Command 可跳到 ask_clarification / plan_tasks / generate_response）
    extract_info          → plan_tasks      （无条件）
    plan_tasks            → execute_tools   （Command 可跳到 ask_clarification）
    execute_tools         → generate_response （Command 可跳到 ask_clarification）
    generate_response     → END             （终端节点，无出边）
    ask_clarification     → END             （终端节点，无出边）
    """
    graph = StateGraph(AgentState)

    # ── 注册节点 ──
    graph.add_node("classify_intent", classify_intent)
    graph.add_node("extract_info", extract_info)
    graph.add_node("plan_tasks", plan_tasks)
    graph.add_node("execute_tools", execute_tools)
    graph.add_node("generate_response", generate_response)
    graph.add_node("ask_clarification", ask_clarification)

    # ── 入口 ──
    graph.set_entry_point("classify_intent")

    # ── 默认边（Command 可动态覆盖）──
    graph.add_edge("classify_intent", "extract_info")
    graph.add_edge("extract_info", "plan_tasks")
    graph.add_edge("plan_tasks", "execute_tools")
    graph.add_edge("execute_tools", "generate_response")
    # generate_response 和 ask_clarification 无出边 → 自动 END

    return graph.compile()


# 编译后的 Agent 实例（供 router 调用）
agent_graph = build_agent_graph()