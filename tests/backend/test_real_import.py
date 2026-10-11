"""Actual Role 01 conversion/loader through Role 03 import transactions and API."""
import os
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from neo4j import GraphDatabase

from config import get_settings
from services.api import create_app
from services.audit import verify_chain
from services.providers import Role01SourceProvider
from services.repository import Neo4jRepository
from services.role01_import import Role01ImportService
from services.role01_runtime import build_adapters


@pytest.fixture(scope="module")
def import_environment():
    uri = os.environ.get("BMW_BACKEND_IMPORT_URI")
    if not uri:
        pytest.skip("Set BMW_BACKEND_IMPORT_URI to a dedicated empty Neo4j test server")
    assert urlparse(uri).hostname in {"localhost", "127.0.0.1", "::1"}
    settings = get_settings()
    driver = GraphDatabase.driver(uri, auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()))
    driver.verify_connectivity()
    service = Role01ImportService(driver)
    prepared = service.prepare_import({})
    try:
        yield driver, service, prepared
    finally:
        driver.close()


@pytest.fixture
def import_backend(import_environment):
    driver, service, prepared = import_environment
    namespace = "real-import-" + uuid4().hex
    repo = Neo4jRepository(driver, "neo4j", namespace)
    repo.initialize()
    try:
        yield driver, repo, service, prepared
    finally:
        with driver.session() as session:
            session.run("MATCH (n) WHERE n.backend_key=$namespace OR n.backend_key STARTS WITH $prefix DETACH DELETE n",
                        namespace=namespace, prefix=namespace + ":").consume()


def evidence_state(driver):
    with driver.session() as session:
        return session.run("MATCH (n) WHERE n.backend_key IS NULL AND (n:SourceFile OR n:SourceRecord OR n:CustomerFeedback OR n:FeedbackAnnotation OR n:NativeFact OR n:SalesFact OR n:SurveyFact OR n:DocumentChunk OR n:VFCTag OR n:VFCTag3 OR n:ImportRun) RETURN n.id AS id, labels(n) AS labels, properties(n) AS properties ORDER BY id").data()


@pytest.mark.parametrize("failure_at", ["first-source-audit", "after-complete-import"])
def test_actual_source_import_and_governance_rollback_together(import_backend, failure_at):
    driver, repo, importer, prepared = import_backend
    before, graph = repo.read(), evidence_state(driver)
    def persist(state, tx):
        def audit(action, identity, previous, current, rationale):
            from services.decisions import GovernanceService
            GovernanceService(repo, None).event(state, action, identity, previous, current,
                                               ("import-test", "AI_SYSTEM"), rationale)
            if failure_at == "first-source-audit":
                raise RuntimeError("injected source audit failure")
        result = importer.commit_import(prepared, tx=tx, audit_sink=audit)
        state["runs"]["probe"] = {"result_ids": result["result_ids"]}
        raise RuntimeError("injected completion failure")
    with pytest.raises(RuntimeError, match="injected"):
        repo.write_with_transaction(persist)
    assert repo.read() == before
    assert evidence_state(driver) == graph


def test_real_import_api_records_all_sources_and_idempotent_reimport(import_backend):
    driver, repo, importer, prepared = import_backend
    sources = Role01SourceProvider(driver)
    with TestClient(create_app(repo, source_provider=sources, import_service=importer)) as client:
        for attempt in range(2):
            admitted = client.post("/api/import-runs", json={})
            assert admitted.status_code == 200, admitted.text
            result = client.get("/api/analysis-runs/" + admitted.json()["id"]).json()
            assert result["state"] == "succeeded", result
            assert set(result["result_ids"]) == set(prepared["source_ids"])
            assert result["import_metadata"]["counts"]["feedback_comments"] == 10_128
            assert result["import_metadata"]["counts"]["feedback_annotations"] == 13_371
            assert result["import_metadata"]["counts"]["document_chunks"] == 78
            state = repo.read()
            events = [e for e in state["events"] if e["action"] == "SOURCE_IMPORTED" and e["entity_id"] in prepared["source_ids"]]
            assert len(events) == (attempt + 1) * len(prepared["source_ids"])
            assert verify_chain(state["events"], state["checkpoint"])
            graph = evidence_state(driver)
            if attempt:
                assert graph == first_graph
            else:
                first_graph = graph
        assert len(client.get("/api/sources").json()) == len(prepared["source_files"])


def test_actual_api_audit_failure_leaves_import_failed_and_no_source_changes(import_backend):
    driver, repo, importer, _ = import_backend
    before = evidence_state(driver)
    def fail(action):
        if action == "SOURCE_IMPORTED":
            raise RuntimeError("injected real import audit failure")
    with TestClient(create_app(repo, import_service=importer, audit_failure=fail)) as client:
        run = client.post("/api/import-runs", json={}).json()
        failed = client.get("/api/analysis-runs/" + run["id"]).json()
        assert failed["state"] == "failed"
        assert failed["result_ids"] == []
        assert "sources" not in failed
    assert repo.read()["events"] == []
    assert evidence_state(driver) == before


def test_runtime_factory_wires_actual_role01_components(import_backend):
    driver, repo, _, prepared = import_backend
    with TestClient(create_app(repo, adapter_factory=build_adapters)) as client:
        result = client.post("/api/evidence/cohort-counts", json={})
        assert result.status_code == 200
        assert result.json()["cohort_distinct_comment_count"] == len(prepared["feedback"])
        assert len(client.get("/api/sources").json()) == len(prepared["source_files"])
        assert client.post("/api/evidence/cohort-counts", json={"scope": {"derivatives": ["G70"], "regions": ["US"]}}).json()["cohort_distinct_comment_count"] == sum(
            r["model"] == "G70" and r["region"] == "US" for r in prepared["feedback"])


def test_prepare_does_not_silently_broaden_subset_import(import_backend):
    driver, repo, importer, _ = import_backend
    before = evidence_state(driver)
    with TestClient(create_app(repo, import_service=importer)) as client:
        run = client.post("/api/import-runs", json={"derivatives": ["G70"]}).json()
        failed = client.get("/api/analysis-runs/" + run["id"]).json()
        assert failed["state"] == "failed"
        assert "subset" in failed["failures"][0]
    assert evidence_state(driver) == before


def test_role01_schema_preparation_uses_repository_database(import_backend):
    from services.role01_import import role01_loader
    driver, repo, _, _ = import_backend
    calls = []
    class RoutedDriver:
        def session(self, **kwargs):
            calls.append(kwargs)
            return driver.session(**kwargs)
    routed = RoutedDriver()
    components = build_adapters(driver=routed, repository=repo)
    assert components["import_service"].database == repo.database
    role01_loader().ensure_schema(routed, components["import_service"].database)
    assert calls == [{"database": repo.database}]
