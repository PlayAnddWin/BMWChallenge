"""Adversarial contract partitions for review and job admission."""
import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient

from conftest import FakeEvidence, fixture_draft
from test_review import FakeEngine, identity
from services.api import create_app
from services.repository import MemoryRepository


@pytest.mark.parametrize('route', ['review-threads', 'requirements/{rid}/challenges'])
@pytest.mark.parametrize('change,status', [({'id': 'wrong'}, 422), ({'expected_revision': 99}, 409), ({'actor_id': 'AI_SYSTEM'}, 422), ({'actor_role': 'AI_SYSTEM'}, 422), ({'rationale': '  '}, 422)])
def test_review_mutation_guards_no_writes(route, change, status):
    repo = MemoryRepository()
    app = create_app(repo, FakeEvidence(), FakeEngine())
    with TestClient(app) as client:
        record = app.state.service.create_requirement(fixture_draft(), 'test')
        before = repo.read()
        body = dict(identity(), entity_kind='requirement', entity_id=record['id'], question='Question')
        body.update(change)
        response = client.post('/api/' + route.format(rid=record['id']), json=body)
        assert response.status_code == status, response.text
        assert repo.read() == before


@pytest.mark.parametrize('patch', [[], 'bad', {'status': 'APPROVED'}, {'title': ''}])
def test_engine_patch_invalid_fails_without_turn_or_mutation(patch):
    repo = MemoryRepository()
    app = create_app(repo, FakeEvidence(), FakeEngine(patch))
    with TestClient(app) as client:
        record = app.state.service.create_requirement(fixture_draft(), 'test')
        before = copy.deepcopy(repo.read()['requirements'])
        result = client.post(f"/api/requirements/{record['id']}/challenges", json=dict(identity(), question='Question')).json()
        run = client.get('/api/analysis-runs/' + result['id']).json()
        assert run['state'] == 'failed'
        assert not repo.read()['turns']
        assert repo.read()['requirements'] == before


@pytest.mark.parametrize('result', [None, {}, {'answer': ' '}, {'answer': 'ok', 'citations': {}}, {'answer': 'ok', 'citations': [{'source_id': 'invented'}]}, {'answer': 'ok', 'tool_records': ['bad']}])
def test_malformed_review_output_fails(result):
    class Engine(FakeEngine):
        def review(self, *args):
            return result
    repo = MemoryRepository()
    app = create_app(repo, FakeEvidence(), Engine())
    with TestClient(app) as client:
        record = app.state.service.create_requirement(fixture_draft(), 'test')
        started = client.post(f"/api/requirements/{record['id']}/challenges", json=dict(identity(), question='Question')).json()
        assert client.get('/api/analysis-runs/' + started['id']).json()['state'] == 'failed'
        assert not repo.read()['turns']


@pytest.mark.parametrize('body', [{'regions': 'US'}, {'regions': [None]}, {'max_drafts': True}, {'max_drafts': 0}, {'max_drafts': -1}, {'refresh': 'yes'}])
@pytest.mark.parametrize('route', ['analysis-runs', 'import-runs'])
def test_malformed_run_scope_no_run_created(route, body):
    repo = MemoryRepository()
    with TestClient(create_app(repo)) as client:
        assert client.post('/api/' + route, json=body).status_code == 422
        assert not repo.read()['runs']


@pytest.mark.parametrize('body', [{'weights': []}, {'region_counts': []}, {'forecasts': [None]}, {'requirement_id': []}])
def test_malformed_preview_is_422(backend, body):
    client, repo, _, _ = backend
    before = repo.read()
    assert client.post('/api/scoring/preview', json=body).status_code == 422
    assert repo.read() == before


def test_import_adapter_progress_cache_audit_and_shared_admission():
    entered, release = Event(), Event()
    class Importer:
        def import_data(self, scope, progress_callback):
            progress_callback('reading', .5, 'cached')
            entered.set()
            assert release.wait(5)
            return {'result_ids': ['source-1'], 'failures': ['One source unavailable']}
    repo = MemoryRepository()
    with TestClient(create_app(repo, FakeEvidence(), FakeEngine(), import_service=Importer())) as client:
        with ThreadPoolExecutor(max_workers=1) as workers:
            pending = workers.submit(client.post, '/api/import-runs', json={})
            try:
                assert entered.wait(5)
                run = next(iter(repo.read()['runs'].values()))
                assert (run['stage'], run['progress'], run['mode']) == ('reading', .5, 'cached')
                assert client.post('/api/analysis-runs', json={}).status_code == 409
                assert client.post('/api/import-runs', json={}).status_code == 409
                assert len(repo.read()['runs']) == 1
            finally:
                release.set()
            started = pending.result(timeout=5).json()
        run = client.get('/api/analysis-runs/' + started['id']).json()
        assert run['state'] == 'partial' and run['progress'] == 1
        assert any(e['action'] == 'SOURCE_IMPORTED' for e in repo.read()['events'])
        assert client.post('/api/analysis-runs', json={}).status_code == 200


def test_configurable_prefix_and_legacy_alias():
    with TestClient(create_app(MemoryRepository(), api_prefix='/v2')) as client:
        assert client.get('/v2/health').status_code == 200
        assert client.get('/health').status_code == 200
        assert client.get('/api/health').status_code == 404


