from fastapi import FastAPI
from sse_starlette.sse import EventSourceResponse
from pydantic import BaseModel
import uvicorn
import asyncio
import json

app = FastAPI(title="对话Agent")

class ChatRequest(BaseModel):
    message: str

class MockLLM:
    async def astream(self, message: str):
        tokens = ["你", "好", "，", "这是", "一个", "流式", "输出", "的", "测试", "。"]
        for token in tokens:
            await asyncio.sleep(0.2)
            yield {"type": "message", "delta": token}
        yield {"type": "_done"}

LLM = MockLLM()

@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest):
    async def event_generator():
        async for ev in LLM.astream(req.message):
            if ev.get("type") == "_done":
                # 发送结束事件，通知客户端流结束
                yield {"event": "done", "data": "[DONE]"}
                return
            # 把每个数据块序列化成字符串，sse-starlette 会帮你加上 "data: " 前缀
            yield {"event": "message", "data": json.dumps(ev, ensure_ascii=False)}

    return EventSourceResponse(event_generator())

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)