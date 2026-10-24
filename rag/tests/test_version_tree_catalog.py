from pathlib import Path
import sys
import unittest

from test_support import WorkspaceTemp
from test_reliable_ingestion import event as legacy_event
from version_tree_support import tree_event

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'rag_api_service'))
from reliable_ingestion.errors import PermanentError
from reliable_ingestion.events import DocumentEvent
from reliable_ingestion.store import Inbox


class VersionTreeCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = WorkspaceTemp()
        self.addCleanup(self.temp.cleanup)
        self.now = 100.0
        self.db = Inbox(self.temp.path / 'inbox.sqlite3', max_attempts=3, retry_base=0,
                        clock=lambda: self.now, jitter=lambda: 0)
        self.db.dependency_timeout = 10
        self.scope = '["tenant-a","oa"]'

    def accept(self, body):
        try:
            return self.db.accept(body)
        except PermanentError as error:
            self.fail(f'valid version-tree event must be accepted: {error}')

    def build(self, revision, version_id, parent=None):
        self.accept(tree_event(revision, version_id, parent))
        job = self.db.claim()
        self.assertEqual(job['version'], revision)
        source = self.db.version_source(DocumentEvent.parse(job['payload'].encode()))
        generation = f'g{version_id}'
        self.db.version_ready(source, generation)
        self.assertTrue(self.db.publish(job, f's{revision}', generation))
        return job

    def test_tree_branch_latest_and_manual_historical_activation_are_independent(self):
        for revision, version_id, parent in [(1, '10', None), (2, '11', '10'),
                                               (3, '12', '11'), (4, '13', '10')]:
            self.build(revision, version_id, parent)
        self.accept(tree_event(5, '12', action='document.version.activated'))
        job = self.db.claim()
        source = self.db.version_source(DocumentEvent.parse(job['payload'].encode()))
        self.assertEqual(source.version_id, '12')
        self.assertTrue(self.db.publish(job, 's5', 'g12'))
        state = self.db.versions(self.scope, 'policy')
        self.assertEqual(state['latest_version_id'], '13')
        self.assertEqual(state['desired_version_id'], '12')
        self.assertEqual(state['active_version_id'], '12')
        self.assertEqual(state['active_revision'], 5)
        self.assertEqual(self.db.active_generations(self.scope), {'policy': 'g12'})
        self.assertEqual({row['version_id']: row['parent_version_id'] for row in state['versions']},
                         {'10': None, '11': '10', '12': '11', '13': '10'})
        self.assertTrue(all(row['index_status'] == 'ready' for row in state['versions']))

    def test_same_target_can_be_activated_repeatedly_with_new_control_revisions(self):
        self.build(1, '10')
        self.build(2, '11', '10')
        for revision, version_id in [(3, '10'), (4, '11'), (5, '10')]:
            self.accept(tree_event(revision, version_id, action='document.version.activated'))
            self.assertTrue(self.db.publish(self.db.claim(), f's{revision}', f'g{version_id}'))
        self.assertEqual(self.db.versions(self.scope, 'policy')['active_version_id'], '10')
        self.assertEqual(len(self.db.jobs()), 5)

    def test_old_create_is_archived_without_replacing_newer_selection(self):
        self.accept(tree_event(4, '13', '10'))
        self.accept(tree_event(1, '10'))
        newer = self.db.claim()
        self.db.version_ready(DocumentEvent.parse(newer['payload'].encode()), 'g13')
        self.assertTrue(self.db.publish(newer, 'new-snapshot', 'g13'))
        older = self.db.claim()
        self.db.version_ready(DocumentEvent.parse(older['payload'].encode()), 'g10')
        self.assertFalse(self.db.publish(older, None, 'g10'))
        self.assertEqual(self.db.job(older['id'])['status'], 'succeeded')
        self.assertEqual(self.db.snapshot(self.scope), 'new-snapshot')
        self.assertEqual(self.db.versions(self.scope, 'policy')['latest_version_id'], '13')

    def test_inflight_creation_cannot_override_a_later_activation_choice(self):
        self.build(1, '10')
        self.accept(tree_event(4, '13', '10'))
        slow = self.db.claim()
        self.accept(tree_event(5, '10', action='document.version.activated'))
        self.db.version_ready(DocumentEvent.parse(slow['payload'].encode()), 'g13')
        self.assertFalse(self.db.version_should_publish(slow))
        self.assertFalse(self.db.publish(slow, None, 'g13'))
        self.assertTrue(self.db.publish(self.db.claim(), 'manual-snapshot', 'g10'))
        state = self.db.versions(self.scope, 'policy')
        self.assertEqual(state['latest_version_id'], '13')
        self.assertEqual(state['active_version_id'], '10')

    def test_stale_creation_cannot_report_archive_success_before_generation_is_ready(self):
        self.accept(tree_event(1, '10'))
        unfinished = self.db.claim()
        self.accept(tree_event(2, '11', '10'))
        with self.assertRaises(PermanentError):
            self.db.publish(unfinished, None, 'not-built')
        self.assertEqual(self.db.job(unfinished['id'])['status'], 'processing')
        self.assertIsNone(self.db.snapshot(self.scope))

    def test_activation_before_create_waits_without_starving_creation(self):
        from reliable_ingestion.errors import DependencyPending
        self.accept(tree_event(5, '12', action='document.version.activated'))
        activation = self.db.claim()
        with self.assertRaises(DependencyPending) as raised:
            self.db.version_source(DocumentEvent.parse(activation['payload'].encode()))
        self.db.fail(activation, raised.exception)
        self.assertEqual(self.db.job(activation['id'])['status'], 'waiting')
        self.assertEqual(self.db.job(activation['id'])['attempts'], 0)
        self.accept(tree_event(3, '12', '11'))
        created = self.db.claim()
        self.assertEqual(created['version'], 3)
        source = self.db.version_source(DocumentEvent.parse(created['payload'].encode()))
        self.db.version_ready(source, 'g12')
        self.assertFalse(self.db.publish(created, None, 'g12'))
        self.now += 5
        resumed = self.db.claim()
        self.assertEqual(resumed['id'], activation['id'])
        self.assertEqual(self.db.version_source(DocumentEvent.parse(resumed['payload'].encode())).version_id, '12')
        self.assertTrue(self.db.publish(resumed, 'activated', 'g12'))

    def test_dependency_wait_survives_reopen_and_times_out_without_failure_budget(self):
        from reliable_ingestion.errors import DependencyPending
        self.accept(tree_event(5, 'missing', action='document.version.activated'))
        for elapsed in [0, 5, 11]:
            self.now = 100 + elapsed
            job = self.db.claim()
            self.assertIsNotNone(job)
            self.db.fail(job, DependencyPending('target creation has not arrived'))
            self.assertEqual(self.db.job(job['id'])['attempts'], 0)
            self.db = Inbox(self.db.path, max_attempts=3, retry_base=0,
                            clock=lambda: self.now, jitter=lambda: 0)
            self.db.dependency_timeout = 10
        self.assertEqual(self.db.job('tree-5')['status'], 'dead')
        self.assertEqual(len(self.db.pending_dead_letters()), 1)

    def test_stale_unknown_activation_is_superseded_without_waiting(self):
        self.build(1, '10')
        self.accept(tree_event(4, '10', action='document.version.activated'))
        self.accept(tree_event(2, 'missing', action='document.version.activated'))
        self.assertEqual(self.db.job('tree-2')['status'], 'superseded')
        self.assertTrue(self.db.publish(self.db.claim(), 's4', 'g10'))

    def test_event_revision_and_immutable_version_conflicts_roll_back(self):
        original = tree_event(1, 10)
        self.accept(original)
        self.assertEqual(self.db.accept(original), 'duplicate')
        self.assertEqual(self.db.accept(tree_event(1, '10', event_id='alias')), 'duplicate')
        for body in [tree_event(1, '11', event_id='conflicting-revision'),
                     tree_event(2, '10', '11', event_id='conflicting-parent'),
                     tree_event(2, '10', event_id='conflicting-version')]:
            with self.subTest(body=body), self.assertRaises(PermanentError):
                self.db.accept(body)
        self.assertEqual(len(self.db.jobs()), 1)
        self.assertEqual(len(self.db.versions(self.scope, 'policy')['versions']), 1)

    def test_late_parent_order_and_multiple_roots_are_rejected(self):
        self.accept(tree_event(3, '12', '11'))
        state = self.db.versions(self.scope, 'policy')
        self.assertFalse(state['versions'][0]['parent_known'])
        with self.assertRaises(PermanentError):
            self.db.accept(tree_event(4, '11', '10'))
        self.accept(tree_event(1, '10'))
        with self.assertRaises(PermanentError):
            self.db.accept(tree_event(2, 'other-root'))
        with self.assertRaises(PermanentError):
            self.db.accept(tree_event(2, '11', '12'))
        with self.assertRaises(PermanentError):
            self.db.accept(tree_event(20, '20', '20'))

    def test_delete_blocks_new_activation_and_old_controls_but_new_creation_can_restore(self):
        self.build(1, '10')
        self.accept(tree_event(4, action='document.deleted'))
        self.assertTrue(self.db.publish(self.db.claim(), 'deleted-snapshot', None))
        self.accept(tree_event(2, '10', action='document.version.activated'))
        self.assertEqual(self.db.job('tree-2')['status'], 'superseded')
        with self.assertRaises(PermanentError):
            self.db.accept(tree_event(5, '10', action='document.version.activated'))
        self.assertTrue(self.db.versions(self.scope, 'policy')['deleted'])
        self.build(6, '15', '10')
        state = self.db.versions(self.scope, 'policy')
        self.assertFalse(state['deleted'])
        self.assertEqual(state['active_version_id'], '15')

    def test_dead_old_creation_can_be_retried_for_archive_after_newer_selection(self):
        self.accept(tree_event(1, '10'))
        old = self.db.claim()
        self.db.fail(old, PermanentError('broken source'))
        self.build(2, '11', '10')
        self.db.retry(old['id'])
        retried = self.db.claim()
        self.db.version_ready(DocumentEvent.parse(retried['payload'].encode()), 'g10')
        self.assertFalse(self.db.publish(retried, None, 'g10'))
        self.assertEqual(self.db.job(old['id'])['status'], 'succeeded')
        self.assertEqual(self.db.versions(self.scope, 'policy')['active_version_id'], '11')

    def test_different_protocols_can_share_a_knowledge_base_but_not_a_logical_document(self):
        self.db.accept(legacy_event(document_id='legacy-doc'))
        self.accept(tree_event())
        with self.assertRaises(PermanentError):
            self.db.accept(legacy_event(2, event_id='legacy-on-tree'))
        with self.assertRaises(PermanentError):
            self.db.accept(tree_event(2, event_id='tree-on-legacy', document_id='legacy-doc'))
        self.assertEqual(self.db.accept(legacy_event(document_id='legacy-doc')), 'duplicate')

    def test_catalog_lookups_do_not_cross_tenant_scope(self):
        self.build(1, '10')
        self.accept(tree_event(1, '10', event_id='other-tenant', tenant_id='tenant-b'))
        state = self.db.versions('["tenant-b","oa"]', 'policy')
        self.assertIsNone(state['active_version_id'])
        self.assertEqual(state['versions'][0]['version_id'], '10')
        self.assertEqual(self.db.versions(self.scope, 'policy')['active_version_id'], '10')
