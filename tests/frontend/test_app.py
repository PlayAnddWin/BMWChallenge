"""Streamlit AppTest: render-level guarantees of the PM workbench."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from ui.fixtures import FixtureClient

APP = str(Path(__file__).resolve().parents[2] / "pipeline" / "08_streamlit_ui.py")
MUTATIONS = {"decide", "edit", "challenge", "apply_suggestion", "discard_suggestion", "start_analysis",
             "create_freeze"}


def run(client, **state):
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["client"] = client
    state.setdefault("page", "Requirements")
    for k, v in state.items():
        at.session_state[k] = v
    return at.run()


def markdown(at) -> str:
    return "\n".join(m.value for m in at.markdown)


def test_list_renders_without_mutation_and_labels_synthetic():
    c = FixtureClient()
    at = run(c)
    assert not at.exception
    assert "SYNTHETIC PREVIEW" in markdown(at)
    assert not [x for x in c.calls if x[0] in MUTATIONS]


@pytest.mark.parametrize("rid", ["REQ-FIX-001", "REQ-FIX-002", "REQ-FIX-003", "REQ-FIX-004", "REQ-F70-001"])
@pytest.mark.parametrize("tab", ["Requirement", "Priority", "Evidence", "Assistant", "History"])
def test_every_workbench_tab_renders_without_mutation(rid, tab):
    c = FixtureClient()
    at = run(c, selected=rid, **{f"tab:{rid}": tab})
    assert not at.exception, at.exception
    assert not [x for x in c.calls if x[0] in MUTATIONS]


def test_only_server_allowed_actions_are_offered():
    at = run(FixtureClient(), selected="REQ-FIX-004")       # APPROVED: only reopen
    labels = {b.label for b in at.button}
    assert "Reopen" in labels and not {"Approve", "Reject", "Defer"} & labels


def test_range_is_never_called_a_confidence_interval():
    at = run(FixtureClient(), selected="REQ-FIX-001", **{"tab:REQ-FIX-001": "Priority"})
    text = markdown(at)
    assert "not a statistical confidence interval" in text


def test_legacy_record_is_labelled():
    at = run(FixtureClient(), selected="REQ-F70-001", **{"tab:REQ-F70-001": "Priority"})
    assert "Legacy record" in markdown(at) and "not comparable with v1" in markdown(at)


def test_empty_history_is_not_proof():
    at = run(FixtureClient(), selected="REQ-FIX-002", **{"tab:REQ-FIX-002": "History"})
    assert "empty history is not proof" in markdown(at)


def test_evidence_gap_is_shown():
    at = run(FixtureClient(), selected="REQ-FIX-002", **{"tab:REQ-FIX-002": "Evidence"})
    assert "No customer comments support this requirement" in markdown(at)
    assert "not zero demand" in markdown(at)


def test_api_failure_is_not_an_empty_result():
    from ui.client import ApiClient
    at = run(ApiClient(base_url="http://127.0.0.1:9", mode="v1", timeout=1))
    assert "not reachable" in markdown(at)
    assert "nothing to show" not in markdown(at)


def test_submit_decision_sends_reviews_and_zero_override():
    from ui.forms import build_patch, submit_decision
    c = FixtureClient()
    req = c.get_requirement("REQ-FIX-001")
    submit_decision(c, req, "approve", actor="pm", rationale="ok", reviewed=["T-1", "M-1"],
                    accepted=["A-1", "A-2"], override_enabled=True, override_value=0.0)
    call = [x for x in c.calls if x[0] == "decide"][-1]
    assert call[2] == "approve" and call[3] == 2 and call[8] == 0.0 and call[9] is True
    assert c.get_requirement("REQ-FIX-001")["score"]["effective"] == 0.0
    patch = build_patch(req, {"title": req["title"], "description": req["description"],
                              "measure_value": "3", "regions": ["US"]})
    assert patch["smart.measure"]["provenance"] == "PMEntered"
    assert patch["target_scope"]["regions"] == ["US"] and "title" not in patch


def test_overview_renders_charts_without_mutation():
    c = FixtureClient()
    at = run(c, page="Overview")
    assert not at.exception, at.exception
    assert "Priority vs evidence" in markdown(at)
    assert not [x for x in c.calls if x[0] in MUTATIONS]


def test_snapshot_mode_is_read_only_and_labelled(tmp_path):
    import json
    from ui.snapshot import SnapshotClient
    rec = {"requirement": {"req_id": "REQ-T-001", "model": "G60", "title": "T", "bmw_priority": 7.5,
                           "bmw_priority_low": 7.0, "bmw_priority_high": 8.0, "status": "CONFLICTED",
                           "evidence_tier": "STRONG", "gate_color": "GREEN", "data_gaps": []},
           "source_signal": {"feedback_count": 12, "verbatim_samples": ["q"]}, "history": []}
    f = tmp_path / "requirements_full.json"
    f.write_text(json.dumps({"exported_at": "2026-10-10T15:59:38+00:00", "total": 1, "requirements": [rec]}))
    snap = SnapshotClient(f)
    r = snap.get_requirement("REQ-T-001")
    assert r["legacy"] and r["status"] == "PM_REVIEW" and r["has_conflict"] and r["allowed_actions"] == []
    with pytest.raises(Exception):
        snap.decide("REQ-T-001", "approve", None, "pm", "why")
    at = run(snap, page="Overview")
    assert not at.exception, at.exception
    at = run(snap, selected="REQ-T-001", **{"tab:REQ-T-001": "Evidence"})
    assert not at.exception, at.exception
    assert "Not verified" in markdown(at)
