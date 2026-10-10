"""Role 03 review persistence and authoritative hypothetical changes."""
import copy
import pytest
from fastapi.testclient import TestClient
from conftest import FakeEvidence, fixture_draft
from test_review import FakeEngine, identity, create_thread, ask
from test_fresh_api_review import ProbeRepository
from services.api import create_app
from services.repository import MemoryRepository


class MetadataEngine(FakeEngine):
    def review(self, current, question, turns):
        return dict(super().review(current, question, turns), coverage_gaps=['No regional field validation'],
                    model_metadata={'provider': 'fake', 'model': 'synthetic-v1'},
                    prompt_metadata={'version': 'review-v2', 'context_hash': 'synthetic'})


def test_review_metadata_and_complete_preview_are_audited_without_content_writes():
    repo = MemoryRepository()
    with TestClient(create_app(repo, FakeEvidence(), MetadataEngine({'title': 'Proposed title'}))) as client:
        record = client.app.state.service.create_requirement(fixture_draft(), 'contract-review')
        before = copy.deepcopy(repo.read()['requirements'])
        thread = create_thread(client, record['id'])
        run = ask(client, thread)
        assert run['state'] == 'succeeded'
        data = repo.read()
        turn = data['turns'][run['result_ids'][0]]
        assert turn['coverage_gaps'] == ['No regional field validation']
        assert turn['model_metadata']['model'] == 'synthetic-v1'
        assert turn['prompt_metadata']['version'] == 'review-v2'
        preview = turn['change_preview']
        assert preview['new_evidence']['value'] == record['evidence']['value']
        assert preview['new_score'] == turn['score_preview']
        assert preview['proposed_content']['title'] == 'Proposed title'
        assert preview['human_review_required'] is True
        assert preview['unresolved_checks']['reviewed_target_ids']
        completed = next(e for e in data['events'] if e['action'] == 'CHALLENGE_COMPLETED')
        assert completed['after']['prompt_metadata'] == turn['prompt_metadata']
        assert data['requirements'] == before


@pytest.mark.parametrize('field,value', [('coverage_gaps', [3]), ('coverage_gaps', [' ']), ('coverage_gaps', {}),
    ('model_metadata', []), ('prompt_metadata', {'value': float('nan')}), ('model_metadata', {'value': object()})])
def test_malformed_optional_engine_fields_fail_without_successful_turn(field, value):
    class BadEngine(FakeEngine):
        def review(self, current, question, turns):
            return dict(super().review(current, question, turns), **{field: value})
    repo = MemoryRepository()
    with TestClient(create_app(repo, FakeEvidence(), BadEngine())) as client:
        record = client.app.state.service.create_requirement(fixture_draft(), 'contract-review')
        thread = create_thread(client, record['id'])
        run = ask(client, thread)
        assert run['state'] == 'failed'
        assert repo.read()['turns'] == {}


def test_patch_preview_uses_authoritative_evidence_one_snapshot_and_no_writes():
    class ChangedEvidence(FakeEvidence):
        def resolve(self, draft):
            context = super().resolve(draft)
            if draft['title'] == 'Hypothetical':
                context['score_context']['severity_level'] = 4
                context['evidence_context']['assignment_reviews'] = [1]
            return context
    repo = ProbeRepository()
    with TestClient(create_app(repo, ChangedEvidence())) as client:
        record = client.app.state.service.create_requirement(fixture_draft(), 'contract-review')
        before = repo.read()
        repo.reads = 0
        body = {'requirement_id': record['id'], 'expected_revision': 1, 'patch': {'title': 'Hypothetical'}}
        response = client.post('/api/scoring/preview', json=body)
        assert response.status_code == 200, response.text
        assert repo.reads == 1
        preview = response.json()
        assert preview['new_score']['effective_score'] > record['score']['effective_score']
        assert preview['new_evidence']['value'] > record['evidence']['value']
        assert repo.read() == before
        body['expected_revision'] = 2
        assert client.post('/api/scoring/preview', json=body).status_code == 409
        assert repo.read() == before


