"""LlamaIndex parsing/BM25, Chroma upserts and Redis staging mirrors."""
import json
from pathlib import Path

from .backend import VersionedBackend
from .errors import PermanentError
from .objects import ObjectSource


def text_node(row, embedding=False):
    from llama_index.core.schema import TextNode
    metadata = dict(row['metadata'])
    return TextNode(id_=row['id'], text=row['text'], metadata=metadata,
                    excluded_embed_metadata_keys=list(metadata),
                    excluded_llm_metadata_keys=[key for key in metadata if key != 'file_name'],
                    embedding=row.get('embedding') if embedding else None)


class LlamaParser:
    def __init__(self, source, chunk_size=512, chunk_overlap=50):
        self.source, self.chunk_size, self.chunk_overlap = source, chunk_size, chunk_overlap

    def __call__(self, event):
        from llama_index.core import Document
        from llama_index.core.ingestion import IngestionPipeline
        from llama_index.core.node_parser import MarkdownNodeParser, SentenceSplitter
        from llama_index.core.schema import MetadataMode
        with self.source.materialize(event) as path:
            suffix = path.suffix.lower()
            if suffix == '.pdf':
                import pymupdf4llm
                text = pymupdf4llm.to_markdown(str(path), write_images=False)
            elif suffix in {'.md', '.markdown', '.txt'}:
                try:
                    text = path.read_text(encoding='utf-8-sig')
                except UnicodeError as exc:
                    raise PermanentError('text files must use UTF-8 encoding') from exc
            else:
                raise PermanentError('supported files: PDF, Markdown, UTF-8 text')
        transformations = []
        if suffix != '.txt':
            transformations.append(MarkdownNodeParser(include_metadata=True, include_prev_next_rel=False))
        transformations.append(SentenceSplitter(chunk_size=self.chunk_size, chunk_overlap=self.chunk_overlap))
        # No docstore/vector side effects during parsing; versions publish only through Worker.
        nodes = IngestionPipeline(transformations=transformations).run(documents=[Document(text=text)])
        return [{'text': node.text, 'embedding_text': node.get_content(metadata_mode=MetadataMode.EMBED),
                 'metadata': {key: value for key, value in node.metadata.items()
                              if isinstance(value, (str, int, float, bool))}} for node in nodes]


class ChromaWriter:
    def __init__(self, collection):
        self.collection = collection

    def upsert(self, nodes):
        from llama_index.core.vector_stores.utils import node_to_metadata_dict
        self.collection.upsert(
            ids=[row['id'] for row in nodes], embeddings=[row['embedding'] for row in nodes],
            documents=[row['text'] for row in nodes],
            metadatas=[node_to_metadata_dict(text_node(row), remove_text=True, flat_metadata=True)
                       for row in nodes],
        )

    def verify(self, generation, ids):
        result = self.collection.get(where={'generation': generation}, include=[])
        if set(result['ids']) != set(ids):
            raise RuntimeError('Chroma generation is incomplete')


def lexical_tokens(text):
    import jieba
    return ' '.join(token for token in jieba.cut(text, HMM=False) if token.strip())


def build_bm25(nodes, directory):
    from llama_index.retrievers.bm25 import BM25Retriever
    prepared = []
    for row in nodes:
        node = text_node(row)
        node.text = lexical_tokens(row['text'])
        prepared.append(node)
    retriever = BM25Retriever.from_defaults(
        nodes=prepared, similarity_top_k=min(5, len(prepared)),
        language='zh', skip_stemming=True, token_pattern=r'(?u)\b\w+\b')
    path = str(directory / 'bm25')
    retriever.persist(path)
    loaded = BM25Retriever.from_persist_dir(path)
    if len(loaded.corpus) != len(nodes):
        raise RuntimeError('BM25 snapshot is incomplete')


def configure_models():
    from config.settings import Settings as Config
    from llama_index.core import Settings
    from llama_index.embeddings.openai_like import OpenAILikeEmbedding
    from llama_index.llms.openai_like import OpenAILike
    if not Config.API_KEY or Config.API_KEY == 'replace-me':
        raise RuntimeError('configure DASHSCOPE_API_KEY in rag/.env')
    Settings.llm = OpenAILike(
        api_key=Config.API_KEY, api_base=Config.API_BASE_URL, model=Config.MODEL,
        temperature=Config.TEMPERATURE, max_tokens=Config.LLM_MAX_TOKENS,
        context_window=Config.LLM_CONTEXT_WINDOW, is_chat_model=True,
        additional_kwargs={'extra_body': {'enable_thinking': False}})
    Settings.embed_model = OpenAILikeEmbedding(
        api_key=Config.API_KEY, api_base=Config.API_BASE_URL, model_name=Config.EMBEDDING_MODEL,
        dimensions=Config.EMBEDDING_DIMENSIONS, embed_batch_size=Config.EMBEDDING_BATCH_SIZE,
        max_retries=2, timeout=60)
    return Settings, Config


def chroma_collection(config):
    import chromadb
    client = chromadb.HttpClient(host=config.chroma_host, port=config.chroma_port)
    return client.get_or_create_collection(config.chroma_collection, embedding_function=None)


def model_fingerprint(settings, config):
    return json.dumps([settings.API_BASE_URL, settings.EMBEDDING_MODEL,
                       config.embedding_revision], ensure_ascii=False)


def make_backend(config):
    from llama_index.storage.docstore.redis import RedisDocumentStore
    from llama_index.storage.index_store.redis import RedisIndexStore
    from llama_index.storage.kvstore.redis import RedisKVStore
    from llama_index.core.data_structs import IndexDict
    models, settings = configure_models()
    collection = chroma_collection(config)
    redis = RedisKVStore(redis_uri=settings.REDIS_URL, socket_connect_timeout=5,
                        socket_timeout=30, **settings.REDIS_TLS_KWARGS)
    docs = RedisDocumentStore(redis_kvstore=redis, namespace=settings.REDIS_DOCSTORE_NAMESPACE + ':events')
    indexes = RedisIndexStore(redis_kvstore=redis, namespace=settings.REDIS_INDEX_NAMESPACE + ':events')

    def mirror(rows):
        nodes = [text_node(row) for row in rows]
        docs.add_documents(nodes, allow_update=True)
        actual = docs.get_nodes([node.node_id for node in nodes])
        if len(actual) != len(nodes):
            raise RuntimeError('Redis generation is incomplete')
        indexes.add_index_struct(IndexDict(index_id=rows[0]['metadata']['generation'],
                                           nodes_dict={row['id']: row['id'] for row in rows}))

    source = ObjectSource(config.source_mode, config.local_root, config.root / 'staging',
                          max_bytes=config.max_object_bytes, oss_endpoint=config.oss_endpoint,
                          oss_bucket=config.oss_bucket, key_prefix=config.oss_key_prefix)
    fingerprint = model_fingerprint(settings, config)
    pipeline_version = f'{config.pipeline_version}:{settings.CHUNK_SIZE}:{settings.CHUNK_OVERLAP}'
    return VersionedBackend(
        config.root, LlamaParser(source, settings.CHUNK_SIZE, settings.CHUNK_OVERLAP),
        models.embed_model.get_text_embedding_batch, ChromaWriter(collection), mirror, build_bm25,
        model_fingerprint=fingerprint, dimensions=settings.EMBEDDING_DIMENSIONS,
        pipeline_version=pipeline_version, batch_size=settings.EMBEDDING_BATCH_SIZE)
