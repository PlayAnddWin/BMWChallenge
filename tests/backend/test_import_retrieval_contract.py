"""Cross-role contracts stay injected; production source writes share the ledger transaction."""
import pytest
from fastapi.testclient import TestClient
from services.api import create_app
from services.repository import MemoryRepository


class TransactionalImporter:
    def prepare_import(self, scope):
        return {'scope': scope}

    def commit_import(self, prepared, *, tx, audit_sink):
        assert tx is None  # MemoryRepository test double has no graph transaction.
        snapshot = {'id': 'source-test', 'source_version': 'hash-test'}
        audit_sink('SOURCE_IMPORTED', snapshot['id'], None, snapshot, 'Synthetic source imported')
        return {'sources': [snapshot], 'result_ids': [snapshot['id']]}


def test_transaction_import_uses_one_sink_without_duplicate_source_events():
    repo = MemoryRepository()
    with TestClient(create_app(repo, import_service=TransactionalImporter())) as client:
        run = client.post('/api/import-runs', json={}).json()
        assert client.get('/api/analysis-runs/' + run['id']).json()['state'] == 'succeeded'
    events = repo.read()['events']
    assert len([event for event in events if event['entity_id'] == 'source-test']) == 1


def test_transaction_import_audit_failure_rolls_back_source_audit_and_completion():
    repo = MemoryRepository()
    def fail(action):
        if action == 'SOURCE_IMPORTED':
            raise RuntimeError('source audit failed')
    with TestClient(create_app(repo, import_service=TransactionalImporter(), audit_failure=fail)) as client:
        run = client.post('/api/import-runs', json={}).json()
        assert client.get('/api/analysis-runs/' + run['id']).json()['state'] == 'failed'
    assert repo.read()['events'] == []


@pytest.mark.parametrize('operation,body,method', [
    ('feedback-search', {'query': 'seat'}, 'search_feedback'),
    ('survey-facts', {'topic': 'seat'}, 'get_survey_facts'),
    ('sales-facts', {}, 'get_sales_facts'),
    ('document-search', {'query': 'seat'}, 'search_document_chunks'),
    ('cohort-counts', {}, 'get_comment_cohort_counts'),
])
def test_retrieval_routes_delegate_validated_scope(operation, body, method):
    calls = []
    class Provider:
        pass
    provider = Provider()
    setattr(provider, method, lambda **kwargs: calls.append(kwargs) or {'records': []})
    with TestClient(create_app(MemoryRepository(), source_provider=provider)) as client:
        assert client.post('/api/evidence/' + operation, json=body).json() == {'records': []}
        assert calls[0]['scope'] == {}


@pytest.mark.parametrize('body', [{'query': ''}, {'query': 'seat', 'limit': True}, {'query': 'seat', 'limit': 201}, {'query': 'seat', 'scope': {'regions': 'EU'}}, {'query': 'seat', 'unexpected': True}, {'query': 'seat', 'scope': []}, {'query': 'seat', 'scope': {'unknown': []}}])
def test_retrieval_rejects_invalid_request_before_provider(body):
    with TestClient(create_app(MemoryRepository())) as client:
        assert client.post('/api/evidence/feedback-search', json=body).status_code == 422


@pytest.mark.parametrize('result', [None, float('nan'), {'count': float('inf')}, ['invalid-record']])
def test_retrieval_rejects_malformed_or_nonfinite_provider_results(result):
    class Provider:
        def get_comment_cohort_counts(self, scope):
            return result
    with TestClient(create_app(MemoryRepository(), source_provider=Provider())) as client:
        assert client.post('/api/evidence/cohort-counts', json={}).status_code == 503


def test_empty_transaction_import_is_not_a_successful_import():
    class Importer(TransactionalImporter):
        def commit_import(self, prepared, *, tx, audit_sink):
            return {}
    with TestClient(create_app(MemoryRepository(), import_service=Importer())) as client:
        run = client.post('/api/import-runs', json={}).json()
        failed = client.get('/api/analysis-runs/' + run['id']).json()
        assert failed['state'] == 'failed'
        assert failed['result_ids'] == []
        assert 'explicit failures' in failed['failures'][0]


