"""Consistent Role 01 corpus admission for authoritative governance writes."""
from services.decisions import DomainError
from services.providers import Role01SourceProvider

CORPUS_KEY = "role1"


def generation(provider):
    sources = provider.sources
    with sources.driver.session(database=sources.database) as session:
        row = session.run("MATCH (c:EvidenceManifest {id:$key}) RETURN properties(c) AS version", key=CORPUS_KEY).single()
    return {k: v for k, v in row["version"].items() if k != "lock_version"} if row else {}


class _TransactionSession:
    def __init__(self, tx):
        self.tx = tx
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def run(self, *args, **kwargs):
        return self.tx.run(*args, **kwargs)
    def execute_read(self, operation):
        return operation(self.tx)


class _TransactionDriver:
    def __init__(self, tx):
        self.tx = tx
    def session(self, **kwargs):
        return _TransactionSession(self.tx)


def validate_context(provider, tx, context, originals):
    # MemoryRepository is a unit-test convenience; it cannot promise graph
    # atomicity. Live acceptance uses the real repository for mutations.
    if tx is None:
        if generation(provider) != context["_source_generation"]:
            raise DomainError(409, "Source corpus changed during evidence resolution")
        return
    row = tx.run("MERGE (c:EvidenceManifest {id:$key}) ON CREATE SET c.lock_version=0 "
                 "SET c.lock_version=coalesce(c.lock_version,0)+1 RETURN properties(c) AS version", key=CORPUS_KEY).single()
    row = {"version": {k: v for k, v in row["version"].items() if k != "lock_version"}}
    if row["version"] != context["_source_generation"]:
        raise DomainError(409, "Source corpus changed before governance commit; resolve and review again")
    sources = provider.sources.transaction_view(_TransactionDriver(tx), originals)
    with sources.integrity_session():
        for snapshot in context.get("source_snapshots", []):
            sources.validate_original_snapshot(snapshot)
