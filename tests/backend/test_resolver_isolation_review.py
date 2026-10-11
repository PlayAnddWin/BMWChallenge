"""Evidence adapters cannot mutate validated content or establish PM review."""
import copy

import pytest
from conftest import FakeEvidence, decision
from models import ApplyRequest, DecisionRequest, EditRequest
from services.decisions import DomainError, GovernanceService
from services.repository import MemoryRepository


class MutatingEvidence(FakeEvidence):
    def __init__(self, mutation):
        self.mutation = mutation

    def resolve(self, draft):
        result = super().resolve(draft)
        if self.mutation == 'review':
            draft['assumptions'][0]['review_state'] = 'resolved'
            draft['smart']['metrics'][0]['review_state'] = 'reviewed'
        else:
            draft['smart']['metrics'][0]['operator'] = 'invalid'
            draft['title'] = None
            draft['target_scope']['regions'].append('INVALID')
        return result


@pytest.mark.parametrize('mutation', ['review', 'malformed'])
def test_create_isolates_validated_draft_and_audit(draft, mutation):
    original = copy.deepcopy(draft)
    repo = MemoryRepository()
    service = GovernanceService(repo, MutatingEvidence(mutation))
    record = service.create_requirement(draft, 'resolver-isolation')
    assert draft == original
    assert record['title'] == original['title']
    assert record['target_scope'] == original['target_scope']
    assert record['smart']['metrics'][0]['operator'] == '<='
    assert record['assumptions'][0]['review_state'] == 'pending'
    assert record['smart']['metrics'][0]['review_state'] == 'pending'
    state = repo.read()
    assert state['revisions'][record['id']][0] == record
    assert next(event for event in state['events'] if event['action'] == 'REQUIREMENT_DERIVED')['after'] == record
    with pytest.raises(DomainError, match='outstanding assumptions'):
        service.decide_requirement(record['id'], DecisionRequest(**decision(accepted_assumption_ids=[])))
    assert repo.read() == state


@pytest.mark.parametrize('operation', ['edit', 'apply', 'preview'])
@pytest.mark.parametrize('mutation', ['review', 'malformed'])
def test_resolver_mutation_cannot_change_proposed_content(backend, draft, operation, mutation):
    client, repo, service, record = backend
    patch = {'title': 'Human selected title'}
    request_data = dict(expected_revision=1, actor_id='pm-test', actor_role='PM_USER', rationale='Review title', patch=patch)
    if operation == 'apply':
        proposal = copy.deepcopy(draft)
        proposal.update(patch)
        turn_id = service.create_requirement(proposal, 'proposal')['pending_turn_id']
        request_data['turn_id'] = turn_id
    service.evidence_provider = MutatingEvidence(mutation)
    before = repo.read()
    if operation == 'preview':
        response = client.post('/api/scoring/preview', json=dict(requirement_id=record['id'], expected_revision=1, patch=patch))
        assert response.status_code == 200, response.text
        result = response.json()['proposed_content']
        assert response.json()['unresolved_checks']['accepted_assumption_ids'] == ['assumption-1']
        assert repo.read() == before
    else:
        request = ApplyRequest(**request_data) if operation == 'apply' else EditRequest(**request_data)
        untouched = request.model_dump()
        result = service.edit_requirement(record['id'], request, apply=operation == 'apply')
        assert request.model_dump() == untouched
        state = repo.read()
        assert state['revisions'][record['id']][-1] == result
        assert next(event for event in reversed(state['events']) if event['action'] in ('CONTENT_CHANGED', 'SUGGESTION_APPLIED'))['after'] == result
        with pytest.raises(DomainError, match='outstanding assumptions'):
            service.decide_requirement(record['id'], DecisionRequest(**decision(result['revision'], accepted_assumption_ids=[])))
        assert repo.read() == state
    assert result['title'] == patch['title']
    assert result['target_scope'] == draft['target_scope']
    assert result['smart']['metrics'][0]['operator'] == '<='
    assert result['smart']['metrics'][0]['review_state'] == 'pending'
    assert result['assumptions'][0]['review_state'] == 'pending'


def test_context_detaches_returned_adapter_references(draft):
    provider = FakeEvidence()
    context = provider.resolve(draft)
    provider.resolve = lambda value: context
    service = GovernanceService(MemoryRepository(), provider)
    original = copy.deepcopy(draft)
    resolved = service.context(draft)
    context['score_context']['region_counts']['US']['support'] = 999
    context['source_snapshots'][0]['raw_payload']['text'] = 'changed after validation'
    assert draft == original
    assert resolved['score_context']['region_counts']['US']['support'] == 2
    assert resolved['source_snapshots'][0]['raw_payload']['text'] != 'changed after validation'


