"""The UI against Role 3's contract: contracts/openapi.json and contracts/fixture_case.json (origin/backend)."""
import copy
import json
from pathlib import Path

import httpx
import pytest

from .test_app import MUTATIONS, markdown, run
from ui.client import (ApiClient, ApiError, decision_payload, freeze_payload, normalize_detail,
                       normalize_evidence_response, normalize_history, normalize_list, normalize_thread)
from ui.contract_mock import ContractMockClient

ROOT = Path(__file__).resolve().parents[2]
SPEC = json.loads((ROOT / "contracts" / "openapi.json").read_text(encoding="utf-8"))
FX = json.loads((ROOT / "contracts" / "fixture_case.json").read_text(encoding="utf-8"))
RID = FX["requirement_detail_example"]["id"]
TABS = ["Requirement", "Priority", "Evidence", "Assistant", "History"]


def schema(name):
    return SPEC["components"]["schemas"][name]


def assert_matches(body: dict, name: str):
    """Required fields present, no unknown fields, enum/const/minimum/minLength respected."""
    s = schema(name)
    assert set(s["required"]) <= set(body), f"missing {set(s['required']) - set(body)}"
    assert set(body) <= set(s["properties"]), f"unknown {set(body) - set(s['properties'])}"
    for key, value in body.items():
        p = s["properties"][key]
        if "enum" in p:
            assert value in p["enum"], (key, value)
        if "const" in p:
            assert value == p["const"], (key, value)
        if "minLength" in p:
            assert isinstance(value, str) and len(value) >= p["minLength"], key
        if "minimum" in p:
            assert isinstance(value, int) and value >= p["minimum"], key
        if p.get("type") == "array":
            assert isinstance(value, list) and all(isinstance(v, str) for v in value), key


# ---------------------------------------------------------------------------- normalisation
def test_detail_example_normalises_to_server_values():
    d = normalize_detail(copy.deepcopy(FX["requirement_detail_example"]))
    assert d["score"]["effective"] == 55.06394529484361
    assert d["score"]["calculated"] == 55.06394529484361
    assert d["evidence"]["eci"] == 67.27953594466527 and d["evidence"]["level"] == "Moderate"
    assert d["score"]["scale"] == {"policy": "v1", "min": 0, "max": 100, "label": "Priority v1 (0–100)"}
    keys = [f["key"] for f in d["score"]["factors"]]
    assert keys == ["customer", "reach", "strategy", "ease"]
    customer = d["score"]["factors"][0]
    assert customer["contribution"] == 25.063945294843613 and customer["weight"] == 0.4
    assert {"key", "label", "value", "weight", "contribution", "provisional", "reason", "lower", "upper"} <= set(customer)
    assert "not a statistical confidence interval" in d["score"]["range"]["label"]
    assert d["evidence"]["distinct_support_comments"] == 2
    assert d["evidence"]["warnings"] == ["Low distinct customer support"]


def test_targets_assumptions_and_actions():
    d = normalize_detail(copy.deepcopy(FX["requirement_detail_example"]))
    targets = d["smart"]["targets"]
    assert [t["id"] for t in targets] == ["metric-1", "milestone-1"]
    assert all(t["review_state"] == "unreviewed" for t in targets)      # pending -> "Not reviewed"
    assert targets[0]["value"] == "2" and targets[0]["verification"] == "Usability test"
    a = d["assumptions"][0]
    assert a["text"] == "UI feasibility requires validation" and a["state"] == "open"   # pending -> Open
    assert d["allowed_actions"] == ["start_review", "approve", "reject", "defer", "reopen"]
    quote = d["evidence"]["quotes"][0]
    assert quote["id"] == "feedback-1" and quote["text"] == "Synthetic comment about temperature-control access"
    assert quote["validation"] == "verified" and quote["relation"] == "supports"


def test_list_keeps_ranking_id():
    page = normalize_list(copy.deepcopy(FX["list_example"]))
    assert page["ranking_id"] == FX["list_example"]["ranking_id"]
    assert page["total"] == 1 and page["items"][0]["score"]["effective"] == 55.06394529484361


