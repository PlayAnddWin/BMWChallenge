"""Serialized governance transactions. MemoryRepository is a test double only.

The Neo4j ledger lock deliberately serializes governance writes for this prototype.
All snapshots and materialized graph nodes commit in the same managed transaction.
"""
import copy
import json
import threading
from services.audit import canonical_json, verify_or_raise


def same_snapshot(left, right):
    """Compare JSON types as well as values (True, 1 and 1.0 differ)."""
    return canonical_json(left) == canonical_json(right)


def empty_state():
    return {"requirements": {}, "revisions": {}, "events": [], "turns": {},
            "freezes": {}, "rankings": [], "runs": {}, "threads": {},
            "configs": {}, "checkpoint": {"sequence": 0, "hash": "0" * 64}}


def validate_audited_snapshots(state):
    """Bind immutable materializations to the payload protected by the ledger hash.

    Comparing graph copies with ledger JSON alone misses coordinated rewrites of
    both copies. The unchanged, checkpoint-verified events are the trust anchor.
    """
    revision_actions = {"REQUIREMENT_DERIVED", "LEGACY_REQUIREMENT_IMPORTED", "CONTENT_CHANGED", "SUGGESTION_APPLIED", "STATUS_CHANGED"}
    protected = {("revision", rid, record["revision"]) for rid, versions in state["revisions"].items() for record in versions}
    protected.update(("config", key) for key in state["configs"])
    protected.update(("ranking", record["id"]) for record in state["rankings"])
    protected.update(("freeze", key) for key in state["freezes"])
    bound = set()
    for event in state["events"]:
        action, snapshot = event.get("action"), event.get("after")
        if action in revision_actions | {"SCORING_CONFIG_CREATED", "RANKING_RECALCULATED", "FREEZE_CREATED"} and not isinstance(snapshot, dict):
            raise ValueError("Audited immutable snapshot must be an object")
        if action in revision_actions:
            versions = state["revisions"].get(snapshot.get("id"), [])
            revision = snapshot.get("revision")
            current = versions[revision - 1] if type(revision) is int and 1 <= revision <= len(versions) else None
            identity = ("revision", snapshot.get("id"), revision)
        elif action == "SCORING_CONFIG_CREATED":
            current = state["configs"].get(snapshot.get("id"))
            identity = ("config", snapshot.get("id"))
        elif action == "RANKING_RECALCULATED":
            # Bind the complete ordered series below: the final entry is the
            # current baseline, so an unordered identity comparison is insufficient.
            bound.add(("ranking", snapshot.get("id")))
            continue
        elif action == "FREEZE_CREATED":
            identity = ("freeze", snapshot.get("id"))
            frozen = state["freezes"].get(snapshot.get("id"))
            current = {key: value for key, value in frozen.items() if key not in ("audit", "checkpoint")} if frozen else None
            checkpoint = {"sequence": event["sequence"], "hash": event["hash"]}
            expected_audit = {"events": state["events"][:event["sequence"]], "checkpoint": checkpoint,
                              "verification": "tamper-evident relative to retained checkpoint"}
            if frozen and (not same_snapshot(frozen.get("checkpoint"), checkpoint) or not same_snapshot(frozen.get("audit"), expected_audit)):
                raise ValueError("Frozen audit differs from its checkpoint-protected audit event")
        else:
            continue
        if not same_snapshot(current, snapshot):
            raise ValueError("Immutable snapshot differs from its checkpoint-protected audit event")
        bound.add(identity)
    if protected - bound:
        raise ValueError("Immutable snapshot has no checkpoint-protected audit event")
    audited_rankings = [event["after"] for event in state["events"] if event.get("action") == "RANKING_RECALCULATED"]
    if not same_snapshot(state["rankings"], audited_rankings):
        raise ValueError("Ranking chronology differs from checkpoint-protected audit events")
    validate_turn_lifecycle(state)
    threads = {}
    for event in state["events"]:
        if event.get("action") == "REVIEW_THREAD_CREATED":
            thread = event.get("after")
            if not isinstance(thread, dict) or not isinstance(thread.get("id"), str) or thread["id"] in threads or thread.get("entity_kind") not in {"requirement", "signal"} or thread.get("entity_id") != event.get("entity_id"):
                raise ValueError("Audited review thread creation is invalid")
            threads[thread["id"]] = thread
    if not same_snapshot(state["threads"], threads):
        raise ValueError("Review threads differ from checkpoint-protected creation events")
    for turn in state["turns"].values():
        if "thread_id" in turn:
            thread = threads.get(turn["thread_id"])
            if thread is None or thread["entity_id"] != turn.get("entity_id"):
                raise ValueError("Review turn must match its audited thread scope")


