from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class RuntimeConfig:
    root: Path
    rabbit_url: str
    queue: str = 'rag.documents.v1'
    dead_queue: str = 'rag.documents.dead.v1'
    max_attempts: int = 4
    retry_base: float = 5
    version_wait_timeout: float = 3600
    source_mode: str = 'local'
    local_root: Path = Path('file/objects')
    max_object_bytes: int = 100 * 1024 * 1024
    oss_endpoint: str = ''
    oss_bucket: str = ''
    oss_key_prefix: str = ''
    chroma_host: str = '127.0.0.1'
    chroma_port: int = 8002
    chroma_collection: str = 'rag_event_chunks'
    embedding_revision: str = '1'
    pipeline_version: str = 'markdown-sentence-v1'

    @property
    def database(self):
        return self.root / 'inbox.sqlite3'

    @classmethod
    def from_env(cls):
        from dotenv import load_dotenv
        rag_root = Path(__file__).resolve().parents[2]
        load_dotenv(rag_root / '.env')
        return cls(
            root=Path(os.getenv('RAG_EVENT_DATA_DIR', str(rag_root / 'file' / 'events'))).resolve(),
            rabbit_url=os.getenv('RABBITMQ_URL', 'amqp://rag:rag-local-password@127.0.0.1:5672/%2F'),
            queue=os.getenv('RAG_EVENT_QUEUE', 'rag.documents.v1'),
            dead_queue=os.getenv('RAG_DEAD_QUEUE', 'rag.documents.dead.v1'),
            max_attempts=int(os.getenv('RAG_EVENT_MAX_ATTEMPTS', '4')),
            retry_base=float(os.getenv('RAG_EVENT_RETRY_BASE', '5')),
            version_wait_timeout=float(os.getenv('RAG_VERSION_WAIT_TIMEOUT', '3600')),
            source_mode=os.getenv('RAG_OBJECT_SOURCE', 'local'),
            local_root=Path(os.getenv('RAG_LOCAL_OBJECT_ROOT', str(rag_root / 'file' / 'objects'))).resolve(),
            max_object_bytes=int(os.getenv('RAG_MAX_OBJECT_BYTES', str(100 * 1024 * 1024))),
            oss_endpoint=os.getenv('OSS_ENDPOINT', ''), oss_bucket=os.getenv('OSS_BUCKET', ''),
            oss_key_prefix=os.getenv('OSS_KEY_PREFIX', ''),
            chroma_host=os.getenv('RAG_CHROMA_HOST', '127.0.0.1'),
            chroma_port=int(os.getenv('RAG_CHROMA_PORT', '8002')),
            chroma_collection=os.getenv('RAG_CHROMA_COLLECTION', 'rag_event_chunks'),
            embedding_revision=os.getenv('RAG_EMBEDDING_REVISION', '1'),
            pipeline_version=os.getenv('RAG_PIPELINE_VERSION', 'markdown-sentence-v1'),
        )
