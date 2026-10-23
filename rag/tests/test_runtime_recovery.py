from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_support import WorkspaceTemp
from test_reliable_ingestion import event

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'rag_api_service'))
from reliable_ingestion.config import RuntimeConfig
from reliable_ingestion.store import Inbox
from reliable_ingestion import runtime


class RuntimeRecoveryTests(unittest.TestCase):
    def test_failed_recovery_is_retried_before_claiming_after_database_returns(self):
        temp = WorkspaceTemp()
        self.addCleanup(temp.cleanup)
        inbox = Inbox(temp.path / 'inbox.sqlite3', retry_base=0)
        inbox.accept(event())
        recover = inbox.recover_interrupted
        recovery_calls = []
        def transient_recovery():
            recovery_calls.append(True)
            if len(recovery_calls) == 2:
                raise OSError('database still unavailable')
            recover()

        class Stop:
            iterations = 0
            stopped = False
            def is_set(self):
                self.iterations += 1
                return self.stopped or self.iterations > 8 or inbox.job('event-1')['status'] == 'succeeded'
            def wait(self, seconds):
                return self.is_set()
            def set(self):
                self.stopped = True

        class InlineIndexer:
            def __init__(self, *, target, name):
                self.target, self.name = target, name
            def start(self):
                if self.name == 'rag-indexer':
                    self.target()
            def join(self):
                pass

        stage_calls = []
        def stage(ev, db):
            stage_calls.append(True)
            if len(stage_calls) == 1:
                raise TimeoutError('external dependency failed')
            return 'generation'
        backend = SimpleNamespace(stage=stage, build_snapshot=lambda *args: 'snapshot')
        config = RuntimeConfig(root=temp.path, rabbit_url='not-used')
        # IO/model edges are replaced; actual Inbox processing/recovery/publish SQL is exercised.
        with patch.dict(sys.modules, {'pika': SimpleNamespace()}), \
             patch.object(runtime.RuntimeConfig, 'from_env', return_value=config), \
             patch.object(runtime, 'Inbox', return_value=inbox), \
             patch.object(runtime.threading, 'Event', return_value=Stop()), \
             patch.object(runtime.threading, 'Thread', InlineIndexer), \
             patch.object(runtime.signal, 'signal'), \
             patch.object(inbox, 'recover_interrupted', transient_recovery), \
             patch.object(inbox, 'fail', side_effect=OSError('database unavailable')), \
             patch('reliable_ingestion.llama_backend.make_backend', return_value=backend):
            runtime.run()
        self.assertEqual(inbox.job('event-1')['status'], 'succeeded')
        self.assertEqual(inbox.job('event-1')['attempts'], 2)
        self.assertGreaterEqual(len(recovery_calls), 3)