def test_signal_review_uses_same_service_and_revision():
    class Sources:
        def get(self, kind, sid):
            assert kind == 'signal'
            return {'id': sid, 'revision': 2, 'evidence_links': []}
    class Engine(FakeEngine):
        def review(self, current, question, turns):
            return {'answer': 'Signal reviewed', 'citations': []}
    repo = MemoryRepository()
    with TestClient(create_app(repo, FakeEvidence(), Engine(), Sources())) as client:
        assert client.post('/api/review-threads', json=dict(identity(), entity_kind='signal', entity_id='s')).status_code == 409
        thread = client.post('/api/review-threads', json=dict(identity(2), entity_kind='signal', entity_id='s')).json()
        run = client.post(f"/api/review-threads/{thread['id']}/turns", json=dict(identity(2), question='Question')).json()
        assert client.get('/api/analysis-runs/' + run['id']).json()['state'] == 'succeeded'
        assert client.get('/api/review-threads/' + thread['id']).json()['current_revision'] == 2


@pytest.mark.parametrize('question', [None, '', '  ', 3, []])
def test_invalid_convenience_question_does_not_create_orphan_thread(backend, question):
    client, repo, _, record = backend
    before = repo.read()
    response = client.post(f"/api/requirements/{record['id']}/challenges", json=dict(identity(), question=question))
    assert response.status_code == 422
    assert repo.read() == before


@pytest.mark.parametrize('filename', [None, 3, [], '../outside.pdf', 'file.txt', 'missing.pdf'])
def test_source_file_malformed_or_non_pdf_is_404(tmp_path, monkeypatch, filename):
    import config
    class Sources:
        def get(self, kind, sid):
            return {'filename': filename}
    monkeypatch.setattr(config, 'RAW_DATA_DIR', tmp_path)
    with TestClient(create_app(MemoryRepository(), source_provider=Sources())) as client:
        assert client.get('/api/sources/s/file').status_code == 404


def test_allowlisted_pdf_download_and_symlink_escape(tmp_path, monkeypatch):
    import config
    class Sources:
        def get(self, kind, sid):
            return {'filename': sid + '.pdf'}
    root = tmp_path / 'raw'
    root.mkdir()
    (root / 'safe.pdf').write_bytes(b'%PDF-1.4')
    outside = tmp_path / 'outside.pdf'
    outside.write_bytes(b'%PDF-1.4')
    (root / 'escape.pdf').symlink_to(outside)
    monkeypatch.setattr(config, 'RAW_DATA_DIR', root)
    with TestClient(create_app(MemoryRepository(), source_provider=Sources())) as client:
        assert client.get('/api/sources/safe/file').content == b'%PDF-1.4'
        assert client.get('/api/sources/escape/file').status_code == 404


def test_discard_checks_body_id_and_stale_revision(backend):
    client, repo, service, record = backend
    thread = client.post('/api/review-threads', json=dict(identity(), entity_kind='requirement', entity_id=record['id'])).json()
    def create_turn(data):
        turn = {'id': 'turn', 'thread_id': thread['id'], 'entity_id': record['id'],
                'expected_revision': 1, 'patch': {'title': 'Proposed title'}, 'state': 'pending'}
        data['turns']['turn'] = turn
        service.event(data, 'CHALLENGE_COMPLETED', record['id'], None, turn,
                      ('synthetic-engine', 'AI_SYSTEM'), 'Synthetic proposal')
    repo.write(create_turn)
    before = repo.read()
    endpoint = f"/api/review-threads/{thread['id']}/turns/turn/discard"
    assert client.post(endpoint, json=dict(identity(), id='wrong')).status_code == 422
    assert client.post(endpoint, json=identity(99)).status_code == 409
    assert repo.read() == before


def test_legacy_reporting_routes_are_preserved():
    class Sources:
        def list_conflicts(self, model):
            return [{'model': model or 'G60', 'delta': 1}]
    repo = MemoryRepository()
    app = create_app(repo, FakeEvidence(), source_provider=Sources())
    with TestClient(app) as client:
        record = app.state.service.create_requirement(fixture_draft(), 'test')
        assert client.get('/conflicts?model=G68').json() == [{'model': 'G68', 'delta': 1}]
        assert client.get('/data-gaps').json() == []
        summary = client.get('/dashboard/summary').json()
        assert summary['requirement_counts'][0]['cnt'] == 1
        assert summary['top_conflicts'] == [{'model': 'G60', 'delta': 1}]


def test_history_export_download_contract(backend):
    client, repo, _, record = backend
    endpoint = f"/api/requirements/{record['id']}/history/export"
    response = client.get(endpoint)
    assert response.status_code == 200
    assert 'attachment;' in response.headers['content-disposition']
    assert response.json() == client.get(f"/api/requirements/{record['id']}/history").json()
    assert client.get(endpoint + '?format=csv').status_code == 422


@pytest.mark.parametrize('mode', ['infinite', 'error_after_valid'])
def test_analysis_generator_bounds_and_preserves_success(mode):
    consumed = []
    class Engine(FakeEngine):
        def analyze(self, body):
            index = 0
            while True:
                if mode == 'error_after_valid' and index == 1:
                    raise RuntimeError('generator failed after a valid draft')
                consumed.append(index)
                draft = fixture_draft()
                draft['derivation_key'] += str(index)
                yield draft
                index += 1
    repo = MemoryRepository()
    with TestClient(create_app(repo, FakeEvidence(), Engine())) as client:
        started = client.post('/api/analysis-runs', json={'max_drafts': 1}).json()
        run = client.get('/api/analysis-runs/' + started['id']).json()
        assert run['state'] == 'partial'
        assert len(run['result_ids']) == 1 and len(run['failures']) == 1
        assert len(repo.read()['requirements']) == 1
        assert len(consumed) <= 2


def test_configured_draft_upper_bound_rejects_without_run():
    repo = MemoryRepository()
    with TestClient(create_app(repo, max_analysis_drafts=2)) as client:
        assert client.post('/api/analysis-runs', json={'max_drafts': 3}).status_code == 422
        assert not repo.read()['runs']