@pytest.mark.parametrize('action', ['reject', 'defer'])
def test_change_preview_rank_reentry_matches_actual_apply(action):
    class Evidence(FakeEvidence):
        def resolve(self, draft):
            context = super().resolve(draft)
            if draft['title'] == 'Higher severity':
                context['score_context']['severity_level'] = 4
            return context
    repo = MemoryRepository()
    with TestClient(create_app(repo, Evidence(), FakeEngine({'title': 'Higher severity'}))) as client:
        record = client.app.state.service.create_requirement(fixture_draft(), 'rank-review')
        other = fixture_draft()
        other['derivation_key'] += '-other'
        second = client.app.state.service.create_requirement(other, 'rank-review')
        response = client.post(f"/api/requirements/{record['id']}/decisions", json=dict(identity(), action=action))
        assert response.status_code == 200, response.text
        thread = client.post('/api/review-threads', json=dict(identity(2), entity_kind='requirement', entity_id=record['id'])).json()
        started = client.post(f"/api/review-threads/{thread['id']}/turns", json=dict(identity(2), question='Preview reentry')).json()
        run = client.get('/api/analysis-runs/' + started['id']).json()
        turn = repo.read()['turns'][run['result_ids'][0]]
        preview = turn['change_preview']
        assert preview['old_ranks'][record['id']] is None
        assert preview['new_ranks'][record['id']] == 1
        assert preview['new_ranks'][second['id']] == 2
        response = client.post(f"/api/requirements/{record['id']}/challenge-applications", json=dict(identity(2), turn_id=turn['id'], patch=turn['patch']))
        assert response.status_code == 200, response.text
        applied = response.json()
        assert applied['score'] == preview['new_score']
        assert applied['evidence'] == preview['new_evidence']
        assert all(applied[key] == value for key, value in preview['proposed_content'].items())
        assert repo.read()['rankings'][-1]['ranks'] == preview['new_ranks']


def test_read_only_review_preserves_gaps_and_old_adapter_defaults():
    for engine, expected_gaps in [(MetadataEngine(), ['No regional field validation']), (FakeEngine(), [])]:
        repo = MemoryRepository()
        with TestClient(create_app(repo, FakeEvidence(), engine)) as client:
            record = client.app.state.service.create_requirement(fixture_draft(), 'read-only-contract')
            thread = create_thread(client, record['id'])
            run = ask(client, thread)
            turn = repo.read()['turns'][run['result_ids'][0]]
            assert turn['state'] == 'read_only'
            assert turn['coverage_gaps'] == expected_gaps
            assert turn['change_preview'] is None and turn['score_preview'] is None
            if not expected_gaps:
                assert turn['model_metadata'] == turn['prompt_metadata'] == {}


def test_preview_reports_source_backed_target_provenance_mismatch():
    repo = MemoryRepository()
    with TestClient(create_app(repo, FakeEvidence())) as client:
        record = client.app.state.service.create_requirement(fixture_draft(), 'target-preview')
        smart = copy.deepcopy(record['smart'])
        metric = smart['metrics'][0]
        metric.update(provenance='SourceBacked', source_references=['unrelated-source'])
        body = {'requirement_id': record['id'], 'expected_revision': 1, 'patch': {'smart': smart}}
        before = repo.read()
        response = client.post('/api/scoring/preview', json=body)
        assert response.status_code == 200, response.text
        assert response.json()['unresolved_checks']['unresolved_target_provenance_ids'] == [metric['id']]
        assert repo.read() == before


