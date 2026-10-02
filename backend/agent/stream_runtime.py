"""FastAPI 进程内的流式执行管理：有界线程池 + 原有 PostgreSQL 任务记录。

这里不实现 Agent 编排。图的执行、事件、暂停、恢复仍由 LangGraph 提供。
节点目前使用同步数据库/SDK，所以放进线程池，不能阻塞 ASGI 事件循环。
SSE GET 只读进度，POST 受理后才安排执行，重连不会重复执行图。
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import monotonic

from starlette.concurrency import run_in_threadpool
from backend.agent.run_execution import execute_run
from backend.config import AGENT_STREAM_MAX_RUNS
from backend.db.agent_stream_store import pending_runs

logger = logging.getLogger(__name__)


class AgentStreamRuntime:
    """随 Web lifespan 创建/关闭，每个进程最多同时运行 max_runs 张图。

    本地线程集合只负责限流；是否允许执行由 PG claim_run 和会话锁决定。
    因此刷新页面、重复 POST 或多 Web 实例都不会绕过原来的执行幂等。
    """

    def __init__(self, max_runs=AGENT_STREAM_MAX_RUNS):
        if max_runs < 1:
            raise ValueError("AGENT_STREAM_MAX_RUNS 必须至少为 1")
        self.max_runs = max_runs
        self._executor = ThreadPoolExecutor(max_workers=max_runs, thread_name_prefix="agent-stream")
        self._active = {}
        self._retry_after = {}
        self._lock = Lock()
        self._closing = False
        self._stop = asyncio.Event()
        self._recovery = None

    def start(self):
        self._recovery = asyncio.create_task(self._recover_pending(), name="agent-stream-recovery")

    def schedule(self, run_id):
        """安排一次执行，不创建无界的内存待办队列。

        线程满时，任务继续保存在 PG 的 queued 状态；下面的恢复循环会领取。
        重复调用返回同一安排，不重新规划，不生成新的 call_id。
        """
        with self._lock:
            if self._closing:
                return False
            if run_id in self._active:
                return True
            if self._retry_after.get(run_id, 0) > monotonic():
                return False
            if len(self._active) >= self.max_runs:
                return False
            future = self._executor.submit(execute_run, run_id)
            self._active[run_id] = future
        # 快任务可能已经完成，add_done_callback 会同步调用回调，不能持锁注册。
        future.add_done_callback(lambda result: self._finished(run_id, result))
        return True

    def _finished(self, run_id, future):
        retry = False
        try:
            retry = future.result() is False  # 会话锁正被旧执行线程持有。
        except Exception as exc:
            retry = True
            # 连接库/取租约之前就失败时，任务还在 PG 中，下轮会再次尝试。
            logger.warning("Agent 执行暂未启动: run_id=%s error_type=%s", run_id, type(exc).__name__)
        with self._lock:
            self._active.pop(run_id, None)
            if retry:
                # 短暂跳过争锁任务，让后面的健康任务也能得到线程。
                # 这只是本进程的退避，不修改业务执行状态和持久化恢复位置。
                self._retry_after[run_id] = monotonic() + 5.0

    async def _recover_pending(self):
        while not self._stop.is_set():
            try:
                with self._lock:
                    available = self.max_runs - len(self._active)
                    self._retry_after = {run_id: due for run_id, due in self._retry_after.items() if due > monotonic()}
                    excluded = [*self._active, *self._retry_after]
                if available:
                    # 列表查询不抢占；实际执行前再原子 claim，防止多进程重复领取。
                    for run_id in await run_in_threadpool(pending_runs, self.max_runs, excluded):
                        self.schedule(run_id)
            except Exception as exc:
                logger.warning("Agent 检查点恢复暂不可用: error_type=%s", type(exc).__name__)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass

    async def close(self):
        with self._lock:
            self._closing = True
        self._stop.set()
        if self._recovery is not None:
            await self._recovery
        # 先等待正在执行的图结束，再关闭 Checkpointer/业务连接池。
        # 不用取消 Future 来强杀一个可能已经提交业务写入的 Tool。
        await run_in_threadpool(self._executor.shutdown, wait=True)


def schedule_accepted(request, task):
    """路由受理成功后安排执行；已完成/等待交互的任务不会重新运行。

    必须用请求所属 app 的 runtime，避免 reload/多个 TestClient 共享全局线程池。
    即使此刻线程满或正在关闭，PG 记录仍存在，重启后可以继续。
    """
    if task["status"] in {"queued", "running"}:
        request.app.state.agent_stream_runtime.schedule(task["run_id"])
