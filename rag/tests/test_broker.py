from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from test_support import WorkspaceTemp

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'rag_api_service'))
from reliable_ingestion.broker import Consumer, relay_dead_letters
from reliable_ingestion.store import Inbox
from test_reliable_ingestion import event


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.temp = WorkspaceTemp()
        self.addCleanup(self.temp.cleanup)
        self.db = Inbox(Path(self.temp.name) / 'inbox.sqlite3')

    def test_ack_happens_after_durable_handoff(self):
        received = []
        db = self.db
        class Channel:
            def basic_ack(self, delivery_tag):
                reopened = Inbox(db.path)
                received.append((delivery_tag, reopened.jobs()[0]['status']))
        Consumer(self.db).handle(Channel(), SimpleNamespace(delivery_tag=7), None, event())
        self.assertEqual(received, [(7, 'pending')])

    def test_database_failure_never_acknowledges_message(self):
        acknowledgements = []
        class BrokenInbox:
            def accept(self, body):
                raise OSError('disk full')
        channel = SimpleNamespace(basic_ack=lambda **kwargs: acknowledgements.append(kwargs))
        with self.assertRaises(OSError):
            Consumer(BrokenInbox()).handle(channel, SimpleNamespace(delivery_tag=7), None, event())
        self.assertEqual(acknowledgements, [])

    def test_invalid_message_is_persisted_before_ack(self):
        persisted = []
        db = self.db
        class Channel:
            def basic_ack(self, delivery_tag):
                persisted.extend(Inbox(db.path).pending_dead_letters())
        Consumer(self.db).handle(Channel(), SimpleNamespace(delivery_tag=8), None, b'bad JSON')
        self.assertEqual(persisted[0]['body'], b'bad JSON')
        self.assertEqual(self.db.jobs(), [])

    def test_dead_letter_publish_failure_leaves_durable_outbox_pending(self):
        self.db.quarantine(b'bad', ValueError('malformed'))
        class BrokenPublisher:
            def publish_dead(self, body, message_id, error):
                raise ConnectionError('no confirm')
        with self.assertRaises(ConnectionError):
            relay_dead_letters(self.db, BrokenPublisher())
        self.assertEqual(len(Inbox(self.db.path).pending_dead_letters()), 1)

    def test_confirmed_dead_letter_clears_outbox_after_publish(self):
        self.db.quarantine(b'bad', ValueError('malformed'))
        published = []
        db = self.db
        class Publisher:
            def publish_dead(self, body, message_id, error):
                self.assert_pending = len(Inbox(db.path).pending_dead_letters())
                published.append((body, self.assert_pending))
        self.assertEqual(relay_dead_letters(self.db, Publisher()), 1)
        self.assertEqual(published, [(b'bad', 1)])
        self.assertEqual(self.db.pending_dead_letters(), [])