@pytest.mark.parametrize('prior_resolved,changed', [(False, False), (True, False), (True, True)])
def test_engine_cannot_resolve_assumptions_and_preview_matches_application(prior_resolved, changed):
    from conftest import decision
    repo = MemoryRepository()
    engine = FakeEngine()
    with TestClient(create_app(repo, FakeEvidence(), engine)) as client:
        record = client.app.state.service.create_requirement(fixture_draft(), 'human-state')
        if prior_resolved:
            assumptions = copy.deepcopy(record['assumptions'])
            assumptions[0]['review_state'] = 'resolved'
            response = client.patch('/api/requirements/' + record['id'], json=dict(identity(), patch={'assumptions': assumptions}))
            assert response.status_code == 200, response.text
            record = response.json()
        assumptions = copy.deepcopy(record['assumptions'])
        assumptions[0]['review_state'] = 'resolved'
        if changed:
            assumptions[0]['description'] = 'Changed feasibility assumption'
        engine.patch = {'assumptions': assumptions}
        thread = client.post('/api/review-threads', json=dict(identity(record['revision']), entity_kind='requirement', entity_id=record['id'])).json()
        started = client.post('/api/review-threads/' + thread['id'] + '/turns', json=dict(identity(record['revision']), question='Check assumption')).json()
        run = client.get('/api/analysis-runs/' + started['id']).json()
        assert run['state'] == 'succeeded', run
        turn = repo.read()['turns'][run['result_ids'][0]]
        expected = 'resolved' if prior_resolved and not changed else 'pending'
        assert turn['patch']['assumptions'][0]['review_state'] == expected
        # A caller reasserting the engine's forged state cannot bypass normalization.
        response = client.post('/api/requirements/' + record['id'] + '/challenge-applications', json=dict(identity(record['revision']), turn_id=turn['id'], patch={'assumptions': assumptions}))
        assert response.status_code == 200, response.text
        applied = response.json()
        assert applied['assumptions'] == turn['change_preview']['proposed_content']['assumptions']
        response = client.post('/api/requirements/' + record['id'] + '/decisions', json=decision(applied['revision'], accepted_assumption_ids=[]))
        assert response.status_code == (200 if expected == 'resolved' else 422), response.text


def test_regeneration_preserves_unchanged_human_states_within_changed_collection():
    from models import EditRequest, ApplyRequest
    repo = MemoryRepository()
    with TestClient(create_app(repo, FakeEvidence())) as client:
        service = client.app.state.service
        draft = fixture_draft()
        draft['assumptions'].append({'id': 'second', 'description': 'Second assumption'})
        record = service.create_requirement(draft, 'initial')
        assumptions = copy.deepcopy(record['assumptions'])
        assumptions[0]['review_state'] = 'resolved'
        record = service.edit_requirement(record['id'], EditRequest(**dict(identity(), patch={'assumptions': assumptions})))
        draft['assumptions'][1]['description'] = 'Changed second assumption'
        regenerated = service.create_requirement(draft, 'regenerated')
        turn = repo.read()['turns'][regenerated['pending_turn_id']]
        assert turn['patch']['assumptions'][0]['review_state'] == 'resolved'
        applied = service.edit_requirement(record['id'], ApplyRequest(**dict(identity(2), turn_id=turn['id'], patch=turn['patch'])), apply=True)
        assert applied['assumptions'][0]['review_state'] == 'resolved'
        assert applied['assumptions'][1]['review_state'] == 'pending'


def test_list_exposes_global_ranks_without_comparing_legacy_and_v1_scales():
    repo = MemoryRepository()
    with TestClient(create_app(repo, FakeEvidence())) as client:
        service = client.app.state.service
        low = fixture_draft()
        low['factor_proposals'].update(severity_level=1, strategic_fit_level=0, complexity_level=5)
        first = service.create_requirement(low, 'rank')
        high = fixture_draft()
        high['derivation_key'] += '-high'
        high['factor_proposals'].update(severity_level=4, strategic_fit_level=4, complexity_level=1)
        second = service.create_requirement(high, 'rank')
        service.import_legacy([{'req_id': 'legacy', 'impact': 10, 'market_reach_norm': 10, 'effort': 1}])
        before = repo.read()
        items = client.get('/api/requirements').json()['items']
        assert [(r['id'], r['rank']) for r in items] == [(second['id'], 1), (first['id'], 2), ('legacy', None)]
        filtered = client.get('/api/requirements', params={'search': first['title'], 'offset': 1, 'limit': 1}).json()
        assert filtered['items'][0]['rank'] == 2
        assert repo.read() == before


