# ============================================================
# LLM Service — 统一的 LLM 调用封装
# ============================================================

from backend.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from langfuse.openai import OpenAI

def get_llm_client() -> OpenAI:
    """获取 LLM 客户端"""
    return OpenAI(
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL,
    )


def chat(
    client: OpenAI,
    system_prompt: str,
    messages: list[dict],
    temperature: float = 0.3,
    response_format: dict | None = None,
) -> str:
    """发送对话请求到 LLM"""
    full_messages = [{"role": "system", "content": system_prompt}] + messages

    kwargs = {
        "model": LLM_MODEL,
        "messages": full_messages,
        "temperature": temperature,
    }
    if response_format:
        kwargs["response_format"] = response_format

    response = client.chat.completions.create(**kwargs)
    return response.choices[0].message.content