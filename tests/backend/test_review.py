"""Fake-engine acceptance of asynchronous orchestration and separate PM apply."""
import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from fastapi.testclient import TestClient

from conftest import FakeEvidence, decision, fixture_draft
from services.api import create_app
from services.repository import MemoryRepository


class FakeEngine:
    def __init__(self, patch=None):
        self.patch = patch

    def analyze(self, scope):
        return [fixture_draft()]

    def review(self, current, question, turns):
        return {"answer": "Synthetic cited review of the current requirement.",
                "citations": current["evidence_links"],
                "tool_records": [{"tool": "retrieve_evidence", "record_ids": ["feedback-1"]}],
                "patch": self.patch}


def identity(revision=1):
    return {"expected_revision": revision, "actor_id": "pm-review", "actor_role": "PM_USER",
            "rationale": "Review synthetic source and proposed target"}


def create_thread(client, rid):
    response = client.post("/api/review-threads", json=dict(identity(), entity_kind="requirement", entity_id=rid))
    assert response.status_code == 200, response.text
    return response.json()["id"]


def ask(client, thread_id):
    response = client.post(f"/api/review-threads/{thread_id}/turns",
                           json=dict(identity(), question="Does the source support this target?"))
    assert response.status_code == 200, response.text
    started = response.json()
    assert started["state"] == "queued"
    finished = client.get(f"/api/analysis-runs/{started['id']}").json()
    return finished


def test_analysis_running_status_and_partial_completion():
    entered, release = Event(), Event()

    class PartialEngine(FakeEngine):
        def analyze(self, scope):
            entered.set()
            assert release.wait(5), "Test did not release fake engine"
            invalid = fixture_draft()
            invalid["factor_proposals"]["severity_level"] = 99
            return [fixture_draft(), invalid]

    repo = MemoryRepository()
    with TestClient(create_app(repo, FakeEvidence(), engine=PartialEngine())) as client:
        with ThreadPoolExecutor(max_workers=1) as workers:
            pending = workers.submit(client.post, "/api/analysis-runs", json={"regions": ["US"]})
            try:
                assert entered.wait(5)
                run = next(iter(repo.read()["runs"].values()))
                progress = client.get(f"/api/analysis-runs/{run['id']}").json()
                assert progress["state"] == "running" and progress["progress"] == 0
                assert not pending.done()
            finally:
                release.set()
            response = pending.result(timeout=5)
        assert response.status_code == 200
        finished = client.get(f"/api/analysis-runs/{response.json()['id']}").json()
        assert finished["state"] == "partial" and finished["progress"] == 1
        assert len(finished["result_ids"]) == 1 and len(finished["failures"]) == 1
        assert len(repo.read()["requirements"]) == 1


def test_generation_metadata_envelope_preserves_partial_draft_results():
    class MetadataEngine(FakeEngine):
        def analyze(self, scope):
            invalid = fixture_draft()
            invalid["factor_proposals"]["severity_level"] = 99
            return {"drafts": [fixture_draft(), invalid], "model_metadata": {"model": "synthetic-model"},
                    "prompt_metadata": {"version": "synthetic-prompt"}}
    repo = MemoryRepository()
    with TestClient(create_app(repo, FakeEvidence(), engine=MetadataEngine())) as client:
        response = client.post("/api/analysis-runs", json={})
        run = client.get(f"/api/analysis-runs/{response.json()['id']}").json()
        assert run["state"] == "partial" and len(run["result_ids"]) == 1
        metadata = {key: run[key] for key in ("model_metadata", "prompt_metadata")}
        assert metadata == {"model_metadata": {"model": "synthetic-model"}, "prompt_metadata": {"version": "synthetic-prompt"}}
        data = repo.read()
        event = next(event for event in data["events"] if event["action"] == "REQUIREMENT_DERIVED")
        assert event["after"]["generation_metadata"] == metadata
        assert data["revisions"][run["result_ids"][0]][0]["generation_metadata"] == metadata


