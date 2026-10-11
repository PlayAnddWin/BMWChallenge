"""Native Role 1 DTO/graph contracts, with no writes to the evidence database.

Opt-in corpus checks use ROLE1_SOURCE_TEST_NEO4J_URI and optional
ROLE1_SOURCE_TEST_NEO4J_{USER,PASSWORD,DATABASE}, or *_AUTH_DISABLED=1.
These tests only execute managed reads. Existing mutating backend graph
fixtures require a separate disposable BACKEND_TEST_NEO4J_URI.
"""
from copy import deepcopy
import json
import os
import re

import pytest

from models import SourceReference
from services.decisions import DomainError
from services.evidence import EvidenceScope
from services.providers import GraphSourceProvider


class FakeDriver:
    """Exercise the real Role 1 adapter and inspect every query/session."""

    def __init__(self):
        self.entities = {label: [] for label in GraphSourceProvider.LABELS.values()}
        self.annotations = []
        self.mappings = []
        self.queries = []
        self.sessions = []

    def add(self, label, **properties):
        self.entities[label].append(properties)
        return properties

    def session(self, **kwargs):
        self.sessions.append(kwargs)
        driver = self

        class Session:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute_read(self, callback):
                return callback(self)

            def execute_write(self, callback):
                pytest.fail("Source provider must never open a write transaction")

            def run(self, query, **params):
                assert not re.search(r"\b(CREATE|MERGE|SET|DELETE|REMOVE|DROP)\b", query)
                driver.queries.append((query, deepcopy(params)))
                return driver.run(query, params)

        return Session()

    def find(self, label, identity):
        return next(entity for entity in self.entities[label] if entity["id"] == identity)

    @staticmethod
    def matches(entity, scope, kind="feedback"):
        model = entity.get("model") if kind == "feedback" else entity.get("derivative")
        return (not scope.get("model") or scope["model"] == model) and (
            not scope.get("derivative") or scope["derivative"] == model) and (
            not scope.get("region") or scope["region"] == entity.get("region", entity.get("market"))) and (
            not scope.get("powertrain") or scope["powertrain"] == entity.get("powertrain")) and (
            not scope.get("source_version") or scope["source_version"] == entity.get("source_version"))

    def fact_row(self, fact, mapping=None):
        record = self.find("SourceRecord", fact["source_record_id"])
        return {"fact": deepcopy(fact), "source_record": deepcopy(record),
                "source_file": deepcopy(self.find("SourceFile", record["source_file_id"])),
                "mapping": deepcopy(mapping)}

    def run(self, query, params):
        lookup = re.search(r"MATCH \(s:(\w+) \{id:\$id\}\)", query)
        if lookup:
            return [{"entity": deepcopy(entity)} for entity in self.entities[lookup[1]]
                    if entity["id"] == params["id"]][:2]
        if "AS source ORDER BY" in query:
            return [{"source": deepcopy(entity)} for entity in self.entities["SourceFile"]]
        if "AS signal ORDER BY" in query:
            return [{"signal": deepcopy(entity)} for entity in self.entities["Signal"]]
        descendant = re.search(r"MATCH \(e:(Fact|DocumentChunk)\)", query)
        if descendant:
            return [{"id": entity["id"]} for entity in self.entities[descendant[1]]
                    if entity.get("source_file_id") == params["source_id"] and (
                        entity.get("source_record_id") == params["record_id"]
                        or params["record_token"] in entity.get("cell_locators_json", ""))]
        if "UNWIND $scopes AS scope" in query:
            assert "WITH DISTINCT fb" in query
            assert "fb.model = scope.model" in query
            assert "fb.vehicle_line" not in query
            return [{"id": feedback["id"], "source_version": feedback["source_version"],
                     "source_label": feedback.get("source_label"),
                     "annotations": [deepcopy(annotation) for annotation in self.annotations
                                     if annotation["feedback_id"] == feedback["id"]]}
                    for feedback in self.entities["CustomerFeedback"]
                    if any(self.matches(feedback, scope) for scope in params["scopes"])]
        if "MATCH (fb:CustomerFeedback)" in query:
            assert "fb.model = $model" in query
            feedbacks = sorted(self.entities["CustomerFeedback"], key=lambda value: value["id"])
            rows = []
            for feedback in feedbacks:
                if not self.matches(feedback, params) or not any(
                        term in feedback["original_text"].lower() for term in params["terms"]):
                    continue
                records = [record for record in self.entities["SourceRecord"]
                           if record.get("feedback_comment_id") == feedback["id"]]
                annotations = [annotation for annotation in self.annotations
                               if annotation["feedback_id"] == feedback["id"]]
                source_refs = []
                annotation_refs = []
                for record in records:
                    source = self.find("SourceFile", record["source_file_id"])
                    source_refs.append({"id": record["id"], "locator_json": record["locator_json"],
                                        "source_file_id": source["id"], "source_sha256": source["sha256"]})
                for annotation in annotations:
                    record = self.find("SourceRecord", annotation["source_record_id"])
                    source = self.find("SourceFile", record["source_file_id"])
                    annotation_refs.append({**annotation, "locator_json": record["locator_json"],
                                            "source_file_id": source["id"], "source_sha256": source["sha256"]})
                rows.append({"feedback": deepcopy(feedback), "source_records": source_refs,
                             "annotations": annotation_refs})
            return rows[:params["limit"]]
        if "MATCH (fact:Fact {kind: 'sales'})" in query:
            assert "fact.model" not in query
            return [self.fact_row(fact) for fact in self.entities["Fact"]
                    if fact.get("kind") == "sales" and fact["year"] == params["year"]
                    and (not params.get("region") or fact["region"] == params["region"])
                    and (not params.get("source_version") or fact["source_version"] == params["source_version"])]
        if "fact.kind STARTS WITH 'survey'" in query:
            matching = [fact for fact in self.entities["Fact"] if fact["kind"].startswith("survey")
                        and self.matches(fact, {**params, "powertrain": None}, "survey")]
            if params.get("topic"):
                return [self.fact_row(fact, mapping) for mapping in self.mappings
                        if mapping["topic"] == params["topic"] for fact in matching
                        if fact["id"] in mapping["fact_ids"]]
            return [self.fact_row(fact) for fact in matching[:200]]
        if "MATCH (mapping:StudyTopicMapping" in query:
            return [{"mapping": deepcopy(mapping)} for mapping in self.mappings
                    if mapping["topic"] == params["topic"]]
        if "MATCH (chunk:DocumentChunk)" in query:
            chunks = sorted(self.entities["DocumentChunk"], key=lambda value: value["id"])
            rows = []
            for chunk in chunks:
                if (params.get("derivative") and chunk.get("derivative") != params["derivative"]
                        or params.get("region") and chunk.get("market") != params["region"]
                        or params.get("source_version") and chunk["source_version"] != params["source_version"]
                        or params.get("document_kind") and chunk["document_kind"] != params["document_kind"]
                        or not any(term in chunk["original_text"].lower() for term in params["terms"])):
                    continue
                record = self.find("SourceRecord", chunk["source_record_id"])
                rows.append({"chunk": deepcopy(chunk), "source_record": deepcopy(record),
                             "source_file": deepcopy(self.find("SourceFile", record["source_file_id"]))})
            return rows[:params["limit"]]
        pytest.fail(f"Unexpected source query: {query}")


