"""An unchanged trusted checkpoint must reject unaudited immutable additions."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from conftest import FakeEvidence, decision, fixture_draft
from models import DecisionRequest
from services.api import create_app
from services.repository import MemoryRepository
from test_neo4j_integration import graph_backend, graph_snapshot


def inject_unaudited(state, target, record):
    if target == "revision":
        snapshot = dict(copy.deepcopy(record), revision=2, status="APPROVED", approved_revision=2,
                        title="Unaudited approval")
        state["requirements"][record["id"]] = snapshot
        state["revisions"][record["id"]].append(copy.deepcopy(snapshot))
        return "RequirementRevision", snapshot
    if target == "config":
        snapshot = dict(copy.deepcopy(state["configs"]["v1"]), id="unaudited-policy")
        state["configs"][snapshot["id"]] = snapshot
        return "ScoringConfig", snapshot
    if target == "ranking":
        snapshot = dict(copy.deepcopy(state["rankings"][-1]), id="unaudited-ranking")
        state["rankings"].append(snapshot)
        return "RankingSnapshot", snapshot
    snapshot = {"id": "unaudited-freeze", "requirements": [copy.deepcopy(record)]}
    state["freezes"][snapshot["id"]] = snapshot
    return "FreezeSnapshot", snapshot


@pytest.mark.parametrize("target", ["revision", "config", "ranking", "freeze"])
def test_repository_rejects_unaudited_immutable_additions_atomically(backend, target):
    _, repo, _, record = backend
    before = repo.read()
    with pytest.raises(ValueError, match="no checkpoint-protected audit event"):
        repo.write(lambda state: inject_unaudited(state, target, record))
    assert repo.read() == before


@pytest.mark.parametrize("target", ["revision", "config", "ranking", "freeze"])
def test_real_matching_graph_and_ledger_additions_cannot_bypass_checkpoint(graph_backend, target):
    repo, _, record = graph_backend
    state = repo.read()
    retained_checkpoint = copy.deepcopy(state["checkpoint"])
    label, snapshot = inject_unaudited(state, target, record)
    assert state["checkpoint"] == retained_checkpoint
    def attack(tx):
        tx.run("MATCH (l:GovernanceLedger {backend_key:$key}) SET l.payload=$payload",
               key=repo.namespace, payload=json.dumps(state)).consume()
        if target == "revision":
            tx.run("MATCH (r:Requirement {backend_key:$reqkey}) SET r.payload=$payload, r.status=$status, r.revision=$revision "
                   "CREATE (v:RequirementRevision {backend_key:$revkey, payload:$payload, req_id:$id, revision:$revision}) "
                   "CREATE (r)-[:HAS_REVISION]->(v)", reqkey=f"{repo.namespace}:{record['id']}",
                   revkey=f"{repo.namespace}:{record['id']}:2", id=record['id'], revision=2,
                   status="APPROVED", payload=json.dumps(snapshot)).consume()
        else:
            tx.run(f"CREATE (n:{label} {{backend_key:$key, payload:$payload}})",
                   key=f"{repo.namespace}:{snapshot['id']}", payload=json.dumps(snapshot)).consume()
    with repo.session() as session:
        session.execute_write(attack)
    before = graph_snapshot(repo)
    for operation in (repo.read, lambda: repo.write(lambda candidate: None)):
        with pytest.raises(ValueError, match="no checkpoint-protected audit event"):
            operation()
        assert graph_snapshot(repo) == before


@pytest.mark.parametrize("bad", [None, {"title": "incomplete"}, {"factor_proposals": {"severity_level": float("nan")}}])
@pytest.mark.parametrize("envelope", [False, True])
def test_analysis_preserves_valid_siblings_when_one_draft_is_invalid(bad, envelope):
    valid = fixture_draft()
    if isinstance(bad, dict) and "factor_proposals" in bad:
        bad = dict(copy.deepcopy(valid), **bad)
    class Engine:
        def analyze(self, scope):
            drafts = [valid, bad]
            return {"drafts": drafts, "model_metadata": {"model": "synthetic"}} if envelope else drafts
    repo = MemoryRepository()
    with TestClient(create_app(repo, FakeEvidence(), engine=Engine())) as client:
        admitted = client.post("/api/analysis-runs", json={}).json()
        run = client.get(f"/api/analysis-runs/{admitted['id']}").json()
    assert run["state"] == "partial"
    assert len(run["result_ids"]) == 1
    assert len(run["failures"]) == 1
    assert len(repo.read()["requirements"]) == 1
    if envelope:
        assert run["model_metadata"] == {"model": "synthetic"}


@pytest.mark.parametrize("tampering", ["duplicate_old_baseline", "reorder_baselines"])
def test_real_historical_ranking_cannot_become_current_without_audit(graph_backend, tampering):
    repo, service, record = graph_backend
    service.decide_requirement(record["id"], DecisionRequest(**decision(action="start_review")))
    state = repo.read()
    if tampering == "duplicate_old_baseline":
        state["rankings"].append(copy.deepcopy(state["rankings"][0]))
    else:
        state["rankings"].reverse()
    # Copies already match every ranking identity; only the sequence changed.
    with repo.session() as session:
        session.run("MATCH (l:GovernanceLedger {backend_key:$key}) SET l.payload=$payload",
                    key=repo.namespace, payload=json.dumps(state)).consume()
    before = graph_snapshot(repo)
    for operation in (repo.read, lambda: repo.write(lambda candidate: None)):
        with pytest.raises(ValueError, match="Ranking chronology"):
            operation()
        assert graph_snapshot(repo) == before
