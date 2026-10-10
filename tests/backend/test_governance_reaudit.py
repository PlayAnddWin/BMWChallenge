"""Regression boundaries uncovered by a fresh independent governance review."""
import copy

import pytest

from conftest import decision
from models import DecisionRequest
from services.decisions import DomainError
from services.repository import Neo4jRepository


@pytest.mark.parametrize("missing", ["strategy_references", "strategy_rationale"])
def test_uncited_strategy_remains_provisional_on_override_and_preview(backend, draft, missing):
    client, repo, service, _ = backend
    original = service.evidence_provider.resolve

    def uncited(proposed):
        context = original(proposed)
        context["score_context"].pop(missing)
        return context

    service.evidence_provider.resolve = uncited
    draft["derivation_key"] = "uncited-override-" + missing
    record = service.create_requirement(draft, "uncited-run")
    factor = record["score"]["factors"]["strategy"]
    enabled = service.decide_requirement(record["id"], DecisionRequest(**decision(action="start_review", score_override=0)))
    assert enabled["score"]["factors"]["strategy"] == factor
    cleared = service.decide_requirement(record["id"], DecisionRequest(**decision(2, "reject", score_override=None)))
    assert cleared["score"]["factors"]["strategy"] == factor
    assert cleared["score"]["override"] is None
    before = repo.read()
    preview = client.post("/api/scoring/preview", json={"requirement_id": record["id"]})
    assert preview.status_code == 200, preview.text
    assert preview.json()["new_score"]["factors"]["strategy"] == factor
    assert repo.read() == before


@pytest.mark.parametrize("changed", [True, 1.0])
def test_legacy_import_retry_rejects_changed_json_type_without_writes(backend, changed):
    _, repo, service, _ = backend
    original = {"req_id": "legacy-types", "impact": 2, "market_reach_norm": .5, "effort": 3, "metadata": {"source_row": 1}}
    record = service.import_legacy([original])[0]
    before = repo.read()
    assert service.import_legacy([copy.deepcopy(original)])[0] == record
    assert repo.read() == before
    modified = copy.deepcopy(original)
    modified["metadata"]["source_row"] = changed
    with pytest.raises(DomainError) as error:
        service.import_legacy([modified])
    assert error.value.status == 409
    assert repo.read() == before


@pytest.mark.parametrize("namespace", ["a:b", "", " ", " a", "a ", None, 7])
def test_invalid_graph_namespace_fails_before_driver_use(namespace):
    with pytest.raises(ValueError, match="namespace"):
        Neo4jRepository(object(), namespace=namespace)


@pytest.mark.parametrize("coordinate", ["row", "page"])
@pytest.mark.parametrize("value", [0, -1, "one", "1", True, 1.0])
@pytest.mark.parametrize("field", ["strategy_references", "customer_references", "forecast"])
def test_score_rejects_invalid_source_coordinates(draft, coordinate, value, field):
    from conftest import FakeEvidence
    from services.scoring import calculate_score
    context = FakeEvidence().resolve(draft)["score_context"]
    reference = {"source_id": "source-1", "locator": {coordinate: value}}
    if field == "forecast":
        context["forecasts"][0]["source_reference"] = reference
    else:
        context[field] = [reference]
    before = copy.deepcopy(context)
    with pytest.raises(ValueError, match="source_reference|traceable source references"):
        calculate_score(context)
    assert context == before


@pytest.mark.parametrize("route", ["analysis-runs", "import-runs"])
@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_run_scope_nonfinite_metadata_rejected_before_persistence(backend, route, token):
    client, repo, _, _ = backend
    before = repo.read()
    response = client.post("/api/" + route, content='{"adapter_metadata":{"unknown":[' + token + ']}}', headers={"content-type": "application/json"})
    assert response.status_code == 422, response.text
    assert repo.read() == before


