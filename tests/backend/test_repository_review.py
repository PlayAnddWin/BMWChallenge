import copy
import pytest

from models import DecisionRequest
from conftest import decision
from services.repository import MemoryRepository
from test_neo4j_integration import graph_backend, graph_snapshot


@pytest.mark.parametrize("kind", ["revision", "event", "ranking", "config", "freeze"])
def test_committed_snapshots_cannot_be_rewritten(backend, kind):
    client, repository, service, record = backend
    if kind == "freeze":
        approved = client.post(f"/api/requirements/{record['id']}/decisions", json=decision()).json()
        result = client.post("/api/freezes", json={"selections": {record["id"]: approved["revision"]}, "expected_ranking_id": repository.read()["rankings"][-1]["id"], "actor_id": "pm-test", "actor_role": "PM_USER", "rationale": "Reviewed freeze"})
        assert result.status_code == 200, result.text
    before = repository.read()
    def corrupt(state):
        if kind == "revision":
            state["revisions"][record["id"]][0]["title"] = "silently rewritten"
        elif kind == "event":
            state["events"].clear()
            state["checkpoint"] = {"sequence": 0, "hash": "0" * 64}
        elif kind == "ranking":
            state["rankings"][0]["ranks"] = {}
        elif kind == "config":
            state["configs"]["v1"]["fixed_reference_volume"] = 999
        else:
            state["freezes"][next(iter(state["freezes"]))]["rationale"] = "rewritten"
    with pytest.raises(ValueError):
        repository.write(corrupt)
    assert repository.read() == before


def test_memory_and_graph_json_finiteness_contract():
    repo = MemoryRepository()
    with pytest.raises(ValueError):
        repo.write(lambda state: state["runs"].update({"invalid": {"id": "invalid", "progress": float("nan")}}))
    assert repo.read()["runs"] == {}


def test_past_revision_cannot_change_when_current_revision_is_consistent(backend):
    _, repo, service, record = backend
    service.decide_requirement(record["id"], DecisionRequest(**decision(action="start_review")))
    before = repo.read()
    def rewrite_past(state):
        state["revisions"][record["id"]][0]["title"] = "Rewritten historical content"
    with pytest.raises(ValueError, match="revisions are immutable"):
        repo.write(rewrite_past)
    assert repo.read() == before


@pytest.mark.parametrize("corruption", ["current_title", "revision_number", "delete_current", "orphan_revision"])
def test_inconsistent_candidate_cannot_commit(backend, corruption):
    _, repo, _, record = backend
    before = repo.read()
    def corrupt(state):
        if corruption == "current_title":
            state["requirements"][record["id"]]["title"] = "unversioned mutation"
        elif corruption == "revision_number":
            version = copy.deepcopy(record)
            version["revision"] = 3
            state["requirements"][record["id"]] = version
            state["revisions"][record["id"]].append(version)
        elif corruption == "delete_current":
            del state["requirements"][record["id"]]
        else:
            state["revisions"]["orphan"] = [dict(record, id="orphan")]
    with pytest.raises(ValueError):
        repo.write(corrupt)
    assert repo.read() == before


@pytest.mark.parametrize("property,value", [("status", "APPROVED"), ("revision", 999), ("revision", True), ("revision", 1.0), ("req_id", "wrong")])
def test_native_requirement_index_tampering_blocks_reads_and_writes(graph_backend, property, value):
    repo, service, record = graph_backend
    with repo.session() as session:
        session.run(f"MATCH (r:Requirement {{backend_key:$key}}) SET r.{property}=$value", key=f"{repo.namespace}:{record['id']}", value=value).consume()
    before = graph_snapshot(repo)
    with pytest.raises(ValueError, match="index"):
        repo.read()
    with pytest.raises(ValueError, match="index"):
        service.decide_requirement(record["id"], DecisionRequest(**decision(action="start_review")))
    assert graph_snapshot(repo) == before


@pytest.mark.parametrize("label", ["Requirement", "RequirementRevision", "RankingSnapshot", "ScoringConfig", "AuditEvent"])
def test_corrupt_graph_copy_blocks_writes_instead_of_repairing_history(graph_backend, label):
    repo, service, record = graph_backend
    with repo.session() as session:
        key = session.run(f"MATCH (n:{label}) WHERE n.backend_key STARTS WITH $prefix RETURN n.backend_key AS key LIMIT 1", prefix=repo.namespace+":").single()["key"]
        session.run(f"MATCH (n:{label} {{backend_key:$key}}) SET n.payload=$payload", key=key, payload='{"tampered": true}').consume()
    before = graph_snapshot(repo)
    with pytest.raises(ValueError, match="graph copies|revisions"):
        service.decide_requirement(record["id"], DecisionRequest(**decision(action="start_review")))
    assert graph_snapshot(repo) == before


