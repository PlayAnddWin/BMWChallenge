"""Fresh regressions for engine review authority and semantic rerun identity."""
import copy
import pytest
from conftest import FakeEvidence, decision
from models import DecisionRequest, EditRequest
from services.decisions import DomainError, GovernanceService
from services.repository import MemoryRepository


@pytest.mark.parametrize('claimed_state', ['accepted', 'resolved'])
def test_engine_cannot_self_resolve_assumptions(draft, claimed_state):
    repo = MemoryRepository()
    service = GovernanceService(repo, FakeEvidence())
    draft['assumptions'][0]['review_state'] = claimed_state
    draft['smart']['metrics'][0]['review_state'] = 'reviewed'
    created = service.create_requirement(draft, 'engine')
    assert created['assumptions'][0]['review_state'] == 'pending'
    assert created['smart']['metrics'][0]['review_state'] == 'pending'
    before = repo.read()
    with pytest.raises(DomainError, match='outstanding assumptions'):
        service.decide_requirement(created['id'], DecisionRequest(**decision(accepted_assumption_ids=[])))
    assert repo.read() == before


@pytest.mark.parametrize('action', ['approve', 'reject', 'defer'])
def test_unchanged_engine_rerun_after_human_decision_has_no_redundant_turn(backend, draft, action):
    _, repo, service, created = backend
    decided = service.decide_requirement(created['id'], DecisionRequest(**decision(action=action)))
    before = repo.read()
    repeated = service.create_requirement(copy.deepcopy(draft), 'rerun')
    assert repeated == decided
    assert repo.read() == before


def test_pm_resolved_assumptions_survive_unchanged_engine_rerun(backend, draft):
    _, repo, service, created = backend
    resolved = copy.deepcopy(draft['assumptions'])
    resolved[0]['review_state'] = 'resolved'
    edited = service.edit_requirement(created['id'], EditRequest(expected_revision=1, actor_id='pm', actor_role='PM_USER', rationale='Independently established feasibility', patch={'assumptions': resolved}))
    approved = service.decide_requirement(created['id'], DecisionRequest(**decision(edited['revision'], accepted_assumption_ids=[])))
    before = repo.read()
    assert service.create_requirement(draft, 'rerun') == approved
    assert repo.read() == before


def test_changed_engine_assumption_proposal_requires_human_resolution(backend, draft):
    _, repo, service, created = backend
    service.decide_requirement(created['id'], DecisionRequest(**decision()))
    draft['assumptions'][0].update(description='New feasibility uncertainty', review_state='resolved')
    result = service.create_requirement(draft, 'rerun')
    turn = repo.read()['turns'][result['pending_turn_id']]
    assert turn['patch']['assumptions'][0]['review_state'] == 'pending'
    assert repo.read()['requirements'][created['id']]['assumptions'][0]['review_state'] == 'accepted'


@pytest.mark.parametrize('legacy_smart', [{}, 'historical self-checked SMART'])
def test_regeneration_proposes_reconciliation_of_legacy_identity_without_overwriting(draft, legacy_smart):
    computed = GovernanceService(MemoryRepository(), FakeEvidence()).create_requirement(draft, 'identity')
    repo = MemoryRepository()
    service = GovernanceService(repo, FakeEvidence())
    legacy = service.import_legacy([{'id': computed['id'], 'smart': legacy_smart, 'impact': 2, 'market_reach_norm': .5, 'effort': 3}])[0]
    result = service.create_requirement(draft, 'reconciliation')
    state = repo.read()
    assert state['requirements'][legacy['id']] == legacy
    assert state['turns'][result['pending_turn_id']]['state'] == 'pending'
    assert state['turns'][result['pending_turn_id']]['patch']['smart']['metrics'][0]['review_state'] == 'pending'


@pytest.mark.parametrize('drift', [0, 1e-10, -1e-10])
def test_installed_scoring_config_records_actual_normalized_weights_and_remains_stable(draft, drift):
    repo = MemoryRepository()
    provider = FakeEvidence()
    original = provider.resolve
    def resolve(value):
        context = original(value)
        context['score_context']['weights'] = {'customer': .4 + drift, 'reach': .25, 'strategy': .2, 'ease': .15}
        return context
    provider.resolve = resolve
    service = GovernanceService(repo, provider)
    created = service.create_requirement(draft, 'first')
    actual = {key: factor['weight'] for key, factor in created['score']['factors'].items()}
    assert created['scoring_policy']['weights'] == actual
    assert repo.read()['configs']['v1']['weights'] == actual
    assert created['score_context']['weights']['customer'] == .4 + drift
    other = copy.deepcopy(draft)
    other['derivation_key'] = 'second-derivation'
    second = service.create_requirement(other, 'second')
    assert second['scoring_policy'] == created['scoring_policy']
    assert len(repo.read()['configs']) == 1
