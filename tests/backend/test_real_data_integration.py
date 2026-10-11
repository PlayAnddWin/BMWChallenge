"""Actual processed BMW corpus → real providers → HTTP/governance/Neo4j.

Use BACKEND_DATA_TEST_NEO4J_URI for a disposable loopback graph. These checks
never substitute fixture source records or model-generated counts.
"""

from copy import deepcopy
import hashlib
import importlib
import os
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi.testclient import TestClient
from neo4j import GraphDatabase
import pytest

from config import get_settings
from services.api import create_app
from services.audit import verify_chain
from services.providers import GraphSourceProvider
from services.repository import Neo4jRepository


@pytest.fixture(scope="module")
def imported_graph():
    uri = os.environ.get("BACKEND_DATA_TEST_NEO4J_URI")
    if not uri:
        pytest.skip("Set BACKEND_DATA_TEST_NEO4J_URI to a disposable local graph")
    assert urlsplit(uri).hostname in {"localhost", "127.0.0.1", "::1"}
    assert urlsplit(uri).port != 7687, "Use a separate test graph, not the local application database"
    settings = get_settings()
    driver = GraphDatabase.driver(uri, auth=(os.environ.get("BACKEND_DATA_TEST_NEO4J_USER", "neo4j"),
                                           os.environ["BACKEND_DATA_TEST_NEO4J_PASSWORD"]))
    driver.verify_connectivity()
    loader = importlib.import_module("pipeline.01_load_neo4j")
    result = loader.load_processed_bundle(driver, settings.data_dir, database=settings.neo4j_database)
    assert result["status"] == "succeeded"
    assert result["counts"]["feedback_comments"] == 10128
    assert result["counts"]["source_files"] == 9
    yield driver, settings.neo4j_database
    driver.close()


@pytest.fixture
def real_backend(imported_graph):
    driver, database = imported_graph
    namespace = "real-data-" + uuid4().hex
    repository = Neo4jRepository(driver, database, namespace)
    repository.initialize()
    app = create_app(repository=repository)
    with TestClient(app) as client:
        yield client, app, repository
    with driver.session(database=database) as session:
        session.run("MATCH (n) WHERE n.backend_key=$key OR n.backend_key STARTS WITH $prefix DETACH DELETE n",
                    key=namespace, prefix=namespace + ":").consume()


def real_draft(client):
    result = client.post("/api/evidence/feedback-search", json={"query": "temperature", "scope": {
        "derivatives": ["G60"], "regions": ["US"], "powertrains": ["BEV"]}, "limit": 2})
    assert result.status_code == 200, result.text
    comments = result.json()
    assert len(comments) == 2
    links = [{"source_id": comment["source_file_id"], "record_id": comment["source_records"][0]["source_record_id"],
              "source_version": comment["source_version"], "locator": comment["source_records"][0]["source_locator"],
              "claim_key": "selected-temperature-feedback", "relation": "supports", "validation_state": "verified"}
             for comment in comments]
    identity = hashlib.sha256("|".join(comment["id"] for comment in comments).encode()).hexdigest()
    signal_id = "source-selection-" + identity
    return {"derivation_key": "review-temperature-feedback", "signal_id": signal_id, "signal_revision": 1,
            "title": "Review temperature-control access", "description": "Evaluate usability of temperature-control access using the selected source feedback.",
            "problem_statement": comments[0]["original_text"],
            "source_scope": {"derivatives": ["G60"], "regions": ["US"], "powertrains": ["BEV"]},
            "target_scope": {"derivatives": ["G60"], "regions": ["US"], "powertrains": ["BEV"]},
            "finding_links": [{"signal_id": signal_id, "revision": 1}], "evidence_links": links,
            "smart": {"specific": "Evaluate cabin-temperature UI access", "attainability": "Feasibility remains a review assumption",
                      "relevance": "Review the exact cited source feedback",
                      "metrics": [{"id": "access-actions", "operator": "<=", "target": 2, "unit": "actions",
                                   "conditions": "Stationary usability evaluation", "verification_method": "Observed usability test", "provenance": "AIProposed"}],
                      "milestone": {"id": "review-milestone", "value": "Next reviewed concept gate", "provenance": "AIProposed"}},
            "factor_proposals": {}, "assumptions": [{"id": "whole-line-upper-bound", "kind": "powertrain_proxy",
                "description": "Whole G60_G68 line sales are only an upper-bound proxy; no BEV or derivative split is supplied."}],
            "uncertainties": [], "has_conflict": False}


def create_real_requirement(client):
    draft = real_draft(client)
    response = client.post("/api/requirements", json={"draft": draft, "run_id": "source-selection-integration"})
    assert response.status_code == 200, response.text
    return draft, response.json()


