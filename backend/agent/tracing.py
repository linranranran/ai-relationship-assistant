"""按 request_id 串联节点、工具和模型调用，只记录排障元数据。"""

import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from time import perf_counter

_trace = ContextVar("agent_request_trace", default={})
logger = logging.getLogger("agent.trace")


@contextmanager
def request_trace(request_id: str, thread_id: str):
    token = _trace.set({"request_id": request_id, "thread_id": thread_id})
    try:
        yield
    finally:
        _trace.reset(token)


def trace_event(event: str, **metadata):
    """调用方只传 ID、状态、数量和耗时；禁止传原文、参数、工具结果及凭据。"""
    logger.info(json.dumps({**_trace.get(), "event": event, **metadata},
                           ensure_ascii=False, default=str))


def traced_node(name, node):
    """节点函数被包裹后仍保留签名，LangGraph 可正常注入状态参数。"""
    @wraps(node)
    def wrapped(state):
        parent = _trace.get()
        token = _trace.set({**parent, "node": name,
                            "request_id": state.get("request_id", parent.get("request_id")),
                            "thread_id": state.get("thread_id", parent.get("thread_id"))})
        started = perf_counter()
        trace_event("node.start")
        try:
            from backend.agent.streaming import check_execution
            check_execution(force=True)  # 节点边界重新读取取消标志，不强杀数据库事务。
            result = node(state)
        except BaseException as exc:
            # GraphInterrupt 是框架正常的暂停控制信号，不作为业务异常报警。
            from langgraph.errors import GraphInterrupt
            trace_event("node.interrupt" if isinstance(exc, GraphInterrupt) else "node.failed",
                        error_type=type(exc).__name__,
                        latency_ms=int((perf_counter() - started) * 1000))
            raise
        else:
            trace_event("node.finished", latency_ms=int((perf_counter() - started) * 1000))
            return result
        finally:
            _trace.reset(token)
    return wrapped
