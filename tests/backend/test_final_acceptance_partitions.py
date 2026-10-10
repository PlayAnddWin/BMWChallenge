"""Remaining public-boundary partitions, without duplicating formula/graph tests."""
import copy

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from conftest import fixture_draft
from models import RequirementDraft, SourceReference
from services.api import create_app
from services.repository import MemoryRepository, same_snapshot


@pytest.mark.parametrize('drafts', [None, {}, 'draft', 1])
def test_analysis_rejects_non_list_envelope_and_releases_admission(drafts):
    from conftest import FakeEvidence

    class Engine:
        def analyze(self, scope):
            return {'drafts': drafts, 'model_metadata': {'model': 'synthetic'}}

    repo = MemoryRepository()
    with TestClient(create_app(repo, FakeEvidence(), engine=Engine())) as client:
        before = repo.read()
        for _ in range(2):
            response = client.post('/api/analysis-runs', json={})
            assert response.status_code == 200, response.text
            run = client.get('/api/analysis-runs/' + response.json()['id']).json()
            assert run['state'] == 'failed'
            assert run['result_ids'] == []
            assert run['pending_turn_ids'] == []
            assert any('drafts must be a list' in failure for failure in run['failures'])
        after = repo.read()
        assert len(after['runs']) == 2
        assert same_snapshot({k: v for k, v in after.items() if k != 'runs'},
                             {k: v for k, v in before.items() if k != 'runs'})


@pytest.mark.parametrize('metadata', [[], 'model', {'unknown': 'value'}])
def test_generation_metadata_invalid_shape_rejected_before_evidence_or_writes(backend, metadata):
    from services.decisions import DomainError

    _, repo, service, _ = backend
    before = repo.read()
    def must_not_resolve(draft):
        pytest.fail('Invalid generation metadata reached evidence resolution')
    service.evidence_provider.resolve = must_not_resolve
    with pytest.raises(DomainError) as error:
        service.create_requirement(fixture_draft(), 'new-run', generation_metadata=metadata)
    assert error.value.status == 422
    assert same_snapshot(repo.read(), before)


@pytest.mark.parametrize('operation,body', [
    ('sales-facts', {'year': True}),
    ('sales-facts', {'year': 1899}),
    ('sales-facts', {'year': 2201}),
    ('document-search', {'query': 'strategy', 'document_kind': '  '}),
])
def test_retrieval_rejects_invalid_year_or_document_kind_before_adapter(operation, body):
    class Provider:
        calls = 0

        def get_sales_facts(self, **kwargs):
            self.calls += 1
            return []

        def search_document_chunks(self, **kwargs):
            self.calls += 1
            return []

    provider = Provider()
    repo = MemoryRepository()
    with TestClient(create_app(repo, source_provider=provider)) as client:
        before = repo.read()
        response = client.post('/api/evidence/' + operation, json=body)
        assert response.status_code == 422, response.text
        assert provider.calls == 0
        assert same_snapshot(repo.read(), before)


@pytest.mark.parametrize('revision', [None, True, 0, -1])
def test_patch_preview_requires_a_positive_integer_revision_without_writes(backend, revision):
    client, repo, _, record = backend
    body = {'requirement_id': record['id'], 'patch': {'title': 'Hypothetical title'}}
    if revision is not None:
        body['expected_revision'] = revision
    before = repo.read()
    response = client.post('/api/scoring/preview', json=body)
    assert response.status_code == 422, response.text
    assert same_snapshot(repo.read(), before)


def test_reconciliation_preview_recomputes_legacy_score_and_enters_v1_ranking_without_writes(backend):
    client, repo, service, current = backend
    legacy = service.import_legacy([{'id': 'legacy-preview', 'impact': 7,
                                    'market_reach_norm': 8, 'effort': 4}])[0]
    before = repo.read()
    patch = fixture_draft()
    response = client.post('/api/scoring/preview', json={
        'requirement_id': legacy['id'], 'expected_revision': 1, 'patch': patch})
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview['old_score']['policy_version'] == 'legacy-v0'
    assert preview['old_score']['scale'] == [0, 10]
    assert preview['new_score']['policy_version'] == 'v1'
    assert preview['new_score']['scale'] == [0, 100]
    assert preview['new_score']['calculated_score'] == pytest.approx(55.06394529484361)
    assert preview['new_evidence']['value'] == pytest.approx(67.27953594466527)
    assert legacy['id'] not in preview['old_ranks']
    assert preview['new_ranks'] == {current['id']: 1, legacy['id']: 2}
    assert preview['rank_movement'][legacy['id']] == {'before': None, 'after': 2}
    assert preview['proposed_content'] == RequirementDraft.model_validate(copy.deepcopy(patch)).model_dump()
    assert same_snapshot(repo.read(), before)


def test_patch_preview_rejects_authoritative_baseline_drift_without_writes(backend):
    client, repo, service, record = backend
    resolve = service.evidence_provider.resolve

    def drift(draft):
        context = resolve(draft)
        context['score_context']['reference_volume'] = 201
        return context

    service.evidence_provider.resolve = drift
    before = repo.read()
    response = client.post('/api/scoring/preview', json={
        'requirement_id': record['id'], 'expected_revision': 1,
        'patch': {'title': 'Hypothetical title'}})
    assert response.status_code == 409, response.text
    assert same_snapshot(repo.read(), before)


@pytest.mark.parametrize('locator', [{1: 'Sheet1'}, {' ': 'Sheet1'},
                                     {'span': {'': 2}}, {'coordinate': object()}])
def test_source_reference_rejects_malformed_keys_and_non_json_coordinates(locator):
    reference = copy.deepcopy(fixture_draft()['evidence_links'][0])
    reference['locator'] = locator
    with pytest.raises(ValidationError):
        SourceReference.model_validate(reference)


@pytest.mark.parametrize('source_error', [404, 503])
def test_citation_missing_source_is_invalid_and_source_failure_stays_unavailable(source_error):
    from services.decisions import DomainError
    from services.providers import GraphSourceProvider
    reference = fixture_draft()['evidence_links'][0]
    class Provider(GraphSourceProvider):
        def get(self, kind, entity_id):
            if kind == 'source':
                raise DomainError(source_error, 'Synthetic source lookup failure')
            if kind == 'chunk':
                raise DomainError(404, 'No chunk')
            return {key: reference[key] for key in ('source_id', 'source_version', 'locator')}
    provider = Provider(None)
    if source_error == 404:
        assert provider.verify_reference(reference) is False
    else:
        with pytest.raises(DomainError) as error:
            provider.verify_reference(reference)
        assert error.value.status == 503
