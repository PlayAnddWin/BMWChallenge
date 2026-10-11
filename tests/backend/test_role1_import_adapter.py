"""Isolated graph/transaction checks; no model, API server or live database."""

from contextlib import contextmanager
from copy import deepcopy
import hashlib
from importlib import import_module
import json
import re
from types import SimpleNamespace

import pytest

from services.role1_import import BundleError, Role1ImportAdapter


loader = import_module("pipeline.01_load_neo4j")


class Result:
    def __init__(self, rows=(), error=None):
        self.rows, self.error = list(rows), error

    def data(self):
        self.consume()
        return deepcopy(self.rows)

    def consume(self):
        if self.error is not None:
            raise self.error


class Transaction:
    """A staged graph: only the enclosing owner can commit its mutations."""

    upserts = {
        loader.UPSERT_SOURCE_FILES: "SourceFile",
        loader.UPSERT_SOURCE_RECORDS: "SourceRecord",
        loader.UPSERT_FEEDBACK: "CustomerFeedback",
        loader.UPSERT_ANNOTATIONS: "FeedbackAnnotation",
        loader.UPSERT_FACTS: "Fact",
        loader.UPSERT_DOCUMENT_CHUNKS: "DocumentChunk",
        loader.UPSERT_STUDY_MAPPING_NODES: "StudyTopicMapping",
    }

    def __init__(self, state, fail_query=None):
        self.state = deepcopy(state)
        self.calls = []
        self.fail_query = fail_query
        self.manifest_locked = False

    @staticmethod
    def set_properties(node, properties):
        for key, value in properties.items():
            if value is None:
                node.pop(key, None)
            else:
                node[key] = deepcopy(value)

    def run(self, query, **params):
        self.calls.append((query, deepcopy(params)))
        assert not query.lstrip().startswith("CREATE CONSTRAINT")
        assert not query.lstrip().startswith("CREATE INDEX")
        nodes = self.state["nodes"]
        if query == loader.INITIALIZE_EVIDENCE_MANIFEST:
            table = nodes.setdefault("EvidenceManifest", {})
            created = "role1" not in table
            if created:
                table["role1"] = {"id": "role1", "lock_version": 0, "_role1_seed_token": params["token"]}
            self.manifest_locked = True
            return Result([{"evidence_manifest": deepcopy(table["role1"]), "created": created}])
        if query == loader.LOCK_EVIDENCE_MANIFEST:
            manifest = nodes.setdefault("EvidenceManifest", {}).setdefault("role1", {"id": "role1", "lock_version": 0})
            manifest["lock_version"] = manifest.get("lock_version", 0) + 1
            self.manifest_locked = True
            return Result([{"evidence_manifest": deepcopy(manifest)}])
        if query == loader.READ_EXISTING_SOURCE_IDENTITIES:
            assert self.manifest_locked, "Bootstrap read sources without acquiring the shared lock"
            return Result([{"id": source["id"], "source_version": source.get("source_version", source.get("sha256")),
                            "conversion_version": source.get("conversion_version")}
                           for source in sorted(nodes.get("SourceFile", {}).values(), key=lambda source: source["id"])])
        if query in (loader.SEED_EVIDENCE_MANIFEST, loader.UPDATE_EVIDENCE_MANIFEST):
            assert self.manifest_locked
            manifest = nodes["EvidenceManifest"]["role1"]
            if query == loader.SEED_EVIDENCE_MANIFEST:
                assert manifest["_role1_seed_token"] == params["token"]
                manifest.pop("_role1_seed_token")
            self.set_properties(manifest, params["properties"])
            return Result([{"evidence_manifest": deepcopy(manifest)}])
        if "RETURN row.id AS id, before" in query:
            assert self.manifest_locked, "Source access occurred before the shared EvidenceManifest lock"
            label = re.search(r"MERGE \(n:(\w+)", query).group(1)
            table = nodes.setdefault(label, {})
            rows = []
            for row in params["rows"]:
                before = deepcopy(table.get(row["id"]))
                if before is None:
                    table[row["id"]] = {"id": row["id"]}
                    if label == "SourceFile":
                        table[row["id"]].update(first_imported_at=params["recorded_at"],
                                                first_import_run_id=params["import_id"])
                rows.append({"id": row["id"], "before": before})
            return Result(rows)
        if query in self.upserts:
            assert self.manifest_locked
            label = self.upserts[query]
            for row in params["rows"]:
                properties = nodes[label][row["id"]]
                self.set_properties(properties, row["properties"])
            return Result(error=RuntimeError("deferred graph write failed") if query == self.fail_query else None)
        if query == loader.LINK_FEEDBACK_SOURCE_RECORDS:
            for row in params["rows"]:
                self.state["links"].add((row["id"], "DESCRIBES", row["feedback_comment_id"]))
        elif query == loader.UPSERT_STUDY_MAPPING_RELATIONSHIPS:
            for row in params["rows"]:
                for fact_id in row["fact_ids"]:
                    self.state["links"].add((row["id"], row["relation"], fact_id))
        elif query == loader.SOURCE_IMPORT_METADATA:
            for row in params["rows"]:
                properties = nodes["SourceFile"][row["id"]]
                properties.update(last_imported_at=params["recorded_at"], last_import_run_id=params["import_run_id"])
                if row["snapshot_json"] is not None:
                    properties["import_snapshot_json"] = row["snapshot_json"]
        elif "MERGE (run:ImportRun" in query:
            table = nodes.setdefault("ImportRun", {})
            properties = table.setdefault(params["id"], {"id": params["id"], "first_imported_at": params["recorded_at"]})
            properties.update({key: deepcopy(value) for key, value in params.items() if key != "recorded_at"})
            properties["last_imported_at"] = params["recorded_at"]
        else:
            raise AssertionError(f"Unexpected graph operation: {query}")
        return Result()


