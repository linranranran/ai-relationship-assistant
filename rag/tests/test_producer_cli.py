import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from test_support import WorkspaceTemp


@unittest.skipUnless(importlib.util.find_spec('dotenv'), 'requires RAG dotenv dependency')
class ProducerCliTests(unittest.TestCase):
    def test_prepare_replay_and_conflict_keep_original_event_and_immutable_object(self):
        temp = WorkspaceTemp()
        self.addCleanup(temp.cleanup)
        source = temp.path / 'policy.md'
        content = '# 请假制度\n\n请假需要审批。'.encode()
        source.write_bytes(content)
        env = dict(os.environ, RAG_EVENT_DATA_DIR=str(temp.path / 'events'),
                   RAG_LOCAL_OBJECT_ROOT=str(temp.path / 'objects'), RAG_OBJECT_SOURCE='local')
        script = Path(__file__).resolve().parents[1] / 'scripts' / 'publish_event.py'
        command = [sys.executable, str(script), 'create', '--document-id', 'policy',
                   '--version', '1', '--file', str(source), '--event-id', 'stable-id', '--prepare-only']
        prepared = subprocess.run(command, env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        event_path = temp.path / 'events' / 'published_events' / 'stable-id.json'
        original = event_path.read_bytes()
        value = json.loads(original)
        self.assertEqual(value['object']['sha256'], hashlib.sha256(content).hexdigest())
        self.assertEqual((temp.path / 'objects' / value['object']['key']).read_bytes(), content)
        replay = subprocess.run([sys.executable, str(script), 'replay', '--event-file', str(event_path),
                                 '--prepare-only'], env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(replay.returncode, 0, replay.stderr)
        source.write_text('different content', encoding='utf-8')
        conflict = subprocess.run(command, env=env, capture_output=True, text=True, timeout=15)
        self.assertNotEqual(conflict.returncode, 0)
        self.assertIn('event-id already used', conflict.stderr)
        self.assertEqual(event_path.read_bytes(), original)
        self.assertEqual((temp.path / 'objects' / value['object']['key']).read_bytes(), content)
