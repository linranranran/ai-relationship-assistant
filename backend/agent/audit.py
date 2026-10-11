"""Langfuse 审计：官方回调采集执行树，项目负责身份关联、脱敏和业务事件。

request_id 对应 Trace，thread_id 对应 Session；重试/恢复各有一个根观察。
审计副本与业务数据分开，Langfuse 故障不能改变工具执行与检查点恢复语义。
"""
from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
from dataclasses import asdict, is_dataclass
from enum import Enum
import json
import logging
import re
from threading import RLock
from uuid import uuid4

from langfuse import Langfuse, propagate_attributes
from backend import config

logger = logging.getLogger(__name__)
_client = None
_lock = RLock()
_scope = ContextVar("langfuse_agent_audit", default=None)
_secrets = ()
_SENSITIVE_KEY = re.compile(
    r"password|passwd|secret|api[_-]?key|authorization|access[_-]?token|"
    r"refresh[_-]?token|execution[_-]?token|^token$", re.I,
)
_BEARER = re.compile(r"\bBearer\s+[^\s\"'<>]+", re.I)
_API_KEY = re.compile(r"\b(?:sk|pk)-(?:lf-)?[A-Za-z0-9_-]{10,}")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_DSN_PASSWORD = re.compile(r"(\w+://[^\s/:]+:)[^\s@]+(@)")


def _scrub(value, depth=0):
    if depth > 50:
        return {"audit_truncated": True, "reason": "nesting_limit"}
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if _SENSITIVE_KEY.search(str(key))
            else _scrub(item, depth + 1) for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_scrub(item, depth + 1) for item in value]
    if isinstance(value, str):
        # SDK 会传入原始数据或已编码 JSON，两种都要识别敏感字段。
        if value.lstrip().startswith(("{", "[")):
            try:
                parsed = json.loads(value)
            except (ValueError, RecursionError):
                pass
            else:
                return json.dumps(_scrub(parsed, depth + 1), ensure_ascii=False)
        for secret in _secrets:
            value = value.replace(secret, "[REDACTED]")
        value = _BEARER.sub("Bearer [REDACTED]", value)
        value = _API_KEY.sub("[REDACTED]", value)
        value = _JWT.sub("[REDACTED]", value)
        return _DSN_PASSWORD.sub(r"\1[REDACTED]\2", value)
    if value is None or isinstance(value, (int, float, bool)):
        return value
    # 不调用未知对象 repr，数据库/SDK 对象可能带连接凭据。
    return {"audit_object_type": type(value).__name__}


def audit_payload(value):
    """只返回审计副本，不原地修改 AgentState、模型消息或工具参数。"""
    scrubbed = _scrub(value)
    serialized = json.dumps(scrubbed, ensure_ascii=False, default=str)
    limit = config.AGENT_AUDIT_MAX_PAYLOAD_CHARS
    if len(serialized) <= limit:
        return scrubbed
    return {"audit_truncated": True, "original_chars": len(serialized),
            "limit_chars": limit, "preview": serialized[:limit]}


def _mask_sdk(*, data, **kwargs):
    return audit_payload(data)


def _mask_export(*, params):
    """用官方导出钩子再保护一次：异常信息等原始 OTEL 属性也不能带凭据。"""
    from langfuse.types import MaskOtelSpansResult, OtelSpanPatch
    patches = {}
    for identifier, span in params.spans.items():
        updates = {}
        for key, value in span.attributes.items():
            masked = "[REDACTED]" if _SENSITIVE_KEY.search(key) else audit_payload(value)
            # OTEL 的数组通常是 tuple（如 trace.tags），保持数组类型而非 JSON 字符串。
            if isinstance(value, (list, tuple)) and isinstance(masked, list):
                masked = tuple(masked)
            if masked != value:
                updates[key] = masked if isinstance(masked, (str, int, float, bool, tuple)) else json.dumps(masked, ensure_ascii=False)
        if updates:
            patches[identifier] = OtelSpanPatch(set_attributes=updates)
    return MaskOtelSpansResult(span_patches=patches)


