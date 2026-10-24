import importlib.util
import hashlib
import os
from pathlib import Path
import sys
import time
import unittest
import uuid
from test_support import WorkspaceTemp

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'rag_api_service'))
from reliable_ingestion.broker import Consumer, Publisher
from reliable_ingestion.config import RuntimeConfig
from reliable_ingestion.store import Inbox
from test_reliable_ingestion import event


@unittest.skipUnless(os.getenv('RAG_TEST_RABBITMQ_URL') and importlib.util.find_spec('pika'),
                     'set RAG_TEST_RABBITMQ_URL to run against a real RabbitMQ 4 broker')
class RabbitMQIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = WorkspaceTemp()
        self.addCleanup(self.temp.cleanup)
        suffix = uuid.uuid4().hex
        self.config = RuntimeConfig(root=self.temp.path, rabbit_url=os.environ['RAG_TEST_RABBITMQ_URL'],
                                    queue='rag.test.' + suffix, dead_queue='rag.dead.test.' + suffix)
        self.publisher = Publisher(self.config)
        self.addCleanup(self.cleanup_broker)
        self.db = Inbox(self.config.database)

    def cleanup_broker(self):
        if not self.publisher.connection.is_open:
            self.publisher = Publisher(self.config)
        self.publisher.channel.queue_delete(self.config.queue)
        self.publisher.channel.queue_delete(self.config.dead_queue)
        self.publisher.close()

    def receive(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            method, properties, body = self.publisher.channel.basic_get(self.config.queue, auto_ack=False)
            if method:
                return method, properties, body
            self.publisher.connection.process_data_events(time_limit=.1)
        self.fail('expected RabbitMQ delivery within 5 seconds')

    def test_confirmed_duplicate_messages_create_one_inbox_task(self):
        for _ in range(2):
            self.publisher.publish(event(), 'event-1')
        for _ in range(2):
            method, properties, body = self.receive()
            Consumer(self.db).handle(self.publisher.channel, method, properties, body)
        self.assertEqual(len(self.db.jobs()), 1)
        self.assertEqual(self.db.jobs()[0]['status'], 'pending')

    def test_unacknowledged_message_is_redelivered_after_connection_closes(self):
        self.publisher.publish(event(), 'event-1')
        self.receive()
        self.publisher.close()
        self.publisher = Publisher(self.config)
        method, properties, body = self.receive()
        self.assertTrue(method.redelivered)
        Consumer(self.db).handle(self.publisher.channel, method, properties, body)
        self.assertEqual(len(self.db.jobs()), 1)

    @unittest.skipUnless(importlib.util.find_spec('llama_index'), 'requires RAG dependencies')
    def test_create_update_delete_and_stale_replay_through_real_broker(self):
        import chromadb
        from chromadb.config import Settings
        from reliable_ingestion.backend import VersionedBackend
        from reliable_ingestion.events import DocumentEvent
        from reliable_ingestion.llama_backend import ChromaWriter, LlamaParser, build_bm25
        from reliable_ingestion.objects import ObjectSource
        from reliable_ingestion.query import lexical_retriever
        from reliable_ingestion.worker import Worker
        client = chromadb.EphemeralClient(Settings(anonymized_telemetry=False))
        collection = client.create_collection('test-' + uuid.uuid4().hex, embedding_function=None)
        self.addCleanup(client.delete_collection, collection.name)
        source = ObjectSource('local', self.temp.path, self.temp.path / 'staging')
        backend = VersionedBackend(
            self.temp.path, LlamaParser(source, 128, 16), lambda texts: [[1., 0.] for _ in texts],
            ChromaWriter(collection), lambda nodes: None, build_bm25,
            model_fingerprint='offline-test-model', dimensions=2, pipeline_version='test')
        worker = Worker(self.db, backend)

        def deliver(body):
            self.publisher.publish(body, DocumentEvent.parse(body).data['event_id'])
            method, properties, received = self.receive()
            Consumer(self.db).handle(self.publisher.channel, method, properties, received)

        first_snapshot = None
        for version, text in [(1, '# 请假制度\n\n员工请假需要主管审批。'),
                              (2, '# 请假制度\n\n员工请假需要人事审批。')]:
            content = text.encode()
            key = f'policy-v{version}.md'
            (self.temp.path / key).write_bytes(content)
            body = event(version, 'document.created' if version == 1 else 'document.updated',
                         object={'bucket': 'knowledge', 'key': key,
                                 'sha256': hashlib.sha256(content).hexdigest()})
            deliver(body)
            self.assertEqual(worker.run_once(), 'succeeded')
            scope = DocumentEvent.parse(body).scope
            snapshot_id = self.db.snapshot(scope)
            snapshot = backend.read_snapshot(snapshot_id)
            self.assertTrue(all(row['metadata']['document_version'] == version for row in snapshot['nodes']))
            hits = lexical_retriever(self.temp.path / 'snapshots' / snapshot_id, snapshot['nodes']).retrieve('请假审批')
            self.assertIn('主管' if version == 1 else '人事', hits[0].node.text)
            if first_snapshot is None:
                first_snapshot = snapshot_id
            else:
                self.assertNotEqual(first_snapshot, snapshot_id)
        deliver(event(3, 'document.deleted'))
        self.assertEqual(worker.run_once(), 'succeeded')
        deleted_snapshot = self.db.snapshot(scope)
        self.assertEqual(backend.read_snapshot(deleted_snapshot)['nodes'], [])
        deliver(event(2, 'document.updated', event_id='stale-after-delete',
                      object={'bucket': 'knowledge', 'key': key,
                              'sha256': hashlib.sha256(content).hexdigest()}))
        self.assertIsNone(worker.run_once())
        self.assertEqual(self.db.snapshot(scope), deleted_snapshot)
        self.assertEqual(len(self.db.jobs()), 3)

    @unittest.skipUnless(importlib.util.find_spec('llama_index'), 'requires RAG dependencies')
    def test_tree_versions_and_manual_activation_through_real_broker(self):
        import json
        from unittest.mock import patch
        import chromadb
        from chromadb.config import Settings
        from llama_index.core.embeddings import MockEmbedding
        from llama_index.core.schema import QueryBundle
        from reliable_ingestion.backend import VersionedBackend
        from reliable_ingestion.events import DocumentEvent
        from reliable_ingestion.llama_backend import ChromaWriter, LlamaParser, build_bm25
        from reliable_ingestion.objects import ObjectSource
        from reliable_ingestion.query import dense_retriever, lexical_retriever
        from reliable_ingestion.worker import Worker

        client = chromadb.EphemeralClient(Settings(anonymized_telemetry=False))
        collection = client.create_collection('test-' + uuid.uuid4().hex, embedding_function=None)
        self.addCleanup(client.delete_collection, collection.name)
        source = ObjectSource('local', self.temp.path, self.temp.path / 'staging')
        parser = LlamaParser(source, 128, 16)
        parsed_versions, embedding_batches = [], []
        def parse(ev):
            parsed_versions.append(ev.version_id)
            return parser(ev)
        def embed(texts):
            embedding_batches.append(list(texts))
            return [[1., 0.] for _ in texts]
        backend = VersionedBackend(
            self.temp.path, parse, embed, ChromaWriter(collection), lambda nodes: None, build_bm25,
            model_fingerprint='offline-tree-test-model', dimensions=2, pipeline_version='test')
        worker = Worker(self.db, backend)

        def deliver(value):
            ev = DocumentEvent.parse(json.dumps(value, ensure_ascii=False).encode('utf-8'))
            self.publisher.publish(ev.to_bytes(), ev.data['event_id'])
            method, properties, body = self.receive()
            Consumer(self.db).handle(self.publisher.channel, method, properties, body)
            return ev.scope

        common = {'schema_version': 2, 'tenant_id': 'tenant-a', 'knowledge_base_id': 'oa',
                  'document_id': 'tree-policy'}
        for revision, version, parent, approver in [
                (1, 10, None, '主管'), (2, 11, 10, '部门经理'),
                (3, 12, 11, '人事'), (4, 13, 10, '财务')]:
            content = f'# 请假制度 V{revision}\n\n员工请假需要{approver}审批。'.encode('utf-8')
            key = f'tree-policy-v{version}.md'
            (self.temp.path / key).write_bytes(content)
            scope = deliver(common | {'event_id': f'create-tree-{version}',
                                     'event_type': 'document.version.created',
                                     'document_revision': revision, 'version_id': version,
                                     'parent_version_id': parent, 'version_label': f'V{revision}',
                                     'file_name': 'policy.md',
                                     'object': {'bucket': 'knowledge', 'key': key,
                                                'sha256': hashlib.sha256(content).hexdigest()}})
            self.assertEqual(worker.run_once(), 'succeeded')
        self.assertEqual(self.db.versions(scope, 'tree-policy')['active_version_id'], '13')
        before = (len(parsed_versions), len(embedding_batches))
        deliver(common | {'event_id': 'activate-tree-12', 'event_type': 'document.version.activated',
                          'document_revision': 5, 'version_id': 12})
        # A ready version must activate from its immutable manifest even when source
        # download is unavailable. Any model request would also fail this test.
        with patch.object(source, 'materialize', side_effect=AssertionError('source accessed on activation')), \
             patch.object(backend, 'embed', side_effect=AssertionError('embedding called on activation')):
            self.assertEqual(worker.run_once(), 'succeeded')
        self.assertEqual((len(parsed_versions), len(embedding_batches)), before)
        state = self.db.versions(scope, 'tree-policy')
        self.assertEqual(state['latest_version_id'], '13')
        self.assertEqual(state['desired_version_id'], '12')
        self.assertEqual(state['active_version_id'], '12')
        snapshot_id = self.db.snapshot(scope)
        snapshot = backend.read_snapshot(snapshot_id)
        self.assertTrue(snapshot['nodes'])
        self.assertEqual({row['metadata']['version_id'] for row in snapshot['nodes']}, {'12'})
        self.assertEqual({row['version_id']: row['parent_version_id'] for row in state['versions']},
                         {'10': None, '11': '10', '12': '11', '13': '10'})
        lexical = lexical_retriever(self.temp.path / 'snapshots' / snapshot_id, snapshot['nodes'])
        lexical_hits = lexical.retrieve('请假审批')
        self.assertTrue(lexical_hits)
        self.assertTrue(all(hit.node.metadata['version_id'] == '12' for hit in lexical_hits))
        self.assertIn('人事', lexical_hits[0].node.text)
        stored = collection.get(include=['metadatas'])
        self.assertEqual({metadata['version_id'] for metadata in stored['metadatas']},
                         {'10', '11', '12', '13'})
        dense = dense_retriever(collection, snapshot, 'tenant-a', 'oa', MockEmbedding(embed_dim=2), top_k=5)
        dense_hits = dense.retrieve(QueryBundle('请假审批', embedding=[1., 0.]))
        self.assertTrue(dense_hits)
        self.assertTrue(all(hit.node.metadata['version_id'] == '12' for hit in dense_hits))
