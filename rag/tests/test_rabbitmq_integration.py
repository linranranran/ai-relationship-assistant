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
