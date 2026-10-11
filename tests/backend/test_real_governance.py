"""Live Role 03 adversarial checks using the complete Role 01 import.

Proposals, targets and PM actions are explicitly authored acceptance inputs.
Every evidence reference, cohort, snapshot and forecast uses Role 01 code/data.
"""
import copy
import csv
import io
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

# Complete source-backed audit history can exceed csv's small default cell limit.
csv.field_size_limit(16 * 1024 * 1024)

import pytest
from pydantic import ValidationError

from conftest import decision, fixture_draft
from test_real_bmw_data import real_data, reference
from test_neo4j_integration import graph_snapshot
from models import ApplyRequest, DecisionRequest, EditRequest, FreezeRequest
from services.audit import verify_chain
from services.decisions import DomainError, GovernanceService
from services.export import export_freeze, export_requirements
from services.real_evidence import RealEvidenceResolver
from services.repository import Neo4jRepository


@pytest.fixture(params=[("F70", "EU"), ("G60", "US"), ("G60", "EU"), ("G70", "US"), ("G70", "EU")],
                ids=["F70-EU", "G60-US", "G60-EU", "G70-US", "G70-EU"])
def real_governance(real_data, request):
    driver, bundle, sources, evidence = real_data
    model, region = request.param
    comment = next(r for r in bundle.feedback_comments if r["model"] == model and r["region"] == region and r.get("powertrain"))
    row = next(r for r in bundle.source_records if r.get("feedback_comment_id") == comment["id"])
    draft = fixture_draft()
    scope = {"derivatives": [comment["model"]], "regions": [comment["region"]], "powertrains": [comment["powertrain"]]}
    draft.update(derivation_key="manual-real-" + uuid4().hex, signal_id="manual-review-" + comment["id"],
        title="PM-authored investigation of original BMW feedback", description=comment["original_text"],
        problem_statement=comment["original_text"], source_scope=scope, target_scope=scope,
        evidence_links=[reference(row, {})], factor_proposals={})
    draft["finding_links"] = [{"signal_id": draft["signal_id"], "revision": 1}]
    namespace = "real-governance-" + uuid4().hex
    repo = Neo4jRepository(driver, "neo4j", namespace)
    repo.initialize()
    baseline = sum({(f["vehicle_line"], f["region"]): f["volume"] for f in bundle.facts
                    if f.get("kind") == "sales" and f.get("year") == 2030}.values())
    resolver = RealEvidenceResolver(sources, evidence, fixed_reference_volume=baseline)
    service = GovernanceService(repo, resolver)
    try:
        record = service.create_requirement(draft, "manual-real-acceptance")
        yield repo, service, record, draft, row, bundle
    finally:
        with driver.session() as session:
            session.run("MATCH (n) WHERE n.backend_key=$namespace OR n.backend_key STARTS WITH $prefix DETACH DELETE n",
                        namespace=namespace, prefix=namespace + ":").consume()


def pm(record, **kwargs):
    return DecisionRequest(**decision(revision=record["revision"],
        reviewed_source_ids=[l["record_id"] for l in record["evidence_links"]],
        rationale="Human acceptance review of original BMW source assignment and proposed targets", **kwargs))


def freeze_request(repo, record, **kwargs):
    values = dict(selections={record["id"]: record["revision"]},
        expected_ranking_id=repo.read()["rankings"][-1]["id"], actor_id="real-test-pm",
        actor_role="PM_USER", rationale="Freeze reviewed original-source acceptance plan")
    values.update(kwargs)
    return FreezeRequest(**values)


@pytest.mark.parametrize("bad", ["ai", "identity", "targets", "sources", "assumptions", "stale"])
def test_real_sources_do_not_bypass_human_approval_guards(real_governance, bad):
    repo, service, record, *_ = real_governance
    overrides = {"ai": {"actor_id": "AI_SYSTEM"}, "identity": {"id": "wrong-id"},
                 "targets": {"reviewed_target_ids": []}, "sources": {"reviewed_source_ids": []},
                 "assumptions": {"accepted_assumption_ids": []}, "stale": {"expected_revision": 99}}[bad]
    request = pm(record).model_copy(update=overrides)
    before, graph = repo.read(), graph_snapshot(repo)
    with pytest.raises(DomainError):
        service.decide_requirement(record["id"], request)
    assert repo.read() == before
    assert graph_snapshot(repo) == graph


