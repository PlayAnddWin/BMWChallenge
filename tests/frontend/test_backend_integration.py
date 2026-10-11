"""Exercise the UI adapter against the current FastAPI application, not saved contract JSON.

Only the repository, source data and model are synthetic. Real routes, validation,
governance, pagination, asynchronous run records and export code are used.
"""
import copy
from contextlib import contextmanager
import json
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import httpx
import pytest

from services.api import create_app
from services.repository import MemoryRepository
from tests.backend.conftest import FakeEvidence, fixture_draft
from ui.client import ApiClient, ApiError
from ui.snapshot import SnapshotClient

from .test_app import markdown, run


COMMENT = "Synthetic original customer feedback, including its punctuation."


class NativeEvidence(FakeEvidence):
    def resolve(self, draft):
        context = super().resolve(draft)
        snapshot = context["source_snapshots"][0]
        snapshot["source_file"] = {"filename": "feedback_SYNTHETIC.xlsx", "kind": "feedback"}
        snapshot["raw_payload"] = {
            "columns": [{"header": "Country", "value": "US"}, {"header": "Customer Feedback", "value": COMMENT}],
            "values_by_header": {"Country": "US", "Customer Feedback": COMMENT},
        }
        return context


class Sources:
    def list_sources(self):
        return [{"id": "source-1", "filename": "feedback_SYNTHETIC.xlsx", "source_version": "hash-1",
                 "kind": "feedback"}]

    def get(self, kind, identity):
        return {"id": identity, "source_file_id": "source-1", "source_version": "hash-1",
                "locator": {"filename": "feedback_SYNTHETIC.xlsx", "sheet": "Feedback", "row": 2},
                "raw_payload": {"columns": [{"header": "Customer Feedback", "value": COMMENT}]}}


class Engine:
    def analyze(self, scope):
        return [fixture_draft()]

    def review(self, current, question, turns):
        return {"answer": "Synthetic answer citing the original server-bound feedback.",
                "citations": current["evidence_links"], "patch": None}


@contextmanager
def connected_backend(monkeypatch, prefix="/api", engine=True):
    repository = MemoryRepository()
    app = create_app(repository, NativeEvidence(), source_provider=Sources(),
                     engine=Engine() if engine else None, api_prefix=prefix)
    with TestClient(app) as server:
        draft = fixture_draft()
        draft["evidence_links"].append(dict(draft["evidence_links"][0], claim_key="factor-severity"))
        record = app.state.service.create_requirement(draft, "frontend-synthetic-run")
        calls = []

        def transport(method, url, params=None, json=None, timeout=None):
            path = urlsplit(url).path
            calls.append((method, path, copy.deepcopy(params), copy.deepcopy(json)))
            return server.request(method, path, params=params, json=json)

        monkeypatch.setattr(httpx, "request", transport)
        client = ApiClient(base_url="http://backend.test", api_prefix=prefix)
        yield client, server, repository, record["id"], calls


@pytest.fixture
def backend(monkeypatch):
    with connected_backend(monkeypatch) as context:
        yield context


@pytest.mark.parametrize("prefix", ["/api", "/workbench/v2", ""])
def test_configured_prefix_is_used_for_reads_writes_and_downloads(monkeypatch, prefix):
    with connected_backend(monkeypatch, prefix) as (client, _, repository, rid, calls):
        assert client.health()["engine_available"] is True
        assert client.list_requirements()["items"][0]["id"] == rid
        detail = client.get_requirement(rid)
        assert detail["evidence"]["quotes"][0]["text"] == COMMENT
        assert client.get_source_record("feedback-1")["values"]["Customer Feedback"] == COMMENT
        client.decide(rid, "start_review", detail["revision"], "pm", "Start the synthetic review")
        data, _, _ = client.download("sample_list", "json")
        assert json.loads(data)[0]["status"] == "PM_REVIEW"
        assert repository.read()["requirements"][rid]["revision"] == 2
        assert all(path.startswith(prefix + "/") for _, path, _, _ in calls)