@pytest.mark.parametrize('verified', [True, False, None])
def test_review_new_citation_requires_authoritative_verification(verified):
    citation = copy.deepcopy(fixture_draft()['evidence_links'][0])
    citation['record_id'] = 'retrieved-record'
    class Sources:
        def verify_reference(self, reference):
            assert reference == citation
            return verified
    class Engine(FakeEngine):
        def review(self, *args):
            return {'answer': 'Review expanded evidence', 'citations': [citation]}
    repo = MemoryRepository()
    app = create_app(repo, FakeEvidence(), Engine(), Sources())
    with TestClient(app) as client:
        record = app.state.service.create_requirement(fixture_draft(), 'test')
        started = client.post(f"/api/requirements/{record['id']}/challenges", json=dict(identity(), question='Question')).json()
        run = client.get('/api/analysis-runs/' + started['id']).json()
        assert run['state'] == ('succeeded' if verified is True else 'failed')
        assert bool(repo.read()['turns']) is (verified is True)


@pytest.mark.parametrize('stage,progress,mode', [('', .5, None), ('read', float('nan'), None), ('read', True, None), ('read', -1, None), ('read', 1.1, None), ('read', .5, 'fictional')])
def test_invalid_adapter_progress_is_failed_and_releases_admission(stage, progress, mode):
    class Engine(FakeEngine):
        def analyze(self, body, progress_callback):
            progress_callback(stage, progress, mode)
            return []
    repo = MemoryRepository()
    with TestClient(create_app(repo, FakeEvidence(), Engine())) as client:
        for _ in range(2):
            started = client.post('/api/analysis-runs', json={}).json()
            assert client.get('/api/analysis-runs/' + started['id']).json()['state'] == 'failed'
        assert not repo.read()['requirements']


def test_import_snapshots_are_preserved_and_linked_in_history():
    class Importer:
        def import_data(self, body):
            return {'result_ids': ['source-1'], 'sources': [{'id': 'source-1', 'source_version': 'hash-1', 'parser_version': 'v2', 'row_count': 3}], 'signals': [{'id': 'signal-us-temp', 'revision': 1, 'record_ids': ['feedback-1']}]}
    repo = MemoryRepository()
    app = create_app(repo, FakeEvidence(), import_service=Importer())
    with TestClient(app) as client:
        record = app.state.service.create_requirement(fixture_draft(), 'test')
        started = client.post('/api/import-runs', json={}).json()
        run = client.get('/api/analysis-runs/' + started['id']).json()
        assert run['sources'][0]['parser_version'] == 'v2'
        assert run['signals'][0]['revision'] == 1
        history = client.get(f"/api/requirements/{record['id']}/history").json()
        assert any(e['action'] == 'SOURCE_IMPORTED' and e['entity_id'] == 'source-1' for e in history['events'])
        assert any(e['action'] == 'SIGNAL_CONSOLIDATED' for e in history['events'])


def test_review_result_records_stale_base_during_model_call():
    entered, release = Event(), Event()
    class Engine(FakeEngine):
        def review(self, *args):
            entered.set()
            assert release.wait(5)
            return {'answer': 'Old base', 'patch': {'title': 'Old proposal'}}
    repo = MemoryRepository()
    app = create_app(repo, FakeEvidence(), Engine())
    with TestClient(app) as client:
        record = app.state.service.create_requirement(fixture_draft(), 'test')
        with ThreadPoolExecutor(max_workers=1) as workers:
            pending = workers.submit(client.post, f"/api/requirements/{record['id']}/challenges", json=dict(identity(), question='Question'))
            try:
                assert entered.wait(5)
                assert client.patch(f"/api/requirements/{record['id']}", json=dict(identity(), patch={'title': 'New human title'})).status_code == 200
            finally:
                release.set()
            run_id = pending.result(timeout=5).json()['id']
        run = client.get('/api/analysis-runs/' + run_id).json()
        assert run['state'] == 'succeeded'
        turn = repo.read()['turns'][run['result_ids'][0]]
        assert turn['stale'] is True
        assert client.post(f"/api/requirements/{record['id']}/challenge-applications", json=dict(identity(), turn_id=turn['id'], patch=turn['patch'])).status_code == 409


def test_same_thread_review_admission_preserves_order():
    entered, release = Event(), Event()
    class Engine(FakeEngine):
        def review(self, *args):
            entered.set()
            assert release.wait(5)
            return {'answer': 'First answer'}
    repo = MemoryRepository()
    app = create_app(repo, FakeEvidence(), Engine())
    with TestClient(app) as client:
        record = app.state.service.create_requirement(fixture_draft(), 'test')
        thread = client.post('/api/review-threads', json=dict(identity(), entity_kind='requirement', entity_id=record['id'])).json()
        endpoint = f"/api/review-threads/{thread['id']}/turns"
        with ThreadPoolExecutor(max_workers=1) as workers:
            pending = workers.submit(client.post, endpoint, json=dict(identity(), question='First'))
            try:
                assert entered.wait(5)
                assert client.post(endpoint, json=dict(identity(), question='Second')).status_code == 409
                assert len(repo.read()['runs']) == 1
            finally:
                release.set()
            assert pending.result(timeout=5).status_code == 200
        assert client.post(endpoint, json=dict(identity(), question='Second')).status_code == 200
        assert len(repo.read()['turns']) == 2


