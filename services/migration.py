"""Explicit preservation of unresolved baseline candidates, never auto-approval.

Inputs remain private; source graph nodes are read and never overwritten.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from services.decisions import GovernanceService
from services.providers import UnavailableEvidenceProvider
from services.repository import MemoryRepository, Neo4jRepository


# Existing integration callers override these compatibility constants. Normal
# invocation always takes its connection values from the canonical reader.
_COMPAT_GRAPH_DEFAULTS = {name: getattr(config, name) for name in
                          ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD", "NEO4J_DATABASE")}


def _graph_value(name, configured):
    value = getattr(config, name)
    return value if value != _COMPAT_GRAPH_DEFAULTS[name] else configured


def read_legacy_json(path):
    data = json.loads(config.resolve_path(path).read_text(encoding="utf-8"))
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
    settings = config.get_settings()
    uri = _graph_value("NEO4J_URI", settings.neo4j_uri)
    user = _graph_value("NEO4J_USER", settings.neo4j_user)
    password = _graph_value("NEO4J_PASSWORD", settings.neo4j_password.get_secret_value())
    database = _graph_value("NEO4J_DATABASE", settings.neo4j_database)
    from neo4j import GraphDatabase
    driver = None
    try:
        if args.from_json:
            records = read_legacy_json(args.from_json)
        else:
            driver = GraphDatabase.driver(uri, auth=(user, password))
            with driver.session(database=database) as session:
                records = session.execute_read(lambda tx: [dict(row["original"]) for row in tx.run("MATCH (r:Requirement) WHERE r.backend_key IS NULL AND r.req_id IS NOT NULL RETURN r{.*} AS original ORDER BY r.req_id")])
        if args.dry_run:
            repository = MemoryRepository()
        else:
            driver = driver or GraphDatabase.driver(uri, auth=(user, password))
            repository = Neo4jRepository(driver, database, args.namespace)
            repository.initialize()
        results = GovernanceService(repository, UnavailableEvidenceProvider()).import_legacy(records)
        print(json.dumps({"count": len(results), "dry_run": args.dry_run, "validation_state": "unverified", "namespace": args.namespace}))
    finally:
        if driver is not None:
            driver.close()


if __name__ == "__main__":
    main()
