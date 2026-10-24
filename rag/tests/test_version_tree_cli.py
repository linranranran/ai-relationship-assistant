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
class VersionTreeCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = WorkspaceTemp()
        self.addCleanup(self.temp.cleanup)
        self.source = self.temp.path / 'policy.md'
        self.source.write_text('# 报销制度\n\n报销需要审批。', encoding='utf-8')
        self.script = Path(__file__).resolve().parents[1] / 'scripts' / 'publish_version_event.py'
        self.env = dict(os.environ, RAG_EVENT_DATA_DIR=str(self.temp.path / 'events'),
                        RAG_LOCAL_OBJECT_ROOT=str(self.temp.path / 'objects'), RAG_OBJECT_SOURCE='local')

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(self.script), *args, '--prepare-only'],
                              env=self.env, capture_output=True, text=True, timeout=15)

    def load_event(self, event_id):
        path = self.temp.path / 'events' / 'published_events' / (event_id + '.json')
        return path, json.loads(path.read_bytes())

    def test_prepare_tree_and_select_older_version_with_new_revision(self):
        for revision, version, parent in [(1, '10', None), (2, '11', '10'),
                                          (3, '12', '11'), (4, '13', '10')]:
            command = ['create', '--document-id', 'policy', '--revision', str(revision),
                       '--version-id', version, '--version-label', f'V{revision}',
                       '--file', str(self.source), '--event-id', f'create-{version}']
            if parent is not None:
                command += ['--parent-version-id', parent]
            result = self.run_cli(*command)
            self.assertEqual(result.returncode, 0, result.stderr)
            _, value = self.load_event('create-' + version)
            self.assertEqual(value['schema_version'], 2)
            self.assertEqual(value['document_revision'], revision)
            self.assertEqual(value['version_id'], version)
            self.assertEqual(value.get('parent_version_id'), parent)
            content = self.source.read_bytes()
            self.assertEqual(value['object']['sha256'], hashlib.sha256(content).hexdigest())
            self.assertEqual((self.temp.path / 'objects' / value['object']['key']).read_bytes(), content)
        result = self.run_cli('activate', '--document-id', 'policy', '--revision', '5',
                              '--version-id', '12', '--event-id', 'activate-12')
        self.assertEqual(result.returncode, 0, result.stderr)
        path, value = self.load_event('activate-12')
        self.assertEqual(value['event_type'], 'document.version.activated')
        self.assertEqual(value['document_revision'], 5)
        self.assertEqual(value['version_id'], '12')
        self.assertNotIn('object', value)
        self.assertEqual(self.run_cli('replay', '--event-file', str(path)).returncode, 0)

    def test_event_id_conflict_keeps_original_event_and_object(self):
        command = ['create', '--document-id', 'policy', '--revision', '1', '--version-id', '10',
                   '--file', str(self.source), '--event-id', 'immutable-event']
        first = self.run_cli(*command)
        self.assertEqual(first.returncode, 0, first.stderr)
        event_path, original = self.load_event('immutable-event')
        event_bytes = event_path.read_bytes()
        object_path = self.temp.path / 'objects' / original['object']['key']
        original_bytes = object_path.read_bytes()
        self.source.write_text('changed bytes', encoding='utf-8')
        conflict = self.run_cli(*command)
        self.assertNotEqual(conflict.returncode, 0)
        self.assertIn('event-id already used', conflict.stderr)
        self.assertEqual(event_path.read_bytes(), event_bytes)
        self.assertEqual(object_path.read_bytes(), original_bytes)

    def test_missing_revision_target_and_unknown_schema_replay_are_rejected(self):
        cases = [
            ['activate', '--document-id', 'policy', '--version-id', '12'],
            ['activate', '--document-id', 'policy', '--revision', '5'],
            ['create', '--document-id', 'policy', '--revision', '0', '--version-id', '10',
             '--file', str(self.source)],
            ['delete', '--document-id', 'policy', '--revision', '6', '--version-id', '12'],
        ]
        for args in cases:
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertNotEqual(result.returncode, 0)
        old = self.temp.path / 'old.json'
        old.write_text('{"schema_version": 1}', encoding='utf-8')
        self.assertNotEqual(self.run_cli('replay', '--event-file', str(old)).returncode, 0)

    def test_delete_event_uses_control_revision_and_omits_version(self):
        result = self.run_cli('delete', '--document-id', 'policy', '--revision', '6',
                              '--event-id', 'delete-policy')
        self.assertEqual(result.returncode, 0, result.stderr)
        _, value = self.load_event('delete-policy')
        self.assertEqual(value['event_type'], 'document.deleted')
        self.assertEqual(value['document_revision'], 6)
        self.assertNotIn('version_id', value)
        self.assertNotIn('object', value)