def add_source(driver, identity, filename, version="a" * 64, sha=None, kind="survey"):
    return driver.add("SourceFile", id=identity, filename=filename, kind=kind,
                      source_version=version, sha256=sha or version)


def add_record(driver, source, identity, locator, payload, **extra):
    return driver.add("SourceRecord", id=identity, source_file_id=source["id"],
                      source_version=source["source_version"], locator_json=json.dumps(locator),
                      raw_payload_json=json.dumps(payload), **extra)


def add_fact(driver, source, record, identity, locator, **extra):
    return driver.add("Fact", id=identity, source_file_id=source["id"],
                      source_record_id=record["id"], source_version=source["source_version"],
                      locator_json=json.dumps(locator), **extra)


def reference(entity, locator=None, record_id=None):
    return {"source_id": entity["source_id"], "record_id": record_id or entity["id"],
            "source_version": entity["source_version"],
            "locator": deepcopy(locator if locator is not None else entity["locator"]),
            "claim_key": "native-evidence", "relation": "context", "validation_state": "verified"}


@pytest.fixture
def native_graph():
    driver = FakeDriver()
    source = add_source(driver, "SRC-STUDY", "study.xlsx", sha="b" * 64)
    locator = {"filename": "study.xlsx", "sheet": "CN_2025", "row": 4}
    record = add_record(driver, source, "SR-STUDY", locator,
                        {"columns": [{"header": "Question", "value": "Native rating"},
                                     {"header": "Unit", "value": "rating"},
                                     {"header": "G68_CN", "value": 8.1}]})
    add_fact(driver, source, record, "FACT-STUDY", {**locator, "column": 3, "column_header": "G68_CN"},
             kind="survey_cell", derivative="G68", region="CN", value=8.1, source_value=8.1,
             value_provenance="source_numeric_cell", mapping_strength="unmapped")
    pdf = add_source(driver, "SRC-PDF", "report.pdf", version="c" * 64, kind="document")
    text = "Leading text\nNative BMW passage.\nTrailing text"
    page = add_record(driver, pdf, "SR-PAGE", {"filename": "report.pdf", "page": 3, "printed_page": None},
                      {"text": text, "extraction": "pypdf", "page_text_empty": False})
    start = text.index("Native")
    driver.add("DocumentChunk", id="DOC-NATIVE", source_file_id=pdf["id"], source_record_id=page["id"],
               source_version=pdf["source_version"], document_kind="corporate_report", page=3,
               chunk_number=2, start_offset=start, end_offset=start + len("Native BMW passage."),
               original_text="Native BMW passage.", applicability="unknown")
    driver.add("Signal", id="SIGNAL-NATIVE", revision=2, evidence_links_json="[]")
    driver.add("SignalRevision", id="SIGNAL-NATIVE:2", revision=2,
               payload_json=json.dumps({"summary": "Reviewed native evidence", "source_scope": {"regions": ["CN"]}}))
    return driver, GraphSourceProvider(driver, "role1-evidence")


