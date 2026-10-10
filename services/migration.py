"""Explicit preservation of unresolved baseline candidates, never auto-approval.

Inputs remain private; source graph nodes are read and never overwritten.
"""
import argparse
import json
from pathlib import Path

from services.decisions import GovernanceService
from services.providers import UnavailableEvidenceProvider
from services.repository import MemoryRepository, Neo4jRepository


def read_legacy_json(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    records = data.get("requirements") if isinstance(data, dict) else data
    if not isinstance(records, list):
        raise ValueError("Legacy JSON must contain a list or a requirements list")
    return records


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--from-json", type=Path)
    inputs.add_argument("--from-legacy-graph", action="store_true")
    parser.add_argument("--namespace", default="backend-v1")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    import config
    from neo4j import GraphDatabase
    driver = None
    try:
        if args.from_json:
            records = read_legacy_json(args.from_json)
        else:
            driver = GraphDatabase.driver(config.NEO4J_URI, auth=(config.NEO4J_USER, config.NEO4J_PASSWORD))
            with driver.session(database=config.NEO4J_DATABASE) as session:
                records = session.execute_read(lambda tx: [dict(row["original"]) for row in tx.run("MATCH (r:Requirement) WHERE r.backend_key IS NULL AND r.req_id IS NOT NULL RETURN r{.*} AS original ORDER BY r.req_id")])
        if args.dry_run:
            repository = MemoryRepository()
        else:
            driver = driver or GraphDatabase.driver(config.NEO4J_URI, auth=(config.NEO4J_USER, config.NEO4J_PASSWORD))
            repository = Neo4jRepository(driver, config.NEO4J_DATABASE, args.namespace)
            repository.initialize()
        results = GovernanceService(repository, UnavailableEvidenceProvider()).import_legacy(records)
        print(json.dumps({"count": len(results), "dry_run": args.dry_run, "validation_state": "unverified", "namespace": args.namespace}))
    finally:
        if driver is not None:
            driver.close()


if __name__ == "__main__":
    main()
