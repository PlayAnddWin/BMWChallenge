import copy
import pytest
from models import DecisionRequest, ApplyRequest
from services.audit import verify_chain
from services.decisions import DomainError
from conftest import decision


@pytest.mark.parametrize("changes", [{"actor_role": "AI_SYSTEM"}, {"rationale": "   "}, {"id": "wrong"}, {"score_override": -1}, {"score_override": 999}, {"reviewed_target_ids": []}, {"accepted_assumption_ids": []}, {"reviewed_source_ids": []}])
def test_invalid_approval_no_writes(backend, changes):
    client, repo, service, record = backend
    before = repo.read()
    response = client.post(f"/api/requirements/{record['id']}/decisions", json=decision(**changes))
    assert response.status_code == 422, response.text
    assert repo.read() == before


def test_approve_stale_edit_reopen_and_zero_override(backend):
    client, repo, service, record = backend
    url = f"/api/requirements/{record['id']}"
    approved = client.post(url+"/decisions", json=decision(score_override=0))
    assert approved.status_code == 200, approved.text
    assert approved.json()["score"]["effective_score"] == 0
    assert approved.json()["approved_revision"] == 2
    assert client.post(url+"/decisions", json=decision(2)).status_code == 422
    assert client.post(url+"/decisions", json=decision()).status_code == 409
    edit = {"expected_revision": 2, "actor_id": "pm-test", "actor_role": "PM_USER", "rationale": "Clarified target", "patch": {"title": "Accessible cabin controls"}}
    response = client.patch(url, json=edit)
    assert response.status_code == 200, response.text
    changed = response.json()
    assert changed["status"] == "PM_REVIEW" and changed["approved_revision"] is None
    assert changed["score"]["override"] is None
    assert repo.read()["revisions"][record["id"]][1]["status"] == "APPROVED"
    assert verify_chain(repo.read()["events"], repo.read()["checkpoint"])


def test_atomic_audit_failure(backend):
    client, repo, service, record = backend
    before = repo.read()
    def fail(action):
        if action == "RANKING_RECALCULATED":
            raise RuntimeError("injected audit failure")
    service.audit_failure = fail
    with pytest.raises(RuntimeError, match="injected"):
        service.decide_requirement(record["id"], DecisionRequest(**decision(score_override=0)))
    assert repo.read() == before


def test_regeneration_idempotent_apply_and_rollback(backend, draft):
    client, repo, service, record = backend
    service.decide_requirement(record["id"], DecisionRequest(**decision()))
    draft["title"] = "Regenerated new proposal"
    pending = service.create_requirement(draft, "run-2")
    assert repo.read()["requirements"][record["id"]]["status"] == "APPROVED"
    assert repo.read()["requirements"][record["id"]]["title"] == record["title"]
    request = ApplyRequest(turn_id=pending["pending_turn_id"], expected_revision=2, actor_id="pm-test", actor_role="PM_USER", rationale="Apply regenerated title", patch={"title": draft["title"]})
    before = repo.read()
    service.audit_failure = lambda action: (_ for _ in ()).throw(RuntimeError("fail"))
    with pytest.raises(RuntimeError):
        service.edit_requirement(record["id"], request, apply=True)
    assert repo.read() == before
    service.audit_failure = None
    applied = service.edit_requirement(record["id"], request, apply=True)
    retry = service.edit_requirement(record["id"], request, apply=True)
    assert applied == retry and applied["status"] == "PM_REVIEW"
    assert len(repo.read()["revisions"][record["id"]]) == 3
    changed = request.model_copy(update={"patch": {"title": "Conflicting retry"}})
    with pytest.raises(DomainError) as exc:
        service.edit_requirement(record["id"], changed, apply=True)
    assert exc.value.status == 409


def test_freeze_snapshot_and_stale_baseline(backend):
    client, repo, service, record = backend
    rid = record["id"]
    service.decide_requirement(rid, DecisionRequest(**decision()))
    request = {"selections": {rid: 2}, "expected_ranking_id": repo.read()["rankings"][-1]["id"], "actor_id": "pm-test", "actor_role": "PM_USER", "rationale": "Freeze reviewed scope"}
    bad = dict(request, expected_ranking_id="stale")
    assert client.post("/api/freezes", json=bad).status_code == 409
    result = client.post("/api/freezes", json=request)
    assert result.status_code == 200, result.text
    frozen = result.json()
    service.decide_requirement(rid, DecisionRequest(**decision(2, "reopen")))
    assert client.get(f"/api/freezes/{frozen['id']}").json() == frozen
    bundle = client.get(f"/api/freezes/{frozen['id']}/audit").json()
    assert verify_chain(bundle["events"], frozen["checkpoint"])
    request.update(expected_ranking_id=repo.read()["rankings"][-1]["id"], selections={rid: 3})
    assert client.post("/api/freezes", json=request).status_code == 409


@pytest.mark.parametrize("value", [-1, 5, float("nan"), float("inf")])
def test_invalid_model_factors_fail_at_boundary(backend, draft, value):
    _, repo, service, _ = backend
    draft["factor_proposals"]["severity_level"] = value
    before = repo.read()
    with pytest.raises(ValueError):
        service.create_requirement(draft, "invalid")
    assert repo.read() == before


def test_legacy_unverified_and_missing_sources_block(backend, draft):
    client, repo, service, record = backend
    draft["evidence_links"][0]["record_id"] = "invented"
    with pytest.raises(DomainError):
        service.create_requirement(draft, "invalid")
    legacy = service.import_legacy([{"req_id": "unverified-legacy", "impact": 5, "market_reach_norm": 5, "effort": 5}])[0]
    assert client.post(f"/api/requirements/{legacy['id']}/decisions", json=decision()).status_code == 422


def test_complete_export_and_routes(backend):
    client, repo, service, record = backend
    def many(state):
        for number in range(250):
            replica = dict(copy.deepcopy(record), id=f"fixture-{number}")
            state["requirements"][replica["id"]] = replica
            state["revisions"][replica["id"]] = [copy.deepcopy(replica)]
            service.event(state, "REQUIREMENT_DERIVED", replica["id"], None, replica, ("fixture", "AI_SYSTEM"), "Synthetic export fixture")
    repo.write(many)
    assert len(client.get("/api/requirements/export").json()) == 251
    assert len(client.get("/api/requirements?limit=200").json()["items"]) == 200
    assert client.get(f"/requirements/{record['id']}").status_code == 200
    assert client.get("/api/sources").status_code == 503
    run = client.post("/api/analysis-runs", json={}).json()
    assert run["state"] == "failed" and run["mode"] == "unavailable"


def test_legacy_route_mismatch_and_expected_revision_required(backend):
    client, repo, service, record = backend
    body = decision()
    body.pop("action")
    body["req_id"] = "wrong"
    body["to_status"] = "APPROVED"
    body["actor"] = body.pop("actor_id")
    url = f"/requirements/{record['id']}/transition"
    assert client.post(url, json=body).status_code == 422
    body["req_id"] = record["id"]
    body.pop("expected_revision")
    assert client.post(url, json=body).status_code == 422