def add_distribution(driver):
    source = driver.find("SourceFile", "SRC-STUDY")
    categories = ["I Hate It", "A Failure", "Unsatisfactory", "Satisfactory", "Excellent", "Delightful", "I Love It"]
    values = [.01, .02, .07, .2, .2, .2, .3]
    cells = []
    records = []
    for row, (category, value) in enumerate(zip(categories, values), 10):
        locator = {"filename": source["filename"], "sheet": "CN_2025", "row": row}
        record = add_record(driver, source, f"SR-DIST-{row}", locator,
                            {"columns": [{"header": "Category", "value": category},
                                         {"header": "Unit", "value": "% of column, sample base"},
                                         {"header": "G68_CN", "value": value}]})
        records.append(record)
        cells.append({**locator, "source_record_id": record["id"], "column": 3,
                      "column_header": "G68_CN", "response_category": category,
                      "value_provenance": "source_numeric_cell"})
    locator = {"filename": source["filename"], "sheet": "CN_2025", "question": "Native rating", "cells": cells}
    fact = add_fact(driver, source, records[0], "FACT-DISTRIBUTION", locator,
                    kind="survey_distribution", derivative="G68", region="CN",
                    distribution_json=json.dumps(dict(zip(categories, values))),
                    category_labels_json=json.dumps(categories), cell_locators_json=json.dumps(cells),
                    scale_provenance="not_reported", mapping_strength="unmapped")
    return fact, cells


@pytest.mark.parametrize("kind,identity", [("source", "SRC-STUDY"), ("record", "SR-STUDY"),
                                          ("chunk", "DOC-NATIVE"), ("fact", "FACT-STUDY"),
                                          ("signal", "SIGNAL-NATIVE"), ("signal_revision", "SIGNAL-NATIVE:2")])
def test_allowlisted_get_binds_explicit_database_and_native_json(native_graph, kind, identity):
    driver, provider = native_graph
    entity = provider.get(kind, identity)
    assert entity["id"] == identity
    assert not any(key.endswith("_json") for key in entity)
    assert driver.sessions and all(session == {"database": "role1-evidence", "default_access_mode": "READ"}
                                   for session in driver.sessions)
    assert f"MATCH (s:{provider.LABELS[kind]} {{id:$id}})" in driver.queries[0][0]
    assert driver.queries[0][1] == {"id": identity}
    if kind in {"record", "fact"}:
        assert entity["source_id"] == entity["source_file_id"] == "SRC-STUDY"
        assert entity["source_version"] == "a" * 64
        assert entity["source_sha256"] == "b" * 64
        assert entity["source_file"]["sha256"] == "b" * 64
    if kind == "fact":
        assert entity["locator"]["column"] == 3
        assert entity["source_record_locator"] == provider.get("record", "SR-STUDY")["locator"]
        assert entity["source_locator"] == entity["locator"]
    if kind == "chunk":
        assert entity["locator"] == {"filename": "report.pdf", "page": 3, "printed_page": None}
        assert entity["source_record"]["raw_payload"]["text"][entity["start_offset"]:entity["end_offset"]] == entity["original_text"]
    if kind == "signal_revision":
        assert entity["payload"]["source_scope"] == {"regions": ["CN"]}


@pytest.mark.parametrize("kind", list(GraphSourceProvider.LABELS))
def test_duplicate_ids_fail_for_every_entity_kind(native_graph, kind):
    driver, provider = native_graph
    driver.add(provider.LABELS[kind], id="DUPLICATE")
    driver.add(provider.LABELS[kind], id="DUPLICATE")
    with pytest.raises(DomainError) as error:
        provider.get(kind, "DUPLICATE")
    assert error.value.status == 503


def test_kind_and_id_are_not_interpolated_as_untrusted_cypher(native_graph):
    driver, provider = native_graph
    with pytest.raises(DomainError) as error:
        provider.get("SourceFile) MATCH (n)", "ID")
    assert error.value.status == 422 and not driver.queries
    identity = "id'} MATCH (n) RETURN n //"
    with pytest.raises(DomainError) as error:
        provider.get("record", identity)
    assert error.value.status == 404
    assert identity not in driver.queries[0][0]
    assert driver.queries[0][1]["id"] == identity


@pytest.mark.parametrize("properties", [
    {"distribution_json": "not-json"}, {"distribution_json": "[]"},
    {"cell_locators_json": "{}"}, {"cell_locators_json": "[3]"},
    {"locator_json": '{"cells":{}}'}, {"payload_json": "null"},
    {"source_locator": {"cells": "not-cells"}}, {"source_locator": {"cells": [3]}},
    {"category_labels_json": "{}"}, {"distribution_json": '{"category":NaN}'},
    {"source_file_id": "NATIVE", "source_id": "OTHER"},
    {"locator_json": '{"row":1}', "locator": {"row": 1.0}},
])
def test_malformed_native_metadata_fails_closed(properties):
    with pytest.raises(DomainError) as error:
        GraphSourceProvider._normalize(properties)
    assert error.value.status == 503


def test_fact_verifies_stored_cell_with_fact_or_parent_record_id(native_graph):
    _, provider = native_graph
    fact = provider.get("fact", "FACT-STUDY")
    assert provider.verify_reference(reference(fact)) is True
    assert provider.verify_reference(reference(fact, record_id=fact["source_record_id"])) is True
    row = provider.get("record", fact["source_record_id"])
    assert provider.verify_reference(reference(fact, row["locator"])) is False
    # Column 2 exists in the raw row, but there is no imported fact for it.
    assert provider.verify_reference(reference(fact, {**fact["locator"], "column": 2}, row["id"])) is False
    assert provider.verify_reference(reference(fact, {**fact["locator"], "quote": "Invented rating"})) is False
    wrong_hash = {**reference(fact), "source_sha256": "d" * 64}
    assert provider.verify_reference(wrong_hash) is False
    assert provider.verify_reference({**reference(fact), "source_sha256": "b" * 64}) is True


