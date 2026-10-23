import hashlib
import json
from pathlib import Path
import sys
import unittest
from test_support import WorkspaceTemp

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'rag_api_service'))
from reliable_ingestion.backend import VersionedBackend
from reliable_ingestion.errors import PermanentError
from reliable_ingestion.objects import ObjectSource
from reliable_ingestion.store import Inbox
from reliable_ingestion.worker import Worker
from test_reliable_ingestion import event


class Vectors:
    def __init__(self):
        self.rows = {}

    def upsert(self, nodes):
        for node in nodes:
            self.rows[node['id']] = node

    def verify(self, generation, ids):
        actual = {key for key, value in self.rows.items() if value['metadata']['generation'] == generation}
        if actual != set(ids):
            raise RuntimeError('incomplete vector write')


class VersionedIndexingTests(unittest.TestCase):
    def setUp(self):
        self.temp = WorkspaceTemp()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = Inbox(self.root / 'jobs.sqlite3', retry_base=0)
        self.texts = [f'policy paragraph {i}' for i in range(100)]
        self.successful_embeddings = []
        self.fail_embedding_call = None
        self.calls = 0
        self.fail_bm25 = False
        self.vectors = Vectors()
        self.backend = VersionedBackend(
            self.root, self.parse, self.embed, self.vectors, lambda nodes: None,
            self.build_bm25, model_fingerprint='model-1:revision-1', dimensions=2,
            pipeline_version='parser-v1', batch_size=20)
        self.worker = Worker(self.db, self.backend)

    def parse(self, ev):
        return [{'text': text, 'embedding_text': text, 'metadata': {}} for text in self.texts]

    def embed(self, texts):
        self.calls += 1
        if self.calls == self.fail_embedding_call:
            raise TimeoutError('chunk 21 failed')
        self.successful_embeddings.extend(texts)
        return [[1.0, 0.0] for _ in texts]

    def build_bm25(self, nodes, directory):
        if self.fail_bm25:
            raise OSError('bm25 write failed')
        (directory / 'bm25-test.json').write_text(json.dumps([n['id'] for n in nodes]))

    def ingest(self, version=1, action='document.created'):
        self.db.accept(event(version, action))
        return self.worker.run_once()

    def test_chunk_21_failure_keeps_old_snapshot_and_reuses_first_20_embeddings(self):
        self.assertEqual(self.ingest(), 'succeeded')
        old = self.db.snapshot(self.db.jobs()[0]['scope'])
        self.texts = [f'updated paragraph {i}' for i in range(100)]
        self.fail_embedding_call = self.calls + 2
        self.assertEqual(self.ingest(2, 'document.updated'), 'retry')
        self.assertEqual(self.db.snapshot(self.db.jobs()[0]['scope']), old)
        count = len(self.successful_embeddings)
        self.assertEqual(count, 120)
        self.fail_embedding_call = None
        self.assertEqual(self.worker.run_once(), 'succeeded')
        self.assertEqual(len(self.successful_embeddings), 200)
        self.assertNotEqual(self.db.snapshot(self.db.jobs()[0]['scope']), old)

    def test_bm25_failure_does_not_publish_new_vectors(self):
        self.ingest()
        old = self.db.snapshot(self.db.jobs()[0]['scope'])
        self.fail_bm25 = True
        self.texts = ['changed text']
        self.assertEqual(self.ingest(2, 'document.updated'), 'retry')
        self.assertEqual(self.db.snapshot(self.db.jobs()[0]['scope']), old)

    def test_only_changed_chunk_requires_new_embedding(self):
        self.ingest()
        self.texts[42] = 'changed paragraph'
        self.ingest(2, 'document.updated')
        self.assertEqual(len(self.successful_embeddings), 101)
        snapshot = self.backend.read_snapshot(self.db.snapshot(self.db.jobs()[0]['scope']))
        self.assertEqual(len(snapshot['nodes']), 100)
        self.assertTrue(all(n['metadata']['document_version'] == 2 for n in snapshot['nodes']))

    def test_model_revision_invalidates_embedding_cache(self):
        self.ingest()
        self.backend.model_fingerprint = 'model-1:revision-2'
        self.ingest(2, 'document.updated')
        self.assertEqual(len(self.successful_embeddings), 200)

    def test_empty_document_is_permanent_failure(self):
        self.texts = []
        self.assertEqual(self.ingest(), 'dead')
        self.assertIsNone(self.db.snapshot(self.db.jobs()[0]['scope']))

    def test_delete_does_not_parse_or_embed_and_snapshot_excludes_document(self):
        self.ingest()
        self.texts = None
        self.assertEqual(self.ingest(2, 'document.deleted'), 'succeeded')
        snapshot = self.backend.read_snapshot(self.db.snapshot(self.db.jobs()[0]['scope']))
        self.assertEqual(snapshot['nodes'], [])
        self.assertEqual(snapshot['generations'], [])

    def test_embedding_dimension_error_never_publishes(self):
        self.backend.dimensions = 3
        self.assertEqual(self.ingest(), 'dead')
        self.assertEqual(self.vectors.rows, {})

    def test_snapshot_contains_other_documents_when_one_is_replaced(self):
        self.ingest()
        self.db.accept(event(event_id='other', document_id='another'))
        self.worker.run_once()
        self.texts = ['replacement']
        self.ingest(2, 'document.updated')
        snapshot = self.backend.read_snapshot(self.db.snapshot(self.db.jobs()[0]['scope']))
        self.assertEqual(len(snapshot['nodes']), 101)
        self.assertEqual(len(snapshot['generations']), 2)

    def test_one_document_cannot_silently_mix_new_embedding_model_with_other_documents(self):
        self.assertEqual(self.ingest(), 'succeeded')
        self.db.accept(event(event_id='other', document_id='another'))
        self.assertEqual(self.worker.run_once(), 'succeeded')
        old = self.db.snapshot(self.db.jobs()[0]['scope'])
        self.backend.model_fingerprint = 'different-vector-space'
        self.assertEqual(self.ingest(2, 'document.updated'), 'dead')
        self.assertEqual(self.db.snapshot(self.db.jobs()[0]['scope']), old)

    def test_process_exit_before_publish_is_recoverable(self):
        self.assertEqual(self.ingest(), 'succeeded')
        old = self.db.snapshot(self.db.jobs()[0]['scope'])
        original = self.backend.lexical_builder
        def interrupted(*args):
            raise SystemExit('simulated process termination')
        self.backend.lexical_builder = interrupted
        self.db.accept(event(2, 'document.updated'))
        with self.assertRaises(SystemExit):
            self.worker.run_once()
        self.assertEqual(self.db.snapshot(self.db.jobs()[0]['scope']), old)
        self.db.recover_interrupted()
        self.backend.lexical_builder = original
        self.assertEqual(self.worker.run_once(), 'succeeded')
        self.assertEqual(len(self.successful_embeddings), 100)


class ObjectSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = WorkspaceTemp()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.objects = self.root / 'objects'
        self.objects.mkdir()
        self.source = ObjectSource('local', self.objects, self.root / 'staging', max_bytes=32)

    def payload(self, key, digest):
        from reliable_ingestion.events import DocumentEvent
        return DocumentEvent.parse(event(object={'bucket': 'knowledge', 'key': key, 'sha256': digest}))

    def test_download_checks_exact_content_and_produces_safe_temporary_name(self):
        data = b'# policy\nhello'
        (self.objects / 'file.md').write_bytes(data)
        with self.source.materialize(self.payload('file.md', hashlib.sha256(data).hexdigest())) as path:
            self.assertEqual(path.read_bytes(), data)
        self.assertFalse(path.exists())

    def test_path_escape_is_rejected(self):
        (self.root / 'secret.md').write_bytes(b'secret')
        with self.assertRaises(PermanentError):
            with self.source.materialize(self.payload('../secret.md', 'a' * 64)):
                self.fail('escaped configured object root')

    def test_overwritten_object_cannot_be_indexed_as_old_version(self):
        (self.objects / 'file.md').write_bytes(b'new version')
        with self.assertRaises(PermanentError):
            with self.source.materialize(self.payload('file.md', 'a' * 64)):
                self.fail('checksum mismatch was ignored')

    def test_download_size_is_bounded(self):
        (self.objects / 'large.md').write_bytes(b'x' * 33)
        with self.assertRaises(PermanentError):
            with self.source.materialize(self.payload('large.md', hashlib.sha256(b'x' * 33).hexdigest())):
                self.fail('oversized content was indexed')
