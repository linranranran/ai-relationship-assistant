from pathlib import Path
import sys
import unittest

from test_support import WorkspaceTemp
from version_tree_support import tree_event
from test_versioned_indexing import Vectors

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'rag_api_service'))
from reliable_ingestion.backend import VersionedBackend
from reliable_ingestion.events import DocumentEvent
from reliable_ingestion.errors import PermanentError
from reliable_ingestion.store import Inbox
from reliable_ingestion.worker import Worker


class TreeContractTests(unittest.TestCase):
    def test_version_identity_and_control_revision_are_independent(self):
        ev = DocumentEvent.parse(tree_event(5, 12, action='document.version.activated'))
        self.assertTrue(ev.is_versioned)
        self.assertEqual(ev.revision, 5)
        self.assertEqual(ev.version_id, '12')
        self.assertNotIn('object', ev.data)
        created = DocumentEvent.parse(tree_event(4, 13, 10))
        self.assertEqual(created.data['parent_version_id'], '10')

    def test_invalid_version_events_rejected_before_persistence(self):
        for body in [tree_event(document_revision=True), tree_event(version_id=False),
                     tree_event(parent=10), tree_event(extra='unknown'),
                     tree_event(action='document.version.activated', object={}),
                     tree_event(action='document.deleted', parent_version_id='illegal'),
                     tree_event(version_label='\ud800')]:
            with self.subTest(body=repr(body)), self.assertRaises(PermanentError):
                DocumentEvent.parse(body)


