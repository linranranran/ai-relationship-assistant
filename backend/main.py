# ============================================================
# AI 人际关系助手 — FastAPI 入口
# ============================================================

import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.config import HOST, PORT, LOG_LEVEL
from backend.agent.graph import (
    close_agent_runtime,
    get_checkpointer_backend,
    initialize_agent_runtime,
)
from backend.agent.memory.runtime import (
    MemoryRuntime,
    configure_memory_runtime,
    get_memory_runtime,
)
from backend.agent.memory.worker import memory_worker_loop, validate_memory_worker_config
from backend.db.conversation_memory_store import PostgresConversationMemoryStore
from backend.db.memory_job_store import PostgresMemoryJobStore
from backend.db.postgres_client import close_postgres_pool, open_postgres_pool
from backend.db.agent_interaction_store import ensure_interaction_schema
from backend.db.agent_session_lock import close_session_lock_pool
from backend.db.agent_stream_store import ensure_stream_schema
from backend.agent.stream_runtime import AgentStreamRuntime
from backend.config import AGENT_SSE_MAX_CONNECTIONS, AGENT_SSE_POLL_SECONDS, AGENT_RUN_LEASE_SECONDS, AGENT_EVENT_RETENTION, AGENT_CHECKPOINTER_BACKEND
from backend.routers.stream import router as stream_router

# TO-DO: 实现完 router 后取消注释
from backend.routers.chat import router as chat_router
from backend.routers.auth import router as auth_router


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """管理与整个服务进程同寿命的基础设施资源。

    Checkpointer 必须在多次 chat/resume 请求之间持续存在。PostgreSQL 连接池
    不能放进单个路由的 ``with`` 块中。记忆仓储也应在启动时注入一次，供
    prepare_context 和 finalize_memory 在每轮请求中共同使用。
    """
    previous_memory_runtime = get_memory_runtime()
    memory_worker_task: asyncio.Task | None = None
    memory_worker_stop = asyncio.Event()
    stream_runtime = None
    try:
        # 任务记录可跨 Web 重启保存，图检查点也必须持久化，才能继续原计划。
        if AGENT_CHECKPOINTER_BACKEND.strip().lower() != "postgres":
            raise ValueError("可恢复流式执行需要 AGENT_CHECKPOINTER_BACKEND=postgres；memory 仅用于学习脚本")
        # 启动时验证业务库连接。Repository 不持有连接，只在方法执行时从池中
        # 借一个连接，因此可以由三个记忆端口共享同一个实例。
        open_postgres_pool()
        # 仅初始化新增的人工答案回执表，已有业务/记忆表继续沿用现有迁移。
        ensure_interaction_schema()
        ensure_stream_schema()
        if AGENT_SSE_POLL_SECONDS <= 0 or AGENT_RUN_LEASE_SECONDS < 30 or AGENT_EVENT_RETENTION < 100 or AGENT_SSE_MAX_CONNECTIONS < 1:
            raise ValueError("流式配置无效：轮询间隔>0、租约>=30秒、事件保留>=100、连接数>=1")
        _app.state.agent_sse_slots = asyncio.Semaphore(AGENT_SSE_MAX_CONNECTIONS)
        memory_store = PostgresConversationMemoryStore()
        runtime = MemoryRuntime(
            message_store=memory_store,
            summary_store=memory_store,
            long_term_store=memory_store,
            job_store=PostgresMemoryJobStore(),
            # Token 计数器和预算配置继续使用当前运行时的设置。
            token_counter=previous_memory_runtime.token_counter,
            settings=previous_memory_runtime.settings,
        )
        configure_memory_runtime(runtime)
        validate_memory_worker_config()
        # 图在启动阶段编译；节点执行时才通过 get_memory_runtime 取得仓储。
        initialize_agent_runtime()
        memory_worker_task = asyncio.create_task(
            memory_worker_loop(runtime, memory_worker_stop),
            name="agent-memory-worker",
        )
        # 同一 Web 进程直接消费 LangGraph 流，PyCharm 的节点断点仍在此进程。
        stream_runtime = AgentStreamRuntime()
        _app.state.agent_stream_runtime = stream_runtime
        stream_runtime.start()
        logger.info("PostgreSQL 业务连接池及 Agent 记忆仓储已启动")
        logger.info("Agent Checkpointer 已启动: backend=%s", get_checkpointer_backend())
        yield
    finally:
        # 先让工作者退出，再关闭它依赖的业务连接池。若正在调用摘要模型，关闭过程
        # 会等待当前任务结束，避免后台线程继续使用已经关闭的连接。
        memory_worker_stop.set()
        if stream_runtime is not None:
            await stream_runtime.close()
        if memory_worker_task is not None:
            await memory_worker_task
        # 即使启动阶段失败，也要恢复此前的运行时并释放已经打开的连接池。
        # reload/重复 lifespan 时不能留下指向已关闭连接池的仓储实例。
        configure_memory_runtime(previous_memory_runtime)
        try:
            close_agent_runtime()
        finally:
            try:
                close_session_lock_pool()
            finally:
                close_postgres_pool()
        logger.info("Agent Checkpointer 已关闭")
        logger.info("PostgreSQL 业务连接池已关闭")

app = FastAPI(
    title="AI 人际关系助手",
    description="用 AI 管理你的人际关系网络，再也不怕叫不出名字",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — 允许前端跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 开发阶段允许所有来源
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(stream_router)


@app.get("/api/health")
async def health_check():
    """健康检查"""
    return {
        "status": "ok",
        "app": "AI 人际关系助手",
        "version": "0.1.0",
        "checkpointer": get_checkpointer_backend(),
    }


def main():
    """启动服务"""
    uvicorn.run(
        "backend.main:app",
        host=HOST,
        port=PORT,
        reload=True,
        log_level=LOG_LEVEL.lower(),
    )


if __name__ == "__main__":
    main()