@pytest.mark.parametrize("value", [-1, 999, float("nan"), float("inf"), True])
def test_real_drafts_reject_invalid_override_before_writes(real_governance, value):
    repo, _, record, *_ = real_governance
    before = graph_snapshot(repo)
    with pytest.raises(ValidationError):
        pm(record, score_override=value)
    assert graph_snapshot(repo) == before


def test_real_context_deduplicates_original_support_and_never_trusts_model_factors(real_governance):
    _, service, record, draft, row, bundle = real_governance
    repeated = copy.deepcopy(draft)
    repeated["evidence_links"] *= 3
    repeated["factor_proposals"] = {"severity_level": 4, "strategic_fit_level": 4, "complexity_level": 1}
    resolved = service.context(repeated)
    region = draft["target_scope"]["regions"][0]
    model = draft["target_scope"]["derivatives"][0]
    assert resolved["evidence_context"]["support_ids"] == [row["feedback_comment_id"]]
    assert resolved["score_context"]["region_counts"][region] == {"support": 1, "cohort": sum(
        c["model"] == model and c["region"] == region and c["powertrain"] in draft["target_scope"]["powertrains"] for c in bundle.feedback_comments)}
    assert "severity_level" not in resolved["score_context"]
    assert "strategic_fit_level" not in resolved["score_context"]
    assert record["score"]["factors"]["strategy"]["provisional"]
    originals = {f["id"]: f for f in bundle.facts}
    for forecast in resolved["score_context"]["forecasts"]:
        matches = [f for f in originals.values() if f.get("kind") == "sales" and
                   f.get("vehicle_line") == forecast["vehicle_line"] and f.get("region") == region and f.get("year") == 2030]
        assert matches and forecast["volume"] == matches[0]["volume"]
        assert forecast["source_reference"]["locator"] == matches[0]["locator"]


@pytest.mark.parametrize("field,value", [("source_version", "wrong"), ("source_id", "absent"),
                                           ("locator", {"sheet": "missing", "row": 999999})])
def test_real_source_tampering_rejected_without_persistence(real_governance, field, value):
    repo, service, _, draft, *_ = real_governance
    corrupt = copy.deepcopy(draft)
    corrupt["evidence_links"][0][field] = value
    before, nodes = repo.read(), graph_snapshot(repo)
    with pytest.raises(DomainError):
        service.create_requirement(corrupt, "tamper-real-acceptance")
    assert repo.read() == before
    assert graph_snapshot(repo) == nodes


def test_real_approval_zero_override_reopen_freeze_export_reload_and_audit_tampering(real_governance):
    repo, service, record, _, row, _ = real_governance
    with pytest.raises(DomainError):
        service.freeze(freeze_request(repo, record))
    approved = service.decide_requirement(record["id"], pm(record, score_override=0))
    assert approved["score"]["effective_score"] == 0
    assert approved["score"]["override"] == 0
    with pytest.raises(DomainError):
        service.freeze(freeze_request(repo, approved, expected_ranking_id="stale"))
    frozen = service.freeze(freeze_request(repo, approved))
    assert frozen["source_snapshots"][0]["raw_payload"] == row["raw_payload"]
    assert json.loads(export_freeze(frozen)) == frozen
    assert "Approved plan" in export_freeze(frozen, "markdown")
    rows = list(csv.DictReader(io.StringIO(export_requirements([approved], "csv"))))
    assert json.loads(rows[0]["source_references"]) == approved["evidence_links"]
    assert verify_chain(frozen["audit"]["events"], frozen["audit"]["checkpoint"])
    for transform in ("alter", "delete", "reorder"):
        events = copy.deepcopy(frozen["audit"]["events"])
        if transform == "alter":
            events[0]["rationale"] = "tampered human rationale"
        elif transform == "delete":
            events.pop(0)
        else:
            events[0], events[1] = events[1], events[0]
        assert not verify_chain(events, frozen["audit"]["checkpoint"])
    reopened = service.decide_requirement(approved["id"], pm(approved, action="reopen"))
    edited = service.edit_requirement(record["id"], EditRequest(expected_revision=reopened["revision"],
        actor_id="real-pm", actor_role="PM_USER", rationale="Update authored requirement after source review",
        patch={"title": "Updated original-source investigation"}))
    assert edited["status"] == "PM_REVIEW" and edited["approved_revision"] is None
    restored = Neo4jRepository(repo.driver, repo.database, repo.namespace).read()
    assert restored["freezes"][frozen["id"]] == frozen
    assert frozen["requirements"][0] == approved
    assert restored["revisions"][record["id"]][0] == record


