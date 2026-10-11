"""请求级预算入口：Checkpoint 保存流程，PG 账本保存已经发生的外部消耗。

同一节点可能在崩溃后从头运行，因此只在 State 中累加会漏记或重复计数。
每次真实调用先预占，未知结果保守记账；成功模型结果可按操作摘要复用。
背景摘要/事实维护不处于这个 scope，有独立的记忆任务重试策略。
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from hashlib import sha256
import json

from backend.config import (
    AGENT_TASK_MAX_GOALS,
    AGENT_TASK_MAX_MODEL_CALLS,
    AGENT_TASK_MAX_TOOL_ATTEMPTS,
    AGENT_TASK_MAX_TOKENS,
    AGENT_TASK_MAX_REPLANS,
    AGENT_TASK_MAX_CLARIFICATIONS,
    AGENT_TASK_MAX_ACTIVE_SECONDS,
)


class BudgetExceeded(RuntimeError):
    """预算耗尽是不可重试的业务终态，不能通过换 run_id 恢复额度。"""


@dataclass(frozen=True)
class BudgetLimits:
    goals: int = AGENT_TASK_MAX_GOALS
    model_calls: int = AGENT_TASK_MAX_MODEL_CALLS
    tool_attempts: int = AGENT_TASK_MAX_TOOL_ATTEMPTS
    tokens: int = AGENT_TASK_MAX_TOKENS
    replans: int = AGENT_TASK_MAX_REPLANS
    clarifications: int = AGENT_TASK_MAX_CLARIFICATIONS
    active_seconds: float = AGENT_TASK_MAX_ACTIVE_SECONDS

    def __post_init__(self):
        if any(value <= 0 for value in asdict(self).values()) or self.goals > 4:
            raise ValueError("任务预算必须为正数，目标数最多为 4")


_scope = ContextVar("request_budget", default=None)
_run = ContextVar("budget_execution_run", default=None)


def current_budget():
    return _scope.get()


def current_run():
    return _run.get()


@contextmanager
def execution_budget_run(run):
    """传入可信 run/token，仅用于仓储 fencing；不写进 Checkpoint 或前端事件。"""
    token = _run.set(run)
    try:
        yield
    finally:
        _run.reset(token)


@contextmanager
def budget_scope(state, node):
    if state.get("execution_version") != 2:
        yield
        return
    from backend.db.agent_budget_store import BudgetStore

    store = BudgetStore(state["user_id"], state["request_id"])
    store.ensure(asdict(BudgetLimits()))
    token = _scope.set({"store": store, "node": node, "state": state})
    begun = False
    try:
        store.begin()
        begun = True
        yield
    finally:
        try:
            if begun:
                store.pause()
        finally:
            _scope.reset(token)


def reserve(kind, operation_key, tokens=0):
    scope = current_budget()
    if scope is None:
        return None
    from backend.agent.streaming import check_execution

    check_execution(force=True)
    return scope["store"].reserve(kind, operation_key, tokens)


def settle(attempt, actual_tokens=None, result=None):
    scope = current_budget()
    if scope is not None and attempt:
        scope["store"].settle(attempt, actual_tokens, result)


def model_operation(messages, schema="text"):
    """摘要包含目标/计划版本/输入；同一节点重放不能复用其他目标的模型结果。"""
    scope = current_budget()
    state = scope["state"] if scope else {}
    payload = [
        scope["node"] if scope else "standalone",
        state.get("active_goal_id"),
        state.get("plan_revision", state.get("replan_count", 0)),
        schema,
        messages,
    ]
    return sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()
