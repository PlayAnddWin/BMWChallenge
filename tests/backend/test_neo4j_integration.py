"""Opt-in real-graph acceptance proofs; no mock substitutes for transaction checks.

Set BACKEND_TEST_NEO4J_URI to a dedicated test server. Optional credentials:
BACKEND_TEST_NEO4J_USER, BACKEND_TEST_NEO4J_PASSWORD, BACKEND_TEST_NEO4J_DATABASE.
Only this test's randomly namespaced nodes are deleted; constraints are retained.
"""
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from neo4j import GraphDatabase

from conftest import FakeEvidence, decision, fixture_draft
from models import ApplyRequest, DecisionRequest
from services.decisions import DomainError, GovernanceService
from services.repository import Neo4jRepository


@pytest.fixture
def graph_backend():
    uri = os.environ.get('BACKEND_TEST_NEO4J_URI')
    if not uri:
        pytest.skip('Real Neo4j verification requires BACKEND_TEST_NEO4J_URI; not verified offline')
    driver = GraphDatabase.driver(uri, auth=(
        os.environ.get('BACKEND_TEST_NEO4J_USER', 'neo4j'),
        os.environ.get('BACKEND_TEST_NEO4J_PASSWORD', 'bmwpassword')))
    namespace = 'backend-test-' + uuid.uuid4().hex
    repo = Neo4jRepository(driver, os.environ.get('BACKEND_TEST_NEO4J_DATABASE', 'neo4j'), namespace)
    try:
        # Configured but inaccessible graph is a failure, never a silent skip.
        driver.verify_connectivity()
        repo.initialize()
        service = GovernanceService(repo, FakeEvidence())
        record = service.create_requirement(fixture_draft(), 'synthetic-test-run')
        yield repo, service, record
    finally:
        try:
            with repo.session() as session:
                session.run('MATCH (n) WHERE n.backend_key=$namespace OR '
                            'n.backend_key STARTS WITH $prefix DETACH DELETE n',
                            namespace=namespace, prefix=namespace + ':').consume()
        finally:
            driver.close()


def graph_snapshot(repo):
    """Read materialized nodes directly, including ledger lock/head properties."""
    with repo.session() as session:
        return [dict(row) for row in session.run(
            'MATCH (n) WHERE n.backend_key=$namespace OR n.backend_key STARTS WITH $prefix '
            'RETURN n.backend_key AS key, labels(n) AS labels, properties(n) AS properties '
            'ORDER BY key', namespace=repo.namespace, prefix=repo.namespace + ':')]


def node_counts(repo):
    with repo.session() as session:
        return {row['label']: row['count'] for row in session.run(
            'MATCH (n) WHERE n.backend_key=$namespace OR n.backend_key STARTS WITH $prefix '
            'UNWIND labels(n) AS label RETURN label, count(*) AS count',
            namespace=repo.namespace, prefix=repo.namespace + ':')}


def fail_ranking(action):
    if action == 'RANKING_RECALCULATED':
        raise RuntimeError('injected audit failure in real transaction')


def test_real_decision_and_apply_audit_failure_roll_back_all_nodes(graph_backend):
    repo, service, record = graph_backend
    rid = record['id']
    assert node_counts(repo) == {'GovernanceLedger': 1, 'Requirement': 1,
                                'RequirementRevision': 1, 'AuditEvent': 3,
                                'RankingSnapshot': 1, 'ScoringConfig': 1}
    assert [e['action'] for e in repo.read()['events']] == ['SCORING_CONFIG_CREATED', 'REQUIREMENT_DERIVED', 'RANKING_RECALCULATED']
    before, nodes_before = repo.read(), graph_snapshot(repo)
    service.audit_failure = fail_ranking
    with pytest.raises(RuntimeError, match='injected audit failure'):
        service.decide_requirement(rid, DecisionRequest(**decision(score_override=0)))
    # Includes status, score, ranking, revisions, audit head, and ledger lock version.
    assert repo.read() == before
    assert graph_snapshot(repo) == nodes_before

    service.audit_failure = None
    approved = service.decide_requirement(rid, DecisionRequest(**decision(score_override=0)))
    proposal = fixture_draft()
    proposal['title'] = 'Regenerated synthetic temperature controls'
    pending = service.create_requirement(proposal, 'synthetic-rerun')
    request = ApplyRequest(turn_id=pending['pending_turn_id'], expected_revision=2,
                           actor_id='pm-test', actor_role='PM_USER', rationale='Review synthetic proposal',
                           patch={'title': proposal['title']})
    before, nodes_before = repo.read(), graph_snapshot(repo)
    service.audit_failure = fail_ranking
    with pytest.raises(RuntimeError, match='injected audit failure'):
        service.edit_requirement(rid, request, apply=True)
    assert repo.read() == before
    assert graph_snapshot(repo) == nodes_before
    assert repo.read()['turns'][request.turn_id]['state'] == 'pending'
    assert repo.read()['requirements'][rid] == approved

    service.audit_failure = None
    applied = service.edit_requirement(rid, request, apply=True)
    assert applied['status'] == 'PM_REVIEW'
    assert applied['score']['override'] is None
    provenance = applied['revision_application']
    assert provenance == {'turn_id': request.turn_id, 'run_id': 'synthetic-rerun',
                          'base_revision': 2, 'proposed_patch': {'title': proposal['title']},
                          'accepted_patch': request.patch}
    state = repo.read()
    assert next(event for event in state['events'] if event['action'] == 'SUGGESTION_APPLIED')['after']['revision_application'] == provenance
    assert state['revisions'][rid][-1]['revision_application'] == provenance
    assert repo.read()['turns'][request.turn_id]['state'] == 'applied'
    counts = node_counts(repo)
    assert counts['RequirementRevision'] == 3
    assert counts['RankingSnapshot'] == 3
    assert counts['ReviewTurn'] == 1
    persisted = graph_snapshot(repo)
    assert service.edit_requirement(rid, request, apply=True) == applied
    assert graph_snapshot(repo) == persisted


