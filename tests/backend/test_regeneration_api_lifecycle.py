"""Regeneration proposals are discoverable/reviewable without inventing chat threads."""
import copy
import pytest
from fastapi.testclient import TestClient
from conftest import FakeEvidence, fixture_draft, decision
from models import DecisionRequest, EditRequest
from services.api import create_app
from services.repository import MemoryRepository
from test_review import identity


@pytest.fixture
def regeneration_case():
    changed = fixture_draft()
    changed['title'] = 'Proposed regenerated controls'
    class Engine:
        def analyze(self, scope):
            return [copy.deepcopy(changed), copy.deepcopy(changed)]
    repo = MemoryRepository()
    app = create_app(repo, FakeEvidence(), engine=Engine())
    with TestClient(app) as client:
        current = app.state.service.create_requirement(fixture_draft(), 'initial')
        approved = app.state.service.decide_requirement(current['id'], DecisionRequest(**decision()))
        original = repo.read()['requirements'][current['id']]
        admitted = client.post('/api/analysis-runs', json={}).json()
        completed = client.get('/api/analysis-runs/' + admitted['id']).json()
        assert completed['state'] == 'succeeded'
        assert completed['result_ids'] == [current['id']]
        assert len(completed['pending_turn_ids']) == 1
        assert repo.read()['threads'] == {}
        assert repo.read()['requirements'][current['id']] == original
        yield client, repo, app.state.service, current['id'], completed['pending_turn_ids'][0], approved['revision']


def test_regeneration_discover_inspect_discard_and_idempotent_retry(regeneration_case):
    client, repo, service, rid, turn_id, revision = regeneration_case
    before = repo.read()
    listed = client.get(f'/api/requirements/{rid}/proposals?state=pending').json()
    assert listed['total'] == 1 and listed['items'][0]['id'] == turn_id
    proposal = client.get(f'/api/requirements/{rid}/proposals/{turn_id}').json()
    assert proposal['patch']['title'] == 'Proposed regenerated controls'
    assert proposal['stale'] is False
    assert repo.read() == before
    endpoint = f'/api/requirements/{rid}/proposals/{turn_id}/discard'
    request = identity(revision)
    discarded = client.post(endpoint, json=request)
    assert discarded.status_code == 200 and discarded.json()['state'] == 'discarded'
    assert repo.read()['requirements'] == before['requirements']
    assert repo.read()['revisions'] == before['revisions']
    events = repo.read()['events']
    assert events[-1]['action'] == 'SUGGESTION_DISCARDED'
    assert client.post(endpoint, json=request).status_code == 200
    assert repo.read()['events'] == events
    assert client.post(endpoint, json=dict(request, rationale='Different retry')).status_code == 409
    assert client.post(endpoint, json=dict(request, actor_id='another-pm')).status_code == 409
    assert client.post(f'/api/requirements/{rid}/challenge-applications', json=dict(request, turn_id=turn_id, patch=proposal['patch'])).status_code == 409
    rerun = client.post('/api/analysis-runs', json={}).json()
    assert client.get('/api/analysis-runs/' + rerun['id']).json()['pending_turn_ids'] == []
    assert client.get(f'/api/requirements/{rid}/proposals?state=pending').json()['total'] == 0


@pytest.mark.parametrize('request_change,status', [({'id': 'wrong'}, 422), ({'expected_revision': 999}, 409), ({'actor_role': 'AI_SYSTEM'}, 422), ({'rationale': ' '}, 422)])
def test_regeneration_discard_guards_preserve_everything(regeneration_case, request_change, status):
    client, repo, service, rid, turn_id, revision = regeneration_case
    before = repo.read()
    response = client.post(f'/api/requirements/{rid}/proposals/{turn_id}/discard', json=dict(identity(revision), **request_change))
    assert response.status_code == status
    assert repo.read() == before


def test_stale_proposal_can_be_inspected_and_discarded_against_current_revision(regeneration_case):
    client, repo, service, rid, turn_id, revision = regeneration_case
    updated = service.edit_requirement(rid, EditRequest(**dict(identity(revision), patch={'title': 'Human current title'})))
    assert client.get(f'/api/requirements/{rid}/proposals/{turn_id}').json()['stale'] is True
    endpoint = f'/api/requirements/{rid}/proposals/{turn_id}/discard'
    assert client.post(endpoint, json=identity(revision)).status_code == 409
    assert client.post(endpoint, json=identity(updated['revision'])).status_code == 200


def test_missing_wrong_requirement_and_pagination(regeneration_case):
    client, repo, service, rid, turn_id, revision = regeneration_case
    assert client.get(f'/api/requirements/{rid}/proposals?limit=1&offset=1').json()['items'] == []
    assert client.get(f'/api/requirements/{rid}/proposals?limit=201').status_code == 422
    assert client.get(f'/api/requirements/{rid}/proposals?state=unknown').status_code == 422
    assert client.get('/api/requirements/missing/proposals').status_code == 404
    assert client.get(f'/api/requirements/{rid}/proposals/missing').status_code == 404
    assert client.post(f'/api/requirements/{rid}/proposals/missing/discard', json=identity(revision)).status_code == 404


def test_changed_analysis_rerun_reuses_pending_proposal_without_duplicate_audit(regeneration_case):
    client, repo, service, rid, turn_id, revision = regeneration_case
    before = repo.read()
    rerun = client.post('/api/analysis-runs', json={}).json()
    completed = client.get('/api/analysis-runs/' + rerun['id']).json()
    assert completed['pending_turn_ids'] == [turn_id]
    assert completed['result_ids'] == [rid]
    assert repo.read()['turns'] == before['turns']
    assert repo.read()['events'] == before['events']


def test_requirement_proposals_cannot_expose_or_discard_other_entities(regeneration_case):
    client, repo, service, rid, turn_id, revision = regeneration_case
    draft = fixture_draft()
    draft['derivation_key'] = 'another-requirement'
    other = service.create_requirement(draft, 'other')
    before = repo.read()
    assert client.get(f"/api/requirements/{other['id']}/proposals/{turn_id}").status_code == 404
    assert client.post(f"/api/requirements/{other['id']}/proposals/{turn_id}/discard", json=identity()).status_code == 404
    assert repo.read() == before