@pytest.mark.parametrize("change", [{"row": True}, {"row": 4.0}, {"column": 3.0}, {"column": True},
                                    {"column_header": None}, {"row": 5}, {"column_header": "G60"}])
def test_fact_reference_requires_exact_json_types_and_coordinates(native_graph, change):
    _, provider = native_graph
    fact = provider.get("fact", "FACT-STUDY")
    assert provider.verify_reference(reference(fact, {**fact["locator"], **change})) is False


@pytest.mark.parametrize("field,value", [("source_version", "wrong"), ("source_file_id", "SRC-PDF"),
                                        ("source_sha256", "wrong"), ("source_value", 9), ("value", 9)])
def test_native_fact_parent_or_cell_drift_is_an_integrity_failure(native_graph, field, value):
    driver, provider = native_graph
    driver.find("Fact", "FACT-STUDY")[field] = value
    with pytest.raises(DomainError) as error:
        provider.get("fact", "FACT-STUDY")
    assert error.value.status == 503


def test_native_distribution_preserves_all_exact_cell_metadata(native_graph):
    driver, provider = native_graph
    raw, cells = add_distribution(driver)
    fact = provider.get("fact", raw["id"])
    assert fact["cell_locators"] == fact["locator"]["cells"] == cells
    assert fact["category_labels"] == [cell["response_category"] for cell in cells]
    assert fact["source_locator"]["row"] == cells[0]["row"]
    assert "row" not in fact["locator"]
    for locator in (fact["locator"], fact["source_locator"], cells[-1]):
        assert provider.verify_reference(reference(fact, locator)) is True
    assert provider.verify_reference(reference(fact, cells[-1], cells[-1]["source_record_id"])) is True
    changed = deepcopy(fact["locator"])
    changed["cells"][-1]["value_provenance"] = "invented"
    assert provider.verify_reference(reference(fact, changed)) is False
    stripped = {key: value for key, value in cells[-1].items() if key != "response_category"}
    assert provider.verify_reference(reference(fact, stripped)) is False


def test_distribution_cell_metadata_or_parent_version_drift_fails(native_graph):
    driver, provider = native_graph
    raw, cells = add_distribution(driver)
    changed = deepcopy(cells)
    changed[-1]["response_category"] = "Invented category"
    raw["cell_locators_json"] = json.dumps(changed)
    with pytest.raises(DomainError, match="conflicting metadata"):
        provider.get("fact", raw["id"])
    raw["cell_locators_json"] = json.dumps(cells)
    driver.find("SourceRecord", cells[-1]["source_record_id"])["source_version"] = "wrong"
    with pytest.raises(DomainError) as error:
        provider.get("fact", raw["id"])
    assert error.value.status == 503


def test_chunk_and_parent_page_references_verify_exact_optional_span(native_graph):
    _, provider = native_graph
    chunk = provider.get("chunk", "DOC-NATIVE")
    ref = reference(chunk)
    assert SourceReference.model_validate(ref).locator["printed_page"] is None
    assert provider.verify_reference(ref) is True
    offsets = {key: chunk[key] for key in ("start_offset", "end_offset")}
    full = {**chunk["locator"], **offsets, "chunk_number": chunk["chunk_number"], "quote": chunk["original_text"]}
    assert provider.verify_reference(reference(chunk, full)) is True
    span = {**chunk["locator"], "span": {**offsets, "quote": chunk["original_text"]}}
    assert provider.verify_reference(reference(chunk, span, chunk["source_record_id"])) is True
    for bad in ({"quote": "Invented quote"}, {"start_offset": chunk["start_offset"] + 1},
                {"start_offset": float(chunk["start_offset"]), "end_offset": chunk["end_offset"]},
                {"chunk_number": True}, {"page": 4}, {"printed_page": "iii"},
                {"span": {**offsets, "quote": "Invented quote"}}, {"unreported_coordinate": 1}):
        assert provider.verify_reference(reference(chunk, {**chunk["locator"], **bad})) is False


@pytest.mark.parametrize("field,value", [("start_offset", -1), ("start_offset", True), ("end_offset", 999),
                                        ("chunk_number", 0), ("page", 4), ("printed_page", "iii"),
                                        ("original_text", "Invented quote")])
def test_chunk_requires_exact_parent_page_text_and_stored_offsets(native_graph, field, value):
    driver, provider = native_graph
    driver.find("DocumentChunk", "DOC-NATIVE")[field] = value
    with pytest.raises(DomainError) as error:
        provider.get("chunk", "DOC-NATIVE")
    assert error.value.status == 503


def test_page_text_drift_rejects_chunk_even_when_chunk_metadata_is_unchanged(native_graph):
    driver, provider = native_graph
    page = driver.find("SourceRecord", "SR-PAGE")
    page["raw_payload_json"] = json.dumps({"text": "A different page"})
    with pytest.raises(DomainError, match="parent page/span"):
        provider.get("chunk", "DOC-NATIVE")