def test_invalid_generation_metadata_fails_before_requirement_persistence():
    class InvalidMetadataEngine(FakeEngine):
        def analyze(self, scope):
            return {"drafts": [fixture_draft()], "model_metadata": {"cost": float("nan")}}
    repo = MemoryRepository()
    with TestClient(create_app(repo, FakeEvidence(), engine=InvalidMetadataEngine())) as client:
        response = client.post("/api/analysis-runs", json={})
        run = client.get(f"/api/analysis-runs/{response.json()['id']}").json()
        assert run["state"] == "failed" and run["failures"]
        assert repo.read()["requirements"] == {}


def test_regenerated_proposal_and_application_pin_generation_metadata(backend, draft):
    from models import ApplyRequest
    _, repo, service, record = backend
    metadata = {"model_metadata": {"model": "regenerated-model"}, "prompt_metadata": {"version": "regenerated-prompt"}}
    draft["title"] = "Regenerated controls"
    pending = service.create_requirement(draft, "regeneration-run", generation_metadata=metadata)
    turn_id = pending["pending_turn_id"]
    event = next(event for event in repo.read()["events"] if event["action"] == "REGENERATION_PROPOSED")
    assert event["after"]["generation_metadata"] == metadata
    applied = service.edit_requirement(record["id"], ApplyRequest(**dict(identity(), turn_id=turn_id, patch={"title": "PM modified regeneration"})), apply=True)
    assert applied["revision_application"]["generation_metadata"] == metadata
    event = next(event for event in repo.read()["events"] if event["action"] == "SUGGESTION_APPLIED")
    assert event["after"]["revision_application"]["generation_metadata"] == metadata


def test_read_only_review_records_citations_and_tools_without_requirement_revision():
    repo = MemoryRepository()
    app = create_app(repo, FakeEvidence(), engine=FakeEngine())
    with TestClient(app) as client:
        record = app.state.service.create_requirement(fixture_draft(), "test-run")
        before = copy.deepcopy(repo.read()["requirements"])
        thread = create_thread(client, record["id"])
        run = ask(client, thread)
        assert run["state"] == "succeeded"
        turn = client.get(f"/api/review-threads/{thread}").json()["turns"][0]
        assert turn["state"] == "read_only" and turn["citations"] and turn["tool_records"]
        data = repo.read()
        assert data["requirements"] == before
        assert len(data["revisions"][record["id"]]) == 1
        assert {"CHALLENGE_REQUESTED", "CHALLENGE_COMPLETED"} <= {e["action"] for e in data["events"]}


def test_suggestion_apply_requires_separate_approval():
    repo = MemoryRepository()
    patch = {"title": "Reviewed accessible temperature controls"}
    app = create_app(repo, FakeEvidence(), engine=FakeEngine(patch))
    with TestClient(app) as client:
        record = app.state.service.create_requirement(fixture_draft(), "test-run")
        thread = create_thread(client, record["id"])
        run = ask(client, thread)
        turn_id = run["result_ids"][0]
        assert repo.read()["requirements"][record["id"]]["revision"] == 1
        response = client.post(f"/api/requirements/{record['id']}/challenge-applications",
                               json=dict(identity(), turn_id=turn_id, patch=patch))
        assert response.status_code == 200, response.text
        applied = response.json()
        assert applied["status"] == "PM_REVIEW" and applied["approved_revision"] is None
        assert applied["revision"] == 2 and applied["title"] == patch["title"]
        approved = client.post(f"/api/requirements/{record['id']}/decisions", json=decision(2))
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "APPROVED" and approved.json()["approved_revision"] == 3


