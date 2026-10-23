import importlib.util
from pathlib import Path
import sys
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'rag_api_service'))
from test_support import WorkspaceTemp

HAS_LLAMA = importlib.util.find_spec('llama_index') is not None


@unittest.skipUnless(HAS_LLAMA, 'run with rag_api_service dependencies for LlamaIndex integration')
class LlamaAdaptersTests(unittest.TestCase):
    def test_chroma_prefilter_excludes_unpublished_and_other_tenant_nodes_before_topk(self):
        import chromadb
        from chromadb.config import Settings
        from llama_index.core.embeddings import MockEmbedding
        from llama_index.core.schema import QueryBundle
        from reliable_ingestion.llama_backend import ChromaWriter
        from reliable_ingestion.query import dense_retriever
        client = chromadb.EphemeralClient(Settings(anonymized_telemetry=False))
        collection = client.create_collection('test-' + uuid.uuid4().hex, embedding_function=None)
        self.addCleanup(client.delete_collection, collection.name)
        writer = ChromaWriter(collection)
        def row(identifier, generation, tenant, vector):
            return {'id': identifier, 'text': identifier, 'embedding': vector,
                    'metadata': {'generation': generation, 'tenant_id': tenant, 'knowledge_base_id': 'oa'}}
        writer.upsert([row('old-1', 'old', 'tenant-a', [1., 0.]),
                       row('staged-1', 'staged', 'tenant-a', [1., 0.]),
                       row('foreign-1', 'live', 'tenant-b', [1., 0.]),
                       row('active-1', 'live', 'tenant-a', [0., 1.])])
        retriever = dense_retriever(collection, {'generations': ['live']}, 'tenant-a', 'oa',
                                    MockEmbedding(embed_dim=2), top_k=1)
        result = retriever.retrieve(QueryBundle('policy', embedding=[1., 0.]))
        self.assertEqual([node.node.node_id for node in result], ['active-1'])
        self.assertEqual(result[0].node.text, 'active-1')

    def test_bm25_jieba_persistence_keeps_original_text_and_node_id(self):
        from reliable_ingestion.llama_backend import build_bm25
        from reliable_ingestion.query import lexical_retriever
        temp = WorkspaceTemp()
        self.addCleanup(temp.cleanup)
        nodes = [
            {'id': 'leave', 'text': '员工请假需要提交审批流程', 'metadata': {}},
            {'id': 'travel', 'text': '差旅报销发票审核', 'metadata': {}},
        ]
        build_bm25(nodes, temp.path)
        result = lexical_retriever(temp.path, nodes, top_k=1).retrieve('请假审批')
        self.assertEqual(result[0].node.node_id, 'leave')
        self.assertEqual(result[0].node.text, '员工请假需要提交审批流程')

    def test_llama_parser_metadata_does_not_pollute_embedding_input(self):
        import hashlib
        from reliable_ingestion.llama_backend import LlamaParser
        from reliable_ingestion.objects import ObjectSource
        from reliable_ingestion.events import DocumentEvent
        from test_reliable_ingestion import event
        temp = WorkspaceTemp()
        self.addCleanup(temp.cleanup)
        data = '# 请假制度\n\n员工请假需要主管审批。'.encode()
        (temp.path / 'policy.md').write_bytes(data)
        source = ObjectSource('local', temp.path, temp.path / 'staging')
        parser = LlamaParser(source, chunk_size=64, chunk_overlap=8)
        ev = DocumentEvent.parse(event(object={'bucket': 'knowledge', 'key': 'policy.md',
                                              'sha256': hashlib.sha256(data).hexdigest()}))
        nodes = parser(ev)
        self.assertIn('员工请假', nodes[0]['text'])
        self.assertNotIn('policy.md', nodes[0]['embedding_text'])
        self.assertNotIn('document_version', nodes[0]['embedding_text'])