def test_real_regeneration_apply_retry_and_audit_failure_roll_back_all_nodes(real_governance):
    repo, service, record, draft, *_ = real_governance
    approved = service.decide_requirement(record["id"], pm(record))
    regenerated = copy.deepcopy(draft)
    regenerated["title"] = "Regenerated human-authored acceptance proposal"
    pending = service.create_requirement(regenerated, "manual-real-regeneration")
    assert repo.read()["requirements"][record["id"]] == approved
    request = ApplyRequest(expected_revision=approved["revision"], turn_id=pending["pending_turn_id"],
        actor_id="real-pm", actor_role="PM_USER", rationale="Apply reviewed authored proposal",
        patch={"title": regenerated["title"]})
    before, graph = repo.read(), graph_snapshot(repo)
    def fail(action):
        if action == "RANKING_RECALCULATED":
            raise RuntimeError("injected audit failure")
    service.audit_failure = fail
    with pytest.raises(RuntimeError, match="injected audit failure"):
        service.edit_requirement(record["id"], request, apply=True)
    assert repo.read() == before and graph_snapshot(repo) == graph
    service.audit_failure = None
    applied = service.edit_requirement(record["id"], request, apply=True)
    assert applied["status"] == "PM_REVIEW"
    before = graph_snapshot(repo)
    assert service.edit_requirement(record["id"], request, apply=True) == applied
    assert graph_snapshot(repo) == before
    with pytest.raises(DomainError):
        service.edit_requirement(record["id"], request.model_copy(update={"rationale": "conflicting retry"}), apply=True)


def test_concurrent_real_source_pm_updates_have_one_winner(real_governance):
    repo, service, record, *_ = real_governance
    def update(label):
        try:
            return service.edit_requirement(record["id"], EditRequest(expected_revision=record["revision"],
                actor_id="real-pm", actor_role="PM_USER", rationale="Concurrent original-source review",
                patch={"title": label}))["revision"]
        except DomainError as exc:
            return exc.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(update, ["Human review A", "Human review B"]))
    assert sorted(outcomes) == [2, 409]
    state = repo.read()
    assert len(state["revisions"][record["id"]]) == 2
    assert verify_chain(state["events"], state["checkpoint"])


def test_real_source_drift_cannot_be_laundered_through_revision_refresh(real_governance):
    repo, service, record, _, row, _ = real_governance
    driver = repo.driver
    with driver.session() as session:
        original = session.run("MATCH (r:SourceRecord {id:$id}) RETURN r.raw_payload_json AS raw",
                               id=row["id"]).single()["raw"]
    altered = json.loads(original)
    altered["acceptance_corruption_probe"] = "source snapshot changed without version change"
    before = repo.read()
    try:
        with driver.session() as session:
            session.run("MATCH (r:SourceRecord {id:$id}) SET r.raw_payload_json=$raw",
                        id=row["id"], raw=json.dumps(altered)).consume()
        with pytest.raises(DomainError):
            service.decide_requirement(record["id"], pm(record))
        with pytest.raises(DomainError):
            service.edit_requirement(record["id"], EditRequest(expected_revision=record["revision"],
                actor_id="real-pm", actor_role="PM_USER", rationale="Attempt to refresh unversioned corruption",
                patch={"evidence_links": record["evidence_links"]}))
        assert repo.read() == before
    finally:
        # Restore using the authoritative Role 01 loader, not a backend snapshot.
        from test_real_bmw_data import real_loader
        processed = real_loader[driver]
        processed["loader"].load_processed_bundle(driver, processed["processed"])
    assert repo.read() == before
    approved = service.decide_requirement(record["id"], pm(record))
    frozen = service.freeze(freeze_request(repo, approved))
    assert frozen["source_snapshots"][0]["raw_payload"] == row["raw_payload"]


