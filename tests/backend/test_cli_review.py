import importlib
import json
import os
from pathlib import Path

import httpx
import pytest

from services import migration
from test_neo4j_integration import graph_backend


@pytest.mark.parametrize("data", [[], {"requirements": []}])
def test_legacy_json_supported_containers(tmp_path, data):
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(data))
    assert migration.read_legacy_json(path) == []


@pytest.mark.parametrize("data", [{}, {"requirements": {}}, "bad"])
def test_legacy_json_rejects_wrong_container(tmp_path, data):
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="requirements list"):
        migration.read_legacy_json(path)


def test_actual_shape_legacy_cli_dry_run_without_database(tmp_path, capsys):
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps({"requirements": [{"requirement": {"req_id": "legacy-1", "title": "Synthetic historical proposal", "status": "CONFLICTED", "impact": 5, "market_reach_norm": 4, "effort": 3}, "source_signal": {"signal_id": "legacy-signal"}, "history": []}]}))
    migration.main(["--from-json", str(path), "--dry-run"])
    summary = json.loads(capsys.readouterr().out)
    assert summary == {"count": 1, "dry_run": True, "validation_state": "unverified", "namespace": "backend-v1"}


@pytest.mark.parametrize("kind,fmt,route", [("requirements", "csv", "/requirements/export"), ("requirements", "json", "/requirements/export"), ("freeze", "json", "/freezes/REQ-1/export"), ("freeze", "markdown", "/freezes/REQ-1/export"), ("audit", "json", "/freezes/REQ-1/audit"), ("history", "json", "/requirements/REQ-1/history/export")])
def test_export_cli_routes_and_exact_payload(tmp_path, monkeypatch, kind, fmt, route):
    module = importlib.import_module("pipeline.09_export_report")
    payload = "Complete preserved export\n"
    observed = []
    def get(url, params, timeout):
        observed.append((url, params, timeout))
        return httpx.Response(200, text=payload, request=httpx.Request("GET", url))
    monkeypatch.setattr(module.httpx, "get", get)
    args = ["--kind", kind, "--format", fmt, "--api-prefix", "/v2", "--output-dir", str(tmp_path)]
    if kind != "requirements":
        args += ["--id", "REQ-1"]
    module.main(args)
    assert observed == [("http://localhost:8000/v2" + route, {"format": fmt}, 60)]
    assert (tmp_path / f"{kind}.{fmt}").read_text() == payload


@pytest.mark.parametrize("args", [["--kind", "freeze"], ["--kind", "audit", "--id", "r", "--format", "csv"], ["--format", "markdown"], ["--kind", "history", "--id", "r", "--format", "markdown"]])
def test_export_cli_invalid_modes_no_network(tmp_path, monkeypatch, args):
    module = importlib.import_module("pipeline.09_export_report")
    monkeypatch.setattr(module.httpx, "get", lambda *a, **k: pytest.fail("Invalid CLI request reached the network"))
    with pytest.raises(SystemExit):
        module.main(args + ["--output", str(tmp_path / "export")])


def test_export_http_failure_never_writes_file(tmp_path, monkeypatch):
    module = importlib.import_module("pipeline.09_export_report")
    monkeypatch.setattr(module.httpx, "get", lambda url, **k: httpx.Response(503, request=httpx.Request("GET", url)))
    target = tmp_path / "export.json"
    with pytest.raises(httpx.HTTPStatusError):
        module.main(["--output", str(target)])
    assert not target.exists()


def test_docker_context_excludes_private_inputs_and_caches():
    rules = (Path(__file__).resolve().parents[2] / ".dockerignore").read_text().splitlines()
    assert {"BMW-Data", ".env", ".venv", ".git", "output", "var"} <= set(rules)


def test_legacy_cli_persists_originals_and_retry_is_idempotent(graph_backend, tmp_path, monkeypatch, capsys):
    import config
    repo, _, _ = graph_backend
    monkeypatch.setattr(config, "NEO4J_URI", os.environ["BACKEND_TEST_NEO4J_URI"])
    monkeypatch.setattr(config, "NEO4J_DATABASE", repo.database)
    original = {"req_id": "legacy-cli", "title": "Historical synthetic proposal", "status": "APPROVED", "impact": 5, "market_reach_norm": 4, "effort": 3}
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps([original]))
    args = ["--from-json", str(path), "--namespace", repo.namespace]
    migration.main(args)
    first = repo.read()
    imported = [r for r in first["requirements"].values() if r.get("legacy_original") == original]
    assert len(imported) == 1
    assert imported[0]["validation_state"] == "unverified"
    assert imported[0]["approved_revision"] is None
    assert imported[0]["id"] not in first["rankings"][-1]["ranks"]
    migration.main(args)
    assert repo.read() == first
    assert [json.loads(line)["count"] for line in capsys.readouterr().out.splitlines()] == [1, 1]


def test_legacy_cli_closes_driver_when_initialization_fails(tmp_path, monkeypatch):
    from neo4j import GraphDatabase
    path = tmp_path / "legacy.json"
    path.write_text("[]")
    closed = []
    class Driver:
        def close(self):
            closed.append(True)
    monkeypatch.setattr(GraphDatabase, "driver", lambda *a, **k: Driver())
    def fail(_):
        raise RuntimeError("database unavailable")
    monkeypatch.setattr(migration.Neo4jRepository, "initialize", fail)
    with pytest.raises(RuntimeError, match="database unavailable"):
        migration.main(["--from-json", str(path)])
    assert closed == [True]


def test_legacy_graph_dry_run_reads_original_without_changing_either_graph(graph_backend, monkeypatch, capsys):
    import config
    repo, _, _ = graph_backend
    monkeypatch.setattr(config, "NEO4J_URI", os.environ["BACKEND_TEST_NEO4J_URI"])
    monkeypatch.setattr(config, "NEO4J_DATABASE", repo.database)
    original = {"req_id": repo.namespace + "-original", "title": "Synthetic graph import", "status": "CONFLICTED", "impact": 5, "market_reach_norm": 4, "effort": 3}
    before = repo.read()
    try:
        with repo.session() as session:
            session.run("CREATE (r:Requirement) SET r=$original", original=original).consume()
        migration.main(["--from-legacy-graph", "--dry-run", "--namespace", repo.namespace])
        assert json.loads(capsys.readouterr().out)["count"] >= 1
        assert repo.read() == before
        with repo.session() as session:
            saved = session.run("MATCH (r:Requirement {req_id:$id}) RETURN properties(r) AS original", id=original["req_id"]).single()["original"]
            assert saved == original
    finally:
        with repo.session() as session:
            session.run("MATCH (r:Requirement {req_id:$id}) WHERE r.backend_key IS NULL DETACH DELETE r", id=original["req_id"]).consume()
