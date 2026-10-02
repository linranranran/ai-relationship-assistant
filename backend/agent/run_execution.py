"""在 Web 进程内执行 LangGraph 流，不依赖 Celery、Redis 或独立 worker。

阅读顺序：execute_run -> _execute_claimed -> StreamingGraph.invoke。
图运行与 SSE 连接分离：关闭页面只关闭订阅，不会取消正在进行的工具操作。
同一进程崩溃时图也停止，重启后通过 PG 租约、检查点和原 call_id 继续执行。
"""

import logging
from datetime import datetime, timezone
from threading import Event, Thread
from time import perf_counter
from fastapi import Response
from backend.agent.graph import get_agent_graph
from backend.agent.streaming import StreamingGraph, execution_control, ExecutionCancelled
from backend.agent.tracing import request_trace, trace_event
from backend.db.agent_stream_store import claim_run, heartbeat, append_event, finish_run, RunLeaseLost
from backend.db.agent_session_lock import lock_agent_session, SessionBusy
from backend.db.agent_interaction_store import get_interaction
from backend.agent.request_idempotency import claim_agent_request, save_agent_response, AgentRequestAction
from backend.models.schemas import ChatRequest, ChatResponse
from backend.config import AGENT_RUN_LEASE_SECONDS
from backend.db.postgres_client import get_postgres_connection

logger = logging.getLogger(__name__)


def _finish_cancellation(run, graph, on_claim):
    """把取消写成终态，清掉 next；不删除检查点和业务执行回执。"""
    from backend.routers.chat import _graph_config
    claim = claim_agent_request(owner_id=run["owner_id"], request_id=run["request_id"],
                                thread_id=run["thread_id"], message=run["input"]["message"],
                                stale_after_seconds=0)
    if claim.action == AgentRequestAction.RETURN_RESPONSE:
        if claim.response["status"] in {"COMPLETED", "CANCELLED"}:
            return ChatResponse.model_validate(claim.response)
        from backend.agent.request_idempotency import claim_agent_resume
        claim = claim_agent_resume(owner_id=run["owner_id"], request_id=run["request_id"], stale_after_seconds=0)
    if not claim.execution_token:
        raise RuntimeError("取消未取得请求执行权")
    on_claim(claim.execution_token)
    config = _graph_config(run["owner_id"])
    snapshot = graph.get_state(config)
    text = "已停止后续步骤；已经完成的操作仍然保留。"
    if (snapshot.values or {}).get("request_id") == run["request_id"]:
        # finalize_memory 是无后继的终端节点。update_state(as_node=...) 使用
        # 官方 API 结束调度，不 invoke(None) 重放一个本应取消的工具。
        graph.update_state(config, {"execution_decision": "CANCELLED", "response": text},
                           as_node="finalize_memory")
    response = ChatResponse(request_id=run["request_id"], status="CANCELLED", response=text)
    save_agent_response(owner_id=run["owner_id"], request_id=run["request_id"],
                        response=response.model_dump(mode="json"), execution_token=claim.execution_token)
    return response


def execute_run(run_id):
    """在 FastAPI 进程内直接运行；身份和输入仍从服务端持久化记录读取。"""
    # 尚未 claim，先找到服务端受理时绑定的 thread_id。
    with get_postgres_connection() as connection:
        record = connection.execute("SELECT t.thread_id FROM agent_run r JOIN agent_task t "
                                    "USING(owner_id,request_id) WHERE r.run_id=%s", (run_id,)).fetchone()
    if not record:
        return
    try:
        with lock_agent_session(record["thread_id"]):
            run = claim_run(run_id)
            if not run:
                return  # 重复消息，不重新执行，也不重新生成进度。
            _execute_claimed(run)
    except SessionBusy:
        # 进程内恢复循环稍后重试；争锁不是任务失败，不等待一个长会话锁。
        return False


