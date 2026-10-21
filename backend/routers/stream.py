"""SSE 是只读订阅：断开/重连仅改变看进度的连接，永远不重新启动 Agent。"""

import asyncio
import json
from time import perf_counter
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import run_in_threadpool

from backend.auth import get_current_user
from backend.config import AGENT_SSE_POLL_SECONDS
from backend.db.agent_stream_store import load_task, load_events, request_cancel
from backend.agent.tracing import trace_event

router = APIRouter(prefix="/api/chat/requests", tags=["chat-stream"])
TERMINAL = frozenset({"completed", "waiting", "failed", "cancelled"})


def task_response(task):
    """受理回执/状态查询共用一份投影，不把服务器内部输入和身份快照返回前端。"""
    from backend.models.schemas import ChatResponse
    if task["response"]:
        response = ChatResponse.model_validate({**task["response"], "run_id": task["run_id"]})
        if task["status"] == "waiting":
            # /chat 幂等重试、history、状态查询和 SSE 都通过这里展示等待任务。
            # 旧模式不可恢复时去掉交互按钮，避免点击后才发现流程已经移除。
            # 只改变展示结果；真实任务仍可由独立 STOP 终止，完成响应仍直接复用。
            from backend.agent.contracts import ModelBusinessValidationError
            from backend.agent.strategies.contracts import require_supported_strategy
            from backend.agent.request_idempotency import load_agent_request
            record = load_agent_request(task["owner_id"], task["request_id"])
            try:
                require_supported_strategy(record or {})
            except ModelBusinessValidationError as exc:
                return response.model_copy(update={
                    "status": "FAILED", "response": exc.feedback,
                    "confirmation": None, "clarification": None,
                })
        return response
    return ChatResponse(request_id=task["request_id"], run_id=task["run_id"],
                        status="QUEUED" if task["status"] == "queued" else "RUNNING",
                        response="任务已接收，等待执行" if task["status"] == "queued" else "任务正在执行")


@router.post("/{request_id}/cancel")
async def stop_running_request(request_id: str, request: Request, current_user=Depends(get_current_user)):
    from backend.routers.chat import _normalize_request_id
    task = await run_in_threadpool(request_cancel, current_user["user_id"], _normalize_request_id(request_id))
    if not task:
        # 升级前只有 agent_request / Checkpoint 的任务也能停止，不能要求用户
        # 先恢复已移除的旧流程来创建 agent_task。此路径不 invoke 任何业务节点。
        return await run_in_threadpool(_stop_request_without_task, current_user["user_id"], _normalize_request_id(request_id))
    from backend.agent.stream_runtime import schedule_accepted
    schedule_accepted(request, task)
    return await run_in_threadpool(task_response, task)


def _stop_request_without_task(owner_id: str, request_id: str):
    """在会话锁内终止旧任务，只保留已提交回执，不重放旧模型/工具。"""
    from backend.agent.request_idempotency import load_agent_request
    from backend.agent.run_execution import _finish_cancellation
    from backend.agent.session import derive_thread_id
    from backend.db.agent_session_lock import lock_agent_session, SessionBusy
    from backend.routers.chat import _runtime_graph, _graph_config, _saved_chat_response
    record = load_agent_request(owner_id, request_id)
    if not record:
        raise HTTPException(404, "请求不存在")
    if record.get("status") == "completed":
        return _saved_chat_response(record.get("response"))
    try:
        with lock_agent_session(derive_thread_id(owner_id)):
            graph = _runtime_graph()
            values = graph.get_state(_graph_config(owner_id)).values or {}
            if values.get("request_id") != request_id or not isinstance(values.get("user_input"), str):
                raise HTTPException(409, "原任务检查点已丢失，无法安全停止")
            return _finish_cancellation({
                "owner_id": owner_id, "request_id": request_id,
                "thread_id": derive_thread_id(owner_id),
                "input": {"message": values["user_input"], "response_kind": values.get("response_kind", "chat")},
            }, graph, lambda token: None)
    except SessionBusy as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/{request_id}/events")