@pytest.mark.parametrize('field,value', [('signal_revision', True), ('signal_revision', 1.0), ('signal_revision', '1'), ('has_conflict', 'false'), ('has_conflict', 1)])
def test_draft_identity_and_conflict_fields_do_not_coerce(field, value):
    from pydantic import ValidationError
    from models import RequirementDraft
    draft = fixture_draft()
    draft[field] = value
    with pytest.raises(ValidationError):
        RequirementDraft.model_validate(draft)


def test_huge_out_of_policy_factor_rejects_without_numeric_overflow():
    from pydantic import ValidationError
    from models import RequirementDraft
    draft = fixture_draft()
    draft['factor_proposals']['severity_level'] = 10 ** 1000
    with pytest.raises(ValidationError):
        RequirementDraft.model_validate(draft)
    # Integer metric targets are mathematically finite and need no float conversion.
    draft = fixture_draft()
    draft['smart']['metrics'][0]['target'] = 10 ** 1000
    assert RequirementDraft.model_validate(draft).smart['metrics'][0]['target'] == 10 ** 1000


@pytest.mark.parametrize('changed', [False, True])
def test_engine_target_states_follow_content_identity_and_not_requested_state(changed):
    from models import EditRequest
    repo = MemoryRepository()
    engine = FakeEngine()
    with TestClient(create_app(repo, FakeEvidence(), engine)) as client:
        service = client.app.state.service
        record = service.create_requirement(fixture_draft(), 'target-state')
        smart = copy.deepcopy(record['smart'])
        smart['metrics'][0]['review_state'] = 'reviewed'
        smart['milestone']['review_state'] = 'resolved'
        record = service.edit_requirement(record['id'], EditRequest(**dict(identity(), patch={'smart': smart})))
        smart['metrics'][0]['review_state'] = 'accepted'
        smart['milestone']['review_state'] = 'accepted'
        if changed:
            smart['metrics'][0]['target'] = '3'
            smart['milestone']['value'] = 'Changed milestone'
        engine.patch = {'smart': smart}
        thread = client.post('/api/review-threads', json=dict(identity(2), entity_kind='requirement', entity_id=record['id'])).json()
        started = client.post('/api/review-threads/' + thread['id'] + '/turns', json=dict(identity(2), question='Review targets')).json()
        run = client.get('/api/analysis-runs/' + started['id']).json()
        assert run['state'] == 'succeeded', run
        turn = repo.read()['turns'][run['result_ids'][0]]
        assert turn['patch']['smart']['metrics'][0]['review_state'] == ('pending' if changed else 'reviewed')
        assert turn['patch']['smart']['milestone']['review_state'] == ('pending' if changed else 'resolved')
        response = client.post('/api/requirements/' + record['id'] + '/challenge-applications', json=dict(identity(2), turn_id=turn['id'], patch=turn['patch']))
        assert response.status_code == 200, response.text
        assert response.json()['smart'] == turn['change_preview']['proposed_content']['smart']


def test_source_import_entity_type_is_explicit_and_not_inferred_from_payload_state():
    from services.decisions import GovernanceService
    repo = MemoryRepository()
    service = GovernanceService(repo, FakeEvidence())
    source = {'id': 'source', 'state': 'imported'}
    run = {'id': 'run', 'state': 'succeeded'}
    repo.write(lambda data: service.event(data, 'SOURCE_IMPORTED', 'source', None, source, ('import', 'AI_SYSTEM'), 'Source imported'))
    repo.write(lambda data: service.event(data, 'SOURCE_IMPORTED', 'run', None, run, ('import', 'AI_SYSTEM'), 'Run completed', entity_type='ImportRun'))
    assert [event['entity_type'] for event in repo.read()['events']] == ['SourceFile', 'ImportRun']


