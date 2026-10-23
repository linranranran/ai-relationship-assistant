import os
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).parent.parent


class Settings:
    PROJECT_ROOT: Path = PROJECT_ROOT
    LOG_DIR: Path = PROJECT_ROOT / "file"
    DATA_DIR: Path = Path(os.getenv("RAG_DATA_DIR", str(PROJECT_ROOT / "file")))

    API_KEY: Optional[str] = os.getenv("DASHSCOPE_API_KEY")
    API_BASE_URL: str = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    MODEL: str = os.getenv("RAG_DASHSCOPE_MODEL", os.getenv("DASHSCOPE_MODEL", "qwen3.7-plus"))
    TEMPERATURE: float = float(os.getenv("MODEL_TEMPERATURE", "0.1"))
    LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "2048"))
    LLM_CONTEXT_WINDOW: int = int(os.getenv("LLM_CONTEXT_WINDOW", "32768"))

    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "qwen3.7-text-embedding")
    EMBEDDING_DIMENSIONS: int = int(os.getenv("EMBEDDING_DIMENSIONS", "1024"))
    EMBEDDING_BATCH_SIZE: int = int(os.getenv("EMBEDDING_BATCH_SIZE", "20"))
    RERANK_MODEL: str = os.getenv("RERANK_MODEL", "qwen3.7-text-rerank")

    REDIS_HOST: str = os.getenv("REDIS_HOST", "127.0.0.1")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_USERNAME: str = os.getenv("REDIS_USERNAME", "")
    REDIS_PASSWORD: str = os.getenv("REDIS_PASSWORD", "")
    REDIS_DB: int = int(os.getenv("REDIS_DB", "0"))
    REDIS_SSL: bool = os.getenv("REDIS_SSL", "false").lower() in {"1", "true", "yes"}
    REDIS_SSL_CA_CERTS: Optional[str] = os.getenv("REDIS_SSL_CA_CERTS") or None
    REDIS_TLS_KWARGS: dict = (
        {"ssl_cert_reqs": "required", "ssl_check_hostname": True,
         "ssl_ca_certs": REDIS_SSL_CA_CERTS} if REDIS_SSL else {}
    )
    _redis_auth = (
        f"{quote(REDIS_USERNAME, safe='')}:{quote(REDIS_PASSWORD, safe='')}@"
        if REDIS_PASSWORD else ""
    )
    REDIS_URL: str = f"{'rediss' if REDIS_SSL else 'redis'}://{_redis_auth}{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"

    REDIS_DOCSTORE_NAMESPACE: str = os.getenv("REDIS_DOCSTORE_NAMESPACE", "redis_docs")
    REDIS_INDEX_NAMESPACE: str = os.getenv("REDIS_INDEX_NAMESPACE", "redis_index")
    INGESTION_CACHE_COLLECTION: str = os.getenv("INGESTION_CACHE_COLLECTION", "redis_cache")

    CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "512"))
    CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", "50"))
    TITLE_EXTRACTOR_NODES: int = int(os.getenv("TITLE_EXTRACTOR_NODES", "5"))
    SIMILARITY_TOP_K: int = int(os.getenv("SIMILARITY_TOP_K", "5"))
    RERANK_TOP_K: int = int(os.getenv("RERANK_TOP_K", "3"))

    CHROMA_PERSIST_DIR: str = str(DATA_DIR / "chroma_db")
    BM25_PERSIST_DIR: str = str(DATA_DIR / "storage_bm25")
    DOCUMENTS_DIR: str = str(DATA_DIR / "documents")
    DEFAULT_PERSIST_DIR: str = str(DATA_DIR / "storage")
    PDF_IMAGE_DIR: str = str(DATA_DIR / "image")

    HOST: str = os.getenv("RAG_API_HOST", "0.0.0.0")
    PORT: int = int(os.getenv("RAG_API_PORT", "8011"))
