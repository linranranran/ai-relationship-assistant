import json
from pathlib import Path
import sys
import unittest
from test_support import WorkspaceTemp

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'rag_api_service'))
from reliable_ingestion.events import DocumentEvent
from reliable_ingestion.errors import PermanentError
from reliable_ingestion.store import Inbox


def event(version=1, action='document.created', event_id=None, **overrides):
    value = {
        'schema_version': 1, 'event_id': event_id or f'event-{version}',
        'event_type': action, 'tenant_id': 'tenant-a', 'knowledge_base_id': 'oa',
        'document_id': 'policy', 'document_version': version,
        'file_name': 'policy.md',
        'object': {'bucket': 'knowledge', 'key': f'policy/{version}/policy.md',
                   'sha256': 'a' * 64},
    }
    if action == 'document.deleted':
        value.pop('object')
    value.update(overrides)
    return json.dumps(value).encode()


class InboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = WorkspaceTemp()
        self.addCleanup(self.temp.cleanup)
        self.now = 100.0
        self.db = Inbox(Path(self.temp.name) / 'inbox.sqlite3', max_attempts=3,
                        retry_base=1, clock=lambda: self.now, jitter=lambda: 0)

    def test_duplicate_does_not_create_another_job(self):
        self.assertEqual(self.db.accept(event()), 'accepted')
        self.assertEqual(self.db.accept(event()), 'duplicate')
        self.assertEqual(len(self.db.jobs()), 1)

    def test_due_retry_is_not_starved_by_newer_pending_tasks(self):
        self.db.accept(event())
        job = self.db.claim()
        self.db.fail(job, TimeoutError('transient'))
        self.now += 2
        self.db.accept(event(event_id='new-document', document_id='other'))
        self.assertEqual(self.db.claim()['id'], job['id'])

    def test_same_event_id_different_payload_is_rejected(self):
        self.db.accept(event())
        with self.assertRaises(PermanentError):
            self.db.accept(event(2, event_id='event-1'))
        self.assertEqual(len(self.db.jobs()), 1)

    def test_revision_conflict_is_rejected_but_identical_alias_is_idempotent(self):
        self.db.accept(event())
        self.assertEqual(self.db.accept(event(event_id='alias')), 'duplicate')
        with self.assertRaises(PermanentError):
            self.db.accept(event(event_id='different', file_name='changed.md'))
        with self.assertRaises(PermanentError):
            self.db.accept(event(2, event_id='alias'))

    def test_old_update_cannot_resurrect_deleted_document(self):
        self.db.accept(event(3, 'document.deleted'))
        job = self.db.claim()
        self.assertTrue(self.db.publish(job, 'deleted-snapshot', None))
        self.db.accept(event(2, 'document.updated'))
        self.assertIsNone(self.db.claim())
        self.assertEqual(self.db.active_generations(DocumentEvent.parse(event()).scope), {})
        self.assertEqual(self.db.snapshot(DocumentEvent.parse(event()).scope), 'deleted-snapshot')

    def test_publish_rechecks_version_after_build(self):
        self.db.accept(event())
        old = self.db.claim()
        self.db.accept(event(2, 'document.updated'))
        self.assertFalse(self.db.publish(old, 'incomplete-old', 'g1'))
        self.assertIsNone(self.db.snapshot(old['scope']))
        current = self.db.claim()
        self.assertTrue(self.db.publish(current, 's2', 'g2'))
        self.assertEqual(self.db.active_generations(current['scope']), {'policy': 'g2'})

    def test_retry_is_durable_and_bounded(self):
        self.db.accept(event())
        for attempt in range(1, 4):
            job = self.db.claim()
            self.assertEqual(job['attempts'], attempt)
            self.db.fail(job, TimeoutError('temporary'))
            self.assertIsNone(self.db.claim())
            self.now += 100
        self.assertEqual(self.db.jobs()[0]['status'], 'dead')
        self.assertEqual(len(self.db.pending_dead_letters()), 1)

    def test_crash_recovery_counts_attempts_and_stops_crash_loops(self):
        self.db.accept(event())
        for _ in range(3):
            self.assertIsNotNone(self.db.claim())
            self.db.recover_interrupted()
        self.assertIsNone(self.db.claim())
        self.assertEqual(self.db.jobs()[0]['status'], 'dead')
        self.assertEqual(len(self.db.pending_dead_letters()), 1)

    def test_permanent_failure_goes_to_durable_dead_letter(self):
        self.db.accept(event())
        self.db.fail(self.db.claim(), PermanentError('checksum mismatch'))
        dead = self.db.pending_dead_letters()[0]
        self.assertEqual(json.loads(dead['body'])['document_id'], 'policy')
        self.db.mark_dead_letter_sent(dead['id'])
        self.assertEqual(self.db.pending_dead_letters(), [])

    def test_success_is_persisted_across_new_inbox_instance(self):
        self.db.accept(event())
        job = self.db.claim()
        self.db.publish(job, 'complete-snapshot', 'g1')
        reopened = Inbox(self.db.path)
        reopened.recover_interrupted()
        self.assertIsNone(reopened.claim())
        self.assertEqual(reopened.snapshot(job['scope']), 'complete-snapshot')
        self.assertEqual(reopened.accept(event()), 'duplicate')

    def test_scopes_do_not_share_document_heads(self):
        self.db.accept(event(9))
        self.db.accept(event(1, event_id='other', tenant_id='tenant-b'))
        for _ in range(2):
            job = self.db.claim()
            self.assertTrue(self.db.publish(job, f"s-{job['version']}", f"g-{job['version']}"))
        self.assertEqual(len(self.db.jobs()), 2)

    def test_invalid_contract_never_creates_job(self):
        for payload in [b'not json', event(document_version=True), event(schema_version=2),
                        event(event_type='unknown'), event(object=None), event(tenant_id='')]:
            with self.subTest(payload=payload):
                with self.assertRaises(PermanentError):
                    self.db.accept(payload)
        self.assertEqual(self.db.jobs(), [])

    def test_poison_json_types_depth_and_encoding_are_permanent_errors(self):
        payloads = [event(event_type=[]), event(event_type={}), b'[' * 2000 + b']' * 2000,
                    event().decode().encode('utf-16')]
        for payload in payloads:
            with self.subTest(size=len(payload)):
                with self.assertRaises(PermanentError):
                    self.db.accept(payload)

    def test_manual_replay_failure_creates_a_new_dead_letter_delivery(self):
        self.db.accept(event())
        self.db.fail(self.db.claim(), PermanentError('bad content'))
        first = self.db.pending_dead_letters()[0]
        self.db.mark_dead_letter_sent(first['id'])
        self.db.retry('event-1')
        self.db.fail(self.db.claim(), PermanentError('still bad content'))
        pending = self.db.pending_dead_letters()
        self.assertEqual(len(pending), 1)
        self.assertNotEqual(pending[0]['id'], first['id'])


if __name__ == '__main__':
    unittest.main()
