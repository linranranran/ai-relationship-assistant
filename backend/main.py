# ============================================================
# AI 人际关系助手 — FastAPI 入口
# ============================================================

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.config import HOST, PORT, LOG_LEVEL

# TO-DO: 实现完 router 后取消注释
from backend.routers.chat import router as chat_router
from backend.routers.auth import router as auth_router

app = FastAPI(
    title="AI 人际关系助手",
    description="用 AI 管理你的人际关系网络，再也不怕叫不出名字",
    version="0.1.0",
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
# app.include_router(auth_router)
# app.include_router(chat_router)


@app.get("/api/health")
async def health_check():
    """健康检查"""
    return {
        "status": "ok",
        "app": "AI 人际关系助手",
        "version": "0.1.0",
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