@pytest.mark.parametrize("operation", ["decision", "edit", "freeze"])
def test_real_evidence_mutation_audit_failures_leave_complete_graph_unchanged(real_governance, operation):
    repo, service, record, *_ = real_governance
    if operation == "freeze":
        record = service.decide_requirement(record["id"], pm(record))
    before, graph = repo.read(), graph_snapshot(repo)
    def fail(action):
        if action in {"STATUS_CHANGED", "CONTENT_CHANGED", "FREEZE_CREATED"}:
            raise RuntimeError("injected real-source transaction failure")
    service.audit_failure = fail
    with pytest.raises(RuntimeError, match="injected real-source transaction failure"):
        if operation == "decision":
            service.decide_requirement(record["id"], pm(record))
        elif operation == "edit":
            service.edit_requirement(record["id"], EditRequest(expected_revision=record["revision"],
                actor_id="real-pm", actor_role="PM_USER", rationale="Edit reviewed source proposal",
                patch={"description": "Authored follow-up to original BMW source"}))
        else:
            service.freeze(freeze_request(repo, record))
    assert repo.read() == before
    assert graph_snapshot(repo) == graph


def test_real_opposition_and_powertrain_forecasts_remain_explicit_gaps(real_governance):
    _, service, _, draft, *_ = real_governance
    proposal = copy.deepcopy(draft)
    proposal["evidence_links"][0]["relation"] = "contradicts"
    context = service.context(proposal)
    assert context["evidence_context"]["support_ids"] == []
    assert context["evidence_context"]["contradiction"]
    assert context["evidence_context"]["corroboration"] == "none"
    assert context["evidence_context"]["proposed_targets"]
    proposal["target_scope"]["powertrains"] = ["BEV"]
    context = service.context(proposal)
    assert not context["score_context"]["forecasts"]
    assert any("powertrain" in warning for warning in context["score_context"]["warnings"])


def test_real_governance_api_roundtrip_preserves_original_sources_and_review_history(real_governance):
    from fastapi.testclient import TestClient
    from services.api import create_app
    repo, service, record, _, row, _ = real_governance
    with TestClient(create_app(repo, evidence_provider=service.evidence_provider,
                               source_provider=service.evidence_provider.sources)) as client:
        rid = record["id"]
        detail = client.get(f"/api/requirements/{rid}")
        assert detail.status_code == 200
        assert detail.json()["source_snapshots"][0]["raw_payload"] == row["raw_payload"]
        linked = client.get(f"/api/requirements/{rid}/evidence")
        assert linked.status_code == 200
        assert linked.json()["links"] == record["evidence_links"]
        before = repo.read()
        invalid = pm(record).model_dump()
        invalid["reviewed_source_ids"] = []
        assert client.post(f"/api/requirements/{rid}/decisions", json=invalid).status_code == 422
        assert repo.read() == before
        approved_response = client.post(f"/api/requirements/{rid}/decisions", json=pm(record).model_dump())
        assert approved_response.status_code == 200, approved_response.text
        approved = approved_response.json()
        exported_requirements = client.get("/api/requirements/export").json()
        assert len(exported_requirements) == 1
        exported_requirement = exported_requirements[0]
        assert exported_requirement["source_references"] == record["evidence_links"]
        assert len(exported_requirement["history"]["revisions"]) == 2
        assert exported_requirement["decision"]["action"] == "STATUS_CHANGED"
        assert exported_requirement["decision"]["after"]["status"] == "APPROVED"
        assert exported_requirement["decision"]["actor_id"] == "pm-test"
        assert exported_requirement["decision"]["rationale"] == pm(record).rationale
        csv_export = client.get("/api/requirements/export", params={"format": "csv"})
        csv_row = next(csv.DictReader(io.StringIO(csv_export.text)))
        assert json.loads(csv_row["decision"]) == exported_requirement["decision"]
        assert json.loads(csv_row["history"]) == exported_requirement["history"]
        frozen = client.post("/api/freezes", json=freeze_request(repo, approved).model_dump())
        assert frozen.status_code == 200, frozen.text
        fid = frozen.json()["id"]
        exported = client.get(f"/api/freezes/{fid}/export")
        assert exported.status_code == 200 and exported.json() == frozen.json()
        audit = client.get(f"/api/freezes/{fid}/audit")
        assert audit.status_code == 200
        assert verify_chain(audit.json()["events"], audit.json()["checkpoint"])
        history = client.get(f"/api/requirements/{rid}/history/export")
        assert history.status_code == 200
        assert len(history.json()["revisions"]) == 2
        assert any(e["action"] == "REQUIREMENT_DERIVED" for e in history.json()["events"])