def test_graph_read_and_write_concurrency_has_coherent_snapshots(graph_backend):
    from concurrent.futures import ThreadPoolExecutor
    repo, service, record = graph_backend
    def read_many():
        for _ in range(15):
            state = repo.read()
            assert state["requirements"][record["id"]] == state["revisions"][record["id"]][-1]
    def update():
        service.decide_requirement(record["id"], DecisionRequest(**decision(action="start_review")))
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(read_many), pool.submit(read_many), pool.submit(update)]
        for future in futures:
            future.result(timeout=30)


@pytest.mark.parametrize("corruption", ["missing_ledger", "protected_head", "protected_sequence", "sequence_float"])
def test_missing_or_changed_ledger_fails_closed(graph_backend, corruption):
    repo, _, _ = graph_backend
    current_sequence = repo.read()["checkpoint"]["sequence"]
    with repo.session() as session:
        if corruption == "missing_ledger":
            session.run("MATCH (n:GovernanceLedger {backend_key:$key}) DETACH DELETE n", key=repo.namespace).consume()
        else:
            field, value = ("head_hash", "0" * 64) if corruption == "protected_head" else ("sequence", 999)
            if corruption == "sequence_float":
                value = float(current_sequence)
            session.run(f"MATCH (n:GovernanceLedger {{backend_key:$key}}) SET n.{field}=$value", key=repo.namespace, value=value).consume()
    before = graph_snapshot(repo)
    expected = RuntimeError if corruption == "missing_ledger" else ValueError
    for operation in (repo.read, lambda: repo.write(lambda state: state["runs"].clear())):
        with pytest.raises(expected, match="not initialized|Protected ledger"):
            operation()
        assert graph_snapshot(repo) == before


@pytest.mark.parametrize("corruption,value", [("req_id", "wrong"), ("revision", 999), ("revision", True), ("revision", 1.0), ("ownership", None)])
def test_revision_index_and_lineage_tampering_fails_closed(graph_backend, corruption, value):
    repo, _, record = graph_backend
    with repo.session() as session:
        key = f"{repo.namespace}:{record['id']}:1"
        if corruption == "ownership":
            session.run("MATCH ()-[edge:HAS_REVISION]->(n:RequirementRevision {backend_key:$key}) DELETE edge", key=key).consume()
        else:
            session.run(f"MATCH (n:RequirementRevision {{backend_key:$key}}) SET n.{corruption}=$value", key=key, value=value).consume()
    before = graph_snapshot(repo)
    for operation in (repo.read, lambda: repo.write(lambda state: state["runs"].clear())):
        with pytest.raises(ValueError, match="revision index or ownership"):
            operation()
        assert graph_snapshot(repo) == before


class RecordingGraphTransaction:
    """Return authentic snapshot rows while recording actual repository queries."""
    def __init__(self, state, namespace):
        self.state, self.namespace, self.queries = state, namespace, []

    class Rows(list):
        def single(self):
            return self[0] if self else None

        def consume(self):
            return None

    def run(self, query, **parameters):
        import json
        import re
        self.queries.append((query, parameters))
        if "RETURN l.payload" in query:
            return self.Rows([{"payload": json.dumps(self.state), "head_hash": self.state["checkpoint"]["hash"], "sequence": self.state["checkpoint"]["sequence"]}])
        if query.startswith("MATCH (n:"):
            label = re.match(r"MATCH \(n:(\w+)\)", query)[1]
            field = {"Requirement": "requirements", "AuditEvent": "events", "FreezeSnapshot": "freezes", "RankingSnapshot": "rankings", "ReviewTurn": "turns", "ScoringConfig": "configs", "AnalysisRun": "runs", "ReviewThread": "threads"}.get(label)
            if label == "RequirementRevision":
                records = [record for versions in self.state["revisions"].values() for record in versions]
            else:
                collection = self.state[field]
                records = list(collection.values()) if isinstance(collection, dict) else collection
            rows = []
            for record in records:
                key = f"{self.namespace}:{record.get('id', record.get('sequence'))}"
                properties = {"req_id": record.get("id"), "revision": record.get("revision"), "status": record.get("status")}
                if label == "RequirementRevision":
                    key += f":{record['revision']}"
                rows.append({"key": key, "payload": json.dumps(record), "properties": properties, "owners": [f"{self.namespace}:{record['id']}"]})
            return self.Rows(rows)
        return self.Rows()


