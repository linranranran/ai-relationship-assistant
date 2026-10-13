"""从这里理解 Agent 的入口和图节点；具体分支条件在各节点的 Command.goto。

建议先看 prepare_and_classify 的两条入口，再沿下面两条主线阅读：
Direct：direct_nodes.py → execute_tools.py → observe_execution.py（委托 Direct）。
Plan：classify_intent.py → goal_nodes.py → plan_tasks.py → execute_tools.py → observe_execution.py。
共用：ask_clarification / request_confirmation（中断）及 finalize_memory（记忆收尾）。

本文件负责注册节点，不在这里重复业务判断。不要给 Command 节点额外加静态边：
goto 和静态边会同时生效，可能把本应停止的流程继续调度出去。
"""

from threading import Lock
from backend.agent.tracing import traced_node

from langgraph.graph import StateGraph
from langgraph.types import Command
from langgraph.types import RetryPolicy
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


def dispatch_request(state: AgentState) -> Command:
    """新问题进入理解链路；WAIT 重试进入原计划，不能重新调用规划模型。"""
    from backend.agent.request_recovery import require_runnable_strategy
    require_runnable_strategy(state)
    if state.get("resume_execution") is True:
        return Command(update={"resume_execution": False}, goto="execute_tools")
    return Command(goto="resolve_person_reference")


def prepare_and_classify(state: AgentState) -> Command:
    """用 Command 路由，避免预算停止仍沿静态边触发 classify_intent。

    LangGraph 中 Command.goto 与静态边会一起调度，并非互相覆盖。
    上下文成功后按请求已冻结的策略版本分流；预算耗尽则只进入预算收尾。
    """
    from backend.agent.request_recovery import require_runnable_strategy
    from backend.agent.strategies.contracts import is_request_direct
    require_runnable_strategy(state)
    # Direct 从完整请求开始，跳过意图/goal/提取/调度。
    return Command(update=prepare_context(state), goto="direct_decide" if is_request_direct(state) else "classify_intent")


