"""Role 03 acceptance against Role 01's original BMW corpus and live graph.

Set BMW_BACKEND_REAL_DATA_URI to a dedicated loopback test server. Proposals
and PM actions are authored test inputs; evidence, counts and sales are real.
No Role 02 generation or live model behavior is claimed by these checks.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from neo4j import GraphDatabase

from config import get_settings
from conftest import fixture_draft, decision
from models import DecisionRequest, FreezeRequest
from services.api import create_app
from services.audit import verify_chain
from services.decisions import DomainError, GovernanceService
from services.evidence import EvidenceScope, Neo4jEvidenceProvider
from services.export import export_freeze
from services.ingestion import convert_sources, write_bundle
from services.providers import Role01SourceProvider
from services.repository import MemoryRepository, Neo4jRepository
from services.scoring import calculate_score
from services.real_evidence import RealEvidenceResolver


real_loader = {}


@pytest.fixture(scope="module")
def real_data(tmp_path_factory):
    uri = os.environ.get("BMW_BACKEND_REAL_DATA_URI")
    if not uri:
        pytest.skip("Set BMW_BACKEND_REAL_DATA_URI for real BMW backend acceptance")
    assert urlparse(uri).hostname in {"localhost", "127.0.0.1", "::1"}
    settings = get_settings()
    bundle = convert_sources(settings.raw_data_dir, check_known_feedback_counts=True)
    assert bundle.manifest["status"] == "succeeded", bundle.manifest
    processed = tmp_path_factory.mktemp("real-bmw-processed")
    write_bundle(bundle, processed, settings.raw_data_dir)
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("real_bmw_loader", root / "pipeline/01_load_neo4j.py")
    loader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loader)
    driver = GraphDatabase.driver(uri, auth=(
        os.environ.get("BMW_BACKEND_REAL_DATA_USER", settings.neo4j_user),
        os.environ.get("BMW_BACKEND_REAL_DATA_PASSWORD", settings.neo4j_password.get_secret_value())))
    driver.verify_connectivity()
    try:
        loader.load_processed_bundle(driver, processed)
        real_loader[driver] = {"loader": loader, "processed": processed}
        sources = Role01SourceProvider(driver)
        yield driver, bundle, sources, sources.evidence
    finally:
        real_loader.pop(driver, None)
        driver.close()


def reference(row, records):
    locator = {k: v for k, v in (row.get("locator") or records[row["source_record_id"]]["locator"]).items() if v is not None}
    return {"source_id": row["source_file_id"], "record_id": row["id"],
            "source_version": row["source_version"], "locator": locator,
            "claim_key": "problem", "relation": "supports", "validation_state": "verified"}


@pytest.mark.parametrize("kind", ["record", "chunk"])
def test_original_excel_and_pdf_references_verify_and_reject_tampering(real_data, kind):
    _, bundle, sources, _ = real_data
    records = {r["id"]: r for r in bundle.source_records}
    row = (next(r for r in bundle.source_records if r.get("feedback_comment_id"))
           if kind == "record" else bundle.document_chunks[0])
    ref = reference(row, records)
    assert sources.verify_reference(ref)
    stored = sources.get(kind, row["id"])
    assert stored["source_id"] == row["source_file_id"]
    assert stored["locator"] == ref["locator"]
    if kind == "record":
        assert stored["raw_payload"] == row["raw_payload"]
    else:
        page = records[row["source_record_id"]]["raw_payload"]["text"]
        assert stored["original_text"] == page[row["start_offset"]:row["end_offset"]]
    for key, value in (("source_version", "incorrect-hash"), ("source_id", "absent-source"),
                       ("locator", {**ref["locator"], "page" if kind == "chunk" else "row": 99999})):
        assert not sources.verify_reference({**ref, key: value})


def test_backend_source_and_pdf_routes_return_original_files(real_data):
    _, bundle, sources, _ = real_data
    with TestClient(create_app(MemoryRepository(), source_provider=sources)) as client:
        response = client.get("/api/sources")
        assert response.status_code == 200
        assert {r["id"] for r in response.json()} == {r["id"] for r in bundle.source_files}
        for source in bundle.source_files:
            if source["filename"].lower().endswith(".pdf"):
                response = client.get(f'/api/sources/{source["id"]}/file')
                assert response.status_code == 200
                assert hashlib.sha256(response.content).hexdigest() == source["sha256"]
        chunk = bundle.document_chunks[0]
        response = client.get(f'/api/document-chunks/{chunk["id"]}')
        assert response.status_code == 200
        assert response.json()["original_text"] == chunk["original_text"]


def test_backend_retrieval_returns_real_counts_sales_surveys_and_catalogs(real_data):
    _, bundle, _, evidence = real_data
    with TestClient(create_app(MemoryRepository(), source_provider=evidence)) as client:
        counts = client.post("/api/evidence/cohort-counts", json={})
        assert counts.status_code == 200
        assert counts.json()["cohort_distinct_comment_count"] == len(bundle.feedback_comments)
        assert counts.json()["cohort_annotation_count"] == len(bundle.feedback_annotations)
        for operation, body, expected in (
            ("sales-facts", {"year": 2030}, "facts"),
            ("survey-facts", {"topic": "unmapped-topic"}, "facts"),
            ("document-search", {"query": "BMW", "document_kind": "option_catalog"}, None),
            ("feedback-search", {"query": "seat"}, None),
        ):
            result = client.post("/api/evidence/" + operation, json=body)
            assert result.status_code == 200, result.text
            if expected:
                assert expected in result.json()
            else:
                assert result.json()
    scope = EvidenceScope(model="G70", region="US")
    actual = evidence.get_comment_cohort_counts(scope)
    assert actual["cohort_distinct_comment_count"] == sum(
        r["model"] == "G70" and r["region"] == "US" for r in bundle.feedback_comments)
    native = evidence.get_survey_facts(None, EvidenceScope(model="G68", region="CN"))
    original = {r["id"]: r for r in bundle.facts}
    assert native["facts"]
    for fact in native["facts"]:
        assert fact["value"] == original[fact["id"]]["value"]


def test_priority_reach_uses_actual_2030_sales_cells(real_data):
    _, bundle, _, _ = real_data
    facts = [r for r in bundle.facts if r["kind"] == "sales" and r["year"] == 2030]
    assert facts
    reference_volume = sum({(r["vehicle_line"], r["region"]): r["volume"] for r in facts}.values())
    for fact in facts:
        score = calculate_score({"target_regions": [fact["region"]],
            "target_vehicle_lines": [fact["vehicle_line"]], "reference_volume": reference_volume,
            "forecasts": [{"vehicle_line": fact["vehicle_line"], "region": fact["region"],
                           "year": fact["year"], "volume": fact["volume"],
                           "source_reference": {"source_id": fact["source_file_id"], "locator": fact["locator"]}}]})
        assert score["factors"]["reach"]["value"] == pytest.approx(fact["volume"] / reference_volume)
        assert score["factors"]["strategy"]["provisional"]  # No supplied strategy report.



def test_real_evidence_survives_backend_approval_freeze_export_and_reopen(real_data):
    driver, bundle, sources, evidence = real_data
    comment = next(r for r in bundle.feedback_comments if r["model"] == "G70" and r["region"] == "US" and r["powertrain"])
    original = next(r for r in bundle.source_records if r.get("feedback_comment_id") == comment["id"])
    proposal = fixture_draft()
    proposal.update(derivation_key="real-bmw-" + comment["id"], signal_id="manual-review-" + comment["id"],
        title="Review original G70 US customer feedback", description=comment["original_text"],
        problem_statement=comment["original_text"], factor_proposals={},
        source_scope={"derivatives": ["G70"], "regions": ["US"], "powertrains": [comment["powertrain"]]},
        target_scope={"derivatives": ["G70"], "regions": ["US"], "powertrains": [comment["powertrain"]]},
        evidence_links=[reference(original, {})])
    proposal["finding_links"] = [{"signal_id": proposal["signal_id"], "revision": 1}]
    proposal["smart"]["specific"] = "PM-authored follow-up to the cited original feedback"
    namespace = "real-bmw-backend-" + uuid4().hex
    repo = Neo4jRepository(driver, "neo4j", namespace)
    repo.initialize()
    resolver = RealEvidenceResolver(sources, evidence)
    service = GovernanceService(repo, resolver)
    try:
        record = service.create_requirement(proposal, "manual-real-source-acceptance")
        assert record["source_snapshots"][0]["raw_payload"] == original["raw_payload"]
        assert record["score"]["factors"]["strategy"]["provisional"]
        with pytest.raises(DomainError):
            service.create_requirement({**proposal, "derivation_key": "tampered",
                "evidence_links": [{**proposal["evidence_links"][0], "source_version": "wrong-hash"}]}, "manual")
        approved = service.decide_requirement(record["id"], DecisionRequest.model_validate(
            decision(reviewed_source_ids=[original["id"]], rationale="Acceptance exercise reviewing original BMW evidence")))
        ranking = repo.read()["rankings"][-1]
        frozen = service.freeze(FreezeRequest(selections={approved["id"]: approved["revision"]},
            expected_ranking_id=ranking["id"], actor_id="acceptance-pm", actor_role="PM_USER",
            rationale="Real-source freeze acceptance"))
        exported = json.loads(export_freeze(frozen))
        assert exported["source_snapshots"][0]["raw_payload"] == original["raw_payload"]
        assert verify_chain(frozen["audit"]["events"], frozen["audit"]["checkpoint"])
        restored = Neo4jRepository(driver, "neo4j", namespace).read()
        assert restored["requirements"][approved["id"]]["description"] == comment["original_text"]
        assert restored["freezes"][frozen["id"]] == frozen
    finally:
        with driver.session() as session:
            session.run("MATCH (n) WHERE n.backend_key=$namespace OR n.backend_key STARTS WITH $prefix DETACH DELETE n",
                        namespace=namespace, prefix=namespace + ":").consume()
