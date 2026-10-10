"""Original legacy identities and conversation bindings survive audited reuse."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from conftest import FakeEvidence, decision
from models import DecisionRequest, EditRequest
from services.api import create_app
from services.decisions import DomainError, GovernanceService
from services.repository import MemoryRepository
from test_neo4j_integration import graph_backend, graph_snapshot
from test_review import FakeEngine, identity


def reconcile(service, draft, action='approve', rid='REQ-F70-001'):
    legacy = service.import_legacy([{'req_id': rid, 'impact': 2,
                                     'market_reach_norm': .5, 'effort': 3}])[0]
    reconciled = service.edit_requirement(legacy['id'], EditRequest(
        expected_revision=1, actor_id='pm', actor_role='PM_USER',
        rationale='Reconcile original evidence', patch=draft))
    approved = service.decide_requirement(legacy['id'], DecisionRequest(**decision(2, action)))
    assert reconciled['provenance_state'] == 'reconciled_legacy'
    return approved


def check_rerun(repo, service, draft, action='approve'):
    approved = reconcile(service, draft, action)
    before = repo.read()
    assert service.create_requirement(draft, 'same-rerun') == approved
    assert repo.read() == before
    changed = copy.deepcopy(draft)
    changed['description'] += ' with tactile controls'
    result = service.create_requirement(changed, 'changed-rerun')
    state = repo.read()
    assert result['id'] == 'REQ-F70-001'
    assert state['requirements'] == before['requirements']
    assert state['turns'][result['pending_turn_id']]['entity_id'] == approved['id']
    assert state['revisions'][approved['id']][0]['legacy_original']['req_id'] == approved['id']


@pytest.mark.parametrize('action', ['approve', 'reject'])
def test_original_legacy_id_is_reused_on_decided_regeneration(draft, action):
    repo = MemoryRepository()
    check_rerun(repo, GovernanceService(repo, FakeEvidence()), draft, action)


def test_ambiguous_reconciled_identities_fail_without_writes(draft):
    repo = MemoryRepository()
    service = GovernanceService(repo, FakeEvidence())
    reconcile(service, draft, rid='REQ-F70-001')
    # A historical writer could have persisted duplicate semantic identities.
    def historical_duplicate(state):
        record = copy.deepcopy(state['requirements']['REQ-F70-001'])
        record.update(id='REQ-F70-002', revision=1, status='DRAFT', approved_revision=None)
        state['requirements'][record['id']] = record
        state['revisions'][record['id']] = [copy.deepcopy(record)]
        service.event(state, 'REQUIREMENT_DERIVED', record['id'], None, record,
                      ('engine', 'AI_SYSTEM'), 'Historical duplicate fixture')
    repo.write(historical_duplicate)
    before = repo.read()
    with pytest.raises(DomainError) as error:
        service.create_requirement(draft, 'ambiguous-rerun')
    assert error.value.status == 409
    assert repo.read() == before


def test_http_reconciliation_identity_collision_is_atomic(backend, draft):
    client, repo, service, _ = backend
    legacy = service.import_legacy([{'req_id': 'REQ-F70-001', 'impact': 2,
                                     'market_reach_norm': .5, 'effort': 3}])[0]
    before = repo.read()
    response = client.patch('/api/requirements/' + legacy['id'], json=dict(
        expected_revision=1, actor_id='pm', actor_role='PM_USER',
        rationale='Reconcile original evidence', patch=draft))
    assert response.status_code == 409, response.text
    assert repo.read() == before


def test_hashed_alias_and_reconciled_identity_conflict_without_writes(backend, draft):
    _, repo, service, original = backend
    service.edit_requirement(original['id'], EditRequest(expected_revision=1,
        actor_id='pm', actor_role='PM_USER', rationale='Expand target market',
        patch={'target_scope': dict(draft['target_scope'], regions=['US', 'EU'])}))
    reconcile(service, draft)
    before = repo.read()
    with pytest.raises(DomainError) as error:
        service.create_requirement(draft, 'ambiguous-alias-rerun')
    assert error.value.status == 409
    assert repo.read() == before


def test_target_scope_edit_cannot_collide_with_reconciled_identity(backend, draft):
    _, repo, service, original = backend
    other = copy.deepcopy(draft)
    other['target_scope']['regions'] = ['US', 'EU']
    reconcile(service, other)
    before = repo.read()
    with pytest.raises(DomainError) as error:
        service.edit_requirement(original['id'], EditRequest(expected_revision=1,
            actor_id='pm', actor_role='PM_USER', rationale='Expand target market',
            patch={'target_scope': other['target_scope']}))
    assert error.value.status == 409
    assert repo.read() == before


def test_real_original_legacy_id_is_reused_on_approved_regeneration(graph_backend, draft):
    repo, service, _ = graph_backend
    draft['derivation_key'] = 'reconciled-ordinal'
    check_rerun(repo, service, draft)


def create_thread(client, rid):
    response = client.post('/api/review-threads', json=dict(
        expected_revision=1, actor_id='pm', actor_role='PM_USER',
        rationale='Review scoped requirement', entity_kind='requirement', entity_id=rid))
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize('field,value', [('entity_id', 'different'), ('entity_kind', 'signal'),
                                       ('actor_id', 'forged'), ('timestamp', 'forged')])
def test_thread_binding_cannot_change_without_audit(backend, field, value):
    client, repo, _, record = backend
    thread = create_thread(client, record['id'])
    before = repo.read()
    assert next(e for e in before['events'] if e['action'] == 'REVIEW_THREAD_CREATED')['after'] == thread
    def corrupt(state):
        state['threads'][thread['id']][field] = value
    with pytest.raises(ValueError, match='Review threads differ'):
        repo.write(corrupt)
    assert repo.read() == before


def test_real_coordinated_thread_rebinding_rejected_without_writes(graph_backend):
    repo, service, record = graph_backend
    with TestClient(create_app(repo, service.evidence_provider)) as client:
        thread = create_thread(client, record['id'])
    state = repo.read()
    thread['entity_kind'] = 'signal'
    thread['entity_id'] = 'signal-us-temp'
    state['threads'][thread['id']] = thread
    def corrupt(tx):
        tx.run('MATCH (l:GovernanceLedger {backend_key:$key}) SET l.payload=$payload',
               key=repo.namespace, payload=json.dumps(state)).consume()
        tx.run('MATCH (n:ReviewThread {backend_key:$key}) SET n.payload=$payload',
               key=f"{repo.namespace}:{thread['id']}", payload=json.dumps(thread)).consume()
    with repo.session() as session:
        session.execute_write(corrupt)
    before = graph_snapshot(repo)
    for operation in (repo.read, lambda: repo.write(lambda candidate: None)):
        with pytest.raises(ValueError, match='Review threads differ'):
            operation()
        assert graph_snapshot(repo) == before


def test_convenience_challenge_audits_thread_scope_and_turn(backend):
    _, repo, service, record = backend
    with TestClient(create_app(repo, service.evidence_provider, engine=FakeEngine())) as client:
        response = client.post('/api/requirements/' + record['id'] + '/challenges',
                               json=dict(identity(), question='Review current evidence'))
        assert response.status_code == 200, response.text
    state = repo.read()
    thread = state['threads'][response.json()['thread_id']]
    event = next(e for e in state['events'] if e['action'] == 'REVIEW_THREAD_CREATED')
    assert event['after'] == thread and event['entity_id'] == record['id']
    turn = next(iter(state['turns'].values()))
    assert turn['thread_id'] == thread['id'] and turn['entity_id'] == thread['entity_id']


def test_thread_audit_failure_rolls_back_creation(backend):
    client, repo, service, record = backend
    before = repo.read()
    def fail(action):
        if action == 'REVIEW_THREAD_CREATED':
            raise RuntimeError('thread audit failure')
    service.audit_failure = fail
    with pytest.raises(RuntimeError, match='thread audit failure'):
        create_thread(client, record['id'])
    assert repo.read() == before


def test_audited_turn_cannot_bind_to_another_thread_scope(backend):
    client, repo, service, record = backend
    thread = create_thread(client, record['id'])
    before = repo.read()
    def insert(state):
        turn = dict(id='wrong-scope', thread_id=thread['id'], entity_id='another-signal',
                    state='read_only', patch=None)
        state['turns'][turn['id']] = turn
        service.event(state, 'CHALLENGE_COMPLETED', turn['entity_id'], None, turn,
                      ('pm', 'PM_USER'), 'Synthetic cross-scope turn')
    with pytest.raises(ValueError, match='audited thread scope'):
        repo.write(insert)
    assert repo.read() == before