@pytest.mark.parametrize('locator', [{'page': True}, {'page': 1.0}, {'row': 2.0}, {'page': 0}, {'row': -1}, {'cell': True}, {'cell': None}, {'sheet': ' '}, {'coordinates': []}])
def test_source_locator_rejects_ambiguous_or_empty_coordinates(locator):
    from models import SourceReference
    from pydantic import ValidationError
    reference = fixture_draft()['evidence_links'][0]
    reference['locator'] = locator
    with pytest.raises(ValidationError):
        SourceReference.model_validate(reference)


def test_source_locator_preserves_generic_original_coordinate_types():
    from models import SourceReference
    reference = fixture_draft()['evidence_links'][0]
    reference['locator'] = {'sheet': 'Feedback', 'row': 2, 'column': 'A', 'span': {'start': 0, 'end': 5}, 'bbox': [0.5, 1.5]}
    assert SourceReference.model_validate(reference).locator == reference['locator']


def test_review_citation_requires_exact_coordinate_types_even_when_python_equal():
    class CitationEngine(FakeEngine):
        def review(self, current, question, turns):
            result = super().review(current, question, turns)
            result['citations'][0]['locator']['offset'] = 1.0
            return result
    draft = fixture_draft()
    draft['evidence_links'][0]['locator']['offset'] = 1
    repo = MemoryRepository()
    with TestClient(create_app(repo, FakeEvidence(), CitationEngine())) as client:
        record = client.app.state.service.create_requirement(draft, 'citation-types')
        before = copy.deepcopy(repo.read()['requirements'])
        run = ask(client, create_thread(client, record['id']))
        assert run['state'] == 'failed'
        assert repo.read()['turns'] == {}
        assert repo.read()['requirements'] == before


@pytest.mark.parametrize('mutation', ['edit', 'status'])
def test_pending_turn_staleness_is_derived_from_current_revision_without_writes(mutation):
    repo = MemoryRepository()
    with TestClient(create_app(repo, FakeEvidence(), FakeEngine({'title': 'Proposal'}))) as client:
        record = client.app.state.service.create_requirement(fixture_draft(), 'stale-review')
        thread = create_thread(client, record['id'])
        run = ask(client, thread)
        turn_id = run['result_ids'][0]
        assert client.get('/api/review-threads/' + thread).json()['turns'][0]['stale'] is False
        if mutation == 'edit':
            response = client.patch('/api/requirements/' + record['id'], json=dict(identity(), patch={'title': 'Human edit'}))
        else:
            response = client.post('/api/requirements/' + record['id'] + '/decisions', json=dict(identity(), action='start_review'))
        assert response.status_code == 200, response.text
        before = repo.read()
        observed = client.get('/api/review-threads/' + thread).json()
        assert observed['current_revision'] == 2
        assert observed['turns'][0]['id'] == turn_id and observed['turns'][0]['stale'] is True
        assert repo.read() == before
        assert before['turns'][turn_id]['stale'] is False


@pytest.mark.parametrize('operation', ['approve', 'freeze', 'regenerate'])
def test_pinned_evidence_drift_rejects_equal_valued_different_numeric_types(operation):
    from models import DecisionRequest, FreezeRequest
    from conftest import decision
    from services.decisions import DomainError
    class Evidence(FakeEvidence):
        drift = False
        def resolve(self, draft):
            context = super().resolve(draft)
            context['source_snapshots'][0]['raw_payload']['native_value'] = 1.0 if self.drift else 1
            return context
    provider = Evidence()
    repo = MemoryRepository()
    with TestClient(create_app(repo, provider)) as client:
        service = client.app.state.service
        record = service.create_requirement(fixture_draft(), 'pinned-evidence')
        if operation == 'freeze':
            record = service.decide_requirement(record['id'], DecisionRequest(**decision()))
        provider.drift = True
        before = repo.read()
        if operation == 'regenerate':
            result = service.create_requirement(fixture_draft(), 'changed-source')
            assert result['pending_turn_id']
            assert repo.read()['requirements'] == before['requirements']
        else:
            with pytest.raises(DomainError) as error:
                if operation == 'approve':
                    service.decide_requirement(record['id'], DecisionRequest(**decision()))
                else:
                    service.freeze(FreezeRequest(selections={record['id']: record['revision']}, expected_ranking_id=before['rankings'][-1]['id'], actor_id='pm', actor_role='PM_USER', rationale='Freeze pinned sources'))
            assert error.value.status == 409
            assert repo.read() == before