@pytest.mark.parametrize("model", ["F70", "G60", "G70"])
def test_real_pdf_catalog_evidence_supports_pinned_governance_freeze(real_data, model):
    from services.repository import MemoryRepository
    driver, bundle, sources, evidence = real_data
    source = next(s for s in bundle.source_files if s["filename"].startswith(model) and s["filename"].lower().endswith(".pdf"))
    chunk = next(c for c in bundle.document_chunks if c["source_file_id"] == source["id"])
    records = {r["id"]: r for r in bundle.source_records}
    proposal = fixture_draft()
    proposal.update(derivation_key="manual-catalog-" + chunk["id"], signal_id="manual-catalog-review-" + model,
        title="PM-authored review of the original option catalogue", description=chunk["original_text"],
        problem_statement="Review the cited catalogue passage for a proposed customer requirement", factor_proposals={},
        source_scope={"derivatives": [model], "regions": ["EU"], "powertrains": ["BEV"]},
        target_scope={"derivatives": [model], "regions": ["EU"], "powertrains": ["BEV"]},
        evidence_links=[reference(chunk, records)])
    proposal["finding_links"] = [{"signal_id": proposal["signal_id"], "revision": 1}]
    repo = MemoryRepository()
    service = GovernanceService(repo, RealEvidenceResolver(sources, evidence))
    record = service.create_requirement(proposal, "manual-catalog-acceptance")
    assert record["source_snapshots"][0]["raw_payload"]["text"] == chunk["original_text"]
    assert record["evidence"]["distinct_support_comments"] == 0
    assert record["score"]["factors"]["strategy"]["provisional"]
    approved = service.decide_requirement(record["id"], pm(record))
    frozen = service.freeze(freeze_request(repo, approved))
    assert frozen["source_snapshots"][0]["raw_payload"]["page_payload"] == records[chunk["source_record_id"]]["raw_payload"]
    assert verify_chain(frozen["audit"]["events"], frozen["audit"]["checkpoint"])