@pytest.mark.parametrize('mismatch', [None, 'record_source', 'record_version', 'source_version', 'locator', 'unverified', 'missing'])
def test_graph_reference_verification_requires_exact_provenance(mismatch):
    from services.providers import GraphSourceProvider
    from services.decisions import DomainError
    citation = copy.deepcopy(fixture_draft()['evidence_links'][0])
    record = {'source_id': citation['source_id'], 'source_version': citation['source_version'], 'locator': citation['locator']}
    source = {'source_version': citation['source_version']}
    if mismatch == 'record_source':
        record['source_id'] = 'wrong'
    elif mismatch == 'record_version':
        record['source_version'] = 'wrong'
    elif mismatch == 'source_version':
        source['source_version'] = 'wrong'
    elif mismatch == 'locator':
        record['locator'] = {'row': 99}
    elif mismatch == 'unverified':
        citation['validation_state'] = 'unverified'
    class Provider(GraphSourceProvider):
        def get(self, kind, sid):
            if mismatch == 'missing' or kind == 'chunk':
                raise DomainError(404, 'Not found')
            return record if kind == 'record' else source
    assert Provider(None).verify_reference(citation) is (mismatch is None)


@pytest.mark.parametrize('result', [None, {'result_ids': 'bad'}, {'failures': 'bad'}, {'sources': [None]}, {'signals': [{'id': 3}]}])
def test_invalid_import_result_fails_without_completion_audit(result):
    class Importer:
        def import_data(self, body):
            return result
    repo = MemoryRepository()
    with TestClient(create_app(repo, import_service=Importer())) as client:
        for _ in range(2):
            started = client.post('/api/import-runs', json={}).json()
            assert client.get('/api/analysis-runs/' + started['id']).json()['state'] == 'failed'
        assert not repo.read()['events']


from test_neo4j_integration import graph_backend


def test_graph_source_provider_real_json_locator_and_hash(graph_backend):
    import json
    import uuid
    from services.providers import GraphSourceProvider
    repo, _, _ = graph_backend
    sid, rid = 'source-' + uuid.uuid4().hex, 'record-' + uuid.uuid4().hex
    locator = {'sheet': 'Feedback', 'row': 2}
    with repo.session() as session:
        session.run('CREATE (s:SourceFile {id:$sid, sha256:$hash, backend_key:$source_key}) '
                    'CREATE (r:SourceRecord {id:$rid, source_id:$sid, source_version:$hash, locator_json:$locator, raw_payload_json:$payload, backend_key:$record_key})',
                    sid=sid, rid=rid, hash='test-hash', locator=json.dumps(locator), payload=json.dumps({'original_cell': 'Synthetic feedback'}), source_key=repo.namespace + ':source', record_key=repo.namespace + ':record').consume()
    with repo.session() as session:
        session.run('CREATE (s:Signal {id:$sid, revision:1, backend_key:$signal_key, evidence_links_json:"[]"}) '
                    'CREATE (c:ConflictNode {model:$sid, delta:2, backend_key:$conflict_key})',
                    sid=sid, signal_key=repo.namespace + ':signal', conflict_key=repo.namespace + ':conflict').consume()
    provider = GraphSourceProvider(repo.driver, repo.database)
    assert any(row['id'] == sid for row in provider.list_sources())
    assert any(row['id'] == sid and row['evidence_links'] == [] for row in provider.list_signals())
    assert provider.list_conflicts(sid)[0]['delta'] == 2
    with pytest.raises(Exception) as exc:
        provider.get('chunk', 'not-imported-' + rid)
    assert getattr(exc.value, 'status', None) == 404
    record = provider.get('record', rid)
    assert record['locator'] == locator
    assert record['raw_payload'] == {'original_cell': 'Synthetic feedback'}
    citation = {'source_id': sid, 'record_id': rid, 'locator': locator, 'claim_key': 'problem', 'relation': 'supports', 'validation_state': 'verified', 'source_version': 'test-hash'}
    assert provider.verify_reference(citation) is True
    assert provider.verify_reference(dict(citation, source_version='wrong-version')) is False


def test_preview_rankings_keep_legacy_and_v1_policies_separate(backend):
    client, repo, service, record = backend
    service.import_legacy([{'id': 'legacy-preview', 'impact': 7, 'market_reach_norm': 8, 'effort': 4}])
    before = repo.read()
    v1 = client.post('/api/scoring/preview', json={'requirement_id': record['id']})
    legacy = client.post('/api/scoring/preview', json={'requirement_id': 'legacy-preview'})
    assert v1.status_code == 200, v1.text
    assert legacy.status_code == 200, legacy.text
    assert set(v1.json()['old_ranks']) == {record['id']}
    assert set(legacy.json()['old_ranks']) == {'legacy-preview'}
    assert repo.read() == before


@pytest.mark.parametrize('properties', [{'locator_json': 'not-json'}, {'locator_json': []}, {'locator': 'not-json'}, {'locator_json': '[]'}, {'raw_payload_json': 'null'}, {'evidence_links_json': '{}'}, {'evidence_links_json': '[3]'}])
def test_graph_metadata_malformed_json_or_shape_fails_truthfully(properties):
    from services.providers import GraphSourceProvider
    from services.decisions import DomainError
    with pytest.raises(DomainError) as exc:
        GraphSourceProvider._normalize(properties)
    assert exc.value.status == 503


@pytest.mark.parametrize('endpoint', ['/api/requirements/missing', '/api/freezes/missing', '/api/freezes/missing/export', '/api/freezes/missing/audit', '/api/review-threads/missing', '/api/analysis-runs/missing'])
def test_unknown_entities_are_404(endpoint):
    with TestClient(create_app(MemoryRepository())) as client:
        assert client.get(endpoint).status_code == 404


@pytest.mark.parametrize('prefix', ['api', '/api/', 3, None])
def test_invalid_configured_prefix_fails_at_composition(prefix):
    with pytest.raises(ValueError, match='api_prefix'):
        create_app(MemoryRepository(), api_prefix=prefix)


