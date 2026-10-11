"""The live ApiClient over a fake HTTP layer that answers with the backend's own contract examples,
plus the Role 1 source shapes. Catches pages that only work with fixture-shaped data."""
import copy
import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest

from ui.client import ApiClient, normalize_source_record, normalize_sources
from ui.contract_mock import ContractMockClient
from ui.forms import build_patch, patch_diff

from .test_app import markdown, run

ROOT = Path(__file__).resolve().parents[2]
FX = json.loads((ROOT / "contracts" / "fixture_case.json").read_text(encoding="utf-8"))
RID = FX["requirement_detail_example"]["id"]
THREAD = FX["review_thread_example"]["id"]

# Shapes from Role 1's ingestion (services/ingestion.py on origin/main), as the backend's
# GraphSourceProvider returns them after decoding locator/raw_payload JSON.
SOURCE_FILES = [
    {"id": "SRC-ab12", "filename": "G60_feedback.xlsx", "sha256": "ab12cd34ef56ab12cd34", "source_version": "ab12cd34ef56ab12cd34",
     "kind": "feedback", "document_kind": None, "conversion_version": "1", "size_bytes": 1024},
    {"id": "SRC-cd34", "filename": "strategy.pdf", "sha256": "cd34", "source_version": "cd34", "kind": "document",
     "document_kind": "strategy", "conversion_version": "1", "size_bytes": 2048},
]
ROW_RECORD = {"id": "SR-1", "source_file_id": "SRC-ab12", "source_version": "ab12",
              "locator": {"filename": "G60_feedback.xlsx", "sheet": "Feedback_Explorer", "row": 3},
              "raw_payload": {"columns": [{"header": "Country", "value": "US"},
                                          {"header": "Customer Feedback", "value": "Hard to find."}]},
              "original_record_id": None}
CHUNK = {"id": "DOC-9", "source_file_id": "SRC-cd34", "source_record_id": "SR-PDF-1", "source_version": "cd34",
         "document_kind": "strategy", "page": 4, "text": "Intuitive operation is a priority."}


def routes(method, path):
    if method == "GET" and path == "/api/health":
        return {"status": "ok", "engine_available": True}
    if method == "GET" and path == "/api/requirements":
        return FX["list_example"]
    if path == f"/api/requirements/{RID}":
        return FX["requirement_detail_example"]
    if path == f"/api/requirements/{RID}/evidence":
        return FX["evidence_example"]
    if path == f"/api/requirements/{RID}/history":
        return FX["history_example"]
    if path == f"/api/requirements/{RID}/proposals":
        return FX["proposal_list_example"]
    if path == f"/api/review-threads/{THREAD}":
        return FX["review_thread_example"]
    if path == "/api/sources":
        return SOURCE_FILES
    if path == "/api/source-records/SR-1":
        return ROW_RECORD
    if path == "/api/document-chunks/DOC-9":
        return CHUNK
    return None


@pytest.fixture
def live(monkeypatch):
    calls = []

    def fake(method, url, params=None, json=None, timeout=None):
        path = urlsplit(url).path
        calls.append((method, path))
        body = routes(method, path)
        status = 200 if body is not None else 404
        return httpx.Response(status, json=copy.deepcopy(body) if body is not None else {"detail": "Not Found"},
                              request=httpx.Request(method, url))
    monkeypatch.setattr(httpx, "request", fake)
    return calls


def test_sources_list_from_backend_is_normalised():
    data = normalize_sources(copy.deepcopy(SOURCE_FILES))
    assert [f["file"] for f in data["files"]] == ["G60_feedback.xlsx", "strategy.pdf"]
    assert data["files"][1]["kind"] == "document (strategy)" and data["files"][0]["version"] == "ab12cd34ef56"


