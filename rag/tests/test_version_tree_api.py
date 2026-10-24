import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from test_support import WorkspaceTemp

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'rag_api_service'))


@unittest.skipUnless(importlib.util.find_spec('fastapi'), 'requires RAG FastAPI dependencies')
class VersionTreeApiTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from run import app
        self.temp = WorkspaceTemp()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {'RAG_INGESTION_MODE': 'events', 'RAG_SERVICE_TOKEN': '',
                                      'RAG_EVENT_DATA_DIR': self.temp.name})
        env.start()
        self.addCleanup(env.stop)
        self.client = TestClient(app)

    def test_readonly_tree_returns_latest_desired_and_active_separately(self):
        state = {'document_id': 'policy', 'latest_version_id': '13', 'desired_version_id': '12',
                 'active_version_id': '12', 'desired_revision': 5, 'active_revision': 5,
                 'deleted': False, 'desired_deleted': False,
                 'versions': [{'version_id': str(version), 'parent_version_id': parent}
                              for version, parent in [(10, None), (11, '10'), (12, '11'), (13, '10')]]}
        with patch('reliable_ingestion.store.Inbox.versions', return_value=state, create=True) as versions:
            response = self.client.get('/api/docs/documents/policy/versions',
                                       params={'tenant_id': 'company-a', 'knowledge_base_id': 'oa'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), state)
        versions.assert_called_once_with('["company-a","oa"]', 'policy')
        self.assertEqual(self.client.post('/api/docs/documents/policy/versions', json={}).status_code, 405)

    def test_unknown_document_is_404_but_waiting_selection_is_visible(self):
        with patch('reliable_ingestion.store.Inbox.versions',
                   return_value={'document_id': 'missing', 'desired_revision': None, 'versions': []}, create=True):
            self.assertEqual(self.client.get('/api/docs/documents/missing/versions').status_code, 404)
        pending = {'document_id': 'policy', 'desired_revision': 5, 'desired_version_id': '12',
                   'active_version_id': None, 'versions': []}
        with patch('reliable_ingestion.store.Inbox.versions', return_value=pending, create=True):
            response = self.client.get('/api/docs/documents/policy/versions')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['desired_version_id'], '12')

    def test_legacy_mode_and_invalid_scope_do_not_read_tree(self):
        with patch.dict(os.environ, {'RAG_INGESTION_MODE': 'legacy'}):
            self.assertEqual(self.client.get('/api/docs/documents/policy/versions').status_code, 409)
        with patch('reliable_ingestion.store.Inbox.versions', create=True) as versions:
            response = self.client.get('/api/docs/documents/policy/versions', params={'tenant_id': '../other'})
        self.assertEqual(response.status_code, 422)
        versions.assert_not_called()

    def test_document_list_exposes_active_version_provenance(self):
        snapshot = {'nodes': [{'metadata': {'document_id': 'policy', 'file_name': 'policy.md',
                                           'document_version': '12', 'version_id': '12',
                                           'parent_version_id': '11', 'version_label': 'V3'}}]}
        with patch('rest_api.event_snapshot', return_value=snapshot):
            response = self.client.get('/api/docs/documents')
        self.assertEqual(response.status_code, 200)
        document = response.json()['documents'][0]
        self.assertEqual(document.get('version_id'), '12')
        self.assertEqual(document.get('parent_version_id'), '11')
        self.assertEqual(document.get('version_label'), 'V3')

    def test_actual_catalog_tree_after_manual_activation_preserves_latest_version(self):
        from reliable_ingestion.events import DocumentEvent, canonical
        from reliable_ingestion.store import Inbox
        inbox = Inbox(self.temp.path / 'inbox.sqlite3')
        for revision, version, parent in [(1, '10', None), (2, '11', '10'),
                                          (3, '12', '11'), (4, '13', '10')]:
            payload = {'schema_version': 2, 'event_id': 'create-' + version,
                       'event_type': 'document.version.created', 'tenant_id': 'company-a',
                       'knowledge_base_id': 'oa', 'document_id': 'policy',
                       'document_revision': revision, 'version_id': version,
                       'parent_version_id': parent, 'version_label': f'V{revision}',
                       'file_name': 'policy.md',
                       'object': {'bucket': 'knowledge', 'key': version + '/policy.md', 'sha256': 'a' * 64}}
            inbox.accept(json.dumps(payload).encode())
        # Generation construction is covered by indexing tests. Exercise actual catalog
        # persistence and publication here, then read it through the real HTTP route.
        while job := inbox.claim():
            source = DocumentEvent.parse(job['payload'].encode())
            generation = 'generation-' + source.version_id
            inbox.version_ready(source, generation)
            inbox.publish(job, 'snapshot-' + str(job['version']), generation)
        inbox.accept(json.dumps({'schema_version': 2, 'event_id': 'select-12',
                                 'event_type': 'document.version.activated', 'tenant_id': 'company-a',
                                 'knowledge_base_id': 'oa', 'document_id': 'policy',
                                 'document_revision': 5, 'version_id': '12'}).encode())
        job = inbox.claim()
        self.assertTrue(inbox.publish(job, 'snapshot-selected', 'generation-12'))
        response = self.client.get('/api/docs/documents/policy/versions',
                                   params={'tenant_id': 'company-a', 'knowledge_base_id': 'oa'})
        self.assertEqual(response.status_code, 200, response.text)
        state = response.json()
        self.assertEqual(state['latest_version_id'], '13')
        self.assertEqual(state['desired_version_id'], '12')
        self.assertEqual(state['active_version_id'], '12')
        self.assertEqual(inbox.snapshot(canonical(['company-a', 'oa'])), 'snapshot-selected')
        parents = {row['version_id']: row['parent_version_id'] for row in state['versions']}
        self.assertEqual(parents, {'10': None, '11': '10', '12': '11', '13': '10'})
        self.assertEqual(self.client.get('/api/docs/documents/policy/versions',
                                        params={'tenant_id': 'another-company', 'knowledge_base_id': 'oa'}).status_code,
                         404)