def test_more_than_200_original_feedback_requirements_export_without_pagination(real_data):
    from fastapi.testclient import TestClient
    from services.api import create_app
    from services.repository import MemoryRepository
    _, bundle, sources, evidence = real_data
    originals = {r["feedback_comment_id"]: r for r in bundle.source_records if r.get("feedback_comment_id")}
    comments = [c for c in bundle.feedback_comments if c.get("region") and c.get("powertrain")][:250]
    assert len(comments) == 250
    repo = MemoryRepository()
    resolver = RealEvidenceResolver(sources, evidence)
    service = GovernanceService(repo, resolver)
    expected = {}
    for comment in comments:
        proposal = fixture_draft()
        proposal.update(derivation_key="manual-export-" + comment["id"], signal_id="manual-export-review-" + comment["id"],
            title="PM-authored original feedback investigation", description=comment["original_text"],
            problem_statement=comment["original_text"], factor_proposals={},
            source_scope={"derivatives": [comment["model"]], "regions": [comment["region"]], "powertrains": [comment["powertrain"]]},
            target_scope={"derivatives": [comment["model"]], "regions": [comment["region"]], "powertrains": [comment["powertrain"]]},
            evidence_links=[reference(originals[comment["id"]], {})])
        proposal["finding_links"] = [{"signal_id": proposal["signal_id"], "revision": 1}]
        record = service.create_requirement(proposal, "manual-real-export-acceptance")
        expected[record["id"]] = record
    with TestClient(create_app(repo, evidence_provider=resolver, source_provider=sources)) as client:
        listing = client.get("/api/requirements", params={"limit": 200})
        assert listing.status_code == 200
        assert len(listing.json()["items"]) == 200 and listing.json()["total"] == 250
        result = client.get("/api/requirements/export", params={"format": "json"})
        assert result.status_code == 200
        assert {r["id"] for r in result.json()} == set(expected)
        for exported_record in result.json():
            assert all(exported_record[key] == value for key, value in expected[exported_record["id"]].items())
            assert exported_record["history"]["revisions"] == [expected[exported_record["id"]]]
            assert any(e["action"] == "REQUIREMENT_DERIVED" for e in exported_record["history"]["events"])
            assert exported_record["decision"] is None
        result = client.get("/api/requirements/export", params={"format": "csv"})
        assert result.status_code == 200
        exported = list(csv.DictReader(io.StringIO(result.text)))
        assert len(exported) == 250
        for row in exported:
            original = expected[row["id"]]
            assert json.loads(row["source_references"]) == original["evidence_links"]
            assert json.loads(row["source_snapshots"]) == original["source_snapshots"]
            assert json.loads(row["assumptions"]) == original["assumptions"]
            assert json.loads(row["score_factors"]) == original["score"]["factors"]
            assert row["description"] == original["description"]
    assert verify_chain(repo.read()["events"], repo.read()["checkpoint"])


def test_fixed_real_sales_baseline_is_complete_and_drift_requires_migration(real_governance):
    repo, service, record, draft, _, bundle = real_governance
    baseline = record["score_context"]["reference_baseline"]
    sales = [f for f in bundle.facts if f.get("kind") == "sales" and f.get("year") == 2030]
    assert baseline["total_volume"] == sum({(f["vehicle_line"], f["region"]): f["volume"] for f in sales}.values())
    assert record["score_context"]["reference_volume"] == baseline["total_volume"]
    assert {f["id"] for entry in baseline["entries"] for f in entry["facts"]} == {f["id"] for f in sales}
    wrong = GovernanceService(repo, RealEvidenceResolver(service.evidence_provider.sources,
        service.evidence_provider.evidence, fixed_reference_volume=baseline["total_volume"] + 1))
    before = repo.read()
    with pytest.raises(DomainError) as error:
        wrong.context(draft)
    assert error.value.status == 422
    fact = sales[0]
    try:
        with repo.driver.session() as session:
            session.run("MATCH (f:Fact {id:$id}) SET f.volume=$volume", id=fact["id"], volume=fact["volume"] + 1).consume()
        with pytest.raises(DomainError) as error:
            service.decide_requirement(record["id"], pm(record))
        assert error.value.status in {409, 422}
        fresh = GovernanceService(repo, RealEvidenceResolver(service.evidence_provider.sources, service.evidence_provider.evidence))
        with pytest.raises(DomainError):
            fresh.context(draft)
        assert repo.read() == before
    finally:
        with repo.driver.session() as session:
            session.run("MATCH (f:Fact {id:$id}) SET f.volume=$volume", id=fact["id"], volume=fact["volume"]).consume()


