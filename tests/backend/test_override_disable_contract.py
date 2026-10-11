"""Omitted, disabled and zero overrides are distinct JSON mutation intents."""
import pytest
from conftest import decision


@pytest.mark.parametrize('override', [0, 35])
def test_http_omitted_override_preserves_explicit_null_disables_and_zero_is_enabled(backend, override):
    client, repo, service, record = backend
    url = '/api/requirements/' + record['id'] + '/decisions'
    response = client.post(url, json=decision(action='approve', score_override=override))
    assert response.status_code == 200, response.text
    approved = response.json()
    assert approved['score']['override'] == override
    assert approved['score']['effective_score'] == override
    response = client.post(url, json=decision(2, 'reopen'))
    assert response.status_code == 200, response.text
    reopened = response.json()
    assert reopened['score'] == approved['score']
    assert len([e for e in repo.read()['events'] if e['action'] == 'SCORE_OVERRIDDEN']) == 1
    response = client.post(url, json=decision(3, score_override=None))
    assert response.status_code == 200, response.text
    cleared = response.json()
    assert cleared['score']['override'] is None
    assert cleared['score']['effective_score'] == cleared['score']['calculated_score']
    events = [e for e in repo.read()['events'] if e['action'] == 'SCORE_OVERRIDDEN']
    assert len(events) == 2
    assert events[-1]['before'] == reopened['score'] and events[-1]['after'] == cleared['score']
    assert repo.read()['rankings'][-1]['scores'][record['id']] == cleared['score']['calculated_score']


def test_explicit_null_with_no_override_does_not_create_pointless_override_event(backend):
    client, repo, service, record = backend
    response = client.post('/api/requirements/' + record['id'] + '/decisions', json=decision(action='start_review', score_override=None))
    assert response.status_code == 200, response.text
    assert response.json()['score']['override'] is None
    assert not [e for e in repo.read()['events'] if e['action'] == 'SCORE_OVERRIDDEN']


def test_http_clear_override_recomputes_actual_legacy_policy_without_relabeling(backend):
    client, repo, service, _ = backend
    record = service.import_legacy([{'req_id': 'legacy-clear', 'impact': 2, 'market_reach_norm': .5, 'effort': 3}])[0]
    url = '/api/requirements/' + record['id'] + '/decisions'
    response = client.post(url, json=decision(action='reject', score_override=0))
    assert response.status_code == 200, response.text
    overridden = response.json()
    response = client.post(url, json=decision(2, 'reopen', score_override=None))
    assert response.status_code == 200, response.text
    cleared = response.json()
    assert cleared['score']['policy_version'] == 'legacy-v0'
    assert cleared['score']['override'] is None
    assert cleared['score']['effective_score'] == record['score']['calculated_score']
    assert cleared['score']['scale'] == [0, 10]
    assert record['id'] not in repo.read()['rankings'][-1]['ranks']
    event = [e for e in repo.read()['events'] if e['action'] == 'SCORE_OVERRIDDEN'][-1]
    assert event['before'] == overridden['score'] and event['after'] == cleared['score']