def test_empty_prefix_has_single_root_contract():
    with TestClient(create_app(MemoryRepository(), api_prefix='')) as client:
        assert client.get('/health').status_code == 200
        assert '/health' in client.get('/openapi.json').json()['paths']
        assert client.get('/api/health').status_code == 404


@pytest.mark.parametrize('endpoint', ['/api/sources', '/api/sources/s', '/api/source-records/r', '/api/document-chunks/c', '/api/signals', '/api/conflicts'])
def test_missing_source_provider_is_503(endpoint):
    with TestClient(create_app(MemoryRepository())) as client:
        assert client.get(endpoint).status_code == 503


def test_source_and_signal_routes_resolve_exact_provider_entities():
    class Sources:
        def list_sources(self):
            return [{'id': 'source', 'sha256': 'hash', 'row_count': 3}]
        def list_signals(self):
            return [{'id': 'signal', 'revision': 2}]
        def get(self, kind, sid):
            return {'id': sid, 'kind': kind}
    with TestClient(create_app(MemoryRepository(), source_provider=Sources())) as client:
        assert client.get('/api/sources').json()[0]['row_count'] == 3
        assert client.get('/api/signals').json()[0]['revision'] == 2
        for endpoint, kind in [('sources', 'source'), ('source-records', 'record'), ('document-chunks', 'chunk')]:
            assert client.get('/api/' + endpoint + '/id').json() == {'id': 'id', 'kind': kind}


@pytest.mark.parametrize('body,status', [({'entity_kind': 'unknown', 'entity_id': 's'}, 422), ({'entity_kind': 'signal', 'entity_id': None}, 422), ({'entity_kind': 'requirement', 'entity_id': 'missing'}, 404), ({'entity_kind': 'signal', 'entity_id': 's'}, 503)])
def test_thread_entity_binding_failures_no_write(body, status):
    class Sources:
        def get(self, kind, sid):
            return {'id': sid, 'revision': 0}
    repo = MemoryRepository()
    with TestClient(create_app(repo, source_provider=Sources())) as client:
        assert client.post('/api/review-threads', json=dict(identity(), **body)).status_code == status
        assert not repo.read()['threads']


def test_partial_source_adapters_are_explicitly_unavailable():
    with TestClient(create_app(MemoryRepository(), source_provider=object())) as client:
        assert client.get('/api/signals').status_code == 503
        assert client.get('/api/conflicts').status_code == 503


@pytest.mark.parametrize('endpoint', ['/api/sources', '/api/sources/s', '/api/source-records/r', '/api/document-chunks/c', '/api/signals', '/api/conflicts'])
def test_faulting_source_dependencies_return_503(endpoint):
    class Sources:
        def get(self, *args):
            raise RuntimeError('Graph disconnected')
        list_sources = list_signals = list_conflicts = get
    with TestClient(create_app(MemoryRepository(), source_provider=Sources())) as client:
        assert client.get(endpoint).status_code == 503


@pytest.mark.parametrize('endpoint', ['/api/sources', '/api/sources/s', '/api/source-records/r', '/api/document-chunks/c'])
def test_partial_source_provider_missing_capabilities_return_503(endpoint):
    with TestClient(create_app(MemoryRepository(), source_provider=object())) as client:
        assert client.get(endpoint).status_code == 503


def test_real_graph_lifespan_http_and_injected_driver_ownership(graph_backend, monkeypatch):
    import uuid
    from services.providers import GraphSourceProvider
    repo, _, _ = graph_backend
    import config
    monkeypatch.setattr(config, 'NEO4J_DATABASE', repo.database)
    namespace = 'backend-http-' + uuid.uuid4().hex
    class CallerDriver:
        closed = False
        def verify_connectivity(self):
            repo.driver.verify_connectivity()
        def session(self, **kwargs):
            return repo.driver.session(**kwargs)
        def close(self):
            self.closed = True
    driver = CallerDriver()
    try:
        with TestClient(create_app(driver=driver, namespace=namespace)) as client:
            assert isinstance(client.app.state.source_provider, GraphSourceProvider)
            assert client.get('/api/health').status_code == 200
            assert client.get('/api/requirements').json()['total'] == 0
            assert client.get('/api/requirements/export').status_code == 200
            assert client.get('/api/sources').status_code == 200
        assert driver.closed is False
    finally:
        with repo.session() as session:
            session.run('MATCH (n) WHERE n.backend_key=$namespace OR n.backend_key STARTS WITH $prefix DETACH DELETE n', namespace=namespace, prefix=namespace + ':').consume()


def test_real_graph_lifespan_closes_owned_driver(graph_backend, monkeypatch):
    import uuid
    from neo4j import GraphDatabase
    repo, _, _ = graph_backend
    namespace = 'backend-owned-http-' + uuid.uuid4().hex
    real_factory = GraphDatabase.driver
    import config
    import os
    monkeypatch.setattr(config, "NEO4J_URI", os.environ["BACKEND_TEST_NEO4J_URI"])
    monkeypatch.setattr(config, "NEO4J_USER", os.environ.get("BACKEND_TEST_NEO4J_USER", "neo4j"))
    monkeypatch.setattr(config, "NEO4J_PASSWORD", os.environ.get("BACKEND_TEST_NEO4J_PASSWORD", "bmwpassword"))
    monkeypatch.setattr(config, "NEO4J_DATABASE", repo.database)
    owned = real_factory(config.NEO4J_URI, auth=(config.NEO4J_USER, config.NEO4J_PASSWORD))
    original_close = owned.close
    closed = []
    def close():
        closed.append(True)
        original_close()
    monkeypatch.setattr(owned, 'close', close)
    monkeypatch.setattr(GraphDatabase, 'driver', lambda *args, **kwargs: owned)
    try:
        with TestClient(create_app(namespace=namespace)) as client:
            assert client.get('/api/health').status_code == 200
        assert closed == [True]
    finally:
        original_close()
        with repo.session() as session:
            session.run('MATCH (n) WHERE n.backend_key=$namespace OR n.backend_key STARTS WITH $prefix DETACH DELETE n', namespace=namespace, prefix=namespace + ':').consume()