def forecast_proxy(draft, model, source=None, assumption_id=None):
    source = source or model
    return {"id": assumption_id or "whole-line-proxy-" + model,
        "description": "Use sourced whole-line 2030 volume as an explicitly provisional scope proxy",
        "review_state": "pending", "forecast_proxy": {"policy_version": "whole-line-proxy-v1",
            "provenance": "PMEntered", "actor_id": "real-acceptance-pm", "rationale":
            "No derivative or powertrain split is supplied; review the whole-line proxy explicitly",
            "target_derivative": model, "source_derivative": source,
            "vehicle_line": "G60_G68" if source in {"G60", "G68"} else source,
            "regions": list(draft["target_scope"]["regions"]),
            "powertrains": list(draft["target_scope"]["powertrains"])}}


def test_real_whole_line_proxy_reach_requires_acceptance_of_specific_policy(real_governance):
    repo, service, record, draft, _, bundle = real_governance
    model = draft["target_scope"]["derivatives"][0]
    proxy = forecast_proxy(draft, model)
    edited = service.edit_requirement(record["id"], EditRequest(expected_revision=record["revision"],
        actor_id="real-pm", actor_role="PM_USER", rationale="Propose explicit whole-line forecast proxy",
        patch={"assumptions": draft["assumptions"] + [proxy]}))
    required = edited["required_assumption_ids"]
    assert required == [proxy["id"]]
    reach = edited["score"]["factors"]["reach"]
    source_facts = [f for f in bundle.facts if f.get("kind") == "sales" and f.get("year") == 2030
                    and f.get("vehicle_line") == proxy["forecast_proxy"]["vehicle_line"]
                    and f.get("region") in draft["target_scope"]["regions"]]
    assert source_facts
    assert reach["value"] == pytest.approx(sum(f["volume"] for f in source_facts) / edited["score_context"]["reference_volume"])
    assert reach["references"]
    assert reach["provisional"] and reach["lower"] == 0
    assert reach["upper"] == reach["value"]
    assert any("neither derivative nor powertrain splits" in warning for warning in edited["score"]["warnings"])
    before = repo.read()
    with pytest.raises(DomainError):
        service.decide_requirement(record["id"], pm(edited))
    assert repo.read() == before
    approved = service.decide_requirement(record["id"], pm(edited,
        accepted_assumption_ids=["assumption-1", proxy["id"]]))
    frozen = service.freeze(freeze_request(repo, approved))
    assert frozen["requirements"][0]["score_context"]["forecast_proxy_policies"][model]["assumption_id"] == proxy["id"]


@pytest.mark.parametrize("bad", ["ai", "wrong-line", "wrong-region", "missing-powertrain", "missing-rationale"])
def test_real_forecast_proxy_invalid_policy_fails_closed(real_governance, bad):
    repo, service, record, draft, *_ = real_governance
    proxy = forecast_proxy(draft, draft["target_scope"]["derivatives"][0])
    changes = {"ai": {"actor_id": "AI_SYSTEM"}, "wrong-line": {"vehicle_line": "absent"},
               "wrong-region": {"regions": ["CN"]}, "missing-powertrain": {"powertrains": []},
               "missing-rationale": {"rationale": " "}}[bad]
    proxy["forecast_proxy"].update(changes)
    before = repo.read()
    with pytest.raises(DomainError):
        service.edit_requirement(record["id"], EditRequest(expected_revision=record["revision"],
            actor_id="real-pm", actor_role="PM_USER", rationale="Test invalid proxy policy",
            patch={"assumptions": draft["assumptions"] + [proxy]}))
    assert repo.read() == before


def test_unrelated_real_scope_assumption_cannot_authorize_forecast_proxy(real_governance):
    _, service, record, draft, *_ = real_governance
    assert record["score"]["factors"]["reach"]["provisional"]
    draft["assumptions"].append({"id": "unrelated-proxy-description", "description":
        "Whole-line proxy feasibility accepted in general", "review_state": "accepted"})
    context = service.context(draft)
    assert context["score_context"]["forecasts"] == []
    assert context["required_assumption_ids"] == []