def test_legacy_synthetic_row_and_chunk_still_verify_only_exact_locators():
    driver = FakeDriver()
    driver.add("SourceFile", id="LEGACY-SOURCE", sha256="legacy-hash")
    driver.add("SourceRecord", id="LEGACY-ROW", source_id="LEGACY-SOURCE", source_version="legacy-hash",
               locator_json='{"sheet":"Feedback","row":2}', raw_payload_json='{"original_cell":"Synthetic evidence"}')
    driver.add("DocumentChunk", id="LEGACY-CHUNK", source_id="LEGACY-SOURCE", source_version="legacy-hash",
               locator_json='{"page":3}')
    provider = GraphSourceProvider(driver, "legacy-fixtures")
    for kind, identity in (("record", "LEGACY-ROW"), ("chunk", "LEGACY-CHUNK")):
        entity = provider.get(kind, identity)
        assert provider.verify_reference(reference(entity)) is True
        assert provider.verify_reference(reference(entity, {**entity["locator"], "quote": "Invented evidence"})) is False
        assert provider.verify_reference({**reference(entity), "source_version": "wrong"}) is False
    driver.add("SourceRecord", **deepcopy(driver.find("DocumentChunk", "LEGACY-CHUNK")))
    assert provider.verify_reference(reference(provider.get("chunk", "LEGACY-CHUNK"))) is False


def add_feedback(driver, source, identity, model, region, powertrain, annotation_specs=()):
    feedback = driver.add("CustomerFeedback", id=identity, source_file_id=source["id"],
                          source_version=source["source_version"], original_text="Native seat control feedback.",
                          model=model, region=region, powertrain=powertrain, powertrain_original="BEVE" if powertrain == "BEV" else powertrain,
                          source_label="opaque-source-label")
    record = add_record(driver, source, f"SR-{identity}",
                        {"filename": source["filename"], "sheet": "Feedback_Explorer", "row": len(driver.entities["SourceRecord"]) + 2},
                        {"columns": [{"header": "ID", "value": identity},
                                     {"header": "Text", "value": feedback["original_text"]}]},
                        feedback_comment_id=identity)
    for index, (topic, issue, original_type) in enumerate(annotation_specs):
        driver.annotations.append({"id": f"ANN-{identity}-{index}", "feedback_id": identity,
                                   "source_record_id": record["id"], "vfc2_original": topic,
                                   "vfc3_original": issue, "feedback_type_original": original_type,
                                   "assignment_method": "native"})
    return feedback


@pytest.fixture
def feedback_graph():
    driver = FakeDriver()
    source = add_source(driver, "SRC-FEEDBACK", "feedback.xlsx", version="f" * 64, kind="feedback")
    add_feedback(driver, source, "COMMENT-A", "G70", "US", "BEV", [("Seats", "Controls", "DTU"), ("Seats", "Controls", "Likes")])
    add_feedback(driver, source, "COMMENT-B", "G70", "US", "BEV", [("Seats", "Comfort", "DTU")])
    add_feedback(driver, source, "COMMENT-C", "G70", "EU", "ICE", [("Seats", "Controls", "DTU")])
    add_feedback(driver, source, "COMMENT-D", "G60", "US", "BEV")
    add_feedback(driver, source, "COMMENT-E", "G68", "CN", "BEV")
    return driver, GraphSourceProvider(driver, "feedback-db")


def test_feedback_get_and_search_keep_canonical_identity_and_source_row_provenance(feedback_graph):
    driver, provider = feedback_graph
    assert provider.get("feedback", "COMMENT-A")["model"] == "G70"
    entities = provider.search_feedback("seat", EvidenceScope(model="G70", region="United States", powertrain="BEVE"), limit=2)
    assert [entity["id"] for entity in entities] == ["COMMENT-A", "COMMENT-B"]
    assert len(entities[0]["annotations"]) == 2
    source_ref = entities[0]["source_records"][0]
    assert source_ref["id"] == source_ref["source_record_id"] == "SR-COMMENT-A"
    assert source_ref["source_version"] == "f" * 64
    assert source_ref["source_id"] == source_ref["source_file_id"] == "SRC-FEEDBACK"
    assert source_ref["locator"] == source_ref["source_locator"]
    assert provider.verify_reference(reference(entities[0], source_ref["locator"], source_ref["id"])) is True
    query_params = [params for query, params in driver.queries if "MATCH (fb:CustomerFeedback)" in query]
    assert query_params[0]["model"] == "G70" and query_params[0]["vehicle_line"] is None
    assert query_params[0]["region"] == "US" and query_params[0]["powertrain"] == "BEV"
    assert all(session["database"] == "feedback-db" for session in driver.sessions)


def test_array_feedback_scopes_fan_out_deduplicate_aliases_and_apply_global_limit(feedback_graph):
    driver, provider = feedback_graph
    entities = provider.search_feedback("seat", {"derivatives": ["G70", "BMW G70", "G60"],
                                                "regions": ["US", "EU"], "powertrains": ["BEV", "ICE"],
                                                "source_versions": ["f" * 64]}, limit=3)
    assert [entity["id"] for entity in entities] == ["COMMENT-A", "COMMENT-B", "COMMENT-C"]
    queries = [(query, params) for query, params in driver.queries if "MATCH (fb:CustomerFeedback)" in query]
    assert len(queries) == 8
    assert {params["model"] for _, params in queries} == {"G60", "G70"}
    assert all(params["limit"] == 3 and params["source_version"] == "f" * 64 for _, params in queries)
    assert all(params["vehicle_line"] is None for _, params in queries)