def build_agent_graph(checkpointer=None):
    """
    构建并编译 Agent 状态图。

    路由策略：Command 模式 — 每个节点内部用 Command(update, goto) 决定跳转，
    不能同时添加对应静态边，否则预算停止时可能产生并行执行。

    Direct 跳过 goal 的路径：
    prepare_context → direct_decide → direct_validate → execute_tools → observe_execution
    observe_execution 委托 direct_observe → direct_decide / ask_clarification / direct_finish / END
    direct_finish → generate_response / finalize_memory（请求回执，非逐 goal 验收）
    Plan / Discovery 的节点流转：
    prepare_context       → classify_intent （构造受 Token 预算约束的三层记忆上下文）
    classify_intent       → validate_goals → select_next_goal
    select_next_goal      → extract_info / strategy_dispatch / ask_clarification / generate_response
    extract_info          → plan_tasks （plan_tasks 会按已固定策略转发）
    strategy_dispatch     → plan_tasks（批量计划）/ decide_next（逐步决策）
    plan_tasks            → execute_tools / ask_clarification
    decide_next           → validate_decision（先保存模型动作，再校验与执行）
    validate_decision     → execute_tools / execute_discovery / ask_clarification / finalize_goal
    execute_discovery     → observe_strategy（搜索/描述只返回目录，不执行业务写入）
    request_confirmation  → execute_tools / select_next_goal / END（Direct 分支见确认节点）
    execute_tools         → observe_execution
    observe_execution     → finalize_goal / plan_tasks / ask_clarification / END
    observe_execution     → observe_strategy（Discovery 使用公共执行器的回执）
    observe_strategy      → decide_next / finalize_goal / ask_clarification / END
    finalize_goal         → select_next_goal （继续独立目标或阻断失败依赖）
    generate_response     → finalize_memory （保存本轮短期消息并预留摘要/事实扩展点）
    finalize_memory       → END
    ask_clarification     → execute_tools / extract_info / plan_tasks / refine_goal（interrupt 后恢复）
    任一节点预算耗尽       → finish_budget → finalize_memory；不再调用模型。
    旧 Plan Checkpoint 没有 execution_version=2，仍使用原单目标分支。
    Direct 只支持当前请求级循环，旧模式检查点在执行/恢复前拒绝。
    """
    from backend.agent.nodes.resolve_person_reference import resolve_person_reference

    graph = StateGraph(AgentState)
    from backend.agent.nodes.goal_nodes import (
        validate_goals,
        select_next_goal,
        finalize_goal,
        finish_budget,
        refine_goal,
    )
    from backend.agent.nodes.strategy_nodes import (
        strategy_dispatch, decide_next, validate_decision, execute_discovery, observe_strategy_execution,
    )
    from openai import APIConnectionError, APITimeoutError, RateLimitError

    # 可预期的业务停止由 traced_node 返回 Command；未知系统故障仍让原节点
    # 失败。不要给所有节点装默认 error_handler：框架会在恢复时重放 handler，
    # 其再次 raise 会使用户永远无法恢复原来的模型/工具节点。
    retry = RetryPolicy(
        max_attempts=2,
        initial_interval=0.5,
        retry_on=(APIConnectionError, APITimeoutError, RateLimitError),
    )

    # ── 共用入口：新请求解析指代；WAIT 原请求恢复直接回执行器 ──
    graph.add_node(
        "dispatch_request", traced_node("dispatch_request", dispatch_request)
    )
    graph.add_node(
        "resolve_person_reference",
        traced_node("resolve_person_reference", resolve_person_reference),
    )
    graph.add_node(
        "prepare_context", traced_node("prepare_context", prepare_and_classify)
    )
    # ── 目标模式：分类/提取/批次计划；Direct 不经过这三个节点 ──
    graph.add_node(
        "classify_intent",
        traced_node("classify_intent", classify_intent),
        retry_policy=retry,
    )
    graph.add_node(
        "extract_info", traced_node("extract_info", extract_info), retry_policy=retry
    )
    graph.add_node(
        "plan_tasks", traced_node("plan_tasks", plan_tasks), retry_policy=retry
    )
    # ── 共用执行、观察、交互和收尾；观察函数内部再分流策略 ──
    graph.add_node("execute_tools", traced_node("execute_tools", execute_tools))
    graph.add_node(
        "observe_execution", traced_node("observe_execution", observe_execution)
    )
    graph.add_node(
        "generate_response", traced_node("generate_response", generate_response)
    )
    graph.add_node(
        "ask_clarification", traced_node("ask_clarification", ask_clarification)
    )
    graph.add_node(
        "request_confirmation",
        traced_node("request_confirmation", request_confirmation),
    )
    graph.add_node("finalize_memory", traced_node("finalize_memory", finalize_memory))
    # ── Plan / Discovery 目标调度及 Discovery 逐轮决策 ──
    for name, node in (
        ("validate_goals", validate_goals),
        ("select_next_goal", select_next_goal),
        ("finalize_goal", finalize_goal),
        ("refine_goal", refine_goal),
    ):
        graph.add_node(name, traced_node(name, node))
    graph.add_node("finish_budget", finish_budget)
    for name, node in (("strategy_dispatch", strategy_dispatch), ("decide_next", decide_next),
                       ("validate_decision", validate_decision), ("execute_discovery", execute_discovery),
                       ("observe_strategy", observe_strategy_execution)):
        graph.add_node(name, traced_node(name, node), retry_policy=retry if name == "decide_next" else None)

    # ── Direct：以完整请求为范围，跳过 goal 层 ──
    from backend.agent.nodes.direct_nodes import direct_decide, direct_validate, direct_observe, direct_finish
    for name, node in (("direct_decide", direct_decide), ("direct_validate", direct_validate),
                       ("direct_observe", direct_observe), ("direct_finish", direct_finish)):
        graph.add_node(name, traced_node(name, node), retry_policy=retry if name == "direct_decide" else None)

    # ── 入口 ──
    graph.set_entry_point("dispatch_request")

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