class RecordingGraphSession:
    def __init__(self, tx):
        self.tx = tx

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def execute_write(self, callback):
        return callback(self.tx)


class RecordingGraphDriver:
    def __init__(self, tx):
        self.tx = tx

    def session(self, **_):
        return RecordingGraphSession(self.tx)


def recording_repository(state):
    from services.repository import Neo4jRepository
    tx = RecordingGraphTransaction(state, "query-count-test")
    return Neo4jRepository(RecordingGraphDriver(tx), namespace=tx.namespace), tx


def materialization_queries(tx):
    return [(query, params) for query, params in tx.queries if query.startswith("MERGE") or "SET l.payload=" in query]


def test_idempotent_write_verifies_all_copies_without_materialization(backend):
    _, memory, _, record = backend
    state = memory.read()
    repo, tx = recording_repository(state)
    assert repo.write(lambda candidate: candidate["requirements"][record["id"]]) == record
    assert materialization_queries(tx) == []
    assert len(tx.queries) == 10  # Locked ledger + all eight copy labels + revision lineage.


@pytest.mark.parametrize("field,label", [("runs", "AnalysisRun")])
@pytest.mark.parametrize("existing", [False, True])
def test_progress_updates_only_materialize_changed_mutable_record(backend, field, label, existing):
    _, memory, _, _ = backend
    state = memory.read()
    state[field]["unrelated"] = {"id": "unrelated", "progress": 10}
    if existing:
        state[field]["changed"] = {"id": "changed", "progress": 10}
    repo, tx = recording_repository(state)
    repo.write(lambda candidate: candidate[field].update({"changed": {"id": "changed", "progress": 20}}))
    queries = materialization_queries(tx)
    assert len(queries) == 2
    assert f"MERGE (n:{label}" in queries[0][0]
    assert queries[0][1]["key"] == f"{repo.namespace}:changed"
    assert "SET l.payload=" in queries[1][0]
    assert len(tx.queries) == 12


def test_new_revision_materializes_only_changed_requirement_and_appended_history(backend):
    from services.audit import append_event
    _, memory, _, record = backend
    state = memory.read()
    # Include an unrelated requirement with long history to catch full-history rewrites.
    original = copy.deepcopy(record)
    original["id"] = "unrelated"
    state["requirements"]["unrelated"] = original
    state["revisions"]["unrelated"] = [copy.deepcopy(original)]
    append_event(state, {"id": "unrelated-1", "action": "REQUIREMENT_DERIVED", "after": copy.deepcopy(original)})
    for number in range(2, 11):
        version = dict(original, revision=number)
        state["revisions"]["unrelated"].append(version)
        append_event(state, {"id": f"unrelated-{number}", "action": "CONTENT_CHANGED", "after": copy.deepcopy(version)})
    state["requirements"]["unrelated"] = state["revisions"]["unrelated"][-1]
    repo, tx = recording_repository(state)
    def update(candidate):
        version = dict(candidate["requirements"][record["id"]], revision=2, title="Reviewed content")
        candidate["requirements"][record["id"]] = version
        candidate["revisions"][record["id"]].append(copy.deepcopy(version))
        append_event(candidate, {"id": "new-event", "action": "CONTENT_CHANGED", "after": copy.deepcopy(version)})
        candidate["rankings"].append({"id": "new-ranking", "ranks": {record["id"]: 1}})
        append_event(candidate, {"id": "new-ranking-event", "action": "RANKING_RECALCULATED", "after": copy.deepcopy(candidate["rankings"][-1])})
    repo.write(update)
    queries = materialization_queries(tx)
    assert len(queries) == 6
    assert [params.get("key") for _, params in queries] == [
        f"{repo.namespace}:{record['id']}", f"{repo.namespace}:{record['id']}:2",
        f"{repo.namespace}:new-event", f"{repo.namespace}:new-ranking-event", f"{repo.namespace}:new-ranking", repo.namespace]