def test_real_restart_preserves_revisions_and_current_state(graph_backend):
    repo, service, record = graph_backend
    service.decide_requirement(record['id'], DecisionRequest(**decision()))
    before, nodes_before = repo.read(), graph_snapshot(repo)
    # A fresh driver/repository instance models an application restart.
    uri = os.environ['BACKEND_TEST_NEO4J_URI']
    with GraphDatabase.driver(uri, auth=(os.environ.get('BACKEND_TEST_NEO4J_USER', 'neo4j'),
                                        os.environ.get('BACKEND_TEST_NEO4J_PASSWORD', 'bmwpassword'))) as driver:
        restarted = Neo4jRepository(driver, repo.database, repo.namespace)
        restarted.initialize()
        assert restarted.read() == before
        assert graph_snapshot(restarted) == nodes_before
        assert len(restarted.read()['revisions'][record['id']]) == 2
        fresh_service = GovernanceService(restarted, FakeEvidence())
        with pytest.raises(DomainError) as error:
            fresh_service.decide_requirement(record['id'], DecisionRequest(**decision()))
        assert error.value.status == 409
        assert graph_snapshot(restarted) == nodes_before


def test_real_concurrent_expected_revision_has_exactly_one_winner(graph_backend):
    repo, _, record = graph_backend
    barrier = threading.Barrier(2)

    def mutate():
        service = GovernanceService(Neo4jRepository(repo.driver, repo.database, repo.namespace), FakeEvidence())
        barrier.wait(timeout=10)
        try:
            result = service.decide_requirement(record['id'], DecisionRequest(**decision(action='start_review')))
            return ('ok', result['revision'])
        except DomainError as error:
            return ('error', error.status)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(mutate) for _ in range(2)]
        outcomes = [future.result(timeout=30) for future in futures]
    assert sorted(outcomes) == [('error', 409), ('ok', 2)]
    state = repo.read()
    assert state['requirements'][record['id']]['status'] == 'PM_REVIEW'
    assert len(state['revisions'][record['id']]) == 2
    assert len(state['rankings']) == 2
    counts = node_counts(repo)
    assert counts['RequirementRevision'] == 2
    assert counts['RankingSnapshot'] == 2
    assert counts['AuditEvent'] == 5
    assert [e['action'] for e in state['events']][-2:] == ['STATUS_CHANGED', 'RANKING_RECALCULATED']


@pytest.mark.parametrize('copy_change', ['alter', 'delete'])
def test_real_audit_graph_copy_tampering_rejects_read(graph_backend, copy_change):
    import json
    repo, _, _ = graph_backend
    event = repo.read()['events'][0]
    key = f"{repo.namespace}:{event['id']}"
    with repo.session() as session:
        if copy_change == 'delete':
            session.run('MATCH (e:AuditEvent {backend_key:$key}) DELETE e', key=key).consume()
        else:
            changed = dict(event, actor_id='tampered-actor', rationale='tampered rationale')
            session.run('MATCH (e:AuditEvent {backend_key:$key}) SET e.payload=$payload',
                        key=key, payload=json.dumps(changed)).consume()
    with pytest.raises(ValueError, match='Immutable AuditEvent graph copies'):
        repo.read()