def test_graph_provider_unsupported_kind_is_contract_error():
    from services.providers import GraphSourceProvider
    from services.decisions import DomainError
    with pytest.raises(DomainError) as exc:
        GraphSourceProvider(None).get('invented-label', 'id')
    assert exc.value.status == 422


def test_reference_dependency_failure_remains_503():
    from services.providers import GraphSourceProvider
    from services.decisions import DomainError
    class Provider(GraphSourceProvider):
        def get(self, *args):
            raise DomainError(503, 'Graph unavailable')
    with pytest.raises(DomainError) as exc:
        Provider(None).verify_reference(fixture_draft()['evidence_links'][0])
    assert exc.value.status == 503


def test_unavailable_evidence_cannot_persist_generated_drafts():
    repo = MemoryRepository()
    with TestClient(create_app(repo, engine=FakeEngine())) as client:
        started = client.post('/api/analysis-runs', json={}).json()
        run = client.get('/api/analysis-runs/' + started['id']).json()
        assert run['state'] == 'failed'
        assert 'not integrated' in run['failures'][0]
        assert not repo.read()['requirements']


def test_review_provider_progress_and_cache_status_are_visible():
    entered, release = Event(), Event()
    class Engine(FakeEngine):
        def review(self, current, question, turns, progress_callback):
            progress_callback('retrieving', .4, 'cached')
            entered.set()
            assert release.wait(5)
            return {'answer': 'Cached review'}
    repo = MemoryRepository()
    app = create_app(repo, FakeEvidence(), Engine())
    with TestClient(app) as client:
        record = app.state.service.create_requirement(fixture_draft(), 'test')
        with ThreadPoolExecutor(max_workers=1) as workers:
            pending = workers.submit(client.post, f"/api/requirements/{record['id']}/challenges", json=dict(identity(), question='Question'))
            try:
                assert entered.wait(5)
                run = next(iter(repo.read()['runs'].values()))
                assert (run['state'], run['stage'], run['progress'], run['mode']) == ('running', 'retrieving', .4, 'cached')
            finally:
                release.set()
            assert pending.result(timeout=5).status_code == 200
        assert next(iter(repo.read()['runs'].values()))['mode'] == 'cached'


def test_adapter_factory_receives_repo_driver_and_wires_http_generation():
    repo = MemoryRepository()
    driver = object()
    received = []
    def factory(**kwargs):
        received.append(kwargs)
        return {'evidence_provider': FakeEvidence(), 'engine': FakeEngine()}
    with TestClient(create_app(repo, driver=driver, adapter_factory=factory)) as client:
        started = client.post('/api/analysis-runs', json={}).json()
        run = client.get('/api/analysis-runs/' + started['id']).json()
        assert run['state'] == 'succeeded' and len(run['result_ids']) == 1
        assert client.get('/api/health').json()['engine_available'] is True
    assert received == [{'driver': driver, 'repository': repo}]


def test_env_adapter_factory_is_loaded_during_http_lifespan(monkeypatch):
    import sys
    import types
    module = types.ModuleType('synthetic_backend_factory')
    module.build = lambda **kwargs: {'evidence_provider': FakeEvidence(), 'engine': FakeEngine()}
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setenv('BACKEND_ADAPTER_FACTORY', module.__name__ + ':build')
    with TestClient(create_app(MemoryRepository())) as client:
        started = client.post('/api/analysis-runs', json={}).json()
        assert client.get('/api/analysis-runs/' + started['id']).json()['state'] == 'succeeded'


@pytest.mark.parametrize('result', [None, [], {'unknown': object()}, {'engine': object()}, {'evidence_provider': None}, {'source_provider': object()}, {'import_service': object()}])
def test_invalid_adapter_factory_fails_startup_closes_owned_driver(monkeypatch, result):
    from neo4j import GraphDatabase
    from services.repository import Neo4jRepository
    class Driver:
        closed = False
        def verify_connectivity(self):
            pass
        def close(self):
            self.closed = True
    driver = Driver()
    monkeypatch.setattr(GraphDatabase, 'driver', lambda *args, **kwargs: driver)
    monkeypatch.setattr(Neo4jRepository, 'initialize', lambda self: None)
    with pytest.raises(ValueError, match='factory'):
        with TestClient(create_app(adapter_factory=lambda **kwargs: result)):
            pass
    assert driver.closed is True


@pytest.mark.parametrize('factory', ['bad', 'missing:', ':build', 'module:build:extra'])
def test_bad_env_factory_never_silently_falls_back(monkeypatch, factory):
    monkeypatch.setenv('BACKEND_ADAPTER_FACTORY', factory)
    with pytest.raises(ValueError, match='module:function'):
        with TestClient(create_app(MemoryRepository())):
            pass


def test_adapter_factory_cannot_override_explicit_component():
    with pytest.raises(ValueError, match='conflicts'):
        with TestClient(create_app(MemoryRepository(), FakeEvidence(), adapter_factory=lambda **kwargs: {'evidence_provider': FakeEvidence()})):
            pass


def test_factory_reentered_lifespan_can_create_fresh_adapters():
    calls = []
    def factory(**kwargs):
        calls.append(kwargs)
        return {'evidence_provider': FakeEvidence(), 'engine': FakeEngine()}
    app = create_app(MemoryRepository(), adapter_factory=factory)
    for _ in range(2):
        with TestClient(app) as client:
            assert client.get('/api/health').json()['engine_available'] is True
    assert len(calls) == 2