async def subscribe(request_id: str, request: Request,
                    after: int = Query(default=0, ge=0),
                    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
                    current_user=Depends(get_current_user)):
    """Bearer 登录鉴权 + 单调 seq 回放 + 框架心跳和发送超时。

    after 是刷新后恢复的游标；Last-Event-ID 是同一 SSE 连接自动重连的游标。
    发送前从 PG 分页取100条，不使用无限内存队列；慢客户端会由 send_timeout
    断开，然后按游标补读。生产反向代理还需关闭此路由的响应缓冲。
    """
    from backend.routers.chat import _normalize_request_id
    request_id = _normalize_request_id(request_id)
    owner_id = current_user["user_id"]
    try:
        cursor = max(after, int(last_event_id or 0))
    except ValueError as exc:
        raise HTTPException(422, "事件游标必须是非负整数") from exc
    task = await run_in_threadpool(load_task, owner_id, request_id)
    if not task:
        raise HTTPException(404, "请求不存在")
    if cursor < 0 or cursor > task["seq"]:
        raise HTTPException(409, "事件游标无效，请重新查询任务")
    slots = request.app.state.agent_sse_slots
    try:
        await asyncio.wait_for(slots.acquire(), timeout=0.1)
    except asyncio.TimeoutError as exc:
        raise HTTPException(503, "进度连接繁忙，请稍后重连") from exc

    async def events():
        nonlocal cursor
        started = perf_counter()
        sent = 0
        snapshot_sent = False
        known_run_id = None
        trace_event("stream.subscribed", request_id=request_id, run_id=task["run_id"], after=cursor)
        try:
            while not await request.is_disconnected():
                # 必须先读取快照 seq，再读取事件；不能用晚读的终态提前退出，
                # 否则正好在两次 SELECT 之间提交的 completed 事件会丢失。
                current = await run_in_threadpool(load_task, owner_id, request_id)
                if current["run_id"] != known_run_id:
                    # 先同步“当前批次”，再回放历史。另一个页面可能已经 resume，
                    # 旧批次的 interaction.required 不能把本页带回过期的问题。
                    # 此控制快照用已读 cursor，不跳过后面的 response.started/delta。
                    known_run_id = current["run_id"]
                    response = await run_in_threadpool(task_response, current)
                    event = {"schema_version": 1, "request_id": request_id, "run_id": known_run_id,
                             "seq": cursor, "type": "request.snapshot",
                             "payload": {"response": response.model_dump(mode="json")}}
                    yield {"id": str(cursor), "event": "agent", "data": json.dumps(event, ensure_ascii=False)}
                    sent += 1
                    snapshot_sent = True
                    if current["status"] in TERMINAL:
                        return  # 终态快照本身已包含完整权威结果，不必回放旧草稿。
                batch = await run_in_threadpool(load_events, owner_id, request_id, cursor)
                gap = batch and batch[0]["seq"] > cursor + 1
                at_tip = not batch and cursor >= current["seq"]
                if gap or (at_tip and (not snapshot_sent or current["status"] in TERMINAL)):
                    # 首次查询已完成、刷新/旧游标被淘汰时用权威快照修复界面。
                    cursor = current["seq"]
                    response = await run_in_threadpool(task_response, current)
                    event = {"schema_version": 1, "request_id": request_id, "run_id": current["run_id"],
                             "seq": cursor, "type": "request.snapshot",
                             "payload": {"response": response.model_dump(mode="json")}}
                    yield {"id": str(cursor), "event": "agent", "data": json.dumps(event, ensure_ascii=False)}
                    sent += 1
                    snapshot_sent = True
                    if current["status"] in TERMINAL:
                        return
                else:
                    for event in batch:
                        cursor = event["seq"]
                        yield {"id": str(cursor), "event": "agent", "data": json.dumps(event, ensure_ascii=False)}
                        sent += 1
                    if current["status"] in TERMINAL and cursor >= current["seq"]:
                        return
                await asyncio.sleep(AGENT_SSE_POLL_SECONDS)
        finally:
            slots.release()
            trace_event("stream.closed", request_id=request_id, run_id=task["run_id"],
                        after=cursor, events_sent=sent, latency_ms=int((perf_counter() - started) * 1000))

    return EventSourceResponse(events(), ping=15, send_timeout=20,
                               headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})