@pytest.mark.parametrize('change', ['alter', 'delete', 'reorder', 'rehash'])
def test_real_ledger_tampering_fails_retained_checkpoint(graph_backend, change):
    import copy
    import json
    from services.audit import event_hash, verify_chain
    repo, _, _ = graph_backend
    state = repo.read()
    retained_checkpoint = copy.deepcopy(state['checkpoint'])
    if change == 'alter':
        state['events'][0]['after']['title'] = 'tampered customer content'
    elif change == 'delete':
        state['events'].pop()
    elif change == 'reorder':
        state['events'].reverse()
    else:
        state['events'][0]['actor_id'] = 'tampered-human'
        previous = '0' * 64
        for event in state['events']:
            event['previous_hash'] = previous
            event['hash'] = event_hash(event)
            previous = event['hash']
    assert not verify_chain(state['events'], retained_checkpoint)
    # Leave the trusted checkpoint and graph head intact while corrupting the payload.
    with repo.session() as session:
        session.run('MATCH (l:GovernanceLedger {backend_key:$key}) SET l.payload=$payload',
                    key=repo.namespace, payload=json.dumps(state)).consume()
    with pytest.raises(ValueError, match='retained checkpoint'):
        repo.read()


@pytest.mark.parametrize('operation', ['decision', 'apply'])
def test_real_failure_after_materialized_writes_rolls_back_every_node(graph_backend, monkeypatch, operation):
    """Inject after actual Cypher writes, before the managed transaction commits."""
    import json
    repo, service, record = graph_backend
    rid = record['id']
    if operation == 'apply':
        service.decide_requirement(rid, DecisionRequest(**decision(score_override=0)))
        proposal = fixture_draft()
        proposal['title'] = 'Synthetic replacement controls'
        pending = service.create_requirement(proposal, 'synthetic-late-failure-run')
        request = ApplyRequest(turn_id=pending['pending_turn_id'], expected_revision=2,
                               actor_id='pm-test', actor_role='PM_USER', rationale='Apply reviewed change',
                               patch={'title': proposal['title']})
    before, nodes_before = repo.read(), graph_snapshot(repo)
    real_session = repo.session
    observed = []

    class TransactionProxy:
        def __init__(self, tx):
            self.tx = tx

        def run(self, query, **parameters):
            result = self.tx.run(query, **parameters)
            if 'SET l.payload=$payload, l.head_hash=' in query:
                result.consume()
                # These queries observe successfully written, uncommitted graph nodes.
                snapshot = [dict(row) for row in self.tx.run(
                    'MATCH (n) WHERE n.backend_key=$namespace OR n.backend_key STARTS WITH $prefix '
                    'RETURN n.backend_key AS key, labels(n) AS labels, properties(n) AS properties ORDER BY key',
                    namespace=repo.namespace, prefix=repo.namespace + ':')]
                observed.append(snapshot)
                raise RuntimeError('injected failure after materialized graph and audit writes')
            return result

    class SessionProxy:
        def __init__(self):
            self.session = real_session()

        def __enter__(self):
            self.session.__enter__()
            return self

        def __exit__(self, *args):
            return self.session.__exit__(*args)

        def execute_write(self, callback):
            return self.session.execute_write(lambda tx: callback(TransactionProxy(tx)))

        def __getattr__(self, name):
            return getattr(self.session, name)

    with monkeypatch.context() as patch:
        patch.setattr(repo, 'session', SessionProxy)
        with pytest.raises(RuntimeError, match='after materialized graph and audit writes'):
            if operation == 'decision':
                service.decide_requirement(rid, DecisionRequest(**decision(score_override=0)))
            else:
                service.edit_requirement(rid, request, apply=True)
    assert len(observed) == 1
    assert observed[0] != nodes_before
    written = next(row['properties'] for row in observed[0] if row['key'] == f'{repo.namespace}:{rid}')
    assert written['revision'] == before['requirements'][rid]['revision'] + 1
    if operation == 'decision':
        assert written['status'] == 'APPROVED'
        assert json.loads(written['payload'])['score']['effective_score'] == 0
    else:
        assert written['status'] == 'PM_REVIEW'
        turn = next(row for row in observed[0] if row['key'] == f'{repo.namespace}:{request.turn_id}')
        assert json.loads(turn['properties']['payload'])['state'] == 'applied'
    assert len([row for row in observed[0] if 'RequirementRevision' in row['labels']]) == len(before['revisions'][rid]) + 1
    assert len([row for row in observed[0] if 'AuditEvent' in row['labels']]) > len(before['events'])
    assert len([row for row in observed[0] if 'RankingSnapshot' in row['labels']]) == len(before['rankings']) + 1
    assert repo.read() == before
    assert graph_snapshot(repo) == nodes_before