@pytest.mark.parametrize("field", ["turns", "runs", "threads"])
def test_committed_mutable_entities_cannot_be_deleted(field):
    repo = MemoryRepository()
    def create(state):
        record = {"id": "retained"}
        if field == "turns":
            from services.audit import append_event
            record.update(entity_id="synthetic-signal", state="read_only", patch=None)
            append_event(state, {"id": "creation", "entity_id": "synthetic-signal",
                                 "action": "CHALLENGE_COMPLETED", "after": record})
        if field == "threads":
            from services.audit import append_event
            record.update(entity_kind="signal", entity_id="synthetic-signal", actor_id="pm", timestamp="synthetic")
            append_event(state, {"id": "creation", "entity_id": "synthetic-signal",
                                 "action": "REVIEW_THREAD_CREATED", "after": record})
        state[field]["retained"] = record
    repo.write(create)
    before = repo.read()
    with pytest.raises(ValueError, match="cannot be deleted"):
        repo.write(lambda state: state[field].clear())
    assert repo.read() == before


@pytest.mark.parametrize("new_value", [True, 1.0])
def test_type_change_is_not_mistaken_for_idempotent_mutable_update(new_value):
    from services.repository import empty_state
    state = empty_state()
    state["runs"]["changed"] = {"id": "changed", "progress": 1}
    repo, tx = recording_repository(state)
    repo.write(lambda candidate: candidate["runs"]["changed"].update(progress=new_value))
    queries = materialization_queries(tx)
    assert len(queries) == 2
    import json
    assert type(json.loads(queries[0][1]["payload"])["progress"]) is type(new_value)


@pytest.mark.parametrize("new_value", [True, 1.0])
def test_json_equal_numeric_value_cannot_rewrite_immutable_configuration(new_value):
    from services.audit import append_event
    repo = MemoryRepository()
    def install(state):
        state["configs"]["v1"] = {"id": "v1", "factor": 1}
        append_event(state, {"id": "config-created", "action": "SCORING_CONFIG_CREATED", "after": copy.deepcopy(state["configs"]["v1"])})
    repo.write(install)
    before = repo.read()
    with pytest.raises(ValueError, match="immutable"):
        repo.write(lambda state: state["configs"]["v1"].update(factor=new_value))
    assert repo.read() == before


def test_same_numeric_value_with_changed_graph_json_type_is_tampering(graph_backend):
    import json
    repo, _, _ = graph_backend
    state = repo.read()
    config = copy.deepcopy(state["configs"]["v1"])
    value = config["fixed_reference_volume"]
    assert isinstance(value, (int, float)) and not isinstance(value, bool)
    config["fixed_reference_volume"] = float(value) if type(value) is int else int(value)
    assert config == state["configs"]["v1"]  # Python equality misses this tampering.
    with repo.session() as session:
        session.run("MATCH (n:ScoringConfig {backend_key:$key}) SET n.payload=$payload", key=f"{repo.namespace}:v1", payload=json.dumps(config)).consume()
    before = graph_snapshot(repo)
    for operation in (repo.read, lambda: repo.write(lambda candidate: None)):
        with pytest.raises(ValueError, match="ScoringConfig graph copies"):
            operation()
        assert graph_snapshot(repo) == before


class DelegatingQueryRecorder:
    """Observe real managed transaction queries without owning the caller's driver."""
    def __init__(self, driver):
        self.driver, self.queries = driver, []

    def session(self, **kwargs):
        owner = self
        real_session = self.driver.session(**kwargs)

        class RecordedSession:
            def __enter__(self):
                real_session.__enter__()
                return self

            def __exit__(self, *args):
                return real_session.__exit__(*args)

            def execute_write(self, callback):
                def observe(real_tx):
                    class RecordedTransaction:
                        def run(self, query, **parameters):
                            owner.queries.append((query, parameters))
                            return real_tx.run(query, **parameters)
                    return callback(RecordedTransaction())
                return real_session.execute_write(observe)
        return RecordedSession()


