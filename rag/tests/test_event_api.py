import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from test_support import WorkspaceTemp

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'rag_api_service'))
HAS_FASTAPI = importlib.util.find_spec('fastapi') is not None


@unittest.skipUnless(HAS_FASTAPI, 'run with RAG dependencies for API tests')
class EventApiTests(unittest.TestCase):
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

    def test_event_mode_rejects_legacy_write_endpoints(self):
        responses = [
            self.client.post('/api/docs/upload', files={'files': ('a.md', b'hello')}),
            self.client.delete('/api/docs/documents/knowledge_base/a.md'),
            self.client.post('/api/docs/reset'),
            self.client.put('/api/docs/config/chunk', json={'chunk_size': 512, 'chunk_overlap': 50}),
        ]
        self.assertEqual([response.status_code for response in responses], [409, 409, 409, 409])

    def test_event_query_propagates_namespace_and_structured_sources(self):
        class Service:
            async def query(self, query, tenant, kb):
                return {'answer': query, 'sources': [{'document_id': tenant + '/' + kb}], 'snapshot_id': 's1'}
        with patch('rest_api.get_event_query_service', return_value=Service()):
            response = self.client.post('/api/docs/query', json={
                'query': '如何请假', 'tenant_id': 'tenant-a', 'knowledge_base_id': 'oa'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['sources'], [{'document_id': 'tenant-a/oa'}])
        self.assertEqual(response.json()['snapshot_id'], 's1')

    def test_service_token_guards_event_api(self):
        with patch.dict(os.environ, {'RAG_SERVICE_TOKEN': 'test-service-token'}):
            self.assertEqual(self.client.get('/api/docs/ingestion/jobs').status_code, 401)
            response = self.client.get('/api/docs/ingestion/jobs',
                                       headers={'Authorization': 'Bearer test-service-token'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['jobs'], [])