@pytest.mark.parametrize('failure', ['audit', 'malformed', None])
def test_real_import_source_writes_share_governance_transaction(graph_backend, failure):
    from fastapi.testclient import TestClient
    from services.api import create_app
    repo, service, record = graph_backend
    source_key = repo.namespace + ':source-import-test'
    class Importer:
        def prepare_import(self, scope):
            return {'id': source_key, 'source_version': 'synthetic-hash'}

        def commit_import(self, source, *, tx, audit_sink):
            tx.run('CREATE (s:SourceFile {backend_key:$key, id:$key, source_version:$version})',
                   key=source['id'], version=source['source_version']).consume()
            if failure == 'malformed':
                return {'sources': [source], 'result_ids': [float('nan')]}
            audit_sink('SOURCE_IMPORTED', source['id'], None, source, 'Synthetic source import')
            return {'sources': [source], 'result_ids': [source['id']]}

    def fail(action):
        if failure == 'audit' and action == 'SOURCE_IMPORTED':
            raise RuntimeError('injected source import audit failure')
    before = repo.read()
    with TestClient(create_app(repo, import_service=Importer(), audit_failure=fail)) as client:
        run = client.post('/api/import-runs', json={}).json()
        outcome = client.get('/api/analysis-runs/' + run['id']).json()
    with repo.session() as session:
        count = session.run('MATCH (s:SourceFile {backend_key:$key}) RETURN count(s) AS count', key=source_key).single()['count']
    assert count == (0 if failure else 1)
    assert outcome['state'] == ('failed' if failure else 'succeeded')
    if failure:
        assert repo.read()['events'] == before['events']
    else:
        assert len([event for event in repo.read()['events'] if event['entity_id'] == source_key]) == 1


def test_real_import_rejects_independent_commit_adapter_before_invocation(graph_backend):
    from fastapi.testclient import TestClient
    from services.api import create_app
    repo, service, record = graph_backend
    class Importer:
        def import_data(self, scope):
            pytest.fail('Unsafe legacy import must not be invoked against Neo4j')
    with TestClient(create_app(repo, import_service=Importer())) as client:
        run = client.post('/api/import-runs', json={}).json()
        result = client.get('/api/analysis-runs/' + run['id']).json()
    assert result['state'] == 'failed'
    assert 'prepare_import and commit_import' in result['failures'][0]


@pytest.mark.parametrize('ambiguous', [None, 'record', 'duplicate-chunk', 'dual-label'])
def test_real_pdf_chunk_reference_verification_requires_unique_provenance(graph_backend, ambiguous):
    import json
    from services.providers import GraphSourceProvider
    from services.decisions import DomainError
    repo, _, _ = graph_backend
    sid = repo.namespace + '-pdf-source'
    cid = repo.namespace + '-pdf-chunk'
    locator = {'page': 3}
    with repo.session() as session:
        session.run('CREATE (s:SourceFile {id:$sid, source_version:"pdf-hash", backend_key:$source_key}) '
                    'CREATE (c:DocumentChunk {id:$cid, source_id:$sid, source_version:"pdf-hash", locator_json:$locator, backend_key:$chunk_key})',
                    sid=sid, cid=cid, locator=json.dumps(locator), source_key=repo.namespace + ':pdf-source', chunk_key=repo.namespace + ':pdf-chunk').consume()
        if ambiguous == 'dual-label':
            session.run('MATCH (c:DocumentChunk {id:$cid}) SET c:SourceRecord', cid=cid).consume()
        elif ambiguous:
            label = 'SourceRecord' if ambiguous == 'record' else 'DocumentChunk'
            session.run(f'CREATE (c:{label} {{id:$cid, source_id:$sid, source_version:"pdf-hash", locator_json:$locator, backend_key:$key}})',
                        sid=sid, cid=cid, locator=json.dumps(locator), key=repo.namespace + ':ambiguous-pdf').consume()
    provider = GraphSourceProvider(repo.driver, repo.database)
    reference = {'source_id': sid, 'record_id': cid, 'source_version': 'pdf-hash', 'locator': locator, 'validation_state': 'verified'}
    if ambiguous == 'duplicate-chunk':
        with pytest.raises(DomainError) as exc:
            provider.verify_reference(reference)
        assert exc.value.status == 503
    else:
        assert provider.verify_reference(reference) is (ambiguous is None)
    if ambiguous is None:
        assert provider.verify_reference(dict(reference, locator={'page': 4})) is False
        assert provider.verify_reference(dict(reference, source_version='wrong-version')) is False