def validate_turn_lifecycle(state):
    """Proposal content and retry outcomes are authoritative audited state."""
    turns = {}
    applications = {}
    for event in state["events"]:
        action = event.get("action")
        if action == "SUGGESTION_APPLIED":
            snapshot = event["after"]
            tid = snapshot.get("revision_application", {}).get("turn_id")
            if not isinstance(tid, str) or tid in applications:
                raise ValueError("Application revision must identify one unique turn")
            applications[tid] = snapshot
        if action not in {"CHALLENGE_COMPLETED", "REGENERATION_PROPOSED", "SUGGESTION_DISCARDED", "SUGGESTION_APPLICATION_RECORDED"}:
            continue
        turn = event.get("after")
        if not isinstance(turn, dict) or not isinstance(turn.get("id"), str):
            raise ValueError("Audited turn must have an identity")
        previous = turns.get(turn["id"])
        if action in {"CHALLENGE_COMPLETED", "REGENERATION_PROPOSED"}:
            if previous is not None or turn.get("state") not in {"pending", "read_only"}:
                raise ValueError("Audited turn creation is invalid")
            if turn["state"] == "pending" and (not isinstance(turn.get("patch"), dict) or not turn["patch"] or type(turn.get("expected_revision")) is not int or turn["expected_revision"] < 1):
                raise ValueError("Pending turn requires a patch and a positive base revision")
        else:
            terminal = "discarded" if action == "SUGGESTION_DISCARDED" else "applied"
            extra = {"discard_request"} if terminal == "discarded" else {"fingerprint", "result"}
            if previous is None or previous.get("state") != "pending" or turn.get("state") != terminal or not same_snapshot(event.get("before"), previous):
                raise ValueError("Audited turn transition is invalid")
            expected = dict(previous, state=terminal)
            expected.update({key: turn.get(key) for key in extra})
            if not same_snapshot(turn, expected):
                raise ValueError("Audited turn proposal cannot change during transition")
            if terminal == "applied":
                result = turn.get("result")
                versions = state["revisions"].get(turn.get("entity_id"), [])
                revision = turn.get("expected_revision")
                committed = versions[revision] if type(revision) is int and 0 <= revision < len(versions) else None
                if not isinstance(result, dict) or not same_snapshot(result, committed) or not same_snapshot(result, applications.get(turn["id"])) or result.get("revision_application", {}).get("turn_id") != turn["id"] or not isinstance(turn.get("fingerprint"), str):
                    raise ValueError("Applied turn result must match its immutable application revision")
        if turn.get("entity_id") != event.get("entity_id"):
            raise ValueError("Audited turn entity does not match its event")
        turns[turn["id"]] = turn
    if not same_snapshot(state["turns"], turns):
        raise ValueError("Turn state differs from checkpoint-protected lifecycle events")
    if set(applications) != {tid for tid, turn in turns.items() if turn["state"] == "applied"}:
        raise ValueError("Application revisions must have terminal turn lifecycle events")


def validate_revision_lineage(state):
    if set(state["requirements"]) != set(state["revisions"]):
        raise ValueError("Committed requirements cannot lose revision lineage")
    for rid, record in state["requirements"].items():
        versions = state["revisions"].get(rid)
        if not versions or not same_snapshot(record, versions[-1]) or any(v.get("id") != rid or type(v.get("revision")) is not int or v["revision"] != number for number, v in enumerate(versions, 1)):
            raise ValueError("Current requirements must match contiguous immutable revisions")


def validate_append_only(before, after):
    """Repositories reject attempts to rewrite committed governance snapshots."""
    verify_or_raise(after["events"], after["checkpoint"])
    json.dumps(after, allow_nan=False)
    if not set(before["requirements"]) <= set(after["requirements"]):
        raise ValueError("Committed requirements cannot be deleted or lose revision lineage")
    validate_revision_lineage(after)
    for name in ("events", "rankings"):
        if not same_snapshot(after[name][:len(before[name])], before[name]):
            raise ValueError(f"Committed {name} are append-only")
    for rid, versions in before["revisions"].items():
        if not same_snapshot(after["revisions"].get(rid, [])[:len(versions)], versions):
            raise ValueError("Committed requirement revisions are immutable")
    for name in ("turns", "runs", "threads"):
        if not set(before[name]) <= set(after[name]):
            raise ValueError(f"Committed {name} cannot be deleted")
    for name in ("freezes", "configs"):
        for key, value in before.get(name, {}).items():
            if not same_snapshot(after.get(name, {}).get(key), value):
                raise ValueError(f"Committed {name} are immutable")
    validate_audited_snapshots(after)