def test_history_events_are_mapped():
    events = normalize_history(copy.deepcopy(FX["history_example"]), legacy=False)
    assert len(events) == len(FX["history_example"]["events"])
    first = events[0]
    assert first["time"] == FX["history_example"]["events"][0]["timestamp"] and first["actor_kind"] == "AI_SYSTEM"
    status = next(e for e in events if e["action"] == "STATUS_CHANGED")
    assert status["actor_kind"] == "PM_USER" and status["summary"] == "Proposed → Approved"


def test_evidence_and_thread_responses():
    ev = normalize_evidence_response(copy.deepcopy(FX["evidence_example"]))
    assert ev["eci"] == 67.27953594466527 and ev["quotes"][0]["source_record_id"] == "feedback-1"
    th = normalize_thread(copy.deepcopy(FX["review_thread_example"]),
                          copy.deepcopy(FX["proposal_list_example"]["items"]), RID)
    assert [t["id"] for t in th["turns"]][0] == FX["review_thread_example"]["turns"][0]["id"]
    assert len(th["turns"]) == 2                      # the thread turn plus the regenerated proposal
    turn = th["turns"][0]
    assert turn["citations"] == ["feedback-1"] and turn["patch"] == {"title": "Reviewed synthetic controls"}
    assert turn["proposed_patch"]["title"]["after"] == "Reviewed synthetic controls"


# ---------------------------------------------------------------------------- request bodies
def _capture(monkeypatch):
    sent = []

    def fake(method, url, params=None, json=None, timeout=None):
        sent.append((method, url, json))
        return httpx.Response(200, json={"id": "F-1", "revision": 2, "status": "APPROVED"},
                              request=httpx.Request(method, url))
    monkeypatch.setattr(httpx, "request", fake)
    return sent


def test_decision_payload_matches_decision_request(monkeypatch):
    sent = _capture(monkeypatch)
    c = ApiClient(base_url="http://x", mode="v1")
    c.decide(RID, "approve", 1, "pm-test", "Reviewed original synthetic evidence", ["assumption-1"],
             ["metric-1", "milestone-1"], score_override=0.0, override_enabled=True, reviewed_source_ids=["feedback-1"])
    method, url, body = sent[-1]
    assert method == "POST" and url.endswith(f"/api/requirements/{RID}/decisions")
    assert_matches(body, "DecisionRequest")
    assert body["actor_role"] == "PM_USER" and body["score_override"] == 0.0
    example = FX["decision_request_example"]
    without_override = {k: v for k, v in body.items() if k != "score_override"}
    assert without_override == example                # byte-for-byte the backend's own example
    c.decide(RID, "reject", 1, "pm-test", "why")
    assert "score_override" not in sent[-1][2]


def test_freeze_payload_matches_freeze_request(monkeypatch):
    sent = _capture(monkeypatch)
    c = ApiClient(base_url="http://x", mode="v1")
    example = FX["freeze_request_example"]
    res = c.create_freeze([{"id": k, "revision": v} for k, v in example["selections"].items()],
                          example["expected_ranking_id"], example["actor_id"], example["rationale"])
    method, url, body = sent[-1]
    assert method == "POST" and url.endswith("/api/freezes")
    assert_matches(body, "FreezeRequest")
    assert all(isinstance(v, int) for v in body["selections"].values())
    assert body == example and res["freeze_id"] == "F-1"


def test_mutation_bodies_carry_actor_role(monkeypatch):
    sent = _capture(monkeypatch)
    c = ApiClient(base_url="http://x", mode="v1")
    c.apply_suggestion(RID, "turn-1", 1, {"title": "x"}, "pm", "ok")
    assert_matches(sent[-1][2], "ApplyRequest")
    c.discard_suggestion("thread-1", "turn-1", "pm", "no", rid=RID, expected_revision=1)
    assert sent[-1][1].endswith(f"/api/requirements/{RID}/proposals/turn-1/discard")
    assert_matches(sent[-1][2], "MutationRequest")


# ---------------------------------------------------------------------------- contract mock
def test_mock_serves_contract_through_normalisers():
    m = ContractMockClient()
    page = m.list_requirements()
    assert page["ranking_id"] == FX["list_example"]["ranking_id"] and page["items"][0]["id"] == RID
    d = m.get_requirement(RID)
    assert d["score"]["effective"] == 55.06394529484361 and d["status"] == "DRAFT" and d["revision"] == 1
    assert m.get_evidence(RID)["eci"] == 67.27953594466527
    # history is replayed only up to the requirement's current revision (1)
    assert all(e["action"] != "STATUS_CHANGED" for e in m.get_history(RID))
    assert len(m.get_thread(RID)["turns"]) == 2
    data, mime, _ = m.download("sample_list", "json")
    assert json.loads(data) == FX["export_example"] and mime == "application/json"