def test_missing_retrieval_capability_returns_truthful_unavailable_response():
    with TestClient(create_app(MemoryRepository(), source_provider=object())) as client:
        assert client.post('/api/evidence/cohort-counts', json={}).status_code == 503
        assert client.post('/api/evidence/nonexistent', json={}).status_code == 404


@pytest.mark.parametrize('action,entity_id,after,rationale', [
    ('REQUIREMENT_APPROVED', 'source-test', {'id': 'source-test'}, 'Imported'),
    ('SOURCE_IMPORTED', '', {'id': ''}, 'Imported'),
    ('SOURCE_IMPORTED', 'source-test', {'id': 'source-test'}, '  '),
    ('SOURCE_IMPORTED', 'source-test', {'id': 'different'}, 'Imported'),
    ('SOURCE_IMPORTED', 'source-test', {'id': 'source-test'}, 'Imported'),
    ('SIGNAL_CONSOLIDATED', 'signal-test', {'id': 'signal-test', 'revision': True}, 'Consolidated'),
])
def test_source_audit_sink_rejects_invalid_or_unrelated_provenance(action, entity_id, after, rationale):
    class Importer(TransactionalImporter):
        def commit_import(self, prepared, *, tx, audit_sink):
            audit_sink(action, entity_id, None, after, rationale)
            return {'result_ids': ['source-test']}
    repo = MemoryRepository()
    with TestClient(create_app(repo, import_service=Importer())) as client:
        run = client.post('/api/import-runs', json={}).json()
        assert client.get('/api/analysis-runs/' + run['id']).json()['state'] == 'failed'
    assert repo.read()['events'] == []


@pytest.mark.parametrize('emitted,returned,expected_source_events', [
    ({'id': 'source-test', 'source_version': 'hash-test'}, {'id': 'source-test', 'source_version': 'hash-test', 'parser_version': 'parser-v2'}, 2),
    ({'id': 'source-test', 'source_version': 'hash-test', 'parser_version': 'old'}, {'id': 'source-test', 'source_version': 'hash-test', 'parser_version': 'new'}, 2),
    ({'id': 'source-test', 'source_version': 'hash-test', 'run_id': 'adapter-provenance'}, {'id': 'source-test', 'source_version': 'hash-test'}, 1),
])
def test_import_retains_complete_result_snapshot_when_sink_payload_differs(emitted, returned, expected_source_events):
    class Importer(TransactionalImporter):
        def commit_import(self, prepared, *, tx, audit_sink):
            audit_sink('SOURCE_IMPORTED', 'source-test', None, emitted, 'Imported source')
            audit_sink('SOURCE_IMPORTED', 'source-test', None, emitted, 'Imported source')
            return {'sources': [returned], 'result_ids': ['source-test']}
    repo = MemoryRepository()
    with TestClient(create_app(repo, import_service=Importer())) as client:
        run = client.post('/api/import-runs', json={}).json()
        assert client.get('/api/analysis-runs/' + run['id']).json()['state'] == 'succeeded'
    events = [event for event in repo.read()['events'] if event['entity_id'] == 'source-test']
    assert len(events) == expected_source_events
    assert all(events[-1]['after'][key] == value for key, value in returned.items())


@pytest.mark.parametrize('locator', [{'page': True}, {'page': 1.0}])
def test_reference_provider_compares_locator_json_types_exactly(locator):
    from services.providers import GraphSourceProvider
    from services.decisions import DomainError
    class Provider(GraphSourceProvider):
        def get(self, kind, entity_id):
            if kind == 'chunk':
                raise DomainError(404, 'No chunk')
            if kind == 'source':
                return {'source_version': 'source-hash'}
            return {'source_id': 'source-test', 'source_version': 'source-hash', 'locator': {'page': 1}}
    reference = {'source_id': 'source-test', 'record_id': 'record-test', 'source_version': 'source-hash', 'locator': locator, 'validation_state': 'verified'}
    assert Provider(None).verify_reference(reference) is False
