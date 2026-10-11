"""Client normalisation and error parsing (no network)."""
import sys
import types

import httpx
import pytest

import ui.client as client_mod
from ui.client import ApiClient, ApiError, normalize_detail, normalize_status, normalize_summary, parse_error


def _fake_config(monkeypatch, **values):
    """Stand-in for the root config.py, so tests don't need python-dotenv or a .env file."""
    monkeypatch.setitem(sys.modules, "config", types.SimpleNamespace(**values))


def test_base_url_prefers_config_api_base_url(monkeypatch):
    _fake_config(monkeypatch, API_BASE_URL="http://api.internal:9000/", API_PORT=8000)
    assert client_mod.resolve_base_url() == "http://api.internal:9000"


def test_base_url_env_var_beats_port(monkeypatch):
    _fake_config(monkeypatch, API_PORT=8000)
    monkeypatch.setenv("API_BASE_URL", "http://192.168.1.20:8000")
    assert client_mod.resolve_base_url() == "http://192.168.1.20:8000"


def test_base_url_falls_back_to_api_port(monkeypatch):
    monkeypatch.delenv("API_BASE_URL", raising=False)
    _fake_config(monkeypatch, API_PORT=8123)
    assert client_mod.resolve_base_url() == "http://localhost:8123"


def test_gate_and_gate_color_are_unified():
    a = normalize_summary({"req_id": "R1", "title": "t", "gate": "GREEN", "status": "DRAFT"})
    b = normalize_detail({"req_id": "R1", "title": "t", "gate_color": "GREEN", "status": "DRAFT"})
    assert a["gate"] == b["gate"] == "GREEN"


def test_conflicted_maps_to_review_with_flag():
    assert normalize_status("CONFLICTED") == ("PM_REVIEW", True)
    row = normalize_summary({"req_id": "R1", "status": "CONFLICTED"})
    assert row["status"] == "PM_REVIEW" and row["has_conflict"]


def test_legacy_detail_is_labelled_not_upgraded():
    d = normalize_detail({"req_id": "R1", "title": "t", "bmw_priority": 7.3, "bmw_priority_low": 6.2,
                          "bmw_priority_high": 8.4, "status": "DRAFT",
                          "explainability": {"verbatim_samples": ["quote"]}})
    assert d["legacy"] and d["score"]["scale"]["max"] == 10
    assert "not a statistical confidence interval" in d["score"]["range"]["label"]
    assert d["evidence"]["quotes"][0]["validation"] == "unverified"


def _resp(status, body):
    return httpx.Response(status, json=body, request=httpx.Request("POST", "http://x"))


def test_parse_409_envelope_keeps_current_revision():
    err = parse_error(_resp(409, {"error": {"code": "stale_revision", "message": "Changed",
                                            "current_revision": 4}}))
    assert err.is_conflict and err.current_revision == 4


def test_parse_422_fastapi_detail_lists_problems():
    err = parse_error(_resp(422, {"detail": [{"loc": ["body", "rationale"], "msg": "field required"}]}))
    assert err.is_validation and err.problems == ["rationale: field required"]


def test_unreachable_is_an_error_not_empty(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(httpx, "request", boom)
    c = ApiClient(base_url="http://nowhere", mode="v1")
    with pytest.raises(ApiError) as exc:
        c.list_requirements()
    assert exc.value.is_unreachable


def test_v1_decision_payload_zero_override_and_absent_when_disabled(monkeypatch):
    sent = []

    def fake(method, url, params=None, json=None, timeout=None):
        sent.append(json)
        return httpx.Response(200, json={"ok": True}, request=httpx.Request(method, url))
    monkeypatch.setattr(httpx, "request", fake)
    c = ApiClient(base_url="http://x", mode="v1")
    c.decide("R1", "approve", 2, "pm", "why", ["A-1"], ["T-1"], score_override=0.0, override_enabled=True,
             reviewed_source_ids=["S-1"])
    c.decide("R1", "approve", 2, "pm", "why", [], [], score_override=50, override_enabled=False)
    assert sent[0]["score_override"] == 0.0          # a plain number, as DecisionRequest defines it
    assert sent[0]["accepted_assumption_ids"] == ["A-1"] and sent[0]["reviewed_target_ids"] == ["T-1"]
    assert sent[0]["reviewed_source_ids"] == ["S-1"] and sent[0]["actor_role"] == "PM_USER"
    assert "score_override" not in sent[1]           # absent keeps the score; null would clear an override


def test_legacy_export_refuses_instead_of_capping(monkeypatch):
    c = ApiClient(base_url="http://x", mode="legacy")
    with pytest.raises(ApiError) as exc:
        c.download("sample_list", "csv")
    assert exc.value.status == 501
