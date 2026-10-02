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
        return ChatResponse.model_validate({**task["response"], "run_id": task["run_id"]})
    return ChatResponse(request_id=task["request_id"], run_id=task["run_id"],
                        status="QUEUED" if task["status"] == "queued" else "RUNNING",
                        response="任务已接收，等待执行" if task["status"] == "queued" else "任务正在执行")


@router.post("/{request_id}/cancel")
async def stop_running_request(request_id: str, request: Request, current_user=Depends(get_current_user)):
    from backend.routers.chat import _normalize_request_id
    task = await run_in_threadpool(request_cancel, current_user["user_id"], _normalize_request_id(request_id))
    if not task:
        raise HTTPException(404, "请求不存在")
    from backend.agent.stream_runtime import schedule_accepted
    schedule_accepted(request, task)
    return task_response(task)


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
                    event = {"schema_version": 1, "request_id": request_id, "run_id": known_run_id,
                             "seq": cursor, "type": "request.snapshot",
                             "payload": {"response": task_response(current).model_dump(mode="json")}}
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
                    event = {"schema_version": 1, "request_id": request_id, "run_id": current["run_id"],
                             "seq": cursor, "type": "request.snapshot",
                             "payload": {"response": task_response(current).model_dump(mode="json")}}
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