def test_signal_read_only_turn_staleness_tracks_external_revision_without_writes():
    class Signals:
        revision = 1
        def get(self, kind, entity_id):
            return {'id': entity_id, 'revision': self.revision, 'evidence_links': []}
    provider = Signals()
    repo = MemoryRepository()
    with TestClient(create_app(repo, FakeEvidence(), FakeEngine(), provider)) as client:
        thread = client.post('/api/review-threads', json=dict(identity(), entity_kind='signal', entity_id='signal')).json()['id']
        run = ask(client, thread)
        assert run['state'] == 'succeeded'
        before = repo.read()
        assert client.get('/api/review-threads/' + thread).json()['turns'][0]['stale'] is False
        provider.revision = 2
        observed = client.get('/api/review-threads/' + thread).json()
        assert observed['current_revision'] == 2
        assert observed['turns'][0]['stale'] is True
        assert observed['turns'][0]['state'] == 'read_only'
        assert repo.read() == before


@pytest.mark.parametrize('outcome', ['applied', 'discarded'])
def test_consumed_turn_staleness_outcome_remains_unchanged(outcome):
    repo = MemoryRepository()
    with TestClient(create_app(repo, FakeEvidence(), FakeEngine({'title': 'Proposal'}))) as client:
        record = client.app.state.service.create_requirement(fixture_draft(), 'consumed-review')
        thread = create_thread(client, record['id'])
        run = ask(client, thread)
        turn = repo.read()['turns'][run['result_ids'][0]]
        if outcome == 'applied':
            response = client.post('/api/requirements/' + record['id'] + '/challenge-applications', json=dict(identity(), turn_id=turn['id'], patch=turn['patch']))
            revision = 2
        else:
            response = client.post('/api/review-threads/' + thread + '/turns/' + turn['id'] + '/discard', json=identity())
            revision = 1
        assert response.status_code == 200, response.text
        response = client.patch('/api/requirements/' + record['id'], json=dict(identity(revision), patch={'title': 'Later human edit'}))
        assert response.status_code == 200, response.text
        before = repo.read()
        observed = client.get('/api/review-threads/' + thread).json()['turns'][0]
        assert observed == before['turns'][turn['id']]
        assert observed['state'] == outcome
        assert repo.read() == before


def test_engine_content_type_change_does_not_inherit_human_resolution():
    from services.decisions import engine_review_patch
    current = {'assumptions': [{'id': 'a', 'description': 'Native value', 'native_value': 1, 'review_state': 'resolved'}]}
    patch = {'assumptions': [{'id': 'a', 'description': 'Native value', 'native_value': 1.0, 'review_state': 'resolved'}]}
    assert engine_review_patch(current, patch)['assumptions'][0]['review_state'] == 'pending'
    assert current['assumptions'][0]['review_state'] == 'resolved'


def test_regeneration_detects_exact_numeric_content_changes():
    repo = MemoryRepository()
    with TestClient(create_app(repo, FakeEvidence())) as client:
        service = client.app.state.service
        draft = fixture_draft()
        draft['assumptions'][0]['native_value'] = 1
        record = service.create_requirement(draft, 'initial')
        draft['assumptions'][0]['native_value'] = 1.0
        regenerated = service.create_requirement(draft, 'native-change')
        turn = repo.read()['turns'][regenerated['pending_turn_id']]
        assert type(turn['patch']['assumptions'][0]['native_value']) is float
        assert repo.read()['requirements'][record['id']]['revision'] == 1