def test_real_shared_g60_g68_proxy_deduplicates_whole_line_and_successor_stays_explicit(real_data):
    from services.repository import MemoryRepository
    _, bundle, sources, evidence = real_data
    comment = next(c for c in bundle.feedback_comments if c["model"] == "G60" and c["region"] == "US" and c.get("powertrain"))
    row = next(r for r in bundle.source_records if r.get("feedback_comment_id") == comment["id"])
    proposal = fixture_draft()
    proposal.update(derivation_key="manual-shared-line", signal_id="manual-shared-line-review",
        description=comment["original_text"], problem_statement=comment["original_text"], factor_proposals={},
        source_scope={"derivatives": ["G60", "G68"], "regions": ["US"], "powertrains": [comment["powertrain"]]},
        target_scope={"derivatives": ["G60", "G68"], "regions": ["US"], "powertrains": [comment["powertrain"]]},
        evidence_links=[reference(row, {})])
    proposal["finding_links"] = [{"signal_id": proposal["signal_id"], "revision": 1}]
    proposal["assumptions"] += [forecast_proxy(proposal, "G60"), forecast_proxy(proposal, "G68")]
    service = GovernanceService(MemoryRepository(), RealEvidenceResolver(sources, evidence))
    record = service.create_requirement(proposal, "manual-shared-line-acceptance")
    assert record["score_context"]["target_vehicle_lines"] == ["G60_G68"]
    assert len(record["score_context"]["forecasts"]) == 1
    sales = next(f for f in bundle.facts if f.get("kind") == "sales" and f.get("year") == 2030
                 and f.get("vehicle_line") == "G60_G68" and f.get("region") == "US")
    assert record["score"]["factors"]["reach"]["target_volume"] == sales["volume"]
    proposal["derivation_key"] = "manual-successor-line"
    proposal["target_scope"]["derivatives"] = ["G_NEXT"]
    proxy = forecast_proxy(proposal, "G_NEXT", source="G60", assumption_id="successor-proxy")
    proposal["assumptions"] = [proposal["assumptions"][0], proxy]
    proposal["target_scope"]["successor_mappings"] = [{"source_derivative": "G60", "target_derivative": "G_NEXT", "assumption_id": proxy["id"]}]
    successor = service.create_requirement(proposal, "manual-successor-acceptance")
    assert successor["required_assumption_ids"] == ["successor-proxy"]
    assert successor["score_context"]["target_vehicle_lines"] == ["G60_G68"]
    assert successor["score"]["factors"]["reach"]["target_volume"] == sales["volume"]
    assert "successor forecasts" in " ".join(successor["score"]["warnings"])
    with pytest.raises(DomainError):
        service.decide_requirement(successor["id"], pm(successor))
    approved = service.decide_requirement(successor["id"], pm(successor,
        accepted_assumption_ids=["assumption-1", "successor-proxy"]))
    assert approved["status"] == "APPROVED"


@pytest.mark.parametrize("field,value", [("original_text", "invented customer evidence"), ("model", "G68")])
def test_same_version_feedback_metadata_corruption_never_becomes_authoritative(real_governance, field, value):
    repo, service, record, draft, row, _ = real_governance
    feedback_id = row["feedback_comment_id"]
    with repo.driver.session() as session:
        original = session.run(f"MATCH (fb:CustomerFeedback {{id:$id}}) RETURN fb.{field} AS value",
                               id=feedback_id).single()["value"]
    before = repo.read()
    try:
        with repo.driver.session() as session:
            session.run(f"MATCH (fb:CustomerFeedback {{id:$id}}) SET fb.{field}=$value",
                        id=feedback_id, value=value).consume()
        with pytest.raises(DomainError):
            service.decide_requirement(record["id"], pm(record))
        with pytest.raises(DomainError):
            GovernanceService(repo, RealEvidenceResolver(service.evidence_provider.sources,
                service.evidence_provider.evidence)).context(draft)
        assert repo.read() == before
    finally:
        with repo.driver.session() as session:
            session.run(f"MATCH (fb:CustomerFeedback {{id:$id}}) SET fb.{field}=$value",
                        id=feedback_id, value=original).consume()