def test_application_audit_pins_proposed_and_pm_modified_patch():
    repo = MemoryRepository()
    proposed = {"title": "Engine proposal", "description": "Engine detail"}
    accepted = {"title": "PM adjusted title"}
    app = create_app(repo, FakeEvidence(), engine=FakeEngine(proposed))
    with TestClient(app) as client:
        record = app.state.service.create_requirement(fixture_draft(), "test-run")
        run = ask(client, create_thread(client, record["id"]))
        turn_id = run["result_ids"][0]
        endpoint = f"/api/requirements/{record['id']}/challenge-applications"
        payload = dict(identity(), turn_id=turn_id, patch=accepted)
        applied = client.post(endpoint, json=payload)
        assert applied.status_code == 200, applied.text
        expected = {"turn_id": turn_id, "run_id": run["id"], "base_revision": 1,
                    "proposed_patch": proposed, "accepted_patch": accepted}
        data = repo.read()
        event = next(e for e in data["events"] if e["action"] == "SUGGESTION_APPLIED")
        assert event["after"]["revision_application"] == expected
        assert data["revisions"][record["id"]][-1]["revision_application"] == expected
        assert applied.json()["description"] == record["description"]
        assert client.post(endpoint, json=payload).json() == applied.json()
        assert repo.read() == data
        approved = client.post(f"/api/requirements/{record['id']}/decisions", json=decision(2))
        assert approved.status_code == 200, approved.text
        assert "revision_application" not in approved.json()
        assert repo.read()["revisions"][record["id"]][1]["revision_application"] == expected


def test_discard_is_audited_without_mutating_requirement_and_cannot_apply():
    repo = MemoryRepository()
    patch = {"title": "Discard this proposal"}
    app = create_app(repo, FakeEvidence(), engine=FakeEngine(patch))
    with TestClient(app) as client:
        record = app.state.service.create_requirement(fixture_draft(), "test-run")
        thread = create_thread(client, record["id"])
        turn = ask(client, thread)["result_ids"][0]
        before = repo.read()["requirements"]
        endpoint = f"/api/review-threads/{thread}/turns/{turn}/discard"
        response = client.post(endpoint, json=identity())
        assert response.status_code == 200 and response.json()["state"] == "discarded"
        assert repo.read()["requirements"] == before
        events = repo.read()["events"]
        assert events[-1]["action"] == "SUGGESTION_DISCARDED"
        assert client.post(endpoint, json=identity()).status_code == 200
        assert len(repo.read()["events"]) == len(events)
        assert client.post(f"/api/requirements/{record['id']}/challenge-applications",
                           json=dict(identity(), turn_id=turn, patch=patch)).status_code == 409


def test_missing_adapters_report_failed_runs_and_preserve_requirements():
    repo = MemoryRepository()
    app = create_app(repo, FakeEvidence())
    with TestClient(app) as client:
        record = app.state.service.create_requirement(fixture_draft(), "test-run")
        before = repo.read()["requirements"]
        for endpoint in ("analysis-runs", "import-runs"):
            response = client.post(f"/api/{endpoint}", json={})
            assert response.status_code == 200
            run = response.json()
            assert run["state"] == "failed" and run["mode"] == "unavailable"
            assert "not integrated" in run["failures"][0]
        thread = create_thread(client, record["id"])
        run = ask(client, thread)
        assert run["state"] == "failed" and "not integrated" in run["failures"][0]
        assert not client.get(f"/api/review-threads/{thread}").json()["turns"]
        assert repo.read()["requirements"] == before


def test_freeze_ai_identity_rejected_even_with_pm_role(backend):
    client, repo, service, record = backend
    assert client.post(f"/api/requirements/{record['id']}/decisions", json=decision()).status_code == 200
    before = repo.read()
    response = client.post("/api/freezes", json={"selections": {record["id"]: 2},
        "expected_ranking_id": before["rankings"][-1]["id"], "actor_id": "AI_SYSTEM",
        "actor_role": "PM_USER", "rationale": "Attempt false human identity"})
    assert response.status_code == 422
    assert repo.read() == before
