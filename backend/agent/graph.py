# ============================================================
# Agent 状态图 —— LangGraph 编排
# 这是整个 Agent 的"大脑"，连接所有节点
# ============================================================

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph
from backend.agent.state import AgentState
from backend.agent.nodes.classify_intent import classify_intent
from backend.agent.nodes.extract_info import extract_info
from backend.agent.nodes.plan_tasks import plan_tasks
from backend.agent.nodes.execute_tools import execute_tools
from backend.agent.nodes.observe_execution import observe_execution
from backend.agent.nodes.generate_response import generate_response
from backend.agent.nodes.ask_clarification import ask_clarification
from backend.agent.nodes.request_confirmation import request_confirmation


def build_agent_graph(checkpointer=None):
    """
    构建并编译 Agent 状态图。

    路由策略：Command 模式 — 每个节点内部用 Command(update, goto) 决定跳转，
    graph 只定义默认边提供兜底。

    节点流转（默认边 → Command 可覆盖）：
    classify_intent       → extract_info    （Command 可跳到 ask_clarification / plan_tasks / generate_response）
    extract_info          → plan_tasks      （无条件）
    plan_tasks            → execute_tools / ask_clarification
    request_confirmation  → execute_tools / END
    execute_tools         → observe_execution
    observe_execution     → generate_response / plan_tasks / ask_clarification / END
    generate_response     → END             （终端节点，无出边）
    ask_clarification     → execute_tools / classify_intent / END（interrupt 后恢复）
    """
    graph = StateGraph(AgentState)

    # ── 注册节点 ──
    graph.add_node("classify_intent", classify_intent)
    graph.add_node("extract_info", extract_info)
    graph.add_node("plan_tasks", plan_tasks)
    graph.add_node("execute_tools", execute_tools)
    graph.add_node("observe_execution", observe_execution)
    graph.add_node("generate_response", generate_response)
    graph.add_node("ask_clarification", ask_clarification)
    graph.add_node("request_confirmation", request_confirmation)

    # ── 入口 ──
    graph.set_entry_point("classify_intent")

    # ── 纯 Command 路由，不加默认边以免和 Command goto 并行 ──
    # 每个节点通过 Command(update, goto) 自行决定下一步
    # generate_response 是普通终端节点；两个交互节点使用 interrupt 暂停，
    # 恢复后再由各自的 Command.goto 决定后续路径。

    return graph.compile(checkpointer=checkpointer)


# 编译后的 Agent 实例（供 router 调用）
# 第一版用内存检查点：服务重启后，尚未确认的任务会丢失。
# TODO(你来实现)：进入持久化阶段后替换为 SqliteSaver/PostgresSaver，
# 并增加过期时间、幂等键和审计日志。图和节点代码不需要因此重写。
agent_graph = build_agent_graph(checkpointer=InMemorySaver())