class MemoryRepository:
    def __init__(self):
        self.state = empty_state()
        self.lock = threading.RLock()

    def read(self):
        with self.lock:
            return copy.deepcopy(self.state)

    def write_with_transaction(self, operation):
        return self.write(lambda state: operation(state, None))

    def write(self, operation):
        with self.lock:
            candidate = copy.deepcopy(self.state)
            result = operation(candidate)
            validate_append_only(self.state, candidate)
            self.state = candidate
            return copy.deepcopy(result)


class Neo4jRepository:
    def __init__(self, driver, database=None, namespace="backend-v1"):
        # ':' separates namespace/entity/revision keys; allowing it in namespaces
        # makes integrity scans for 'a' include another ledger's 'a:b' nodes.
        if not isinstance(namespace, str) or not namespace.strip() or ":" in namespace or namespace != namespace.strip():
            raise ValueError("Governance namespace must be nonblank, trimmed and contain no ':'")
        self.driver, self.database, self.namespace = driver, database, namespace

    def session(self):
        return self.driver.session(database=self.database)

    def initialize(self):
        with self.session() as session:
            for label in ("GovernanceLedger", "Requirement", "RequirementRevision", "AuditEvent", "FreezeSnapshot", "RankingSnapshot", "ReviewTurn", "ScoringConfig", "AnalysisRun", "ReviewThread"):
                session.run(f"CREATE CONSTRAINT backend_{label.lower()} IF NOT EXISTS FOR (n:{label}) REQUIRE n.backend_key IS UNIQUE").consume()
            session.execute_write(lambda tx: tx.run(
                "MERGE (l:GovernanceLedger {backend_key:$key}) ON CREATE SET l.payload=$payload, l.lock_version=0, l.head_hash=$hash, l.sequence=0",
                key=self.namespace, payload=json.dumps(empty_state()), hash="0" * 64).consume())

    def _load_state(self, tx, row):
        if row is None:
            raise RuntimeError("Governance ledger is not initialized")
        state = json.loads(row["payload"])
        state.setdefault("configs", {})
        verify_or_raise(state["events"], state["checkpoint"])
        if row["head_hash"] != state["checkpoint"]["hash"] or type(row["sequence"]) is not int or row["sequence"] != state["checkpoint"]["sequence"]:
            raise ValueError("Protected ledger head does not match the payload")
        for label, records in (("AuditEvent", state["events"]), ("FreezeSnapshot", state["freezes"].values()), ("Requirement", state["requirements"].values()), ("RankingSnapshot", state["rankings"]), ("ReviewTurn", state["turns"].values()), ("ScoringConfig", state["configs"].values()), ("AnalysisRun", state["runs"].values()), ("ReviewThread", state["threads"].values())):
            expected = {f"{self.namespace}:{record.get('id', record.get('sequence'))}": record for record in records}
            rows = list(tx.run(f"MATCH (n:{label}) WHERE n.backend_key STARTS WITH $prefix RETURN n.backend_key AS key, n.payload AS payload, properties(n) AS properties", prefix=f"{self.namespace}:"))
            actual = {r["key"]: json.loads(r["payload"]) for r in rows}
            if not same_snapshot(actual, expected):
                raise ValueError(f"Immutable {label} graph copies do not match the ledger")
            if label == "Requirement":
                for row in rows:
                    record = expected[row["key"]]
                    native = row["properties"]
                    if native.get("req_id") != record["id"] or native.get("status") != record["status"] or type(native.get("revision")) is not int or native.get("revision") != record["revision"]:
                        raise ValueError("Current requirement index does not match its immutable payload")
        revisions = {f"{self.namespace}:{rid}:{record['revision']}": record for rid, versions in state["revisions"].items() for record in versions}
        revision_rows = list(tx.run("MATCH (n:RequirementRevision) WHERE n.backend_key STARTS WITH $prefix RETURN n.backend_key AS key, n.payload AS payload, properties(n) AS properties, [(r:Requirement)-[:HAS_REVISION]->(n) | r.backend_key] AS owners", prefix=f"{self.namespace}:"))
        graph_revisions = {r["key"]: json.loads(r["payload"]) for r in revision_rows}
        if not same_snapshot(graph_revisions, revisions):
            raise ValueError("Immutable requirement revisions do not match the ledger")
        for row in revision_rows:
            record = revisions[row["key"]]
            native = row["properties"]
            if native.get("req_id") != record["id"] or type(native.get("revision")) is not int or native.get("revision") != record["revision"] or row["owners"] != [f"{self.namespace}:{record['id']}"]:
                raise ValueError("Requirement revision index or ownership does not match its immutable payload")
        for rid, record in state["requirements"].items():
            if not state["revisions"].get(rid) or not same_snapshot(state["revisions"][rid][-1], record):
                raise ValueError("Current requirement differs from its immutable revision")
        validate_revision_lineage(state)
        validate_audited_snapshots(state)
        return state

    def read(self):
        def load(tx):
            # Same ledger lock as writers gives a coherent multi-query graph read.
            row = tx.run("MATCH (l:GovernanceLedger {backend_key:$key}) SET l.lock_version=l.lock_version RETURN l.payload AS payload, l.head_hash AS head_hash, l.sequence AS sequence", key=self.namespace).single()
            return self._load_state(tx, row)
        with self.session() as session:
            return session.execute_write(load)

    def write(self, operation):
        return self.write_with_transaction(lambda state, tx: operation(state))

    def write_with_transaction(self, operation):
        """Callback uses only the supplied transaction; driver retries are possible."""
        def commit(tx):
            # Property-dependent SET acquires the write lock before the read.
            row = tx.run("MATCH (l:GovernanceLedger {backend_key:$key}) SET l.lock_version=l.lock_version RETURN l.payload AS payload, l.head_hash AS head_hash, l.sequence AS sequence", key=self.namespace).single()
            state = self._load_state(tx, row)
            before = copy.deepcopy(state)
            result = operation(state, tx)
            validate_append_only(before, state)
            if same_snapshot(state, before):
                return result
            for entity_id, record in state["requirements"].items():
                if same_snapshot(before["requirements"].get(entity_id), record):
                    continue
                tx.run("MERGE (r:Requirement {backend_key:$key}) SET r.req_id=$id, r.revision=$revision, r.status=$status, r.payload=$payload",
                       key=f"{self.namespace}:{entity_id}", id=entity_id, revision=record["revision"], status=record["status"], payload=json.dumps(record, allow_nan=False)).consume()
            for entity_id, revisions in state["revisions"].items():
                for record in revisions[len(before["revisions"].get(entity_id, [])):]:
                    tx.run("MERGE (v:RequirementRevision {backend_key:$key}) ON CREATE SET v.req_id=$id, v.revision=$revision, v.payload=$payload WITH v MATCH (r:Requirement {backend_key:$reqkey}) MERGE (r)-[:HAS_REVISION]->(v)",
                           key=f"{self.namespace}:{entity_id}:{record['revision']}", reqkey=f"{self.namespace}:{entity_id}", id=entity_id, revision=record["revision"], payload=json.dumps(record, allow_nan=False)).consume()
            for label, records in (("AuditEvent", state["events"]), ("FreezeSnapshot", state["freezes"].values()), ("RankingSnapshot", state["rankings"]), ("ReviewTurn", state["turns"].values()), ("ScoringConfig", state["configs"].values()), ("AnalysisRun", state["runs"].values()), ("ReviewThread", state["threads"].values())):
                # Immutable collections are append-only; mutable records are keyed.
                field = {"AuditEvent": "events", "FreezeSnapshot": "freezes", "RankingSnapshot": "rankings", "ReviewTurn": "turns", "ScoringConfig": "configs", "AnalysisRun": "runs", "ReviewThread": "threads"}[label]
                previous = before[field]
                if isinstance(previous, list):
                    records = records[len(previous):]
                    previous_by_id = {}
                else:
                    previous_by_id = {record.get("id", record.get("sequence")): record for record in previous.values()}
                for record in records:
                    rid = record.get("id", record.get("sequence"))
                    if same_snapshot(previous_by_id.get(rid), record):
                        continue
                    clause = "SET" if label in ("ReviewTurn", "AnalysisRun", "ReviewThread") else "ON CREATE SET"
                    tx.run(f"MERGE (n:{label} {{backend_key:$key}}) {clause} n.payload=$payload", key=f"{self.namespace}:{rid}", payload=json.dumps(record, allow_nan=False)).consume()
            tx.run("MATCH (l:GovernanceLedger {backend_key:$key}) SET l.payload=$payload, l.head_hash=$hash, l.sequence=$sequence, l.lock_version=l.lock_version+1", key=self.namespace,
                   payload=json.dumps(state, allow_nan=False), hash=state["checkpoint"]["hash"], sequence=state["checkpoint"]["sequence"]).consume()
            return result
        with self.session() as session:
            return session.execute_write(commit)