def test_async_adapter_factory_rejected_without_unawaited_coroutine():
    async def factory(**kwargs):
        return {}
    with pytest.raises(ValueError, match='synchronous'):
        with TestClient(create_app(MemoryRepository(), adapter_factory=factory)):
            pass


def test_noncallable_adapter_factory_fails_startup():
    with pytest.raises(ValueError, match='callable'):
        with TestClient(create_app(MemoryRepository(), adapter_factory=3)):
            pass


@pytest.mark.parametrize('result', [None, [], 'bad', 3, {'value': float('nan')}, {'value': float('inf')}, {'value': object()}])
@pytest.mark.parametrize('endpoint', ['/api/sources/s', '/api/sources/s/file', '/api/source-records/r', '/api/document-chunks/c'])
def test_malformed_source_get_results_return_503(endpoint, result):
    class Sources:
        def get(self, *args):
            return result
    with TestClient(create_app(MemoryRepository(), source_provider=Sources())) as client:
        assert client.get(endpoint).status_code == 503


@pytest.mark.parametrize('result', [None, {}, 'bad', 3, [None], [{'value': float('nan')}], [{'value': object()}]])
@pytest.mark.parametrize('endpoint', ['/api/sources', '/api/signals', '/api/conflicts', '/api/dashboard/summary'])
def test_malformed_source_list_results_return_503(endpoint, result):
    class Sources:
        def list_sources(self):
            return result
        list_signals = list_conflicts = lambda self, *args: result
    with TestClient(create_app(MemoryRepository(), source_provider=Sources())) as client:
        assert client.get(endpoint).status_code == 503


def test_source_provider_not_found_error_is_preserved():
    from services.decisions import DomainError
    class Sources:
        def get(self, *args):
            raise DomainError(404, 'Exact imported entity missing')
    with TestClient(create_app(MemoryRepository(), source_provider=Sources())) as client:
        response = client.get('/api/sources/missing/file')
        assert response.status_code == 404
        assert response.json()['detail'] == 'Exact imported entity missing'


def test_evidence_endpoint_returns_full_authoritative_basis_without_writes(backend):
    client, repo, _, record = backend
    before = repo.read()
    response = client.get(f"/api/requirements/{record['id']}/evidence")
    assert response.status_code == 200
    assert response.json() == {'links': record['evidence_links'], 'evidence': record['evidence'], 'assumptions': record['assumptions'], 'uncertainties': record['uncertainties']}
    assert repo.read() == before


@pytest.mark.parametrize('status', [None, '', 'DRAFT', 'UNKNOWN', [], {}])
def test_legacy_invalid_transition_partition_is_422_without_writes(backend, status):
    client, repo, _, record = backend
    before = repo.read()
    response = client.post(f"/api/requirements/{record['id']}/transition", json={'to_status': status})
    assert response.status_code == 422
    assert repo.read() == before


def test_signal_patch_proposal_is_rejected_without_turn_or_signal_mutation():
    class Sources:
        def get(self, kind, sid):
            return {'id': sid, 'revision': 1, 'evidence_links': []}
    class Engine(FakeEngine):
        def review(self, *args):
            return {'answer': 'Patch signal', 'patch': {'title': 'Changed'}}
    repo = MemoryRepository()
    with TestClient(create_app(repo, FakeEvidence(), Engine(), Sources())) as client:
        thread = client.post('/api/review-threads', json=dict(identity(), entity_kind='signal', entity_id='signal')).json()
        started = client.post(f"/api/review-threads/{thread['id']}/turns", json=dict(identity(), question='Question')).json()
        run = client.get('/api/analysis-runs/' + started['id']).json()
        assert run['state'] == 'failed'
        assert 'read-only' in run['failures'][0]
        assert not repo.read()['turns'] and not repo.read()['requirements']


@pytest.mark.parametrize('route,integrated', [('analysis-runs', True), ('import-runs', True), ('analysis-runs', False), ('import-runs', False)])
def test_run_admission_write_fault_rolls_back_and_releases_lock(route, integrated):
    class FaultingRepository(MemoryRepository):
        fail_next_write = False
        def write(self, callback):
            if self.fail_next_write:
                self.fail_next_write = False
                def fault(state):
                    callback(state)
                    raise RuntimeError('Injected persistence failure after mutation')
                return super().write(fault)
            return super().write(callback)
    class Importer:
        def import_data(self, body):
            return {'result_ids': ['source-1']}
    repo = FaultingRepository()
    app = create_app(repo, FakeEvidence(), FakeEngine() if integrated else None, import_service=Importer() if integrated else None)
    with TestClient(app) as client:
        before = repo.read()
        repo.fail_next_write = True
        response = client.post('/api/' + route, json={})
        assert response.status_code == 503
        assert repo.read() == before
        retry = client.post('/api/' + route, json={})
        assert retry.status_code == 200, retry.text
        run = client.get('/api/analysis-runs/' + retry.json()['id']).json()
        assert run['state'] == ('succeeded' if integrated else 'failed')
        assert len(repo.read()['runs']) == 1


@pytest.mark.parametrize('limit', [0, -1, True, None])
def test_invalid_analysis_cap_fails_composition(limit):
    with pytest.raises(ValueError, match='max_analysis_drafts'):
        create_app(MemoryRepository(), max_analysis_drafts=limit)


def test_factory_source_provider_is_exposed_by_http_routes():
    class Sources:
        def list_sources(self):
            return [{'id': 'factory-source', 'sha256': 'version-1'}]
        def get(self, kind, sid):
            return {'id': sid, 'kind': kind}
    sources = Sources()
    with TestClient(create_app(MemoryRepository(), adapter_factory=lambda **kwargs: {'source_provider': sources})) as client:
        assert client.app.state.source_provider is sources
        assert client.get('/api/sources').json() == [{'id': 'factory-source', 'sha256': 'version-1'}]
        assert client.get('/api/source-records/record').json() == {'id': 'record', 'kind': 'record'}