def test_comment_counts_union_canonical_ids_and_keep_annotations_separate(feedback_graph):
    driver, provider = feedback_graph
    result = provider.get_comment_cohort_counts({"derivatives": ["G70", "BMW G70"],
                                                 "regions": ["US", "EU"], "powertrains": ["BEV", "ICE"]})
    assert result["cohort_distinct_comment_count"] == 3
    assert result["cohort_annotation_count"] == 4
    assert result["topic_bucket_distinct_comments"] == {"Seats": 3}
    assert result["topic_annotation_counts"] == {"Seats": 4}
    assert result["issue_label_distinct_comments"] == {"Seats :: Comfort": 1, "Seats :: Controls": 2}
    assert result["issue_annotation_counts"] == {"Seats :: Comfort": 1, "Seats :: Controls": 3}
    assert result["annotation_counts_by_original_type"] == {"DTU": {"distinct_comments": 3, "annotation_count": 3},
                                                             "Likes": {"distinct_comments": 1, "annotation_count": 1}}
    assert result["source_label_counts"] == {"opaque-source-label": 3}
    assert result["source_labels_are_opaque"] is True
    assert len(driver.queries) == 1
    assert len(driver.queries[0][1]["scopes"]) == 4
    assert provider.get_comment_cohort_counts({"source_versions": ["not-imported"]})["cohort_distinct_comment_count"] == 0


def test_multiple_source_versions_are_fanned_out_and_counted_as_a_distinct_union(feedback_graph):
    driver, provider = feedback_graph
    source = add_source(driver, "SRC-FEEDBACK-V2", "feedback-v2.xlsx", version="e" * 64, kind="feedback")
    add_feedback(driver, source, "COMMENT-F", "G70", "US", "BEV", [("Seats", "Controls", "DTU")])
    scope = {"derivatives": ["G70"], "regions": ["US"], "source_versions": ["f" * 64, "e" * 64]}
    feedbacks = provider.search_feedback("seat", scope, limit=3)
    assert [feedback["id"] for feedback in feedbacks] == ["COMMENT-A", "COMMENT-B", "COMMENT-F"]
    assert {feedback["source_version"] for feedback in feedbacks} == {"e" * 64, "f" * 64}
    queries = [params for query, params in driver.queries if "MATCH (fb:CustomerFeedback)" in query]
    assert {params["source_version"] for params in queries} == {"e" * 64, "f" * 64}
    counts = provider.get_comment_cohort_counts(scope)
    assert counts["cohort_distinct_comment_count"] == 3
    assert counts["cohort_annotation_count"] == 4
    assert counts["source_versions"] == ["e" * 64, "f" * 64]
    assert counts["mixed_source_versions"] is True


def test_cohort_duplicate_comment_id_is_not_hidden_by_union_count(feedback_graph):
    driver, provider = feedback_graph
    driver.entities["CustomerFeedback"].append(deepcopy(driver.entities["CustomerFeedback"][0]))
    with pytest.raises(DomainError) as error:
        provider.get_comment_cohort_counts({})
    assert error.value.status == 503


@pytest.mark.parametrize("operation,args", [("search_feedback", ("seat",)), ("get_survey_facts", (None,)),
                                          ("get_sales_facts", ()), ("search_document_chunks", ("BMW",)),
                                          ("get_comment_cohort_counts", ())])
@pytest.mark.parametrize("filter_name", ["topics", "signal_ids"])
def test_nonempty_topic_and_signal_scope_filters_are_explicitly_rejected(native_graph, operation, args, filter_name):
    driver, provider = native_graph
    with pytest.raises(DomainError, match=filter_name) as error:
        getattr(provider, operation)(*args, scope={filter_name: ["requested-filter"]})
    assert error.value.status == 422 and not driver.queries


@pytest.mark.parametrize("scope", [{"regions": "US"}, {"regions": [True]}, {"source_versions": [""]},
                                   {"unknown_filter": []}, {"model": True}, {"regions": ["US"], "region": "EU"},
                                   {"model": "G70", "derivative": "G60"}, {"region": "US", "market": "EU"}])
def test_invalid_scope_is_rejected_before_graph_queries(native_graph, scope):
    driver, provider = native_graph
    with pytest.raises(DomainError) as error:
        provider.search_feedback("seat", scope)
    assert error.value.status == 422 and not driver.queries


@pytest.mark.parametrize("limit", [True, 0, 201, "2", 1.5])
def test_limits_are_strict_provider_contracts(native_graph, limit):
    driver, provider = native_graph
    with pytest.raises(DomainError) as error:
        provider.search_feedback("seat", limit=limit)
    assert error.value.status == 422 and not driver.queries


def test_survey_retrieval_preserves_g68_cn_native_cell_and_reviewed_mapping(native_graph):
    driver, provider = native_graph
    driver.mappings.append({"id": "MAP-NATIVE", "topic": "Native topic", "relation": "direct",
                            "review_state": "reviewed", "fact_ids": ["FACT-STUDY"]})
    result = provider.get_survey_facts("Native topic", {"derivatives": ["G68_CN", "G60"],
                                                       "regions": ["China"], "source_versions": ["a" * 64]})
    assert result["mapping_status"] == "mixed"
    assert [fact["id"] for fact in result["facts"]] == ["FACT-STUDY"]
    fact = result["facts"][0]
    assert fact["derivative"] == "G68" and fact["region"] == "CN"
    assert fact["mapping_strength"] == "direct" and fact["topic_mapping"]["id"] == "MAP-NATIVE"
    assert fact["source_sha256"] == "b" * 64 and fact["source_version"] == "a" * 64
    assert fact["source_locator"]["column"] == 3
    assert fact["respondent_n"] is None
    assert provider.get_survey_facts("Unreviewed topic", {})["mapping_status"] == "unmapped"
    params = [params for query, params in driver.queries if "fact.kind STARTS WITH 'survey'" in query]
    assert {item["model"] for item in params[:2]} == {"G68", "G60"}


