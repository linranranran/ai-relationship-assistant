# ============================================================
# Agent 状态图 —— LangGraph 编排
# 这是整个 Agent 的"大脑"，连接所有节点
# ============================================================

from threading import Lock

from langgraph.graph import StateGraph
from backend.agent.checkpointing import create_checkpointer_handle
from backend.agent.state import AgentState
from backend.agent.nodes.classify_intent import classify_intent
from backend.agent.nodes.extract_info import extract_info
from backend.agent.nodes.plan_tasks import plan_tasks
from backend.agent.nodes.execute_tools import execute_tools
from backend.agent.nodes.observe_execution import observe_execution
from backend.agent.nodes.generate_response import generate_response
from backend.agent.nodes.ask_clarification import ask_clarification
from backend.agent.nodes.request_confirmation import request_confirmation
from backend.agent.nodes.prepare_context import prepare_context
from backend.agent.nodes.finalize_memory import finalize_memory
from backend.db.postgres_client import build_postgres_conninfo
from backend.config import (
    AGENT_CHECKPOINTER_BACKEND,
    AGENT_CHECKPOINT_POOL_MAX_SIZE,
    AGENT_CHECKPOINT_POOL_MIN_SIZE,
    AGENT_CHECKPOINT_POOL_TIMEOUT_SECONDS,
)


def build_agent_graph(checkpointer=None):
    """
    构建并编译 Agent 状态图。

    路由策略：Command 模式 — 每个节点内部用 Command(update, goto) 决定跳转，
    graph 只定义默认边提供兜底。

    节点流转（默认边 → Command 可覆盖）：
    prepare_context       → classify_intent （构造受 Token 预算约束的三层记忆上下文）
    classify_intent       → extract_info    （Command 可跳到 ask_clarification / plan_tasks / generate_response）
    extract_info          → plan_tasks      （无条件）
    plan_tasks            → execute_tools / ask_clarification
    request_confirmation  → execute_tools / END
    execute_tools         → observe_execution
    observe_execution     → generate_response / plan_tasks / ask_clarification / END
    generate_response     → finalize_memory （保存本轮短期消息并预留摘要/事实扩展点）
    finalize_memory       → END
    ask_clarification     → execute_tools / prepare_context / END（interrupt 后恢复）
    """
    graph = StateGraph(AgentState)

    # ── 注册节点 ──
    graph.add_node("prepare_context", prepare_context)
    graph.add_node("classify_intent", classify_intent)
    graph.add_node("extract_info", extract_info)
    graph.add_node("plan_tasks", plan_tasks)
    graph.add_node("execute_tools", execute_tools)
    graph.add_node("observe_execution", observe_execution)
    graph.add_node("generate_response", generate_response)
    graph.add_node("ask_clarification", ask_clarification)
    graph.add_node("request_confirmation", request_confirmation)
    graph.add_node("finalize_memory", finalize_memory)

    # ── 入口 ──
    graph.set_entry_point("prepare_context")
    graph.add_edge("prepare_context", "classify_intent")
    graph.add_edge("generate_response", "finalize_memory")

    # ── 纯 Command 路由，不加默认边以免和 Command goto 并行 ──
    # 每个节点通过 Command(update, goto) 自行决定下一步
    # finalize_memory 是普通终端节点；两个交互节点使用 interrupt 暂停，恢复后
    # 再由各自的 Command.goto 决定后续路径。

    return graph.compile(checkpointer=checkpointer)


# 不在 import 阶段连接数据库。FastAPI lifespan 会调用 initialize_agent_runtime，
# 这样配置或网络错误会作为“启动失败”暴露，测试导入模块也不会访问远程数据库。
_checkpoint_handle = None
_agent_graph = None
_runtime_lock = Lock()


def initialize_agent_runtime():
    """初始化一次进程级 Checkpointer 和编译后的 Agent 图。"""
    global _checkpoint_handle, _agent_graph
    with _runtime_lock:
        if _agent_graph is not None:
            return _agent_graph

        handle = create_checkpointer_handle(
            backend=AGENT_CHECKPOINTER_BACKEND,
            postgres_dsn=build_postgres_conninfo(),
            pool_min_size=AGENT_CHECKPOINT_POOL_MIN_SIZE,
            pool_max_size=AGENT_CHECKPOINT_POOL_MAX_SIZE,
            pool_timeout_seconds=AGENT_CHECKPOINT_POOL_TIMEOUT_SECONDS,
        )
        try:
            compiled_graph = build_agent_graph(checkpointer=handle.saver)
        except Exception:
            handle.close()
            raise

        _checkpoint_handle = handle
        _agent_graph = compiled_graph
        return _agent_graph


def get_agent_graph():
    """取得运行图；兼容脚本直接调用时的懒初始化。"""
    return _agent_graph or initialize_agent_runtime()


def close_agent_runtime() -> None:
    """由 FastAPI lifespan 在进程退出时释放 Checkpointer 资源。"""
    global _checkpoint_handle, _agent_graph
    with _runtime_lock:
        if _checkpoint_handle is not None:
            _checkpoint_handle.close()
        _checkpoint_handle = None
        _agent_graph = None


def get_checkpointer_backend() -> str:
    """仅暴露存储类型供健康检查/排障，不暴露文件路径或数据库 DSN。"""
    if _checkpoint_handle is not None:
        return _checkpoint_handle.backend.value
    return AGENT_CHECKPOINTER_BACKEND.strip().lower()