@pytest.mark.parametrize('question', [None, '', ' ', [], 3])
def test_direct_thread_question_validation_has_no_job_or_audit_writes(backend, question):
    client, repo, _, record = backend
    thread = client.post('/api/review-threads', json=dict(identity(), entity_kind='requirement', entity_id=record['id'])).json()
    before = repo.read()
    response = client.post(f"/api/review-threads/{thread['id']}/turns", json=dict(identity(), question=question))
    assert response.status_code == 422
    assert repo.read() == before


@pytest.mark.parametrize('case,expected', [('missing', 404), ('wrong_thread', 404), ('read_only', 409)])
def test_discard_unknown_unrelated_and_nonpending_turns_are_not_mutations(case, expected):
    repo = MemoryRepository()
    app = create_app(repo, FakeEvidence(), FakeEngine())
    with TestClient(app) as client:
        record = app.state.service.create_requirement(fixture_draft(), 'test')
        first = client.post('/api/review-threads', json=dict(identity(), entity_kind='requirement', entity_id=record['id'])).json()
        second = client.post('/api/review-threads', json=dict(identity(), entity_kind='requirement', entity_id=record['id'])).json()
        run = client.post(f"/api/review-threads/{first['id']}/turns", json=dict(identity(), question='Read only')).json()
        turn_id = client.get('/api/analysis-runs/' + run['id']).json()['result_ids'][0]
        thread_id = second['id'] if case == 'wrong_thread' else first['id']
        if case == 'missing':
            turn_id = 'unknown-turn'
        before = repo.read()
        response = client.post(f'/api/review-threads/{thread_id}/turns/{turn_id}/discard', json=identity())
        assert response.status_code == expected
        assert repo.read() == before


def test_valid_standalone_score_preview_has_no_baseline_writes():
    context = FakeEvidence().resolve(fixture_draft())['score_context']
    repo = MemoryRepository()
    with TestClient(create_app(repo)) as client:
        before = repo.read()
        response = client.post('/api/scoring/preview', json=context)
        assert response.status_code == 200
        assert response.json()['mode'] == 'preview'
        assert response.json()['score']['calculated_score'] == pytest.approx(55.0639452948)
        assert repo.read() == before


@pytest.mark.parametrize('completion', ['succeeded', 'failed', 'partial'])
def test_restart_recovery_preserves_terminal_run_payloads(completion):
    class Engine(FakeEngine):
        def analyze(self, body):
            invalid = fixture_draft()
            invalid['factor_proposals']['severity_level'] = 99
            if completion == 'succeeded':
                return [fixture_draft()]
            return [invalid] if completion == 'failed' else [fixture_draft(), invalid]
    repo = MemoryRepository()
    with TestClient(create_app(repo, FakeEvidence(), Engine())) as client:
        started = client.post('/api/analysis-runs', json={}).json()
        terminal = client.get('/api/analysis-runs/' + started['id']).json()
        assert terminal['state'] == completion
    with TestClient(create_app(repo)) as client:
        assert client.get('/api/analysis-runs/' + started['id']).json() == terminal


def test_review_queued_base_changed_before_worker_skips_model_call():
    from models import EditRequest
    class InterleavingRepository(MemoryRepository):
        service = None
        interleave_next_challenge = True
        def write(self, callback):
            result = super().write(callback)
            events = self.read()['events']
            if self.service is not None and self.interleave_next_challenge and events and events[-1]['action'] == 'CHALLENGE_REQUESTED':
                self.interleave_next_challenge = False
                rid = events[-1]['entity_id']
                # A real guarded PM mutation occurs after admission commits and
                # before FastAPI starts the queued worker.
                self.service.edit_requirement(rid, EditRequest.model_validate(dict(identity(), patch={'title': 'New PM revision before execution'})))
            return result
    class Engine(FakeEngine):
        calls = 0
        def review(self, *args):
            self.calls += 1
            return {'answer': 'Must not execute on stale base'}
    repo, engine = InterleavingRepository(), Engine()
    app = create_app(repo, FakeEvidence(), engine)
    with TestClient(app) as client:
        repo.service = app.state.service
        record = app.state.service.create_requirement(fixture_draft(), 'test')
        started = client.post(f"/api/requirements/{record['id']}/challenges", json=dict(identity(), question='Question')).json()
        run = client.get('/api/analysis-runs/' + started['id']).json()
        assert run['state'] == 'failed' and 'Stale review base' in run['failures'][0]
        assert engine.calls == 0
        assert not repo.read()['turns']
        updated = client.get(f"/api/requirements/{record['id']}").json()
        assert updated['revision'] == 2 and updated['title'] == 'New PM revision before execution'


def test_progress_callback_without_optional_mode_preserves_existing_mode():
    repo = MemoryRepository()
    visible = []
    class Engine(FakeEngine):
        def analyze(self, body, progress_callback):
            progress_callback('retrieving', .5)
            visible.append(next(iter(repo.read()['runs'].values())))
            return [fixture_draft()]
    with TestClient(create_app(repo, FakeEvidence(), Engine())) as client:
        started = client.post('/api/analysis-runs', json={}).json()
        assert (visible[0]['stage'], visible[0]['progress'], visible[0]['mode']) == ('retrieving', .5, 'live')
        run = client.get('/api/analysis-runs/' + started['id']).json()
        assert run['state'] == 'succeeded' and run['mode'] == 'live' and run['progress'] == 1