def test_source_records_rows_and_chunks(live):
    c = ApiClient(base_url="http://x", mode="v1")
    row = c.get_source_record("SR-1")
    assert row["file"] == "G60_feedback.xlsx" and row["locator"] == {"sheet": "Feedback_Explorer", "row": 3}
    assert row["values"] == {"Country": "US", "Customer Feedback": "Hard to find."}
    chunk = c.get_source_record("DOC-9")
    assert ("GET", "/api/document-chunks/DOC-9") in live
    assert chunk["values"]["text"] == "Intuitive operation is a priority." and chunk["locator"] == {"page": 4}
    assert normalize_source_record({"id": "X", "file": "f", "values": {}})["file"] == "f"   # fixture shape kept


def test_sources_page_renders_live_list(live):
    at = run(ApiClient(base_url="http://x", mode="v1"), page="Sources")
    assert not at.exception, at.exception
    text = markdown(at)
    assert "G60_feedback.xlsx" in text and "strategy.pdf" in text and "version ab12cd34ef56" in text


@pytest.mark.parametrize("tab", ["Requirement", "Priority", "Evidence", "Assistant", "History"])
def test_live_client_renders_every_tab_from_contract_responses(live, tab):
    at = run(ApiClient(base_url="http://x", mode="v1"), selected=RID, **{f"tab:{RID}": tab})
    assert not at.exception, at.exception
    assert not [m for m, _ in live if m != "GET"]                 # rendering never mutates
    assert "not reachable" not in markdown(at)


@pytest.mark.parametrize("page", ["Requirements", "Overview", "Review package"])
def test_live_client_renders_pages_from_contract_responses(live, page):
    at = run(ApiClient(base_url="http://x", mode="v1"), page=page)
    assert not at.exception, at.exception
    assert not [m for m, _ in live if m != "GET"]


# ---------------------------------------------------------------------------- edit form against v1
def _detail():
    return ContractMockClient().get_requirement(RID)


def test_v1_edit_patch_is_a_requirement_draft_patch():
    req = _detail()
    draft = {"title": req["title"], "description": req["description"], "regions": ["US", "EU"],
             "metric_targets": {"metric-1": "3"}}
    patch = build_patch(req, draft)
    assert set(patch) == {"smart", "target_scope"}                 # top-level draft fields, no dotted keys
    metric = patch["smart"]["metrics"][0]
    assert metric["target"] == "3" and metric["provenance"] == "PMEntered" and metric["review_state"] == "pending"
    assert "targets" not in patch["smart"] and patch["smart"]["milestone"] == FX["requirement_detail_example"]["smart"]["milestone"]
    assert patch["target_scope"]["regions"] == ["US", "EU"]
    assert patch_diff(req, patch) == {"Target metric-1": {"before": "2", "after": "3"},
                                      "Target regions": {"before": ["US"], "after": ["US", "EU"]}}
    unchanged = build_patch(req, {"title": req["title"], "description": req["description"], "regions": ["US"],
                                  "metric_targets": {"metric-1": "2"}})
    assert unchanged == {}


def test_v1_edit_round_trip_in_mock():
    m = ContractMockClient()
    req = m.get_requirement(RID)
    patch = build_patch(req, {"metric_targets": {"metric-1": "3"}})
    res = m.edit(RID, 1, patch, "pm", "Two actions is too strict for the stationary test")
    assert res["revision"] == 2 and res["status"] == "PM_REVIEW"
    d = m.get_requirement(RID)
    t = d["smart"]["targets"][0]
    assert t["value"] == "3" and t["provenance"] == "PMEntered" and t["review_state"] == "unreviewed"
    with pytest.raises(Exception) as e:                            # ordered comparison needs a number
        m.edit(RID, 2, build_patch(d, {"metric_targets": {"metric-1": "two"}}), "pm", "x")
    assert e.value.status == 422
    with pytest.raises(Exception) as e:
        m.edit(RID, 1, {"title": "x"}, "pm", "stale")
    assert e.value.status == 409


def test_edit_sheet_renders_one_field_per_metric():
    at = run(ContractMockClient(), selected=RID)
    edit = next(b for b in at.button if b.label == "Edit")
    at = edit.click().run()
    assert not at.exception, at.exception
    labels = [t.label for t in at.text_input]
    assert any(label.startswith("Target metric-1") for label in labels)
