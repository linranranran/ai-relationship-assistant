# ============================================================
# Embedding Service — 文本向量化
# ============================================================

from openai import OpenAI
from backend.config import EMBEDDING_API_KEY, EMBEDDING_BASE_URL, EMBEDDING_MODEL


def get_embedding_client() -> OpenAI:
    """获取 Embedding 客户端"""
    return OpenAI(
        api_key=EMBEDDING_API_KEY,
        base_url=EMBEDDING_BASE_URL,
    )


def embed_text(client: OpenAI, text: str) -> list[float]:
    """将文本转为向量"""
    response = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=text,
    )
    return response.data[0].embedding