def _approve(m, revision=1, **overrides):
    args = dict(rid=RID, action="approve", expected_revision=revision, actor_id="pm-test",
                rationale="Reviewed original synthetic evidence", accepted_assumption_ids=["assumption-1"],
                reviewed_target_ids=["metric-1", "milestone-1"], reviewed_source_ids=["feedback-1"])
    args.update(overrides)
    return m.decide(**args)


def test_mock_decisions_follow_the_server_rules():
    m = ContractMockClient()
    with pytest.raises(ApiError) as e:
        _approve(m, rationale="   ")
    assert e.value.status == 422
    with pytest.raises(ApiError) as e:
        _approve(m, revision=7)
    assert e.value.status == 409
    with pytest.raises(ApiError) as e:
        _approve(m, reviewed_source_ids=[])
    assert e.value.status == 422 and "source" in e.value.message
    res = _approve(m)
    assert res["revision"] == 2 and res["status"] == "APPROVED" and res["approved_revision"] == 2
    assert_matches(m.calls[-1][1], "DecisionRequest")
    d = m.get_requirement(RID)
    assert all(t["review_state"] == "reviewed" for t in d["smart"]["targets"])
    assert d["assumptions"][0]["state"] == "accepted"
    assert any(e["action"] == "STATUS_CHANGED" for e in m.get_history(RID))
    with pytest.raises(ApiError) as e:                 # approving an approved revision is not a transition
        _approve(m, revision=2)
    assert e.value.status == 422


def test_mock_freeze_needs_current_ranking():
    m = ContractMockClient()
    stale_ranking = m.list_requirements()["ranking_id"]
    _approve(m)
    page = m.list_requirements({"status": "APPROVED"})
    sel = [{"id": RID, "revision": 2}]
    with pytest.raises(ApiError) as e:
        m.create_freeze(sel, stale_ranking, "pm-contract", "Reviewed complete synthetic snapshot")
    assert e.value.status == 409
    res = m.create_freeze(sel, page["ranking_id"], "pm-contract", "Reviewed complete synthetic snapshot")
    assert_matches(m.calls[-1][1], "FreezeRequest")
    assert m.get_freeze(res["freeze_id"])["requirements"][0]["revision"] == 2


def test_mock_override_is_reported_not_calculated():
    m = ContractMockClient()
    m.decide(RID, "start_review", 1, "pm", "go", score_override=0.0, override_enabled=True)
    d = m.get_requirement(RID)
    assert d["score"]["effective"] == 0.0 and d["score"]["calculated"] == 55.06394529484361
    assert d["score"]["override"] == {"value": 0.0}


# ---------------------------------------------------------------------------- rendering in mock mode
@pytest.mark.parametrize("tab", TABS)
def test_every_workbench_tab_renders_in_mock_mode_without_mutation(tab):
    m = ContractMockClient()
    at = run(m, selected=RID, **{f"tab:{RID}": tab})
    assert not at.exception, at.exception
    assert not [x for x in m.calls if x[0] in MUTATIONS]
    assert "BACKEND CONTRACT MOCK (SYNTHETIC)" in markdown(at)


def test_mock_requirement_tab_shows_every_target_and_all_actions():
    m = ContractMockClient()
    at = run(m, selected=RID, **{f"tab:{RID}": "Requirement"})
    text = markdown(at)
    assert "metric-1" in text and "milestone-1" in text and "Not reviewed" in text and "Open" in text
    labels = {b.label for b in at.button}
    assert {"Start review", "Approve", "Reject", "Defer", "Reopen"} <= labels


@pytest.mark.parametrize("page", ["Requirements", "Overview", "Review package"])
def test_mock_pages_render_without_mutation(page):
    m = ContractMockClient()
    at = run(m, page=page)
    assert not at.exception, at.exception
    assert not [x for x in m.calls if x[0] in MUTATIONS]


def test_mock_is_labelled_in_the_sidebar_source_list():
    at = run(ContractMockClient())
    assert "Backend contract mock (synthetic)" in markdown(at)
    assert at.sidebar.selectbox(key="source_choice").value == "mock"
