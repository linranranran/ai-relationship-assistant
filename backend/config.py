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

# --- Embedding ---
EMBEDDING_API_KEY = _get("EMBEDDING_API_KEY", "sk-placeholder")
EMBEDDING_BASE_URL = _get("EMBEDDING_BASE_URL", "https://api.openai.com/v1")
EMBEDDING_MODEL = _get("EMBEDDING_MODEL", "text-embedding-3-small")

# --- Neo4j ---
NEO4J_URI = _get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = _get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = _get("NEO4J_PASSWORD", "password")

# --- Mysql ---
MYSQL_HOST = _get("MYSQL_HOST", _get("MYSQL_URI", "localhost"))
MYSQL_PORT = _get_int("MYSQL_PORT", 3306)
MYSQL_USER = _get("MYSQL_USER", "mysql")
MYSQL_PASSWORD = _get("MYSQL_PASSWORD", "mysql")
MYSQL_DATABASE = _get("MYSQL_DATABASE", "relation_agent")

# --- ChromaDB ---
CHROMA_PERSIST_DIR = _get("CHROMA_PERSIST_DIR", "./chroma_data")

# --- 服务 ---
HOST = _get("HOST", "0.0.0.0")
PORT = _get_int("PORT", 8000)
SECRET_KEY = _get("SECRET_KEY", "change-me-to-a-random-string")

# --- 日志 ---
LOG_LEVEL = _get("LOG_LEVEL", "INFO")
