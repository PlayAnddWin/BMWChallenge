"""Manual server-stop/start probe. Synthetic UUID namespace; no real data touched.

Run 'prepare', restart the dedicated Neo4j server, then run 'verify'.
The manifest is retained under /private/tmp, never in the repository.
"""
import argparse
import json
import os
import uuid
from pathlib import Path

from neo4j import GraphDatabase
from conftest import FakeEvidence, fixture_draft, decision
from models import DecisionRequest
from services.decisions import GovernanceService
from services.repository import Neo4jRepository


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "verify"))
    parser.add_argument("--manifest", type=Path, default=Path("/private/tmp/bmw-backend-restart.json"))
    args = parser.parse_args()
    driver = GraphDatabase.driver(os.environ["BACKEND_TEST_NEO4J_URI"], auth=(os.getenv("BACKEND_TEST_NEO4J_USER", "neo4j"), os.getenv("BACKEND_TEST_NEO4J_PASSWORD", "bmwpassword")))
    try:
        if args.mode == "prepare":
            namespace = "backend-restart-" + uuid.uuid4().hex
            repo = Neo4jRepository(driver, "neo4j", namespace)
            repo.initialize()
            service = GovernanceService(repo, FakeEvidence())
            record = service.create_requirement(fixture_draft(), "synthetic-restart")
            service.decide_requirement(record["id"], DecisionRequest(**decision()))
            args.manifest.write_text(json.dumps({"namespace": namespace, "state": repo.read()}))
            print("Saved approved revision and checkpoint before server restart")
        else:
            saved = json.loads(args.manifest.read_text())
            repo = Neo4jRepository(driver, "neo4j", saved["namespace"])
            assert repo.read() == saved["state"], "Restart changed persisted state"
            with repo.session() as session:
                session.run("MATCH (n) WHERE n.backend_key=$key OR n.backend_key STARTS WITH $prefix DETACH DELETE n", key=repo.namespace, prefix=repo.namespace+":").consume()
            print("Server restart preserved exact approved revisions, ranks and audit checkpoint")
    finally:
        driver.close()


if __name__ == "__main__":
    main()