def initialize_audit():
    """进程级 SDK，无密钥/初始化失败降级；不在每个请求创建 exporter。"""
    global _client, _secrets
    if not config.LANGFUSE_TRACING_ENABLED:
        return None
    with _lock:
        if _client is not None:
            return _client
        if not config.LANGFUSE_PUBLIC_KEY or not config.LANGFUSE_SECRET_KEY:
            logger.warning("Langfuse 缺少完整密钥，审计未启用")
            return None
        if config.AGENT_AUDIT_MAX_PAYLOAD_CHARS < 1000 or config.LANGFUSE_TIMEOUT <= 0:
            logger.warning("Langfuse 配置无效，审计未启用：载荷上限至少 1000，超时必须 > 0")
            return None
        _secrets = tuple(sorted({
            value for key, value in vars(config).items()
            if isinstance(value, str) and len(value) >= 6 and _SENSITIVE_KEY.search(key)
        }, key=len, reverse=True))
        try:
            _client = Langfuse(
                public_key=config.LANGFUSE_PUBLIC_KEY, secret_key=config.LANGFUSE_SECRET_KEY,
                base_url=config.LANGFUSE_BASE_URL, environment=config.LANGFUSE_TRACING_ENVIRONMENT,
                sample_rate=1.0, mask=_mask_sdk, mask_otel_spans=_mask_export,
                timeout=config.LANGFUSE_TIMEOUT, flush_interval=1,
                # 官方 v4 实时摄取通道；避免走兼容通道后等待旧数据索引。
                additional_headers={"x-langfuse-ingestion-version": "4"},
                should_export_span=lambda span: span.instrumentation_scope is not None
                and span.instrumentation_scope.name == "langfuse-sdk",
            )
        except Exception as exc:
            logger.warning("Langfuse 初始化失败，审计降级: %s", type(exc).__name__)
            return None
        logger.info("Langfuse 审计已启用: environment=%s", config.LANGFUSE_TRACING_ENVIRONMENT)
        return _client


def shutdown_audit():
    """工作线程停止后 flush；保留 SDK 单例供重复 lifespan 使用，SDK 在退出时关闭。"""
    if _client is not None:
        try:
            _client.flush()
        except Exception as exc:
            logger.warning("Langfuse flush 未完成: %s", type(exc).__name__)


def _safe_update(observation, **kwargs):
    if observation is not None:
        try:
            observation.update(**kwargs)
        except Exception as exc:
            logger.warning("Langfuse 更新失败: %s", type(exc).__name__)


@contextmanager
def audit_request(request_id, thread_id, *, owner_id=None, input=None, action="CHAT", run_id=None, interrupt_id=None, job_id=None):
    """每个执行批次一个根观察，同请求 retry/resume 的 Trace ID 保持一致。"""
    parent = _scope.get()
    if parent and parent["request_id"] == request_id and parent["thread_id"] == thread_id:
        yield parent  # 流式批次调用恢复入口时复用根，不能再生成一个重复根。
        return
    client = initialize_audit()
    if client is None:
        yield None
        return
    trace_id = Langfuse.create_trace_id(seed=f"relation-agent:{thread_id}:{request_id}")
    metadata = {"request_id": request_id, "thread_id": thread_id,
                "action": action, "attempt_id": str(uuid4())}
    if run_id:
        metadata["run_id"] = run_id
    if interrupt_id:
        metadata["interrupt_id"] = interrupt_id
    if job_id:
        metadata["job_id"] = job_id
    stack = ExitStack()
    try:
        span = stack.enter_context(client.start_as_current_observation(
            name=f"agent.{action.lower()}", as_type="agent", trace_context={"trace_id": trace_id},
            input=audit_payload(input), metadata=metadata,
        ))
        stack.enter_context(propagate_attributes(
            trace_name="relation-agent.request", user_id=owner_id, session_id=thread_id,
            metadata=metadata, tags=["relation-agent", "langgraph"],
        ))
    except Exception as exc:
        try:
            stack.close()
        except Exception:
            pass
        logger.warning("Langfuse 根观察创建失败: %s", type(exc).__name__)
        yield None
        return
    token = _scope.set({"client": client, "span": span, "request_id": request_id,
                        "thread_id": thread_id, "trace_id": trace_id, "metadata": metadata})
    try:
        yield _scope.get()
    except BaseException as exc:
        _safe_update(span, level="ERROR", status_message=type(exc).__name__)
        raise  # 业务异常必须交给原来的恢复机制，不能当成审计降级吞掉。
    finally:
        _scope.reset(token)
        try:
            stack.close()
        except Exception as exc:
            logger.warning("Langfuse 根观察关闭失败: %s", type(exc).__name__)


def graph_audit_config():
    """每次图调用一个独立的官方回调，不共享 handler 内部的运行状态。"""
    scope = _scope.get()
    if scope is None:
        return {}
    try:
        from langfuse.langchain import CallbackHandler
        handler = CallbackHandler(
            public_key=config.LANGFUSE_PUBLIC_KEY,
            trace_context={"trace_id": scope["trace_id"], "parent_span_id": scope["span"].id},
        )
    except Exception as exc:
        logger.warning("Langfuse 图回调初始化失败: %s", type(exc).__name__)
        return {}
    return {"callbacks": [handler], "run_name": "agent.graph",
            "metadata": {**scope["metadata"], "langfuse_session_id": scope["thread_id"]}}


def record_request_result(result):
    scope = _scope.get()
    if scope:
        try:
            status = result.get("status") if isinstance(result, dict) else getattr(result, "status", None)
            level = "ERROR" if status == "FAILED" else (
                "WARNING" if status in {"PARTIALLY_COMPLETED", "BUDGET_EXCEEDED", "lease_lost"} else "DEFAULT"
            )
            _safe_update(scope["span"], output=audit_payload(result), level=level,
                         status_message=status, metadata={"result_status": status})
        except Exception as exc:
            logger.warning("Langfuse 请求结果序列化失败: %s", type(exc).__name__)