@pytest.mark.parametrize('invalid', [False, True])
def test_smart_validation_never_mutates_caller_input(draft, invalid):
    from models import RequirementDraft
    from pydantic import ValidationError
    if invalid:
        draft['smart']['metrics'][0]['operator'] = 'invalid'
    original = copy.deepcopy(draft)
    if invalid:
        with pytest.raises(ValidationError):
            RequirementDraft.model_validate(draft)
    else:
        validated = RequirementDraft.model_validate(draft).model_dump()
        assert validated['smart']['metrics'][0]['review_state'] == 'pending'
    assert draft == original


@pytest.mark.parametrize('invalid', [False, True])
def test_scope_normalization_never_mutates_caller_input(draft, invalid):
    from models import RequirementDraft
    from pydantic import ValidationError
    for name in ('source_scope', 'target_scope'):
        draft[name]['regions'] = [' US ', 'US']
        draft[name]['derivatives'] = ['G60', ' G60 ']
    if invalid:
        draft['target_scope']['powertrains'] = ['INVALID']
    original = copy.deepcopy(draft)
    if invalid:
        with pytest.raises(ValidationError):
            RequirementDraft.model_validate(draft)
    else:
        validated = RequirementDraft.model_validate(draft).model_dump()
        assert validated['source_scope']['regions'] == ['US']
        assert validated['target_scope']['derivatives'] == ['G60']
    assert draft == original


def test_edit_request_nested_patch_remains_unchanged(backend, draft):
    _, _, service, record = backend
    scope = copy.deepcopy(draft['target_scope'])
    scope['regions'] = [' US ', 'US']
    request = EditRequest(expected_revision=1, actor_id='pm-test', actor_role='PM_USER', rationale='Normalize scope', patch={'smart': copy.deepcopy(draft['smart']), 'target_scope': scope})
    original = copy.deepcopy(request.model_dump())
    result = service.edit_requirement(record['id'], request)
    assert result['target_scope']['regions'] == ['US']
    assert request.model_dump() == original


def test_citation_verifier_cannot_rewrite_validated_review_output(draft):
    from fastapi.testclient import TestClient
    from services.api import create_app
    from test_review import FakeEngine, create_thread, ask
    citation = copy.deepcopy(draft['evidence_links'][0])
    citation.update(source_id='additional-source', record_id='additional-record')
    expected = copy.deepcopy(citation)

    class CitingEngine(FakeEngine):
        def review(self, current, question, turns):
            return {'answer': 'Additional verified source.', 'citations': [citation]}

    class MutatingVerifier:
        def verify_reference(self, reference):
            assert reference == expected
            reference['source_id'] = 'forged'
            reference['locator'] = {'page': -1}
            return True

    repo = MemoryRepository()
    app = create_app(repo, FakeEvidence(), engine=CitingEngine(), source_provider=MutatingVerifier())
    with TestClient(app) as client:
        record = app.state.service.create_requirement(draft, 'citation-isolation')
        completed = ask(client, create_thread(client, record['id']))
        assert completed['state'] == 'succeeded', completed
        state = repo.read()
        turn = next(iter(state['turns'].values()))
        assert turn['citations'] == [expected]
        assert citation == expected
        event = next(event for event in state['events'] if event['action'] == 'CHALLENGE_COMPLETED')
        assert event['after']['citations'] == [expected]


@pytest.mark.parametrize('progress', [False, True])
def test_analysis_adapter_cannot_raise_validated_generator_limit(draft, progress):
    from fastapi.testclient import TestClient
    from services.api import create_app
    seen = []

    def generate(scope):
        scope['max_drafts'] = 4
        scope['regions'].append('INVALID')
        for index in range(4):
            seen.append(index)
            value = copy.deepcopy(draft)
            value['derivation_key'] += str(index)
            yield value

    class Engine:
        if progress:
            def analyze(self, scope, progress_callback):
                # Mutation before returning generator must also remain isolated.
                scope['max_drafts'] = 4
                progress_callback(stage='derive', progress=.1)
                return generate(scope)
        else:
            def analyze(self, scope):
                scope['max_drafts'] = 4
                return generate(scope)

    repo = MemoryRepository()
    app = create_app(repo, FakeEvidence(), engine=Engine(), max_analysis_drafts=2)
    with TestClient(app) as client:
        response = client.post('/api/analysis-runs', json={'max_drafts': 1, 'regions': ['US']})
        assert response.status_code == 200, response.text
        completed = client.get('/api/analysis-runs/' + response.json()['id']).json()
    assert completed['state'] == 'partial', completed
    assert completed['scope'] == {'max_drafts': 1, 'regions': ['US']}
    assert completed['failures'] == ['Engine output exceeds requested draft limit']
    assert len(completed['result_ids']) == len(repo.read()['requirements']) == 1
    assert seen == [0, 1]
