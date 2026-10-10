"""Legacy aliases must preserve the same mutation identity guard as decisions."""
import pytest


@pytest.mark.parametrize('identities', [
    {'id': 'different'},
    {'req_id': 'different'},
    {'id': 'different', 'req_id': 'route'},
    {'id': 'route', 'req_id': 'different'},
    {'id': False},
    {'req_id': ''},
])
def test_legacy_transition_rejects_every_supplied_conflicting_identity(backend, identities):
    client, repo, _, record = backend
    before = repo.read()
    identities = {key: record['id'] if value == 'route' else value for key, value in identities.items()}
    response = client.post(f"/api/requirements/{record['id']}/transition", json={
        'expected_revision': 1, 'actor': 'pm-test', 'actor_role': 'PM_USER',
        'rationale': 'Review the selected requirement', 'to_status': 'PM_REVIEW', **identities,
    })
    assert response.status_code == 422
    assert repo.read() == before


@pytest.mark.parametrize('fields', [(), ('id',), ('req_id',), ('id', 'req_id')])
def test_legacy_transition_accepts_omitted_or_matching_identities(backend, fields):
    client, repo, _, record = backend
    response = client.post(f"/api/requirements/{record['id']}/transition", json={
        'expected_revision': 1, 'actor': 'pm-test', 'actor_role': 'PM_USER',
        'rationale': 'Review the selected requirement', 'to_status': 'PM_REVIEW',
        **{field: record['id'] for field in fields},
    })
    assert response.status_code == 200
    assert response.json()['status'] == 'PM_REVIEW'
    assert repo.read()['requirements'][record['id']]['revision'] == 2