def test_survey_distribution_dto_retains_exact_cells_after_parent_enrichment(native_graph):
    driver, provider = native_graph
    raw, cells = add_distribution(driver)
    result = provider.get_survey_facts(None, {"derivatives": ["G68"], "regions": ["CN"]})
    fact = next(fact for fact in result["facts"] if fact["id"] == raw["id"])
    assert fact["cell_locators"] == fact["locator"]["cells"] == cells
    assert provider.verify_reference(reference(fact, fact["source_locator"], fact["source_record_id"])) is True


def add_sales(driver, source, identity, line, region, volume):
    locator = {"filename": source["filename"], "sheet": line, "row": len(driver.entities["SourceRecord"]) + 2}
    record = add_record(driver, source, f"SR-{identity}", locator,
                        {"columns": [{"header": "Region", "value": region},
                                     {"header": "volume_2030", "value": volume}]})
    return add_fact(driver, source, record, identity, {**locator, "column": 2, "column_header": "volume_2030"},
                    kind="sales", vehicle_line=line, region=region, year=2030, volume=volume,
                    source_value=volume, value_provenance="source_numeric_cell", unit="vehicles",
                    applicability="whole_line", powertrain_split="unknown")


def test_sales_fanout_keeps_direct_and_proxy_semantics_without_model_properties(native_graph):
    driver, provider = native_graph
    source = add_source(driver, "SRC-SALES", "sales.xlsx", version="d" * 64, kind="sales")
    add_sales(driver, source, "FACT-SALES-G60-G68", "G60_G68", "RoW", 120)
    add_sales(driver, source, "FACT-SALES-G70", "G70", "RoW", 0)
    result = provider.get_sales_facts({"derivatives": ["G60", "G68", "G70"], "regions": ["RoW"],
                                      "powertrains": ["BEV", "ICE"], "source_versions": ["d" * 64]})
    assert [fact["id"] for fact in result["facts"]] == ["FACT-SALES-G70"]
    assert result["facts"][0]["volume"] == 0
    assert [fact["id"] for fact in result["possible_whole_line_proxy_candidates"]] == ["FACT-SALES-G60-G68"]
    assert result["proxy_candidates_are_not_direct_derivative_volumes"] is True
    assert result["powertrain_split"] == "unknown"
    assert "BEV-specific" in result["whole_line_proxy_warning"]
    assert len(result["scope_results"]) == 6
    assert all("model" not in fact for fact in result["facts"] + result["possible_whole_line_proxy_candidates"])
    assert provider.get_sales_facts(EvidenceScope(vehicle_line="G60_G68", region="RoW"))["facts"][0]["vehicle_line"] == "G60_G68"
    assert provider.get_sales_facts({"derivatives": ["F70"], "regions": ["RoW"]})["facts"] == []


def test_conflicting_sales_line_and_derivative_scope_cannot_promote_a_proxy_to_direct(native_graph):
    driver, provider = native_graph
    with pytest.raises(DomainError, match="filters conflict") as error:
        provider.get_sales_facts({"vehicle_line": "G60_G68", "model": "G60"})
    assert error.value.status == 422 and not driver.queries


def test_document_model_scope_is_translated_to_native_derivative_and_global_limit(native_graph):
    driver, provider = native_graph
    chunk = driver.find("DocumentChunk", "DOC-NATIVE")
    chunk.update(derivative="G68", market="CN")
    duplicate = deepcopy(chunk)
    duplicate["id"] = "DOC-SECOND"
    driver.add("DocumentChunk", **duplicate)
    entities = provider.search_document_chunks("BMW", {"derivatives": ["G68_CN", "G60"], "regions": ["CN"]},
                                               document_kind="corporate_report", limit=1)
    assert [entity["id"] for entity in entities] == ["DOC-NATIVE"]
    queries = [params for query, params in driver.queries if "MATCH (chunk:DocumentChunk)" in query]
    assert {params["derivative"] for params in queries} == {"G68", "G60"}
    assert all(params["limit"] == 1 for params in queries)
    assert provider.verify_reference(reference(entities[0])) is True


@pytest.mark.parametrize("operation,args", [("get_survey_facts", (None,)), ("search_document_chunks", ("BMW",))])
def test_unenforceable_native_powertrain_scope_is_explicit(native_graph, operation, args):
    driver, provider = native_graph
    with pytest.raises(DomainError, match="Powertrain filters") as error:
        getattr(provider, operation)(*args, scope={"powertrains": ["BEV"]})
    assert error.value.status == 422 and not driver.queries


def test_graph_failures_do_not_become_empty_success():
    class BrokenDriver:
        def session(self, **kwargs):
            raise RuntimeError("Graph offline")
    provider = GraphSourceProvider(BrokenDriver(), "role1-db")
    for callback in (provider.list_sources, lambda: provider.search_feedback("seat"),
                     lambda: provider.get_comment_cohort_counts({})):
        with pytest.raises(DomainError) as error:
            callback()
        assert error.value.status == 503