def record_execution_error(exc, *, outcome="failed"):
    """捕获的执行异常仍要可见；取消随后会用真实 CANCELLED 回执更新观察级别。"""
    scope = _scope.get()
    if scope:
        _safe_update(scope["span"], level="WARNING" if outcome == "lease_lost" else "ERROR",
                     status_message=type(exc).__name__, metadata={"execution_outcome": outcome,
                                                                 "execution_error_type": type(exc).__name__})


def record_event(name, metadata):
    scope = _scope.get()
    if scope:
        try:
            scope["client"].create_event(name=name, metadata=audit_payload(metadata))
        except Exception as exc:
            logger.warning("Langfuse 业务事件记录失败: %s", type(exc).__name__)


def record_node_result(state, result, *, outcome="completed"):
    """回调记录完整 input/output；补充可读的 Command 路由和状态增量。"""
    scope = _scope.get()
    if scope:
        try:
            scope["client"].update_current_span(metadata={
                "goal_id": state.get("active_goal_id"), "intent": state.get("intent"),
                "plan_revision": state.get("plan_revision", 0), "outcome": outcome,
            })
            # dict.update 是方法；只有 Command.update 才是节点的状态增量。
            update = result if isinstance(result, dict) else getattr(result, "update", result)
            record_event("node.result", {"goto": getattr(result, "goto", None),
                         "update": update, "outcome": outcome})
        except Exception as exc:
            logger.warning("Langfuse 节点结果更新失败: %s", type(exc).__name__)


@contextmanager
def audit_tool(tool_name, args, *, call_id, step_id, attempt):
    """只围绕真正的工具调用；缓存命中记录事件，不伪造真实执行。"""
    scope = _scope.get()
    if scope is None:
        yield None
        return
    stack = ExitStack()
    try:
        span = stack.enter_context(scope["client"].start_as_current_observation(
            name=tool_name, as_type="tool", input=audit_payload(args),
            metadata={"call_id": call_id, "step_id": step_id, "attempt": attempt},
        ))
    except Exception as exc:
        try:
            stack.close()
        except Exception:
            pass
        logger.warning("Langfuse 工具观察创建失败: %s", type(exc).__name__)
        yield None
        return
    try:
        yield span
    except BaseException as exc:
        _safe_update(span, level="ERROR", status_message=type(exc).__name__)
        raise
    finally:
        try:
            stack.close()
        except Exception as exc:
            logger.warning("Langfuse 工具观察关闭失败: %s", type(exc).__name__)


def record_tool_result(span, result):
    if span is None:
        return
    try:
        _safe_update(span, output=audit_payload(result),
                     level="DEFAULT" if result.get("success") else "WARNING",
                     status_message=result.get("error_code"))
    except Exception as exc:
        logger.warning("Langfuse 工具结果序列化失败: %s", type(exc).__name__)


@contextmanager
def audit_model(kwargs, *, mode, observation_type="generation"):
    """原生 OpenAI chat/stream 的官方 generation；不修改 SDK 全局方法。

    LangChain 的模型由图回调捕获，不经过此函数。无请求上下文的后台模型也可
    记录独立 generation；记忆工作者会另外提供 request/session 关联。
    """
    client = initialize_audit()
    if client is None:
        yield None
        return
    stack = ExitStack()
    try:
        span = stack.enter_context(client.start_as_current_observation(
            name=f"model.{mode}", as_type=observation_type, model=kwargs["model"],
            input=audit_payload(kwargs.get("messages", kwargs.get("input"))),
            model_parameters={key: value for key, value in kwargs.items()
                              if key not in {"model", "messages", "input"}}, metadata={"mode": mode},
        ))
    except Exception as exc:
        try:
            stack.close()
        except Exception:
            pass
        logger.warning("Langfuse 模型观察创建失败: %s", type(exc).__name__)
        yield None
        return
    try:
        yield span
    except BaseException as exc:
        _safe_update(span, level="ERROR", status_message=type(exc).__name__)
        raise
    finally:
        try:
            stack.close()
        except Exception as exc:
            logger.warning("Langfuse 模型观察关闭失败: %s", type(exc).__name__)


def record_model_result(span, text, usage, *, incomplete=False):
    if span is None:
        return
    try:
        details = None if usage is None else {
            "input": getattr(usage, "prompt_tokens", 0),
            "output": getattr(usage, "completion_tokens", 0),
            "total": getattr(usage, "total_tokens", 0),
        }
        _safe_update(span, output=audit_payload(text), usage_details=details,
                     level="WARNING" if incomplete else "DEFAULT",
                     status_message="stream_incomplete" if incomplete else None)
    except Exception as exc:
        logger.warning("Langfuse 模型结果序列化失败: %s", type(exc).__name__)