def test_approval_errors_zero_override_and_immutable_package_round_trip(backend):
    client, _, repository, rid, _ = backend
    with pytest.raises(ApiError) as rejected:
        client.decide(rid, "approve", 1, "pm", "Incomplete source review")
    assert rejected.value.status == 422
    assert client.get_requirement(rid)["status"] == "DRAFT"
    before = client.list_requirements()
    approved = client.decide(rid, "approve", 1, "pm", "Reviewed all synthetic evidence and assumptions",
                             accepted_assumption_ids=["assumption-1"],
                             reviewed_target_ids=["metric-1", "milestone-1"],
                             reviewed_source_ids=["feedback-1"], override_enabled=True, score_override=0)
    assert approved["status"] == "APPROVED" and approved["score"]["effective_score"] == 0
    assert client.get_requirement(rid)["score"]["effective"] == 0
    with pytest.raises(ApiError) as stale:
        client.edit(rid, 1, {"title": "Stale edit"}, "pm", "Keep this draft")
    assert stale.value.status == 409
    with pytest.raises(ApiError) as stale_package:
        client.create_freeze([{"id": rid, "revision": 2}], before["ranking_id"], "pm", "Stale ranking")
    assert stale_package.value.status == 409
    current = client.list_all_requirements({"status": "APPROVED"})
    package = client.create_freeze([{"id": rid, "revision": 2}], current["ranking_id"], "pm", "Approved snapshot")
    fid = package["freeze_id"]
    client.edit(rid, 2, {"title": "Later PM edit"}, "pm", "Content changed after freezing")
    assert client.get_requirement(rid)["status"] == "PM_REVIEW"
    frozen = json.loads(client.download("freeze", "json", fid)[0])
    assert frozen["requirements"][0]["revision"] == 2
    assert frozen["requirements"][0]["title"] != "Later PM edit"
    assert json.loads(client.download("audit", "json", fid)[0])["checkpoint"]
    assert any(e["action"] == "CONTENT_CHANGED" for e in client.get_history(rid))
    assert repository.read()["requirements"][rid]["revision"] == 3


def test_read_only_review_survives_a_new_client_session(backend):
    client, _, repository, rid, _ = backend
    before = copy.deepcopy(repository.read()["requirements"])
    started = client.challenge(rid, "Is this original feedback?", 1, "pm")
    assert client.get_run(started["run_id"])["state"] == "succeeded"
    fresh = ApiClient(base_url="http://backend.test", api_prefix="/api", mode="v1")
    turns = fresh.get_thread(rid)["turns"]
    assert len(turns) == 1 and turns[0]["question"] == "Is this original feedback?"
    assert turns[0]["citations"] and turns[0]["state"] == "read_only"
    assert repository.read()["requirements"] == before


def seed_portfolio(server, repository, rid, size=250):
    """Extend the synthetic portfolio in one transaction with valid immutable/audited revisions."""
    original = repository.read()["requirements"][rid]

    def seed(state):
        for index in range(size - 1):
            identity = f"REQ-PAGE-{index:03}"
            record = dict(copy.deepcopy(original), id=identity, derivation_key=f"pagination-{index}")
            state["requirements"][identity] = record
            state["revisions"][identity] = [copy.deepcopy(record)]
            server.app.state.service.event(state, "REQUIREMENT_DERIVED", identity, None, record,
                                           ("synthetic-pagination", "AI_SYSTEM"), "Synthetic pagination fixture")

    repository.write(seed)


def test_pagination_and_full_export_keep_all_250_records(backend):
    client, server, repository, rid, calls = backend
    seed_portfolio(server, repository, rid)
    page = client.list_requirements(limit=250)
    assert page["total"] == len(page["items"]) == 250
    assert len({r["id"] for r in page["items"]}) == 250
    assert len(client.list_all_requirements()["items"]) == 250
    requests = [params for method, path, params, _ in calls if method == "GET" and path == "/api/requirements"]
    assert any(p.get("offset") == 200 for p in requests)
    assert all(p["limit"] <= 200 for p in requests)
    exported = json.loads(client.download("sample_list", "json")[0])
    assert len(exported) == 250
    assert all(r["evidence_links"] and r["assumptions"] and r["smart"] for r in exported)