@pytest.fixture
def read_only_corpus():
    uri = os.environ.get("ROLE1_SOURCE_TEST_NEO4J_URI")
    if not uri:
        pytest.skip("Set ROLE1_SOURCE_TEST_NEO4J_URI for opt-in read-only native corpus checks")
    from config import get_settings
    from neo4j import GraphDatabase

    settings = get_settings()
    auth = None if os.environ.get("ROLE1_SOURCE_TEST_NEO4J_AUTH_DISABLED") == "1" else (
        os.environ.get("ROLE1_SOURCE_TEST_NEO4J_USER", settings.neo4j_user),
        os.environ.get("ROLE1_SOURCE_TEST_NEO4J_PASSWORD", settings.neo4j_password.get_secret_value()))
    driver = GraphDatabase.driver(uri, auth=auth, connection_timeout=5)
    try:
        driver.verify_connectivity()
        yield GraphSourceProvider(driver, os.environ.get("ROLE1_SOURCE_TEST_NEO4J_DATABASE", settings.neo4j_database))
    finally:
        driver.close()


def test_read_only_real_fact_cell_and_parent_reference(read_only_corpus):
    provider = read_only_corpus
    rows = provider._read("MATCH (f:Fact {kind:'sales'}) WHERE f.source_file_id IS NOT NULL RETURN f.id AS id LIMIT 1")
    assert rows, "The opted-in database must contain the native Role 1 corpus"
    fact = provider.get("fact", rows[0]["id"])
    assert fact["locator"]["column"] >= 1 and fact["source_sha256"]
    assert provider.verify_reference(reference(fact)) is True
    assert provider.verify_reference(reference(fact, fact["source_locator"], fact["source_record_id"])) is True
    assert provider.verify_reference(reference(fact, {**fact["locator"], "quote": "Not a sourced quote"})) is False


def test_read_only_real_pdf_chunk_matches_page_and_exact_quote(read_only_corpus):
    provider = read_only_corpus
    chunks = provider.search_document_chunks("BMW", document_kind="corporate_report", limit=2)
    assert chunks
    chunk = chunks[0]
    assert chunk["source_record"]["raw_payload"]["text"][chunk["start_offset"]:chunk["end_offset"]] == chunk["original_text"]
    locator = {**chunk["locator"], "start_offset": chunk["start_offset"], "end_offset": chunk["end_offset"],
               "quote": chunk["original_text"]}
    ref = SourceReference.model_validate(reference(chunk, locator)).model_dump()
    assert provider.verify_reference(ref) is True
    ref["locator"]["quote"] += " invented words"
    assert provider.verify_reference(ref) is False


def test_read_only_real_g68_cn_survey_and_g60_sales_proxy(read_only_corpus):
    provider = read_only_corpus
    survey = provider.get_survey_facts(None, {"derivatives": ["G68_CN"], "regions": ["CN"]})
    assert survey["facts"] and all(fact["derivative"] == "G68" and fact["region"] == "CN" for fact in survey["facts"])
    fact = survey["facts"][0]
    assert provider.verify_reference(reference(fact, fact["source_locator"], fact["source_record_id"])) is True
    sales = provider.get_sales_facts({"derivatives": ["G60"], "regions": ["RoW"], "powertrains": ["BEV"]})
    assert sales["facts"] == []
    assert sales["possible_whole_line_proxy_candidates"]
    assert all(fact["vehicle_line"] == "G60_G68" for fact in sales["possible_whole_line_proxy_candidates"])
    assert sales["powertrain_split"] == "unknown"


def test_read_only_real_distribution_preserves_seven_native_cell_locators(read_only_corpus):
    provider = read_only_corpus
    survey = provider.get_survey_facts(None, {"derivatives": ["G70"], "regions": ["US"]})
    distribution = next(fact for fact in survey["facts"] if fact["kind"] == "survey_distribution")
    assert len(distribution["cell_locators"]) == len(distribution["distribution"]) == 7
    assert distribution["locator"]["cells"] == distribution["cell_locators"]
    assert provider.verify_reference(reference(distribution, distribution["source_locator"], distribution["source_record_id"])) is True


def test_read_only_real_feedback_search_retains_canonical_comment_and_row_identities(read_only_corpus):
    provider = read_only_corpus
    feedbacks = provider.search_feedback("seat", {"derivatives": ["G70", "BMW G70"], "regions": ["US"]}, limit=3)
    assert feedbacks and len(feedbacks) <= 3
    assert len({feedback["id"] for feedback in feedbacks}) == len(feedbacks)
    for feedback in feedbacks:
        assert feedback["model"] == "G70" and feedback["region"] == "US"
        assert feedback["source_records"]
        source_ref = feedback["source_records"][0]
        assert source_ref["source_record_id"] == source_ref["id"]
        assert provider.verify_reference(reference(feedback, source_ref["locator"], source_ref["id"])) is True
        row = provider.get("record", source_ref["id"])
        assert feedback["original_text"] in row["raw_payload"]["values_by_header"].values()


def test_read_only_real_cohort_union_matches_distinct_canonical_graph_ids(read_only_corpus):
    provider = read_only_corpus
    result = provider.get_comment_cohort_counts({"derivatives": ["G70", "BMW G70"], "regions": ["US", "EU"]})
    rows = provider._read("""
        MATCH (fb:CustomerFeedback) WHERE fb.model = 'G70' AND fb.region IN ['US', 'EU']
        OPTIONAL MATCH (fb)-[:HAS_ANNOTATION]->(a:FeedbackAnnotation)
        RETURN count(DISTINCT fb.id) AS comments, count(DISTINCT a.id) AS annotations
    """)
    assert result["cohort_distinct_comment_count"] == rows[0]["comments"]
    assert result["cohort_annotation_count"] == rows[0]["annotations"]
    assert result["cohort_distinct_comment_count"] > 0