def test_default_provider_reads_actual_scoped_inputs(real_backend):
    client, app, repository = real_backend
    assert client.get("/api/health").json()["evidence_provider"] == "Role1EvidenceResolver"
    assert len(client.get("/api/sources").json()) == 9
    counts = client.post("/api/evidence/cohort-counts", json={"scope": {
        "derivatives": ["G60"], "regions": ["US"], "powertrains": ["BEV"]}}).json()
    assert counts["cohort_distinct_comment_count"] == 1271
    sales = client.post("/api/evidence/sales-facts", json={"scope": {"derivatives": ["G60"], "regions": ["RoW"]}, "year": 2030}).json()
    assert sales["facts"] == []
    assert sales["possible_whole_line_proxy_candidates"][0]["volume"] == 31000
    assert sales["possible_whole_line_proxy_candidates"][0]["source_locator"]["column"] == 5
    survey = client.post("/api/evidence/survey-facts", json={"topic": "HVAC", "scope": {"derivatives": ["G70"], "regions": ["US"]}}).json()
    assert survey["mapping_status"] == "unmapped" and survey["facts"] == []


def test_real_draft_scoring_approval_freeze_export_and_rerun(real_backend):
    client, app, repository = real_backend
    draft, record = create_real_requirement(client)
    assert record["score_context"]["reference_volume"] == 486000
    assert len(record["score_context"]["reference_policy"]["fact_ids"]) == 12
    assert record["score_context"]["region_counts"]["US"]["cohort"] == 1271
    assert record["score_context"]["region_counts"]["US"]["support"] == 2
    assert record["score"]["factors"]["reach"]["value"] == pytest.approx(80000 / 486000)
    assert record["score"]["factors"]["reach"]["provisional"] is True
    assert record["evidence"]["corroboration_kind"] == "unmapped"
    rid = record["id"]
    approval = {"expected_revision": 1, "action": "approve", "actor_id": "integration-pm", "actor_role": "PM_USER",
                "rationale": "Reviewed original cited rows and proposed targets; accepted the explicit sales proxy",
                "reviewed_target_ids": ["access-actions", "review-milestone"],
                "reviewed_source_ids": [link["record_id"] for link in draft["evidence_links"]],
                "accepted_assumption_ids": ["whole-line-upper-bound"]}
    response = client.post(f"/api/requirements/{rid}/decisions", json=approval)
    assert response.status_code == 200, response.text
    approved = response.json()
    response = client.post("/api/freezes", json={"selections": {rid: approved["revision"]},
        "expected_ranking_id": repository.read()["rankings"][-1]["id"], "actor_id": "integration-pm",
        "actor_role": "PM_USER", "rationale": "Retain the complete reviewed real-data snapshot"})
    assert response.status_code == 200, response.text
    frozen = response.json()
    assert len(frozen["source_snapshots"]) >= 14
    assert verify_chain(frozen["audit"]["events"], frozen["checkpoint"])
    assert client.get("/api/requirements/export").json()[0]["id"] == rid
    assert client.get(f"/api/freezes/{frozen['id']}/export").json() == frozen
    rerun = deepcopy(draft)
    rerun["title"] = "Updated proposal pending PM application"
    response = client.post("/api/requirements", json={"draft": rerun, "run_id": "rerun-actual-sources"})
    assert response.status_code == 200, response.text
    assert response.json()["title"] == approved["title"]
    assert response.json()["status"] == "APPROVED"
    proposal = client.get(f"/api/requirements/{rid}/proposals").json()["items"][0]
    response = client.post(f"/api/requirements/{rid}/challenge-applications", json={
        "expected_revision": approved["revision"], "actor_id": "integration-pm", "actor_role": "PM_USER",
        "rationale": "Apply the reviewed proposal separately from approval", "patch": {"title": rerun["title"]}, "turn_id": proposal["id"]})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "PM_REVIEW"
    assert client.get(f"/api/freezes/{frozen['id']}/export").json() == frozen


def test_real_import_is_idempotent_and_audited(real_backend):
    client, app, repository = real_backend
    provider = app.state.source_provider
    before = provider._read("MATCH (fb:CustomerFeedback) RETURN count(fb) AS count")[0]["count"]
    run = client.post("/api/import-runs", json={}).json()
    result = client.get("/api/analysis-runs/" + run["id"]).json()
    assert result["state"] == "succeeded", result.get("failures")
    assert result["counts"]["feedback_comments"] == 10128
    assert len(result["sources"]) == 9
    assert provider._read("MATCH (fb:CustomerFeedback) RETURN count(fb) AS count")[0]["count"] == before
    assert len([e for e in repository.read()["events"] if e["entity_type"] == "SourceFile" and e["action"] == "SOURCE_IMPORTED"]) == 9


def test_bad_actual_citation_and_audit_failure_do_not_write(real_backend):
    client, app, repository = real_backend
    draft = real_draft(client)
    bad = deepcopy(draft)
    bad["evidence_links"][0]["locator"]["row"] += 1
    before = repository.read()
    response = client.post("/api/requirements", json={"draft": bad, "run_id": "incorrect-citation"})
    assert response.status_code == 422
    assert repository.read() == before
    _, record = create_real_requirement(client)
    before = repository.read()

    def fail(action):
        if action == "STATUS_CHANGED":
            raise RuntimeError("injected actual-data audit failure")

    app.state.service.audit_failure = fail
    with pytest.raises(RuntimeError, match="injected actual-data"):
        client.post(f"/api/requirements/{record['id']}/decisions", json={"expected_revision": 1,
            "action": "start_review", "actor_id": "integration-pm", "actor_role": "PM_USER", "rationale": "Rollback check"})
    assert repository.read() == before