def test_ranking_change_during_pagination_is_not_a_mixed_snapshot(backend, monkeypatch):
    client, server, repository, rid, _ = backend
    seed_portfolio(server, repository, rid)
    transport = httpx.request
    edited_between_pages = False

    def changing_ranking(method, url, **kwargs):
        nonlocal edited_between_pages
        response = transport(method, url, **kwargs)
        params = kwargs.get("params") or {}
        if not edited_between_pages and params.get("limit") == 200 and params.get("offset") == 0:
            edited_between_pages = True
            edited = server.patch(f"/api/requirements/{rid}", json={
                "expected_revision": 1, "actor_id": "other-pm", "actor_role": "PM_USER",
                "rationale": "Concurrent PM edit between pages", "patch": {"title": "Changed during pagination"}})
            assert edited.status_code == 200, edited.text
        return response

    monkeypatch.setattr(httpx, "request", changing_ranking)
    with pytest.raises(ApiError) as changed:
        client.list_all_requirements()
    assert changed.value.status == 409


@pytest.mark.parametrize("tab", ["Requirement", "Priority", "Evidence", "Assistant", "History"])
def test_real_backend_workbench_tabs_render_without_writes(backend, tab):
    client, _, repository, rid, calls = backend
    before = copy.deepcopy(repository.read())
    at = run(client, selected=rid, **{f"tab:{rid}": tab})
    assert not at.exception, at.exception
    assert all(method == "GET" for method, _, _, _ in calls)
    assert repository.read() == before
    if tab == "Evidence":
        assert COMMENT in markdown(at)
        assert len([b for b in at.button if b.label == "Open feedback-1"]) == 2


@pytest.mark.parametrize("page", ["Overview", "Sources", "Analysis", "Review package"])
def test_real_backend_pages_render_without_writes(backend, page):
    client, _, repository, _, calls = backend
    before = copy.deepcopy(repository.read())
    at = run(client, page=page)
    assert not at.exception, at.exception
    assert all(method == "GET" for method, _, _, _ in calls)
    assert repository.read() == before


def test_failed_analysis_stays_failed_in_the_ui(monkeypatch):
    with connected_backend(monkeypatch, engine=False) as (client, _, _, _, calls):
        started = client.start_analysis({"derivatives": ["G60"], "regions": ["US"]}, 1, False)
        calls.clear()
        at = run(client, page="Analysis", run_id=started["run_id"])
        assert not at.exception, at.exception
        text = markdown(at)
        assert "failed" in text and "not integrated" in text
        assert "succeeded" not in text
        assert all(method == "GET" for method, _, _, _ in calls)
        assert not next(b for b in at.button if b.label == "Start analysis").disabled


def test_current_backend_export_opens_as_a_read_only_snapshot(backend, tmp_path):
    client, _, _, rid, _ = backend
    path = tmp_path / "requirements.json"
    path.write_bytes(client.download("sample_list", "json")[0])
    snapshot = SnapshotClient(path)
    detail = snapshot.get_requirement(rid)
    assert not detail["legacy"] and detail["score"]["scale"]["max"] == 100
    assert detail["evidence"]["quotes"][0]["text"] == COMMENT
    assert detail["allowed_actions"] == []
    assert snapshot.list_requirements({"region": "CN"})["total"] == 0
    assert snapshot.list_requirements({"powertrain": "ICE"})["total"] == 0
    with pytest.raises(ApiError) as read_only:
        snapshot.edit(rid, 1, {"title": "Wrong"}, "pm", "Cannot write a snapshot")
    assert read_only.value.status == 501
    at = run(snapshot, selected=rid)
    assert not at.exception, at.exception
    assert next(b for b in at.button if b.label == "Edit").disabled
    assert next(b for b in at.button if b.label == "Ask assistant").disabled
