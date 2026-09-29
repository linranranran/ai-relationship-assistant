# ============================================================
# AI 人际关系助手 — 配置管理（简单版）
# 加载 .env 文件，通过 os.getenv 读取
# ============================================================
import logging
import os
from dotenv import load_dotenv

# 自动找项目根目录下的 .env 文件
# __file__ = backend/config.py → 上级目录就是项目根目录
_env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
load_dotenv(_env_path)


def _get(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _get_int(key: str, default: int) -> int:
    return int(os.getenv(key, default))


def _get_float(key: str, default: float) -> float:
    return float(os.getenv(key, default))

LOG_LEVEL = _get("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
# logger.info("创建人物成功: %s", person_id)
# logger.warning("缺少 owner_id，已拒绝请求")
# logger.error("Neo4j 连接失败", exc_info=True)   # exc_info=True 自动打印堆栈

# --- LLM ---
LLM_API_KEY = _get("LLM_API_KEY", "sk-placeholder")
LLM_BASE_URL = _get("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = _get("LLM_MODEL", "deepseek-chat")
LLM_REQUEST_TIMEOUT_SECONDS = _get_float("LLM_REQUEST_TIMEOUT_SECONDS", 600.0)

# running 幂等记录多久后允许同一个 ID 的重试抢占；这不是工作流执行超时。
AGENT_REQUEST_STALE_SECONDS = _get_int("AGENT_REQUEST_STALE_SECONDS", 300)
AGENT_TOOL_STALE_SECONDS = _get_int("AGENT_TOOL_STALE_SECONDS", 300)

# --- Embedding ---
EMBEDDING_API_KEY = _get("EMBEDDING_API_KEY", "sk-placeholder")
EMBEDDING_BASE_URL = _get("EMBEDDING_BASE_URL", "https://api.openai.com/v1")
EMBEDDING_MODEL = _get("EMBEDDING_MODEL", "text-embedding-3-small")

# --- Neo4j ---
NEO4J_URI = _get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = _get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = _get("NEO4J_PASSWORD", "password")

# --- PostgreSQL ---
# 业务幂等表和未来的 LangGraph Checkpointer 可以使用同一 PostgreSQL 实例，
# 但它们各自管理自己的表。密码只放在本地 .env，不拼接进日志。
POSTGRES_HOST = _get("POSTGRES_HOST", "localhost")
POSTGRES_PORT = _get_int("POSTGRES_PORT", 5432)
POSTGRES_USER = _get("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = _get("POSTGRES_PASSWORD", "")
POSTGRES_DATABASE = _get("POSTGRES_DATABASE", "relation-agent")
POSTGRES_ADMIN_DATABASE = _get("POSTGRES_ADMIN_DATABASE", "postgres")
POSTGRES_POOL_MIN_SIZE = _get_int("POSTGRES_POOL_MIN_SIZE", 1)
POSTGRES_POOL_MAX_SIZE = _get_int("POSTGRES_POOL_MAX_SIZE", 10)
POSTGRES_POOL_TIMEOUT_SECONDS = _get_int("POSTGRES_POOL_TIMEOUT_SECONDS", 10)

# --- LangGraph Checkpointer ---
# memory 仅用于本地调试；部署环境使用 postgres 才能跨进程恢复中断状态。
AGENT_CHECKPOINTER_BACKEND = _get("AGENT_CHECKPOINTER_BACKEND", "memory")
AGENT_CHECKPOINT_POOL_MIN_SIZE = _get_int("AGENT_CHECKPOINT_POOL_MIN_SIZE", 1)
AGENT_CHECKPOINT_POOL_MAX_SIZE = _get_int("AGENT_CHECKPOINT_POOL_MAX_SIZE", 5)
AGENT_CHECKPOINT_POOL_TIMEOUT_SECONDS = _get_int(
    "AGENT_CHECKPOINT_POOL_TIMEOUT_SECONDS", 10
)

# --- Agent 记忆与上下文预算 ---
# 这些值限制“每次发送给模型的内容”，不会限制 PostgreSQL 保存多少原始消息。
AGENT_MAX_INPUT_TOKENS = _get_int("AGENT_MAX_INPUT_TOKENS", 12000)
AGENT_RESERVED_OUTPUT_TOKENS = _get_int("AGENT_RESERVED_OUTPUT_TOKENS", 2000)
AGENT_CONTEXT_SAFETY_TOKENS = _get_int("AGENT_CONTEXT_SAFETY_TOKENS", 1000)
AGENT_RECENT_MESSAGE_LIMIT = _get_int("AGENT_RECENT_MESSAGE_LIMIT", 12)
AGENT_SUMMARY_TRIGGER_RATIO = _get_float("AGENT_SUMMARY_TRIGGER_RATIO", 0.70)
AGENT_MEMORY_RETRIEVAL_LIMIT = _get_int("AGENT_MEMORY_RETRIEVAL_LIMIT", 5)
AGENT_SUMMARY_BATCH_LIMIT = _get_int("AGENT_SUMMARY_BATCH_LIMIT", 40)
AGENT_SUMMARY_MIN_MESSAGES = _get_int("AGENT_SUMMARY_MIN_MESSAGES", 12)
AGENT_MEMORY_WORKER_POLL_SECONDS = _get_float(
    "AGENT_MEMORY_WORKER_POLL_SECONDS", 2.0
)
AGENT_MEMORY_JOB_STALE_SECONDS = _get_int("AGENT_MEMORY_JOB_STALE_SECONDS", 300)

# --- ChromaDB ---
CHROMA_PERSIST_DIR = _get("CHROMA_PERSIST_DIR", "./chroma_data")

# --- 服务 ---
HOST = _get("HOST", "0.0.0.0")
PORT = _get_int("PORT", 8000)
SECRET_KEY = _get("SECRET_KEY", "change-me-to-a-random-string")

# --- 日志 ---
LOG_LEVEL = _get("LOG_LEVEL", "INFO")
