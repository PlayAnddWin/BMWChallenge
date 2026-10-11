"""Simulated server rules in the synthetic fixture client."""
import csv
import io

import pytest

from ui.client import ApiError
from ui.fixtures import FixtureClient


def test_golden_scores():
    r = FixtureClient().get_requirement("REQ-FIX-001")
    assert r["score"]["effective"] == pytest.approx(55.0639452948)
    assert r["evidence"]["eci"] == pytest.approx(67.2795359447)
    assert r["evidence"]["level"] == "Moderate"


def test_evidence_gap_case_exists():
    r = FixtureClient().get_requirement("REQ-FIX-002")
    assert r["evidence"]["level"] == "Low" and not r["evidence"]["quotes"]
    assert "evidence_gap" in r["flags"] and r["score"]["provisional"]


def test_stale_revision_gives_409():
    with pytest.raises(ApiError) as exc:
        FixtureClient().decide("REQ-FIX-001", "reject", 1, "pm", "why")
    assert exc.value.is_conflict and exc.value.current_revision == 2


def test_approval_needs_review_of_targets_and_assumptions():
    c = FixtureClient()
    with pytest.raises(ApiError) as exc:
        c.decide("REQ-FIX-001", "approve", 2, "pm", "why")
    assert exc.value.code == "approval_incomplete" and len(exc.value.problems) == 4
    res = c.decide("REQ-FIX-001", "approve", 2, "pm", "why", ["A-1", "A-2"], ["T-1", "M-1"])
    assert res["status"] == "APPROVED" and res["revision"] == 3


def test_apply_is_not_approve():
    c = FixtureClient()
    res = c.apply_suggestion("REQ-FIX-001", "TURN-1", 2, {"target_scope.regions": {"after": ["US"]}}, "pm", "ok")
    assert res["status"] == "PM_REVIEW" and res["revision"] == 3
    assert not any(call[0] == "decide" for call in c.calls)


def test_full_export_is_not_capped():
    c = FixtureClient(extra_records=245)
    data, mime, _ = c.download("sample_list", "csv")
    rows = list(csv.reader(io.StringIO(data.decode())))
    assert mime == "text/csv" and len(rows) - 1 == 250
