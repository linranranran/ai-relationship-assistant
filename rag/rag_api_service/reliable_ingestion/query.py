import json
from pathlib import Path

from .events import canonical
from .backend import embedding_signature
from .llama_backend import chroma_collection, configure_models, lexical_tokens, text_node, model_fingerprint
from .store import Inbox


def dense_retriever(collection, snapshot, tenant_id, knowledge_base_id, embed_model, top_k=5):
    from llama_index.core import VectorStoreIndex
    from llama_index.core.vector_stores import MetadataFilter, MetadataFilters, FilterOperator
    from llama_index.vector_stores.chroma import ChromaVectorStore
    filters = MetadataFilters(filters=[
        MetadataFilter(key='tenant_id', value=tenant_id),
        MetadataFilter(key='knowledge_base_id', value=knowledge_base_id),
        MetadataFilter(key='generation', value=snapshot['generations'], operator=FilterOperator.IN),
    ])
    index = VectorStoreIndex.from_vector_store(ChromaVectorStore(chroma_collection=collection),
                                               embed_model=embed_model)
    return index.as_retriever(similarity_top_k=top_k, filters=filters)


def lexical_retriever(directory, nodes, top_k=5):
    from llama_index.core.retrievers import BaseRetriever
    from llama_index.core.schema import NodeWithScore
    from llama_index.retrievers.bm25 import BM25Retriever
    underlying = BM25Retriever.from_persist_dir(str(Path(directory) / 'bm25'))
    # These tokenizer options are not part of this LlamaIndex version's persisted args.
    underlying.skip_stemming = True
    underlying.token_pattern = r'(?u)\b\w+\b'
    underlying.similarity_top_k = min(top_k, len(nodes))
    originals = {row['id']: text_node(row) for row in nodes}

    class OriginalTextRetriever(BaseRetriever):
        def _retrieve(self, query_bundle):
            results = underlying.retrieve(lexical_tokens(query_bundle.query_str))
            return [NodeWithScore(node=originals[result.node.node_id], score=result.score) for result in results]

    return OriginalTextRetriever()


class EventQueryService:
    def __init__(self, config):
        self.config = config
        self.inbox = Inbox(config.database)
        self.models, self.settings = configure_models()
        self.collection = chroma_collection(config)

    async def query(self, query, tenant_id, knowledge_base_id):
        from llama_index.core.retrievers import QueryFusionRetriever
        from llama_index.core.response_synthesizers import get_response_synthesizer
        from llama_index.postprocessor.dashscope_rerank import DashScopeRerank
        from starlette.concurrency import run_in_threadpool
        scope = canonical([tenant_id, knowledge_base_id])
        # Read exactly once. Older snapshots stay immutable for in-flight readers.
        snapshot_id = await run_in_threadpool(self.inbox.snapshot, scope)
        if not snapshot_id:
            return {'answer': '该知识库尚无已发布的文档，请查看索引任务状态。', 'sources': [], 'snapshot_id': None}
        directory = self.config.root / 'snapshots' / snapshot_id
        snapshot = json.loads((directory / 'snapshot.json').read_text(encoding='utf-8'))
        if snapshot['scope'] != scope:
            raise RuntimeError('snapshot scope mismatch')
        if not snapshot['nodes']:
            return {'answer': '该知识库暂无可检索的文档。', 'sources': [], 'snapshot_id': snapshot_id}
        signature = embedding_signature(model_fingerprint(self.settings, self.config),
                                        self.settings.EMBEDDING_DIMENSIONS)
        if snapshot['embedding_config'] != signature:
            raise RuntimeError('configured embedding model differs from the published snapshot')
        dense = dense_retriever(self.collection, snapshot, tenant_id, knowledge_base_id,
                                self.models.embed_model, self.settings.SIMILARITY_TOP_K)
        lexical = await run_in_threadpool(lexical_retriever, directory, snapshot['nodes'],
                                          self.settings.SIMILARITY_TOP_K)
        retriever = QueryFusionRetriever(
            retrievers=[dense, lexical], similarity_top_k=self.settings.SIMILARITY_TOP_K,
            num_queries=1, mode='dist_based_score', use_async=True, verbose=False)
        results = await retriever.aretrieve(query)
        reranker = DashScopeRerank(model=self.settings.RERANK_MODEL, api_key=self.settings.API_KEY,
                                  top_n=self.settings.RERANK_TOP_K)
        ranked = await run_in_threadpool(reranker.postprocess_nodes, results, query_str=query)
        answer = await get_response_synthesizer(streaming=False).asynthesize(query=query, nodes=ranked)
        return {
            'answer': str(answer), 'snapshot_id': snapshot_id,
            'sources': [{'node_id': result.node.node_id, 'score': result.score,
                         'content': result.node.text, 'metadata': result.node.metadata} for result in ranked],
        }
