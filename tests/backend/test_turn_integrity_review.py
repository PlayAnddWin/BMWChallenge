"""Immutable audit anchors protect proposal content and terminal retry outcomes."""
import copy
import json

import pytest

from conftest import fixture_draft
from models import ApplyRequest, EditRequest
from services.audit import append_event
from test_neo4j_integration import graph_backend, graph_snapshot


def pending(service):
    draft = fixture_draft()
    draft['title'] = 'Human-review proposal'
    return service.create_requirement(draft, 'synthetic-rerun')['pending_turn_id']


def application(turn):
    return ApplyRequest(expected_revision=1, actor_id='pm', actor_role='PM_USER',
                        rationale='Reviewed proposal', turn_id=turn,
                        patch={'title': 'Human-review proposal'})


@pytest.mark.parametrize('attack', ['pending_patch', 'pending_base', 'pending_to_applied',
                                   'result', 'fingerprint', 'applied_to_pending', 'addition'])
def test_real_coordinated_turn_corruption_fails_without_writes(graph_backend, attack):
    repo, service, record = graph_backend
    tid = pending(service)
    request = application(tid)
    if attack in {'result', 'fingerprint', 'applied_to_pending'}:
        service.edit_requirement(record['id'], request, apply=True)
    state = repo.read()
    checkpoint = copy.deepcopy(state['checkpoint'])
    turn = state['turns'][tid]
    if attack == 'pending_patch':
        turn['patch']['title'] = 'Forged proposal'
    elif attack == 'pending_base':
        turn['expected_revision'] = 99
    elif attack == 'pending_to_applied':
        turn.update(state='applied', result=copy.deepcopy(record), fingerprint='forged')
    elif attack == 'result':
        turn['result']['title'] = 'Forged retry result'
    elif attack == 'fingerprint':
        turn['fingerprint'] = 'forged'
    elif attack == 'applied_to_pending':
        turn['state'] = 'pending'
    else:
        turn = dict(copy.deepcopy(turn), id='unaudited-turn')
        state['turns'][turn['id']] = turn
    assert state['checkpoint'] == checkpoint
    def corrupt(tx):
        tx.run('MATCH (l:GovernanceLedger {backend_key:$key}) SET l.payload=$payload',
               key=repo.namespace, payload=json.dumps(state)).consume()
        tx.run('MERGE (n:ReviewTurn {backend_key:$key}) SET n.payload=$payload',
               key=f"{repo.namespace}:{turn['id']}", payload=json.dumps(turn)).consume()
    with repo.session() as session:
        session.execute_write(corrupt)
    before = graph_snapshot(repo)
    for operation in (repo.read, lambda: repo.write(lambda candidate: None),
                      lambda: service.edit_requirement(record['id'], request, apply=True)):
        with pytest.raises(ValueError, match='Turn state differs'):
            operation()
        assert graph_snapshot(repo) == before


@pytest.mark.parametrize('attack', ['patch', 'addition', 'delete'])
def test_repository_turn_mutation_rejected_atomically(backend, attack):
    _, repo, service, _ = backend
    tid = pending(service)
    before = repo.read()
    def change(state):
        if attack == 'patch':
            state['turns'][tid]['patch']['title'] = 'Unaudited proposal'
        elif attack == 'addition':
            state['turns']['fake'] = dict(state['turns'][tid], id='fake')
        else:
            del state['turns'][tid]
    with pytest.raises(ValueError):
        repo.write(change)
    assert repo.read() == before


def test_application_retry_after_later_edit_returns_exact_anchored_revision(backend):
    _, repo, service, record = backend
    tid = pending(service)
    request = application(tid)
    applied = service.edit_requirement(record['id'], request, apply=True)
    service.edit_requirement(record['id'], EditRequest(expected_revision=2, actor_id='pm',
                             actor_role='PM_USER', rationale='Later refinement',
                             patch={'title': 'Later title'}))
    before = repo.read()
    assert service.edit_requirement(record['id'], request, apply=True) == applied
    assert repo.read() == before
    event = next(e for e in before['events'] if e['action'] == 'SUGGESTION_APPLICATION_RECORDED')
    assert event['entity_id'] == record['id'] and event['entity_type'] == 'ReviewTurn'
    assert event['after']['result'] == before['revisions'][record['id']][1]


def test_terminal_turn_event_failure_rolls_back_entire_application(backend):
    _, repo, service, record = backend
    tid = pending(service)
    before = repo.read()
    def fail(action):
        if action == 'SUGGESTION_APPLICATION_RECORDED':
            raise RuntimeError('terminal audit failure')
    service.audit_failure = fail
    with pytest.raises(RuntimeError, match='terminal audit failure'):
        service.edit_requirement(record['id'], application(tid), apply=True)
    assert repo.read() == before


@pytest.mark.parametrize('attack', ['reopen', 'changed_proposal', 'wrong_before', 'wrong_result', 'missing_terminal'])
def test_checkpoint_valid_events_cannot_encode_illegal_turn_lifecycle(backend, attack):
    _, repo, service, record = backend
    tid = pending(service)
    applied = service.edit_requirement(record['id'], application(tid), apply=True)
    before = repo.read()
    def corrupt(state):
        turn = copy.deepcopy(state['turns'][tid])
        if attack == 'missing_terminal':
            # A perfectly hashed prefix still needs the application outcome event.
            from services.audit import checkpoint_for
            state['events'].pop()
            state['checkpoint'] = checkpoint_for(state['events'])
            creation = next(e['after'] for e in state['events'] if e['action'] == 'REGENERATION_PROPOSED')
            state['turns'][tid] = copy.deepcopy(creation)
            return
        if attack == 'reopen':
            turn.update(state='pending')
            action = 'REGENERATION_PROPOSED'
        else:
            # Use the authentic pending predecessor; forge a second terminal event.
            prior = next(e['after'] for e in state['events'] if e['action'] == 'REGENERATION_PROPOSED')
            state['events'].pop()
            from services.audit import checkpoint_for
            state['checkpoint'] = checkpoint_for(state['events'])
            action = 'SUGGESTION_APPLICATION_RECORDED'
            if attack == 'changed_proposal':
                turn['patch']['title'] = 'Changed during application'
            elif attack == 'wrong_result':
                turn['result'] = copy.deepcopy(record)
            else:
                prior = dict(prior, expected_revision=99)
        state['turns'][tid] = turn
        append_event(state, {'id': 'invalid-transition', 'entity_id': record['id'],
                             'action': action, 'before': turn if attack == 'reopen' else prior,
                             'after': turn})
    from services.audit import verify_chain
    from services.repository import validate_audited_snapshots
    candidate = copy.deepcopy(before)
    corrupt(candidate)
    assert verify_chain(candidate['events'], candidate['checkpoint'])
    with pytest.raises(ValueError):
        validate_audited_snapshots(candidate)
    assert repo.read() == before and applied == before['turns'][tid]['result']


def test_real_terminal_audit_failure_rolls_back_application(graph_backend):
    repo, service, record = graph_backend
    tid = pending(service)
    before = repo.read()
    nodes = graph_snapshot(repo)
    def fail(action):
        if action == 'SUGGESTION_APPLICATION_RECORDED':
            raise RuntimeError('terminal audit failure')
    service.audit_failure = fail
    with pytest.raises(RuntimeError, match='terminal audit failure'):
        service.edit_requirement(record['id'], application(tid), apply=True)
    assert repo.read() == before
    assert graph_snapshot(repo) == nodes
