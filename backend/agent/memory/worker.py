"""持久化记忆任务工作者。

FastAPI lifespan 启动一个轻量消费循环。数据库任务表负责跨进程抢占、崩溃恢复和
重试；因此部署多个 API 实例时，每个实例都可以安全运行一个工作者。
"""

import asyncio
import logging

from backend.agent.memory.models import MemoryJob, MemoryJobType, MemoryWriteCandidate
from backend.agent.memory.runtime import MemoryRuntime
from backend.agent.memory.summary_pipeline import run_summary_job
from backend.config import (
    AGENT_MEMORY_JOB_STALE_SECONDS,
    AGENT_MEMORY_WORKER_POLL_SECONDS,
    AGENT_SUMMARY_BATCH_LIMIT,
    AGENT_SUMMARY_MIN_MESSAGES,
)


logger = logging.getLogger(__name__)


def validate_memory_worker_config() -> None:
    """在服务开始接收请求前暴露错误配置。"""
    if not 1 <= AGENT_SUMMARY_MIN_MESSAGES <= AGENT_SUMMARY_BATCH_LIMIT <= 100:
        raise ValueError(
            "摘要配置必须满足 1 <= AGENT_SUMMARY_MIN_MESSAGES "
            "<= AGENT_SUMMARY_BATCH_LIMIT <= 100"
        )
    if not 0.1 <= AGENT_MEMORY_WORKER_POLL_SECONDS <= 300:
        raise ValueError("AGENT_MEMORY_WORKER_POLL_SECONDS 必须在 0.1 到 300 之间")
    if not 1 <= AGENT_MEMORY_JOB_STALE_SECONDS <= 86400:
        raise ValueError("AGENT_MEMORY_JOB_STALE_SECONDS 必须在 1 到 86400 之间")


async def memory_worker_loop(
    runtime: MemoryRuntime,
    stop_event: asyncio.Event,
) -> None:
    """持续消费任务；阻塞的数据库和 LLM 调用放入工作线程。"""
    validate_memory_worker_config()
    if not runtime.job_store.persistent:
        logger.info("记忆任务仓储未配置，后台工作者不启动")
        return
    logger.info("Agent 记忆后台工作者已启动")
    while not stop_event.is_set():
        try:
            processed = await asyncio.to_thread(process_next_memory_job, runtime)
        except Exception:
            # claim_next 自身失败时还没有具体任务可以标记，循环保留并退避。
            logger.exception("记忆工作者读取任务失败")
            processed = False
        if not processed:
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=AGENT_MEMORY_WORKER_POLL_SECONDS,
                )
            except TimeoutError:
                pass
    logger.info("Agent 记忆后台工作者已停止")


def process_next_memory_job(runtime: MemoryRuntime) -> bool:
    """处理一个任务；返回 False 表示当前没有可运行任务。"""
    job = runtime.job_store.claim_next(
        stale_after_seconds=AGENT_MEMORY_JOB_STALE_SECONDS
    )
    if job is None:
        return False
    try:
        _execute_job(runtime, job)
        if not runtime.job_store.mark_succeeded(
            owner_id=job.owner_id,
            job_id=job.job_id,
            execution_token=job.execution_token,
        ):
            logger.warning("记忆任务完成但租约已失效: job_id=%s", job.job_id)
    except Exception as exc:
        # 指数退避最大五分钟；错误只记录类型和受限文本，不记录完整 Prompt。
        retry_after = min(300, 5 * (2 ** max(0, job.attempts - 1)))
        runtime.job_store.mark_failed(
            owner_id=job.owner_id,
            job_id=job.job_id,
            execution_token=job.execution_token,
            error_message=f"{type(exc).__name__}: {exc}",
            retry_after_seconds=retry_after,
        )
        logger.exception(
            "记忆任务执行失败: job_id=%s type=%s attempt=%s/%s",
            job.job_id,
            job.job_type.value,
            job.attempts,
            job.max_attempts,
        )
    return True


def _execute_job(runtime: MemoryRuntime, job: MemoryJob) -> None:
    if job.job_type == MemoryJobType.SUMMARY:
        run_summary_job(
            runtime=runtime,
            owner_id=job.owner_id,
            conversation_id=job.conversation_id,
            batch_limit=AGENT_SUMMARY_BATCH_LIMIT,
            min_messages=AGENT_SUMMARY_MIN_MESSAGES,
        )
        return
    if job.job_type == MemoryJobType.PERSIST_FACTS:
        raw_candidates = job.payload.get("candidates", [])
        if not isinstance(raw_candidates, list):
            raise ValueError("事实任务 candidates 必须是数组")
        candidates = tuple(
            MemoryWriteCandidate(**candidate)
            for candidate in raw_candidates
            if isinstance(candidate, dict)
        )
        if len(candidates) != len(raw_candidates):
            raise ValueError("事实任务包含无效候选")
        runtime.long_term_store.save_candidates(
            owner_id=job.owner_id,
            conversation_id=job.conversation_id,
            request_id=job.request_id,
            candidates=candidates,
        )
        return
    raise ValueError(f"未知记忆任务类型：{job.job_type}")
