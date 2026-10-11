"""Fresh independent probes for snapshot coherence and atomic job admission."""
import copy

import pytest
from fastapi.testclient import TestClient

from conftest import FakeEvidence, fixture_draft
from test_review import FakeEngine, identity
from services.api import create_app
from services.repository import MemoryRepository


class ProbeRepository(MemoryRepository):
    reads = 0
    fail_reads = False

    def read(self):
        self.reads += 1
        if self.fail_reads:
            raise RuntimeError('Database offline')
        return super().read()


@pytest.mark.parametrize('route', ['requirements/{rid}', 'requirements/{rid}/history', 'review-threads/{thread_id}', 'dashboard/summary'])
def test_read_endpoint_uses_one_coherent_snapshot(route):
    class Sources:
        def list_conflicts(self, model):
            return []
    repo = ProbeRepository()
    with TestClient(create_app(repo, FakeEvidence(), source_provider=Sources())) as client:
        record = client.app.state.service.create_requirement(fixture_draft(), 'fresh-review')
        thread = client.post('/api/review-threads', json=dict(identity(), entity_kind='requirement', entity_id=record['id'])).json()
        repo.reads = 0
        response = client.get('/api/' + route.format(rid=record['id'], thread_id=thread['id']))
        assert response.status_code == 200, response.text
        assert repo.reads == 1


def test_preview_uses_one_snapshot_without_writes():
    repo = ProbeRepository()
    with TestClient(create_app(repo, FakeEvidence())) as client:
        record = client.app.state.service.create_requirement(fixture_draft(), 'fresh-review')
        before = repo.read()
        repo.reads = 0
        assert client.post('/api/scoring/preview', json={'requirement_id': record['id']}).status_code == 200
        assert repo.reads == 1
        assert repo.read() == before


def test_health_fails_when_persistence_is_unavailable():
    repo = ProbeRepository()
    with TestClient(create_app(repo, FakeEvidence()), raise_server_exceptions=False) as client:
        repo.fail_reads = True
        response = client.get('/api/health')
        assert response.status_code == 503
        assert response.json()['detail'] == 'Persistence is unavailable'


def test_challenge_admission_audit_failure_does_not_leave_orphan_thread():
    def fail(event):
        if event == 'CHALLENGE_REQUESTED':
            raise RuntimeError('Injected audit failure')
    repo = MemoryRepository()
    with TestClient(create_app(repo, FakeEvidence(), FakeEngine(), audit_failure=fail), raise_server_exceptions=False) as client:
        record = client.app.state.service.create_requirement(fixture_draft(), 'fresh-review')
        before = copy.deepcopy(repo.read())
        response = client.post('/api/requirements/' + record['id'] + '/challenges', json=dict(identity(), question='Review this'))
        assert response.status_code >= 500
        assert repo.read() == before


@pytest.mark.parametrize('result', [
    {'result_ids': [1]}, {'result_ids': [' ']}, {'failures': [{}]}, {'failures': ['']},
    {'sources': [{'id': '', 'source_version': 'v1'}]},
    {'sources': [{'id': 'source-1'}]},
    {'sources': [{'id': 'source-1', 'source_version': ' '}]},
    {'sources': [{'id': 'source-1', 'source_version': 'v1'}, {'id': 'source-1', 'source_version': 'v2'}]},
    {'signals': [{'id': 'signal-1'}]},
    {'signals': [{'id': 'signal-1', 'revision': True}]},
    {'signals': [{'id': 'signal-1', 'revision': 0}]},
    {'signals': [{'id': 'signal-1', 'revision': 1}, {'id': 'signal-1', 'revision': 2}]},
])
def test_import_rejects_unreconstructable_metadata(result):
    class Importer:
        def import_data(self, scope):
            return result
    repo = MemoryRepository()
    with TestClient(create_app(repo, import_service=Importer())) as client:
        started = client.post('/api/import-runs', json={}).json()
        run = client.get('/api/analysis-runs/' + started['id']).json()
        assert run['state'] == 'failed'
        assert not repo.read()['events']


@pytest.mark.parametrize('payload', [float('nan'), float('inf'), float('-inf'), object(), {'nested': [float('nan')]}])
def test_invalid_import_metadata_keeps_failure_state_serializable_and_releases_admission(payload):
    class Importer:
        def import_data(self, scope):
            return {'result_ids': ['source-1'], 'sources': [{'id': 'source-1', 'source_version': 'v1', 'raw_payload': payload}]}
    repo = MemoryRepository()
    with TestClient(create_app(repo, import_service=Importer())) as client:
        for _ in range(2):
            response = client.post('/api/import-runs', json={})
            assert response.status_code == 200
            run = client.get('/api/analysis-runs/' + response.json()['id']).json()
            assert run['state'] == 'failed'
            assert 'sources' not in run
            assert not repo.read()['events']
