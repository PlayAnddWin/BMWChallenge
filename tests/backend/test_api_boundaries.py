import pytest
from fastapi.testclient import TestClient
from services.api import create_app


def test_preview_uses_authoritative_inputs_and_does_not_write(backend):
    client, repository, service, record = backend
    before = repository.read()
    response = client.post("/api/scoring/preview", json={"requirement_id": record["id"], "weights": {"customer": 1, "reach": 0, "strategy": 0, "ease": 0}, "region_counts": {"US": {"support": 999, "cohort": 1}}})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["mode"] == "preview"
    assert result["new_score"]["calculated_score"] == pytest.approx(62.65986323710904)
    assert result["old_ranks"] == result["new_ranks"]
    assert repository.read() == before


def test_startup_failure_closes_owned_driver(monkeypatch):
    from neo4j import GraphDatabase
    class FailingDriver:
        closed = False
        def verify_connectivity(self):
            raise RuntimeError("injected connectivity failure")
        def close(self):
            self.closed = True
    driver = FailingDriver()
    monkeypatch.setattr(GraphDatabase, "driver", lambda *args, **kwargs: driver)
    with pytest.raises(RuntimeError, match="connectivity"):
        with TestClient(create_app()):
            pass
    assert driver.closed


def test_recovery_marks_interrupted_runs_truthfully(backend):
    _, repository, service, _ = backend
    repository.write(lambda state: state["runs"].update({"interrupted": {"id": "interrupted", "state": "running", "stage": "draft"}}))
    with TestClient(create_app(repository)) as client:
        run = client.get("/api/analysis-runs/interrupted").json()
    assert run["state"] == "failed" and run["stage"] == "interrupted"


def test_pdf_download_stays_inside_allowlisted_root(tmp_path, monkeypatch):
    import config
    from services.repository import MemoryRepository
    class SourceProvider:
        def get(self, kind, sid):
            return {"id": sid, "filename": "../outside.pdf"}
    monkeypatch.setattr(config, "RAW_DATA_DIR", tmp_path)
    with TestClient(create_app(MemoryRepository(), source_provider=SourceProvider())) as client:
        assert client.get("/api/sources/source-1/file").status_code == 404