class Driver:
    def __init__(self):
        self.state = {"nodes": {}, "links": set(), "events": []}
        self.sessions = []
        self.schema_queries = []
        self.transactions = []
        self.forbid_sessions = False
        self.closed = False

    @contextmanager
    def transaction(self, fail_query=None, tx_class=Transaction):
        tx = tx_class(self.state, fail_query)
        self.transactions.append(tx)
        yield tx
        self.state = tx.state

    @contextmanager
    def session(self, database=None):
        assert not self.forbid_sessions, "commit_import opened another session"
        self.sessions.append(database)
        yield self

    def run(self, query, **params):
        assert query.lstrip().startswith(("CREATE CONSTRAINT", "CREATE INDEX"))
        self.schema_queries.append(query)
        return Result()

    def execute_write(self, work):
        with self.transaction() as tx:
            return work(tx)

    def verify_connectivity(self):
        pass

    def close(self):
        self.closed = True


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def update_table(bundle, name, edit):
    path = bundle / (name + ".jsonl")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    edit(rows)
    write_jsonl(path, rows)


@pytest.fixture
def bundle(tmp_path):
    directory = tmp_path / "processed"
    directory.mkdir()
    sources = [
        {"id": "SRC-feedback", "sha256": "feedback-hash", "filename": "feedback.xlsx", "kind": "feedback"},
        {"id": "SRC-study", "sha256": "study-hash", "filename": "study.xlsx", "kind": "survey"},
        {"id": "SRC-report", "sha256": "report-hash", "filename": "report.pdf", "kind": "document",
         "document_kind": "corporate_report"},
    ]
    for source in sources:
        source.update(source_version=source["sha256"], conversion_version="parser-1", size_bytes=100)
    text = "A source report passage."
    tables = {
        "source_files": sources,
        "source_records": [
            {"id": "SR-feedback", "source_file_id": "SRC-feedback", "source_version": "feedback-hash",
             "locator": {"filename": "feedback.xlsx", "sheet": "Feedback", "row": 2},
             "raw_payload": {"columns": [{"header": "Text", "value": "Too many taps."}],
                             "values_by_header": {"Text": "Too many taps."}},
             "feedback_comment_id": "FB-feedback", "model": "G60"},
            {"id": "SR-study", "source_file_id": "SRC-study", "source_version": "study-hash",
             "locator": {"filename": "study.xlsx", "sheet": "Study", "row": 2},
             "raw_payload": {"columns": [{"header": "Share", "value": 72}], "values_by_header": {"Share": 72}}},
            {"id": "SR-report", "source_file_id": "SRC-report", "source_version": "report-hash",
             "locator": {"filename": "report.pdf", "page": 1, "printed_page": None},
             "raw_payload": {"text": text, "extraction": "pypdf", "page_text_empty": False}},
        ],
        "feedback_comments": [{"id": "FB-feedback", "source_file_id": "SRC-feedback", "source_version": "feedback-hash",
                               "original_text": "Too many taps.", "original_id": "original-1", "model": "G60",
                               "region_mapping_version": "region-map-1"}],
        "feedback_annotations": [{"id": "ANN-feedback", "feedback_id": "FB-feedback", "source_record_id": "SR-feedback",
                                  "vfc2_original": "Climate", "vfc3_original": "Control UI", "polarity": "negative"}],
        "facts": [{"id": "FACT-study", "source_record_id": "SR-study", "kind": "survey_cell", "value": 72,
                   "source_value": 72, "mapping_strength": "unmapped", "region": "US", "derivative": "G60",
                   "locator": {"filename": "study.xlsx", "sheet": "Study", "row": 2, "column": 1}}],
        "document_chunks": [{"id": "DOC-report", "source_file_id": "SRC-report", "source_record_id": "SR-report",
                             "original_text": text, "document_kind": "corporate_report", "page": 1,
                             "chunk_number": 1, "start_offset": 0, "end_offset": len(text)}],
    }
    for name, rows in tables.items():
        write_jsonl(directory / (name + ".jsonl"), rows)
    manifest = {
        "status": "succeeded", "conversion_version": "parser-1",
        "counts": {name: len(rows) for name, rows in tables.items()}, "issues": [],
        "missing_kinds": [], "missing_document_kinds": [],
        "sources": [
            {"source_file_id": "SRC-feedback", "kind": "feedback", "source_rows": 1, "canonical_comments": 1, "annotations": 1},
            {"source_file_id": "SRC-study", "kind": "survey", "source_rows": 1, "facts": 1, "unmapped_numeric_cells": 1},
            {"source_file_id": "SRC-report", "kind": "document", "page_count": 1, "chunks": 1,
             "pages": [{"page": 1, "characters": len(text), "chunks": 1}]},
        ],
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return directory


def adapter_for(bundle, driver=None, **kwargs):
    return Role1ImportAdapter(driver, settings=SimpleNamespace(data_dir=bundle, neo4j_database="evidence-test"), **kwargs)


def sink_for(tx, fail_after=None):
    def sink(action, entity_id, before, after, rationale):
        assert action == "SOURCE_IMPORTED"
        assert after["id"] == entity_id and after["source_version"]
        assert before is None or before["id"] == entity_id
        assert rationale.strip()
        assert "ImportRun" in tx.state["nodes"], "Audit was emitted before all data writes completed"
        assert tx.manifest_locked
        manifest = tx.state["nodes"]["EvidenceManifest"]["role1"]
        assert manifest["import_run_id"] == after["import_run_id"]
        assert entity_id in json.loads(manifest["active_source_ids_json"])
        assert tx.calls[-1][0] == loader.UPDATE_EVIDENCE_MANIFEST
        assert tx.state["nodes"]["SourceFile"][entity_id]["import_snapshot_json"] == json.dumps(after, ensure_ascii=False, sort_keys=True)
        tx.state["events"].append({"action": action, "entity_id": entity_id, "before": deepcopy(before),
                                   "after": deepcopy(after), "rationale": rationale})
        if fail_after is not None and len(tx.state["events"]) == fail_after:
            raise RuntimeError("source audit failed")
    return sink


def commit(adapter, driver, body=None):
    prepared = adapter.prepare_import({} if body is None else body)
    with driver.transaction() as tx:
        return adapter.commit_import(prepared, tx=tx, audit_sink=sink_for(tx))


def test_default_preparation_reads_canonical_directory_without_graph_or_file_writes(bundle, monkeypatch):
    import services.role1_import as module
    monkeypatch.setattr(module, "get_settings", lambda: SimpleNamespace(data_dir=bundle, neo4j_database="canonical-db"))
    original = {path.name: path.read_bytes() for path in bundle.iterdir()}
    adapter = Role1ImportAdapter()
    updates = []
    prepared = adapter.prepare_import({}, progress_callback=lambda *args: updates.append(args))
    assert prepared.processed_dir == bundle
    assert prepared.counts["source_files"] == 3
    assert adapter.database == "canonical-db"
    assert updates == [("read_processed_bundle", 0.1, "live"), ("prepared_import", 0.8, "live")]
    assert {path.name: path.read_bytes() for path in bundle.iterdir()} == original


def test_schema_is_separate_and_every_source_audit_matches_complete_transaction_snapshot(bundle):
    driver = Driver()
    adapter = adapter_for(bundle, driver, database="explicit-db")
    assert driver.sessions == ["explicit-db"]
    assert driver.schema_queries == list(loader.CONSTRAINTS + loader.INDEXES)
    assert len(driver.transactions) == 1
    assert set(driver.state["nodes"]) == {"EvidenceManifest"}
    original = {path.name: path.read_bytes() for path in bundle.iterdir()}
    driver.forbid_sessions = True
    result = commit(adapter, driver)
    assert result["result_ids"] == ["SRC-feedback", "SRC-report", "SRC-study"]
    assert result["failures"] == result["signals"] == []
    assert len(driver.transactions) == 2
    assert driver.transactions[-1].calls[0][0] == loader.LOCK_EVIDENCE_MANIFEST
    assert set(driver.state["nodes"]) == {"SourceFile", "SourceRecord", "CustomerFeedback", "FeedbackAnnotation",
                                           "Fact", "DocumentChunk", "ImportRun", "EvidenceManifest"}
    assert result["evidence_manifest"] == driver.state["nodes"]["EvidenceManifest"]["role1"]
    assert ("SR-feedback", "DESCRIBES", "FB-feedback") in driver.state["links"]
    snapshots = {source["id"]: source for source in result["sources"]}
    feedback = snapshots["SRC-feedback"]
    assert feedback["counts"] == {"source_files": 1, "source_records": 1, "feedback_comments": 1,
                                  "feedback_annotations": 1, "facts": 0, "document_chunks": 0, "study_topic_mappings": 0}
    assert feedback["parser_snapshot"]["summary"]["canonical_comments"] == 1
    assert feedback["mapping_snapshot"]["native_versions"] == {"region_mapping_version": ["region-map-1"]}
    assert feedback["parser_version"] == "parser-1"
    assert feedback["sha256"] == feedback["source_version"] == "feedback-hash"
    assert feedback["reconciliation"]["created_counts"] == feedback["counts"]
    assert not feedback["reconciliation"]["already_imported"]
    assert snapshots["SRC-report"]["parser_snapshot"]["summary"]["pages"][0]["page"] == 1
    assert [event["after"] for event in driver.state["events"]] == result["sources"]
    assert all(event["before"] is None for event in driver.state["events"])
    assert {path.name: path.read_bytes() for path in bundle.iterdir()} == original


def test_deferred_startup_initialization_uses_configured_database_and_writes_no_evidence(bundle):
    driver = Driver()
    adapter = adapter_for(bundle)
    manifest = adapter.initialize(driver)
    assert driver.sessions == ["evidence-test"]
    assert driver.state == {"nodes": {"EvidenceManifest": {"role1": manifest}}, "links": set(), "events": []}
    assert manifest["lock_version"] == 0
    assert json.loads(manifest["active_source_ids_json"]) == []
    assert len(driver.transactions) == 1


@pytest.mark.parametrize("versions,inferred", [
    (("legacy-parser", "legacy-parser"), "legacy-parser"),
    (("parser-1", "parser-2"), None),
    (("parser-1", None), None),
])
def test_schema_bootstraps_sorted_existing_sources_without_inventing_history(versions, inferred):
    driver = Driver()
    driver.state["nodes"]["SourceFile"] = {
        "SRC-z": {"id": "SRC-z", "source_version": "hash-z", "conversion_version": versions[0]},
        "SRC-a": {"id": "SRC-a", "sha256": "hash-a", "conversion_version": versions[1]},
    }
    existing_sources = deepcopy(driver.state["nodes"]["SourceFile"])
    manifest = loader.initialize_schema(driver, database="bootstrap-db")
    assert driver.sessions == ["bootstrap-db"]
    assert json.loads(manifest["active_source_ids_json"]) == ["SRC-a", "SRC-z"]
    assert manifest.get("conversion_version") == inferred
    identities = [["SRC-a", "hash-a", versions[1]], ["SRC-z", "hash-z", versions[0]]]
    canonical = json.dumps(identities, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert manifest["source_manifest_hash"] == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert len(manifest["manifest_hash"]) == 64
    assert manifest["study_mapping_versions_json"] == "[]"
    assert manifest["lock_version"] == 0
    assert "import_run_id" not in manifest
    assert not any(key.endswith("_at") or "timestamp" in key or key.startswith("_role1") for key in manifest)
    assert driver.state["nodes"]["SourceFile"] == existing_sources
    assert driver.state["nodes"]["EvidenceManifest"]["role1"] == manifest
    assert [query for query, _ in driver.transactions[0].calls] == [
        loader.INITIALIZE_EVIDENCE_MANIFEST, loader.READ_EXISTING_SOURCE_IDENTITIES, loader.SEED_EVIDENCE_MANIFEST,
    ]


def test_repeated_schema_initialization_preserves_selected_sources_and_manifest_hash(bundle):
    driver = Driver()
    adapter = adapter_for(bundle)
    imported = commit(adapter, driver)
    driver.state["nodes"]["SourceFile"]["SRC-historical"] = {
        "id": "SRC-historical", "source_version": "old-source-hash", "conversion_version": "old-parser",
    }
    baseline = deepcopy(driver.state)
    for _ in range(2):
        assert loader.initialize_schema(driver, database="selected-db") == imported["evidence_manifest"]
        assert driver.state == baseline
        assert [query for query, _ in driver.transactions[-1].calls] == [loader.INITIALIZE_EVIDENCE_MANIFEST]
    assert "SRC-historical" not in json.loads(imported["evidence_manifest"]["active_source_ids_json"])


def test_same_preparation_reimport_has_stable_selection_fingerprint_and_counts(bundle):
    driver = Driver()
    adapter = adapter_for(bundle)
    prepared = adapter.prepare_import({})
    with driver.transaction() as tx:
        first = adapter.commit_import(prepared, tx, sink_for(tx))
    node_counts = {label: len(rows) for label, rows in driver.state["nodes"].items()}
    with driver.transaction() as tx:
        second = adapter.commit_import(prepared, tx, sink_for(tx))
    for result in (first, second):
        manifest = result["evidence_manifest"]
        assert json.loads(manifest["active_source_ids_json"]) == prepared.source_file_ids
        assert manifest["conversion_version"] == "parser-1"
        assert manifest["import_run_id"] == prepared.import_run_id
        assert not any(key.endswith("_at") or key.startswith("_role1") for key in manifest)
    assert {key: value for key, value in first["evidence_manifest"].items() if key != "lock_version"} == prepared.evidence_manifest
    assert {key: value for key, value in second["evidence_manifest"].items() if key != "lock_version"} == prepared.evidence_manifest
    assert first["evidence_manifest"]["lock_version"] == 1
    assert second["evidence_manifest"]["lock_version"] == 2
    assert first["counts"] == second["counts"] == prepared.counts
    assert [source["counts"] for source in first["sources"]] == [source["counts"] for source in second["sources"]]
    assert {label: len(rows) for label, rows in driver.state["nodes"].items()} == node_counts
    assert all(tx.calls[0][0] == loader.LOCK_EVIDENCE_MANIFEST for tx in driver.transactions)
    assert "coalesce(manifest.lock_version, 0) + 1" in loader.LOCK_EVIDENCE_MANIFEST


def test_selecting_a_new_bundle_is_atomic_and_does_not_reactivate_historical_sources_on_startup(bundle):
    driver = Driver()
    adapter = adapter_for(bundle)
    first = commit(adapter, driver)
    baseline = deepcopy(driver.state)
    def discard_report(rows):
        rows[:] = [row for row in rows if row.get("source_file_id", row["id"]) != "SRC-report"]
    update_table(bundle, "source_files", discard_report)
    update_table(bundle, "source_records", discard_report)
    write_jsonl(bundle / "document_chunks.jsonl", [])
    path = bundle / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["counts"].update(source_files=2, source_records=2, document_chunks=0)
    manifest["sources"] = [source for source in manifest["sources"] if source["source_file_id"] != "SRC-report"]
    path.write_text(json.dumps(manifest))
    prepared = adapter.prepare_import({})
    with pytest.raises(RuntimeError, match="source audit failed"):
        with driver.transaction() as tx:
            adapter.commit_import(prepared, tx, sink_for(tx, fail_after=len(tx.state["events"]) + 1))
    assert driver.state == baseline
    with driver.transaction() as tx:
        selected = adapter.commit_import(prepared, tx, sink_for(tx))
    assert json.loads(selected["evidence_manifest"]["active_source_ids_json"]) == ["SRC-feedback", "SRC-study"]
    assert selected["evidence_manifest"]["manifest_hash"] != first["evidence_manifest"]["manifest_hash"]
    assert selected["evidence_manifest"]["source_manifest_hash"] != first["evidence_manifest"]["source_manifest_hash"]
    assert "SRC-report" in driver.state["nodes"]["SourceFile"]
    assert loader.initialize_schema(driver) == selected["evidence_manifest"]
    assert "SRC-report" not in json.loads(driver.state["nodes"]["EvidenceManifest"]["role1"]["active_source_ids_json"])


def test_source_audit_failure_rolls_back_all_evidence_and_prior_events(bundle):
    driver = Driver()
    adapter = adapter_for(bundle, driver)
    prepared = adapter.prepare_import({})
    baseline = deepcopy(driver.state)
    with pytest.raises(RuntimeError, match="source audit failed"):
        with driver.transaction() as tx:
            adapter.commit_import(prepared, tx=tx, audit_sink=sink_for(tx, fail_after=2))
    assert driver.state == baseline
    assert len(driver.transactions[-1].state["events"]) == 2
    assert "DocumentChunk" in driver.transactions[-1].state["nodes"]


def test_deferred_write_error_prevents_success_and_source_audits(bundle):
    driver = Driver()
    adapter = adapter_for(bundle)
    with pytest.raises(RuntimeError, match="deferred graph write failed"):
        with driver.transaction(fail_query=loader.UPSERT_FACTS) as tx:
            adapter.commit_import(adapter.prepare_import({}), tx=tx, audit_sink=sink_for(tx))
    assert driver.state == {"nodes": {}, "links": set(), "events": []}
    assert driver.transactions[0].state["events"] == []


@pytest.mark.parametrize("record_id,field,replacement", [
    ("SR-feedback", "raw_payload", {"text": "Changed source evidence"}),
    ("SR-feedback", "locator", {"filename": "feedback.xlsx", "sheet": "Feedback", "row": 20}),
    ("SR-report", "raw_payload", {"text": "Changed page text", "extraction": "pypdf", "page_text_empty": False}),
    ("SR-study", "raw_payload", {"columns": [{"header": "Share", "value": True}], "values_by_header": {"Share": True}}),
])
def test_conflicting_raw_rows_pages_and_locators_cannot_overwrite_prior_source_evidence(bundle, record_id, field, replacement):
    driver = Driver()
    adapter = adapter_for(bundle)
    commit(adapter, driver)
    before = deepcopy(driver.state)
    update_table(bundle, "source_records", lambda rows: next(row for row in rows if row["id"] == record_id).__setitem__(field, replacement))
    with pytest.raises(BundleError, match="Conflicting reimport of immutable SourceRecord"):
        commit(adapter, driver)
    assert driver.state == before


def test_converter_metadata_upgrade_preserves_identities_raw_evidence_and_true_first_import_time(bundle):
    driver = Driver()
    adapter = adapter_for(bundle)
    first = commit(adapter, driver)
    identities = {label: set(rows) for label, rows in driver.state["nodes"].items()}
    original_payload = driver.state["nodes"]["SourceRecord"]["SR-feedback"]["raw_payload_json"]
    update_table(bundle, "source_files", lambda rows: [row.update(conversion_version="parser-2") for row in rows])
    update_table(bundle, "source_records", lambda rows: rows[0].update(model="F70", model_original="F70 explicit source label"))
    update_table(bundle, "feedback_comments", lambda rows: rows[0].update(model="F70", model_original="F70 explicit source label",
                                                                          text_quality="retained", region_mapping_version="region-map-2"))
    update_table(bundle, "facts", lambda rows: rows[0].update(derivative="G68", region="CN"))
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["conversion_version"] = "parser-2"
    manifest_path.write_text(json.dumps(manifest))
    second = commit(adapter, driver)
    assert second["import_run_id"] != first["import_run_id"]
    for label, ids in identities.items():
        if label != "ImportRun":
            assert set(driver.state["nodes"][label]) == ids
    assert driver.state["nodes"]["SourceRecord"]["SR-feedback"]["raw_payload_json"] == original_payload
    assert driver.state["nodes"]["CustomerFeedback"]["FB-feedback"]["model_original"] == "F70 explicit source label"
    source = second["sources"][0]
    assert source["first_imported_at"] == first["sources"][0]["first_imported_at"]
    assert source["reconciliation"]["previous_imported_at"] == first["sources"][0]["last_imported_at"]
    assert source["reconciliation"]["previous_parser_version"] == "parser-1"
    assert source["reconciliation"]["already_imported"]
    assert source["reconciliation"]["matched_existing_counts"] == source["counts"]
    assert not any(source["reconciliation"]["created_counts"].values())
    assert source["mapping_snapshot"]["native_versions"] == {"region_mapping_version": ["region-map-2"]}
    assert driver.state["events"][3]["before"] == first["sources"][0]


def test_reconciliation_of_legacy_import_does_not_invent_historical_timestamps_or_snapshots(bundle):
    driver = Driver()
    adapter = adapter_for(bundle)
    commit(adapter, driver)
    for source in driver.state["nodes"]["SourceFile"].values():
        for key in ("first_imported_at", "first_import_run_id", "last_imported_at", "last_import_run_id", "import_snapshot_json"):
            source.pop(key)
    driver.state["events"].clear()
    result = commit(adapter, driver)
    for source in result["sources"]:
        reconciliation = source["reconciliation"]
        assert reconciliation["already_imported"]
        assert source["first_imported_at"] is None
        assert source["first_import_run_id"] is None
        assert source["last_imported_at"]
        assert reconciliation["previous_imported_at"] is None
        assert reconciliation["previous_import_run_id"] is None
        assert not reconciliation["historical_import_time_known"]
        assert not reconciliation["previous_snapshot_available"]
        assert reconciliation["matched_existing_counts"] == source["counts"]
    for event in driver.state["events"]:
        assert event["before"]["counts"] is None
        assert event["before"]["parser_snapshot"] is None
        assert event["before"]["mapping_snapshot"] is None
        assert event["before"]["first_imported_at"] is None


def test_semantically_equal_stored_json_is_a_valid_reimport(bundle):
    driver = Driver()
    adapter = adapter_for(bundle)
    commit(adapter, driver)
    for record in driver.state["nodes"]["SourceRecord"].values():
        for key in ("raw_payload_json", "locator_json"):
            record[key] = json.dumps(json.loads(record[key]), indent=2)
    result = commit(adapter, driver)
    assert result["failures"] == []
    assert all(source["reconciliation"]["already_imported"] for source in result["sources"])


def test_processed_storage_alias_cannot_disguise_a_different_raw_payload(bundle):
    update_table(bundle, "source_records", lambda rows: rows[0].update(raw_payload_json='{"text":"Disguised evidence"}'))
    with pytest.raises(BundleError, match="fields collide"):
        adapter_for(bundle).prepare_import({})


def reviewed_mapping(**overrides):
    return {"id": "reviewed-controls", "topic": "Climate controls", "relation": "direct", "fact_ids": ["FACT-study"],
            "reason": "Exact native study cell reviewed", "reviewed_by": "reviewer-test",
            "reviewed_at": "2026-10-11T12:00:00Z", **overrides}


def test_manifest_fingerprint_binds_source_parser_and_reviewed_mapping_versions_independent_of_order(bundle):
    adapter = adapter_for(bundle)
    original = adapter.prepare_import({}).evidence_manifest
    mappings = [reviewed_mapping(mapping_version="review-1"),
                reviewed_mapping(id="reviewed-second", mapping_version="review-2")]
    mapped = adapter.prepare_import({"study_mappings": mappings}).evidence_manifest
    assert mapped["source_manifest_hash"] == original["source_manifest_hash"]
    assert mapped["manifest_hash"] != original["manifest_hash"]
    assert json.loads(mapped["study_mapping_versions_json"]) == [["reviewed-controls", "review-1"], ["reviewed-second", "review-2"]]
    update_table(bundle, "source_files", lambda rows: rows.reverse())
    assert adapter.prepare_import({"study_mappings": list(reversed(mappings))}).evidence_manifest == mapped
    upgraded = adapter.prepare_import({"study_mappings": [dict(mappings[0], mapping_version="review-3"), mappings[1]]}).evidence_manifest
    assert upgraded["manifest_hash"] != mapped["manifest_hash"]
    assert upgraded["source_manifest_hash"] == mapped["source_manifest_hash"]
    update_table(bundle, "source_files", lambda rows: rows[0].update(conversion_version="source-parser-2"))
    source_upgrade = adapter.prepare_import({"study_mappings": mappings}).evidence_manifest
    assert source_upgrade["source_manifest_hash"] != mapped["source_manifest_hash"]
    assert source_upgrade["manifest_hash"] != mapped["manifest_hash"]
    path = bundle / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["conversion_version"] = "bundle-parser-2"
    path.write_text(json.dumps(manifest))
    bundle_upgrade = adapter.prepare_import({"study_mappings": mappings}).evidence_manifest
    assert bundle_upgrade["source_manifest_hash"] == source_upgrade["source_manifest_hash"]
    assert bundle_upgrade["manifest_hash"] != source_upgrade["manifest_hash"]


def test_reviewed_mapping_file_and_snapshots_retain_exact_versioned_fact_links(bundle, tmp_path):
    path = tmp_path / "reviewed-mappings.json"
    envelope = {"mapping_version": "review-1", "mappings": [reviewed_mapping(),
                reviewed_mapping(id="unmapped-seats", topic="Seats", relation="unmapped", fact_ids=[])]}
    path.write_text(json.dumps(envelope))
    driver = Driver()
    result = commit(adapter_for(bundle), driver, {"study_mappings": str(path)})
    sources = {source["id"]: source for source in result["sources"]}
    snapshot = sources["SRC-study"]["mapping_snapshot"]["reviewed_study_mappings"][0]
    assert snapshot["mapping_id"] == "reviewed-controls"
    assert snapshot["mapping_version"] == "review-1"
    assert snapshot["review_state"] == "reviewed"
    assert snapshot["fact_ids"] == ["FACT-study"]
    assert snapshot["reviewed_by"] == "reviewer-test"
    assert (snapshot["id"], "direct", "FACT-study") in driver.state["links"]
    assert sources["SRC-study"]["counts"]["study_topic_mappings"] == 1
    assert sources["SRC-feedback"]["counts"]["study_topic_mappings"] == 0
    assert sources["SRC-feedback"]["mapping_snapshot"]["reviewed_study_mappings"] == []
    assert sources["SRC-study"]["mapping_snapshot"]["bundle_unmapped_decisions"][0]["fact_ids"] == []
    assert result["counts"]["study_topic_mappings"] == 2
    assert result["signals"] == []
    assert json.loads(result["evidence_manifest"]["study_mapping_versions_json"]) == [
        ["reviewed-controls", "review-1"], ["unmapped-seats", "review-1"],
    ]


@pytest.mark.parametrize("mapping", [
    reviewed_mapping(reviewed_by=""), reviewed_mapping(reviewed_at=None),
    reviewed_mapping(review_state="unreviewed"), reviewed_mapping(fact_ids="FACT-study"),
    reviewed_mapping(fact_ids=["missing-fact"]), reviewed_mapping(relation="unmapped"),
    reviewed_mapping(relation="direct", fact_ids=[]), reviewed_mapping(mapping_version=True),
])
def test_invalid_or_unreviewed_mapping_is_rejected_before_any_data_write(bundle, mapping):
    adapter = adapter_for(bundle)
    with pytest.raises(BundleError):
        adapter.prepare_import({"study_mappings": {"mapping_version": "review-1", "mappings": [mapping]}})


@pytest.mark.parametrize("body", [
    {"allow_partial": True}, {"raw_data_dir": "/not-a-processed-bundle"}, {"regions": ["US"]},
    {"processed_dir": ""}, {"processed_dir": None}, {"study_mappings": None},
    {"study_mappings": {"mapping_version": "1", "unexpected": [], "mappings": []}},
])
def test_unexpected_or_missing_request_values_are_rejected(bundle, body):
    with pytest.raises(BundleError):
        adapter_for(bundle).prepare_import(body)


@pytest.mark.parametrize("status", ["partial", "failed", None])
def test_backend_only_accepts_completed_converter_status(bundle, status):
    path = bundle / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["status"] = status
    path.write_text(json.dumps(manifest))
    with pytest.raises(BundleError, match="Refusing to import"):
        adapter_for(bundle).prepare_import({})


def test_backend_cannot_commit_a_standalone_partial_preparation(bundle):
    path = bundle / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["status"] = "partial"
    path.write_text(json.dumps(manifest))
    prepared = loader.prepare_processed_bundle(bundle, allow_partial=True)
    driver = Driver()
    with pytest.raises(BundleError, match="completed"):
        with driver.transaction() as tx:
            adapter_for(bundle).commit_import(prepared, tx, sink_for(tx))
    assert driver.state["nodes"] == {}


def test_missing_processed_input_and_dishonest_manifest_counts_never_succeed(bundle, tmp_path):
    with pytest.raises(BundleError, match="does not exist"):
        adapter_for(tmp_path / "absent").prepare_import({})
    path = bundle / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["counts"]["source_records"] += 1
    path.write_text(json.dumps(manifest))
    with pytest.raises(BundleError, match="Manifest count"):
        adapter_for(bundle).prepare_import({})
    manifest["counts"]["source_records"] -= 1
    path.write_text(json.dumps(manifest))
    (bundle / "facts.jsonl").unlink()
    with pytest.raises(BundleError, match="Required processed table"):
        adapter_for(bundle).prepare_import({})


def test_adapter_requires_real_transaction_results_and_audit_sink(bundle):
    adapter = adapter_for(bundle)
    prepared = adapter.prepare_import({})
    with pytest.raises(BundleError, match="write transaction"):
        adapter.commit_import(prepared, tx=None, audit_sink=lambda *args: None)
    with pytest.raises(BundleError, match="audit sink"):
        adapter.commit_import(prepared, tx=Transaction({"nodes": {}, "links": set(), "events": []}), audit_sink=None)
    class ConsumeOnly:
        def consume(self):
            pass
    tx = SimpleNamespace(run=lambda *args, **kwargs: ConsumeOnly())
    with pytest.raises(BundleError, match="readable"):
        adapter.commit_import(prepared, tx=tx, audit_sink=lambda *args: None)
    tx = SimpleNamespace(run=lambda *args, **kwargs: Result())
    with pytest.raises(BundleError, match="single role1 evidence manifest"):
        adapter.commit_import(prepared, tx=tx, audit_sink=lambda *args: None)


@pytest.mark.parametrize("snapshots", [
    [{"id": "wrong-id", "lock_version": 1}], [{"id": "role1", "lock_version": True}],
    [{"id": "role1", "lock_version": -1}], [{"id": "role1", "lock_version": 1}] * 2,
])
def test_invalid_shared_manifest_lock_results_are_rejected_before_any_source_access(bundle, snapshots):
    calls = []
    def run(query, **params):
        calls.append(query)
        return Result([{"evidence_manifest": snapshot} for snapshot in snapshots])
    with pytest.raises(BundleError):
        adapter_for(bundle).commit_import(adapter_for(bundle).prepare_import({}), SimpleNamespace(run=run), lambda *args: None)
    assert calls == [loader.LOCK_EVIDENCE_MANIFEST]


@pytest.mark.parametrize("records", [None, {"evidence_manifest": {}}, [None], ["invalid-record"]])
def test_malformed_readable_results_cannot_bypass_manifest_or_source_validation(bundle, records):
    calls = []
    def run(query, **params):
        calls.append(query)
        return SimpleNamespace(data=lambda: records)
    adapter = adapter_for(bundle)
    with pytest.raises(BundleError, match="array of readable Neo4j records"):
        adapter.commit_import(adapter.prepare_import({}), SimpleNamespace(run=run), lambda *args: None)
    assert calls == [loader.LOCK_EVIDENCE_MANIFEST]


def test_incorrect_manifest_publication_rolls_back_evidence_without_emitting_audits(bundle):
    class IncorrectManifestTransaction(Transaction):
        def run(self, query, **params):
            result = super().run(query, **params)
            if query == loader.UPDATE_EVIDENCE_MANIFEST:
                result.rows[0]["evidence_manifest"]["active_source_ids_json"] = '["SRC-other"]'
            return result
    driver = Driver()
    adapter = adapter_for(bundle, driver)
    baseline = deepcopy(driver.state)
    with pytest.raises(BundleError, match="differs from the selected source preparation"):
        with driver.transaction(tx_class=IncorrectManifestTransaction) as tx:
            adapter.commit_import(adapter.prepare_import({}), tx, sink_for(tx))
    assert driver.state == baseline
    assert driver.transactions[-1].state["events"] == []
    assert "DocumentChunk" in driver.transactions[-1].state["nodes"]


def test_shared_lock_does_not_weaken_existing_source_identity_validation(bundle):
    class MissingSourceTransaction(Transaction):
        def run(self, query, **params):
            if "MERGE (n:SourceFile" in query and "RETURN row.id AS id, before" in query:
                assert self.manifest_locked
                return Result()
            return super().run(query, **params)
    tx = MissingSourceTransaction({"nodes": {}, "links": set(), "events": []})
    adapter = adapter_for(bundle)
    with pytest.raises(BundleError, match="resolve every SourceFile"):
        adapter.commit_import(adapter.prepare_import({}), tx, sink_for(tx))
    assert tx.state["events"] == []
    assert "SourceFile" not in tx.state["nodes"]


def test_standalone_api_remains_idempotent_with_legacy_sink_and_explicit_database(bundle):
    driver = Driver()
    events = []
    def legacy_sink(tx, event):
        assert tx in driver.transactions
        assert event["action"] == "source_import"
        events.append(event)
    first = loader.load_processed_bundle(driver, bundle, legacy_sink, database="standalone-db")
    second = loader.load_processed_bundle(driver, bundle, legacy_sink, database="standalone-db")
    assert first == second
    assert set(first) == {"import_run_id", "status", "counts", "source_file_ids"}
    assert driver.sessions == ["standalone-db"] * 4
    assert len(events) == 2
    assert len(driver.state["nodes"]["ImportRun"]) == 1
    assert len(driver.state["nodes"]["SourceFile"]) == 3


@pytest.mark.parametrize("explicit,expected", [(True, "cli-db"), (False, "configured-db")])
def test_standalone_cli_selects_explicit_or_canonical_database(bundle, monkeypatch, capsys, explicit, expected):
    from neo4j import GraphDatabase
    driver = Driver()
    settings = SimpleNamespace(neo4j_uri="bolt://localhost:7687", neo4j_user="test-user",
                               neo4j_password=SimpleNamespace(get_secret_value=lambda: "test-password"),
                               data_dir=bundle, neo4j_database="configured-db")
    monkeypatch.setattr(loader, "get_settings", lambda: settings)
    monkeypatch.setattr(GraphDatabase, "driver", lambda *args, **kwargs: driver)
    assert loader.main(["--database", "cli-db"] if explicit else []) == 0
    assert driver.sessions == [expected, expected]
    assert driver.closed
    assert json.loads(capsys.readouterr().out)["status"] == "succeeded"