class TreeIndexingTests(unittest.TestCase):
    def setUp(self):
        self.temp = WorkspaceTemp()
        self.addCleanup(self.temp.cleanup)
        self.now = 100.
        self.db = Inbox(self.temp.path / 'jobs.sqlite3', retry_base=0, clock=lambda: self.now)
        self.parsed = []
        self.embedded = []
        self.fail_versions = set()
        self.fail_bm25 = False
        self.on_parse = None
        self.vectors = Vectors()
        self.backend = VersionedBackend(self.temp.path, self.parse, self.embed, self.vectors,
                                        lambda nodes: None, self.lexical, 'model', 2, 'parser')
        self.worker = Worker(self.db, self.backend)
        self.scope = DocumentEvent.parse(tree_event()).scope

    def parse(self, event):
        self.parsed.append(event.version_id)
        if self.on_parse:
            self.on_parse(event)
        if event.version_id in self.fail_versions:
            raise TimeoutError('model not available')
        return [{'text': 'policy for version ' + event.version_id, 'metadata': {}}]

    def embed(self, texts):
        self.embedded.extend(texts)
        return [[1., 0.] for _ in texts]

    def lexical(self, nodes, directory):
        if self.fail_bm25:
            raise OSError('BM25 unavailable')
        (directory / 'lexical.txt').write_text('\n'.join(row['id'] for row in nodes))

    def ingest(self, *args, **kwargs):
        self.db.accept(tree_event(*args, **kwargs))
        return self.worker.run_once()

    def graph(self):
        for revision, version, parent in [(1, 10, None), (2, 11, 10), (3, 12, 11), (4, 13, 10)]:
            self.assertEqual(self.ingest(revision, version, parent), 'succeeded')

    def active_nodes(self):
        return self.backend.read_snapshot(self.db.snapshot(self.scope))['nodes']

    def test_branch_then_repeated_manual_activation_reuses_complete_generations(self):
        self.graph()
        self.assertEqual(self.active_nodes()[0]['metadata']['version_id'], '13')
        for revision, version in [(5, 12), (6, 13), (7, 12)]:
            self.assertEqual(self.ingest(revision, version, action='document.version.activated'), 'succeeded')
            self.assertEqual({n['metadata']['version_id'] for n in self.active_nodes()}, {str(version)})
        self.assertEqual(self.parsed, ['10', '11', '12', '13'])
        self.assertEqual(len(self.embedded), 4)
        state = self.db.versions(self.scope, 'policy')
        self.assertEqual(state['latest_version_id'], '13')
        self.assertEqual(state['active_version_id'], '12')
        self.assertEqual(state['desired_revision'], 7)
        self.assertEqual({row['version_id']: row['parent_version_id'] for row in state['versions']},
                         {'10': None, '11': '10', '12': '11', '13': '10'})

    def test_slow_new_branch_cannot_override_later_manual_selection(self):
        for revision, version, parent in [(1, 10, None), (2, 11, 10), (3, 12, 11)]:
            self.ingest(revision, version, parent)
        def change_selection(event):
            if event.version_id == '13':
                self.db.accept(tree_event(5, 12, action='document.version.activated'))
        self.on_parse = change_selection
        self.assertEqual(self.ingest(4, 13, 10), 'succeeded')
        self.assertEqual(self.active_nodes()[0]['metadata']['version_id'], '12')
        self.assertEqual(self.worker.run_once(), 'succeeded')
        state = self.db.versions(self.scope, 'policy')
        self.assertEqual(state['active_version_id'], '12')
        self.assertEqual(state['latest_version_id'], '13')
        self.assertTrue(all(row['index_status'] == 'ready' for row in state['versions']))

    def test_activation_arriving_before_creation_waits_without_consuming_attempts(self):
        self.ingest(4, 13, 10)
        self.assertEqual(self.ingest(5, 12, action='document.version.activated'), 'waiting')
        self.assertEqual(self.db.job('tree-5')['attempts'], 0)
        for _ in range(6):
            self.now += 6
            self.assertEqual(self.worker.run_once(), 'waiting')
        self.assertEqual(self.db.job('tree-5')['attempts'], 0)
        self.assertEqual(self.ingest(3, 12, 11), 'succeeded')
        self.now += 6
        self.assertEqual(self.worker.run_once(), 'succeeded')
        self.assertEqual(self.active_nodes()[0]['metadata']['version_id'], '12')

    def test_failed_activation_snapshot_preserves_previous_active_version(self):
        self.graph()
        old = self.db.snapshot(self.scope)
        self.fail_bm25 = True
        self.assertEqual(self.ingest(5, 12, action='document.version.activated'), 'retry')
        self.assertEqual(self.db.snapshot(self.scope), old)
        state = self.db.versions(self.scope, 'policy')
        self.assertEqual((state['desired_version_id'], state['active_version_id']), ('12', '13'))
        self.fail_bm25 = False
        self.assertEqual(self.worker.run_once(), 'succeeded')
        self.assertEqual(self.active_nodes()[0]['metadata']['version_id'], '12')
        self.assertEqual(len(self.parsed), 4)

    def test_old_created_version_is_archived_without_replacing_active(self):
        self.ingest(4, 13, 10)
        old = self.db.snapshot(self.scope)
        self.assertEqual(self.ingest(1, 10), 'succeeded')
        self.assertEqual(self.db.snapshot(self.scope), old)
        self.assertEqual({row['version_id'] for row in self.db.versions(self.scope, 'policy')['versions']},
                         {'10', '13'})

    def test_switch_preserves_other_documents_in_knowledge_base_snapshot(self):
        self.graph()
        self.ingest(1, 'other-root', document_id='other', event_id='other-created')
        self.ingest(5, 12, action='document.version.activated')
        self.assertEqual({(n['metadata']['document_id'], n['metadata']['version_id']) for n in self.active_nodes()},
                         {('policy', '12'), ('other', 'other-root')})

    def _assert_ready_selection_rejects_config_change(self, attribute):
        self.graph()
        old_snapshot = self.db.snapshot(self.scope)
        old_generation = next(row['generation'] for row in self.db.versions(self.scope, 'policy')['versions']
                              if row['version_id'] == '12')
        setattr(self.backend, attribute, 'changed-config')
        self.assertEqual(self.ingest(5, 12, action='document.version.activated'), 'dead')
        self.assertEqual(self.db.snapshot(self.scope), old_snapshot)
        self.assertEqual(len(self.parsed), 4)
        self.assertEqual(len(self.embedded), 4)
        selected = next(row for row in self.db.versions(self.scope, 'policy')['versions'] if row['version_id'] == '12')
        self.assertEqual(selected['generation'], old_generation)
        self.assertIn('config', self.db.job('tree-5')['last_error'])

    def test_ready_activation_does_not_implicitly_migrate_embedding_model(self):
        self._assert_ready_selection_rejects_config_change('model_fingerprint')

    def test_ready_activation_does_not_implicitly_reparse_with_new_pipeline(self):
        self._assert_ready_selection_rejects_config_change('pipeline_version')