def test_real_graph_retries_and_progress_skip_unrelated_materialization(graph_backend):
    from conftest import fixture_draft
    from models import EditRequest
    from services.decisions import GovernanceService
    from services.repository import Neo4jRepository
    repo, service, record = graph_backend
    unrelated_draft = fixture_draft()
    unrelated_draft["derivation_key"] = "unrelated-query-count-case"
    unrelated = service.create_requirement(unrelated_draft, "unrelated-history-run")
    for number in range(4):
        unrelated = service.edit_requirement(unrelated["id"], EditRequest(
            expected_revision=unrelated["revision"], actor_id="pm-test", actor_role="PM_USER",
            rationale="Review unrelated historical content", patch={"description": f"Unrelated reviewed detail {number}"}))
    repo.write(lambda state: state["runs"].update({
        "observed-run": {"id": "observed-run", "progress": 0},
        "unrelated-run": {"id": "unrelated-run", "progress": 0}}))
    before = repo.read()
    assert len(before["revisions"][unrelated["id"]]) == 5
    recorder = DelegatingQueryRecorder(repo.driver)
    observed_repo = Neo4jRepository(recorder, repo.database, repo.namespace)
    observed_service = GovernanceService(observed_repo, service.evidence_provider)

    graph_before = graph_snapshot(repo)
    assert observed_service.create_requirement(fixture_draft(), "identical-retry-run") == record
    assert materialization_queries(recorder) == []
    assert len(recorder.queries) == 10
    assert graph_snapshot(repo) == graph_before

    recorder.queries.clear()
    observed_repo.write(lambda state: state["runs"]["observed-run"].update(progress=0.5))
    queries = materialization_queries(recorder)
    assert len(queries) == 2
    assert "MERGE (n:AnalysisRun" in queries[0][0]
    assert queries[0][1]["key"] == f"{repo.namespace}:observed-run"
    assert "SET l.payload=" in queries[1][0]
    after = repo.read()
    expected = copy.deepcopy(before)
    expected["runs"]["observed-run"]["progress"] = 0.5
    assert after == expected
    assert len(recorder.queries) == 12

    recorder.queries.clear()
    graph_before = graph_snapshot(repo)
    observed_repo.write(lambda state: state["runs"]["observed-run"].update(progress=0.5))
    assert materialization_queries(recorder) == []
    assert len(recorder.queries) == 10
    assert graph_snapshot(repo) == graph_before

    # Even a no-op retry must check unrelated immutable history before returning.
    with repo.session() as session:
        session.run("MATCH (n:RequirementRevision {backend_key:$key}) SET n.payload=$payload",
                    key=f"{repo.namespace}:{unrelated['id']}:1", payload='{"tampered": true}').consume()
    recorder.queries.clear()
    graph_before = graph_snapshot(repo)
    with pytest.raises(ValueError, match="Immutable requirement revisions"):
        observed_repo.write(lambda state: state["runs"]["observed-run"].update(progress=0.5))
    assert materialization_queries(recorder) == []
    assert graph_snapshot(repo) == graph_before


@pytest.mark.parametrize("target", ["revision", "config", "ranking", "freeze", "freeze_checkpoint", "freeze_audit"])
def test_real_coordinated_snapshot_tampering_preserves_hash_but_fails_binding(graph_backend, target):
    import json
    from models import FreezeRequest
    repo, service, record = graph_backend
    service.decide_requirement(record["id"], DecisionRequest(**decision()))
    if target.startswith("freeze"):
        service.freeze(FreezeRequest(selections={record["id"]: 2}, expected_ranking_id=repo.read()["rankings"][-1]["id"], actor_id="pm-test", actor_role="PM_USER", rationale="Freeze reviewed snapshot"))
    state = repo.read()
    if target == "revision":
        corrupted = state["revisions"][record["id"]][0]
        corrupted["title"] = "Rewritten historical customer content"
        label, key = "RequirementRevision", f"{repo.namespace}:{record['id']}:1"
    elif target == "config":
        corrupted = state["configs"]["v1"]
        corrupted["fixed_reference_volume"] = 999
        label, key = "ScoringConfig", f"{repo.namespace}:v1"
    elif target == "ranking":
        corrupted = state["rankings"][0]
        corrupted["scores"][record["id"]] = 0
        label, key = "RankingSnapshot", f"{repo.namespace}:{corrupted['id']}"
    else:
        corrupted = next(iter(state["freezes"].values()))
        if target == "freeze_checkpoint":
            corrupted["checkpoint"]["hash"] = "0" * 64
        elif target == "freeze_audit":
            corrupted["audit"]["events"] = []
        else:
            corrupted["rationale"] = "Rewritten freeze reason"
        label, key = "FreezeSnapshot", f"{repo.namespace}:{corrupted['id']}"
    with repo.session() as session:
        session.execute_write(lambda tx: (
            tx.run("MATCH (l:GovernanceLedger {backend_key:$key}) SET l.payload=$payload", key=repo.namespace, payload=json.dumps(state)).consume(),
            tx.run(f"MATCH (n:{label} {{backend_key:$key}}) SET n.payload=$payload", key=key, payload=json.dumps(corrupted)).consume()))
    before = graph_snapshot(repo)
    for operation in (repo.read, lambda: repo.write(lambda candidate: None)):
        with pytest.raises(ValueError, match="checkpoint-protected audit event"):
            operation()
        assert graph_snapshot(repo) == before