def _execute_claimed(run):
    stop = Event()
    state = {"cancelled": False, "lease_error": None, "request_token": None}
    started = perf_counter()
    first_event = True
    last_check = 0.0

    def on_claim(token):
        state["request_token"] = token

    def pulse():
        state["cancelled"] = heartbeat(run, state["request_token"])

    def heartbeat_loop():
        while not stop.wait(max(1, min(15, AGENT_RUN_LEASE_SECONDS / 3))):
            try:
                pulse()
            except Exception as exc:
                state["lease_error"] = exc
                return

    def check(*, force=False):
        nonlocal last_check
        if state["lease_error"]:
            raise RunLeaseLost("执行线程无法确认自己仍持有执行权") from state["lease_error"]
        # 每个节点/工具边界强制读取；模型 token 循环最多每半秒读一次，避免逐字查PG。
        if force or perf_counter() - last_check >= 0.5:
            pulse()
            last_check = perf_counter()
        if state["cancelled"]:
            raise ExecutionCancelled("用户已请求停止")

    def publish(event_type, **data):
        nonlocal first_event
        append_event(run, event_type, **data)
        if first_event:
            trace_event("stream.first_progress", run_id=run["run_id"],
                        latency_ms=int((perf_counter() - started) * 1000))
            first_event = False

    pulse_thread = Thread(target=heartbeat_loop, name="agent-run-heartbeat", daemon=True)
    try:
        pulse()
        pulse_thread.start()
        raw_graph = get_agent_graph()
        graph = StreamingGraph(raw_graph, publish)
        from backend.routers.chat import _chat_sync, _handle_interaction
        with request_trace(run["request_id"], run["thread_id"]), execution_control(check):
            trace_event("stream.execution_started", run_id=run["run_id"],
                        queue_ms=max(0, int((datetime.now(timezone.utc) - run["run_queued_at"]).total_seconds() * 1000)))
            if run["action"] == "STOP" or state["cancelled"]:
                response = _finish_cancellation(run, raw_graph, on_claim)
            elif run["action"] == "RESUME":
                receipt = get_interaction(run["owner_id"], run["interrupt_id"])
                if not receipt or receipt["request_id"] != run["request_id"]:
                    raise RuntimeError("恢复答案回执丢失")
                response = _handle_interaction(run["owner_id"], run["interrupt_id"], receipt["answer"],
                                               Response(), graph_override=graph, on_claim=on_claim,
                                               stale_after_seconds=0)
            else:
                # 已独占会话锁，死进程留下的 request 租约可以立即接管，无需等5分钟。
                response = _chat_sync(ChatRequest(message=run["input"]["message"]),
                                      run["input"]["current_user"], idempotency_key=run["request_id"],
                                      graph_override=graph, on_claim=on_claim, stale_after_seconds=0)
            if response.status != "CANCELLED":
                check(force=True)
            finish_run(run, response.model_dump(mode="json"))
            trace_event("stream.finished", run_id=run["run_id"], status=response.status,
                        latency_ms=int((perf_counter() - started) * 1000))
    except RunLeaseLost:
        logger.warning("旧 Agent 执行批次停止写入: run_id=%s", run["run_id"])
    except Exception as exc:
        logger.exception("Agent 批次失败: run_id=%s error_type=%s", run["run_id"], type(exc).__name__)
        try:
            pulse()
            if state["cancelled"]:
                response = _finish_cancellation(run, get_agent_graph(), on_claim)
            else:
                response = ChatResponse(request_id=run["request_id"], status="FAILED",
                                        response="执行暂未完成，请使用原请求重试；已成功的步骤会复用。")
            finish_run(run, response.model_dump(mode="json"))
        except RunLeaseLost:
            pass
        except Exception:
            # 不伪造完成。租约过期后从检查点继续同一个 run_id。
            logger.exception("Agent 批次结果未持久化，等待检查点恢复")
    finally:
        stop.set()
        if pulse_thread.is_alive():
            pulse_thread.join(timeout=10)