@pytest.mark.parametrize("terminal_failure", [False, True])
def test_analysis_admission_remains_locked_until_terminal_persistence_finishes(terminal_failure):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from fastapi.testclient import TestClient
    from conftest import FakeEvidence
    from services.api import create_app
    from services.repository import MemoryRepository

    terminal_entered, finish_terminal = threading.Event(), threading.Event()

    class TerminalRepository(MemoryRepository):
        block_once = True

        def write(self, operation):
            probe = self.read()
            operation(probe)
            if self.block_once and any(run["kind"] == "analysis" and run["state"] == "succeeded" for run in probe["runs"].values()):
                self.block_once = False
                terminal_entered.set()
                assert finish_terminal.wait(10)
                if terminal_failure:
                    raise RuntimeError("Terminal persistence unavailable")
            return super().write(operation)

    class Engine:
        def analyze(self, scope):
            return []

    class Importer:
        def import_data(self, scope):
            return {"result_ids": []}

    repo = TerminalRepository()
    with TestClient(create_app(repo, FakeEvidence(), Engine(), import_service=Importer()), raise_server_exceptions=False) as client:
        with ThreadPoolExecutor(max_workers=1) as pool:
            started = pool.submit(client.post, "/api/analysis-runs", json={})
            try:
                assert terminal_entered.wait(10)
                assert client.post("/api/analysis-runs", json={}).status_code == 409
                assert client.post("/api/import-runs", json={}).status_code == 409
                assert len(repo.read()["runs"]) == 1
                assert next(iter(repo.read()["runs"].values()))["state"] == "running"
            finally:
                finish_terminal.set()
            started.result(timeout=10)
        # A failed persistence attempt must also release in-process admission.
        response = client.post("/api/analysis-runs", json={})
        assert response.status_code == 200, response.text
        run = repo.read()["runs"][response.json()["id"]]
        assert run["state"] == "succeeded"


@pytest.mark.parametrize("target", ["revision", "config", "ranking", "freeze", "freeze_checkpoint", "freeze_audit"])
def test_coordinated_graph_and_ledger_snapshot_rewrite_fails_unchanged_checkpoint(backend, target):
    from test_repository_review import recording_repository
    client, memory, service, record = backend
    service.decide_requirement(record["id"], DecisionRequest(**decision()))
    if target.startswith("freeze"):
        result = client.post("/api/freezes", json={"selections": {record["id"]: 2}, "expected_ranking_id": memory.read()["rankings"][-1]["id"], "actor_id": "pm-test", "actor_role": "PM_USER", "rationale": "Freeze reviewed snapshot"})
        assert result.status_code == 200, result.text
    state = memory.read()
    if target == "revision":
        state["revisions"][record["id"]][0]["title"] = "Rewritten historical evidence"
    elif target == "config":
        state["configs"]["v1"]["fixed_reference_volume"] = 999
    elif target == "ranking":
        state["rankings"][0]["scores"][record["id"]] = 0
    else:
        frozen = next(iter(state["freezes"].values()))
        if target == "freeze_checkpoint":
            frozen["checkpoint"]["hash"] = "0" * 64
        elif target == "freeze_audit":
            frozen["audit"]["events"] = []
        else:
            frozen["rationale"] = "Rewritten freeze reason"
    # The recorder faithfully returns the same corrupted ledger and graph copies.
    # Only the unchanged hashed event payload distinguishes this from valid data.
    graph, _ = recording_repository(state)
    for operation in (graph.read, lambda: graph.write(lambda candidate: None)):
        with pytest.raises(ValueError, match="checkpoint-protected audit event"):
            operation()


@pytest.mark.parametrize("rid", ["历史", 'legacy"quote', "legacy\nheader"])
def test_legacy_history_download_filename_is_safe_utf8(backend, rid):
    import json
    from urllib.parse import quote, unquote
    client, _, service, _ = backend
    original = {"id": rid, "impact": 2, "market_reach_norm": .5, "effort": 3}
    service.import_legacy([original])
    response = client.get("/api/requirements/" + quote(rid, safe="") + "/history/export")
    assert response.status_code == 200, response.text
    disposition = response.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="requirement-history.json"; filename*=UTF-8\'\'')
    assert unquote(disposition.split("UTF-8''", 1)[1]) == f"requirement-{rid}-history.json"
    assert "\n" not in disposition and "\r" not in disposition
    assert json.loads(response.content)["revisions"][0]["legacy_original"] == original
