# ============================================================
# Embedding Service — 文本向量化
# ============================================================

from openai import OpenAI
from backend.config import EMBEDDING_API_KEY, EMBEDDING_BASE_URL, EMBEDDING_MODEL
from backend.agent.audit import audit_model, record_model_result


def get_embedding_client() -> OpenAI:
    """获取 Embedding 客户端"""
    return OpenAI(
        api_key=EMBEDDING_API_KEY,
        base_url=EMBEDDING_BASE_URL,
    )


def embed_text(client: OpenAI, text: str) -> list[float]:
    """将文本转为向量"""
    # 向量检索/人物索引也有模型调用，作为当前 Tool 的 embedding 子观察记录。
    kwargs = {"model": EMBEDDING_MODEL, "input": text}
    with audit_model(kwargs, mode="embedding", observation_type="embedding") as observation:
        response = client.embeddings.create(**kwargs)
        record_model_result(observation, response.data[0].embedding, getattr(response, "usage", None))
    return response.data[0].embedding
