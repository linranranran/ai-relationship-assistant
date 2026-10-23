from __future__ import annotations

import uvicorn
import hmac
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config.settings import Settings
from rest_api import router as rest_router

app = FastAPI(title="RAG API Service", version="1.0.0")


@app.middleware('http')
async def service_auth(request, call_next):
    token = os.getenv('RAG_SERVICE_TOKEN', '')
    if token and not hmac.compare_digest(request.headers.get('authorization', ''), 'Bearer ' + token):
        return JSONResponse(status_code=401, content={'detail': '服务认证失败'})
    return await call_next(request)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(rest_router, prefix="/api/docs")

if __name__ == "__main__":
    uvicorn.run(
        "run:app",
        host=Settings.HOST,
        port=Settings.PORT,
        reload=False,
    )
