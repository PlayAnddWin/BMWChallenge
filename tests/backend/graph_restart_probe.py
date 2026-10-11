"""Manual server-stop/start probe. Synthetic UUID namespace; no real data touched.

Run 'prepare', restart the dedicated Neo4j server, then run 'verify'.
BACKEND_TEST_NEO4J_URI and BACKEND_TEST_NEO4J_DATABASE must be explicit.
The manifest is retained under /tmp/opencode, never in the repository.
"""
import argparse
import json
import os
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from neo4j import GraphDatabase
from conftest import FakeEvidence, fixture_draft, decision
from models import DecisionRequest
from services.decisions import GovernanceService
from services.repository import Neo4jRepository


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "verify"))
    parser.add_argument("--manifest", type=Path, default=Path("/tmp/opencode/bmw-backend-restart.json"))
    args = parser.parse_args(argv)
    uri = os.environ.get("BACKEND_TEST_NEO4J_URI", "").strip()
    database = os.environ.get("BACKEND_TEST_NEO4J_DATABASE", "").strip()
    if not uri or not database:
        parser.error("Set BACKEND_TEST_NEO4J_URI and BACKEND_TEST_NEO4J_DATABASE for a dedicated test server; no production defaults are used")
    saved = None
    if args.mode == "verify":
        saved = json.loads(args.manifest.read_text(encoding="utf-8"))
        if saved.get("uri") != uri or saved.get("database") != database:
            parser.error("Manifest does not match BACKEND_TEST_NEO4J_URI and BACKEND_TEST_NEO4J_DATABASE")
        if not re.fullmatch(r"backend-restart-[0-9a-f]{32}", saved.get("namespace", "")):
            parser.error("Manifest has no synthetic restart namespace")
    driver = GraphDatabase.driver(uri, auth=(os.environ.get("BACKEND_TEST_NEO4J_USER", "neo4j"), os.environ.get("BACKEND_TEST_NEO4J_PASSWORD", "bmwpassword")))
    try:
        driver.verify_connectivity()
        if args.mode == "prepare":
            namespace = "backend-restart-" + uuid.uuid4().hex
            repo = Neo4jRepository(driver, database, namespace)
            repo.initialize()
            service = GovernanceService(repo, FakeEvidence())
            record = service.create_requirement(fixture_draft(), "synthetic-restart")
            service.decide_requirement(record["id"], DecisionRequest(**decision()))
            args.manifest.parent.mkdir(parents=True, exist_ok=True)
            args.manifest.write_text(json.dumps({"uri": uri, "database": database, "namespace": namespace, "state": repo.read()}), encoding="utf-8")
            print("Saved approved revision and checkpoint before server restart")
        else:
            repo = Neo4jRepository(driver, database, saved["namespace"])
            assert repo.read() == saved["state"], "Restart changed persisted state"
            with repo.session() as session:
                session.run("MATCH (n) WHERE n.backend_key=$key OR n.backend_key STARTS WITH $prefix DETACH DELETE n", key=repo.namespace, prefix=repo.namespace+":").consume()
            print("Server restart preserved exact approved revisions, ranks and audit checkpoint")
    finally:
        driver.close()


if __name__ == "__main__":
    main()
