"""Idempotently load a converted evidence bundle into Neo4j."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Callable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import get_settings, resolve_path  # noqa: E402


class BundleError(RuntimeError):
    pass


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise BundleError(f"Required processed table is missing: {path.name}")
    rows = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise BundleError(f"Invalid JSONL at {path.name}:{line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise BundleError(f"Expected an object at {path.name}:{line_number}")
            rows.append(value)
    return rows


def _plain_properties(row: Mapping[str, Any], json_fields: set[str] | None = None) -> dict[str, Any]:
    json_fields = json_fields or set()
    result: dict[str, Any] = {}
    for key, value in row.items():
        if key in {"locator", "raw_payload", "distribution", "category_labels", "cell_locators", "pages"}:
            if value is not None:
                result[f"{key}_json"] = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            continue
        if isinstance(value, (dict, list, tuple, set)):
            result[f"{key}_json"] = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        else:
            result[key] = value
    return result


def _batches(rows: Sequence[Mapping[str, Any]], batch_size: int = 500) -> list[list[dict[str, Any]]]:
    return [list(rows[offset:offset + batch_size]) for offset in range(0, len(rows), batch_size)]


CONSTRAINTS = (
    "CREATE CONSTRAINT source_file_id_unique IF NOT EXISTS FOR (n:SourceFile) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT source_record_id_unique IF NOT EXISTS FOR (n:SourceRecord) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT feedback_id_unique IF NOT EXISTS FOR (n:CustomerFeedback) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT annotation_id_unique IF NOT EXISTS FOR (n:FeedbackAnnotation) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT fact_id_unique IF NOT EXISTS FOR (n:Fact) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT document_chunk_id_unique IF NOT EXISTS FOR (n:DocumentChunk) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT import_run_id_unique IF NOT EXISTS FOR (n:ImportRun) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT vfc2_name_unique IF NOT EXISTS FOR (n:VFCTag) REQUIRE n.vfc2 IS UNIQUE",
    "CREATE CONSTRAINT vfc3_id_unique IF NOT EXISTS FOR (n:VFCTag3) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT study_topic_mapping_id_unique IF NOT EXISTS FOR (n:StudyTopicMapping) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT vfc_analysis_id_unique IF NOT EXISTS FOR (n:VFCAnalysis) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT source_rate_difference_id_unique IF NOT EXISTS FOR (n:SourceRateDifference) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT opinion_mix_id_unique IF NOT EXISTS FOR (n:OpinionMix) REQUIRE n.id IS UNIQUE",
)

INDEXES = (
    "CREATE INDEX feedback_scope_index IF NOT EXISTS FOR (n:CustomerFeedback) ON (n.model, n.region, n.powertrain)",
    "CREATE INDEX feedback_source_version_index IF NOT EXISTS FOR (n:CustomerFeedback) ON (n.source_version)",
    "CREATE INDEX fact_scope_index IF NOT EXISTS FOR (n:Fact) ON (n.kind, n.region, n.year)",
    "CREATE INDEX document_chunk_kind_page_index IF NOT EXISTS FOR (n:DocumentChunk) ON (n.document_kind, n.page)",
    "CREATE INDEX study_topic_mapping_topic_index IF NOT EXISTS FOR (n:StudyTopicMapping) ON (n.topic)",
)

UPSERT_SOURCE_FILES = """
UNWIND $rows AS row
MERGE (sf:SourceFile {id: row.id})
SET sf += row.properties
"""

UPSERT_SOURCE_RECORDS = """
UNWIND $rows AS row
MATCH (sf:SourceFile {id: row.source_file_id})
MERGE (sr:SourceRecord {id: row.id})
SET sr += row.properties
MERGE (sr)-[:FROM_SOURCE_FILE]->(sf)
"""

LINK_FEEDBACK_SOURCE_RECORDS = """
UNWIND $rows AS row
MATCH (sr:SourceRecord {id: row.id})
MATCH (fb:CustomerFeedback {id: row.feedback_comment_id})
MERGE (sr)-[:DESCRIBES]->(fb)
"""

UPSERT_FEEDBACK = """
UNWIND $rows AS row
MATCH (sf:SourceFile {id: row.source_file_id})
MERGE (fb:CustomerFeedback {id: row.id})
SET fb += row.properties
MERGE (fb)-[:FROM_SOURCE_FILE]->(sf)
"""

UPSERT_ANNOTATIONS = """
UNWIND $rows AS row
MATCH (fb:CustomerFeedback {id: row.feedback_id})
MATCH (sr:SourceRecord {id: row.source_record_id})
MERGE (a:FeedbackAnnotation {id: row.id})
SET a += row.properties
MERGE (fb)-[:HAS_ANNOTATION]->(a)
MERGE (a)-[:FROM_SOURCE_RECORD]->(sr)
FOREACH (_ IN CASE WHEN row.properties.vfc2_original IS NULL THEN [] ELSE [1] END |
    MERGE (topic:VFCTag {vfc2: row.properties.vfc2_original})
    MERGE (a)-[:TAGGED_VFC2]->(topic)
)
FOREACH (_ IN CASE WHEN row.properties.vfc3_original IS NULL THEN [] ELSE [1] END |
    MERGE (issue:VFCTag3 {id: row.issue_id})
    SET issue.vfc2 = row.properties.vfc2_original,
        issue.vfc3 = row.properties.vfc3_original
    MERGE (a)-[:TAGGED_VFC3]->(issue)
)
FOREACH (_ IN CASE WHEN row.properties.vfc2_original IS NULL OR row.properties.vfc3_original IS NULL THEN [] ELSE [1] END |
    MERGE (topic:VFCTag {vfc2: row.properties.vfc2_original})
    MERGE (issue:VFCTag3 {id: row.issue_id})
    MERGE (issue)-[:SUBCATEGORY_OF]->(topic)
)
"""

UPSERT_FACTS = """
UNWIND $rows AS row
MATCH (sr:SourceRecord {id: row.source_record_id})
MERGE (fact:Fact {id: row.id})
SET fact += row.properties
MERGE (fact)-[:FROM_SOURCE_RECORD]->(sr)
"""

UPSERT_DOCUMENT_CHUNKS = """
UNWIND $rows AS row
MATCH (sf:SourceFile {id: row.source_file_id})
MATCH (sr:SourceRecord {id: row.source_record_id})
MERGE (chunk:DocumentChunk {id: row.id})
SET chunk += row.properties
MERGE (chunk)-[:FROM_SOURCE_RECORD]->(sr)
MERGE (sr)-[:FROM_SOURCE_FILE]->(sf)
"""

UPSERT_STUDY_MAPPING_NODES = """
UNWIND $rows AS row
MERGE (mapping:StudyTopicMapping {id: row.id})
SET mapping += row.properties
"""

UPSERT_STUDY_MAPPING_RELATIONSHIPS = """
UNWIND $rows AS row
MATCH (mapping:StudyTopicMapping {id: row.id})
UNWIND row.fact_ids AS fact_id
MATCH (fact:Fact {id: fact_id})
MERGE (mapping)-[relation:MAPS_TO]->(fact)
SET relation.kind = row.relation
"""


def _clean_source_file(row: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": row["id"], "properties": _plain_properties(row)}


def _clean_source_record(row: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": row["id"], "source_file_id": row["source_file_id"],
            "properties": _plain_properties(row)}


def _clean_feedback(row: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": row["id"], "source_file_id": row["source_file_id"],
            "properties": _plain_properties(row)}


def _clean_annotation(row: Mapping[str, Any]) -> dict[str, Any]:
    issue_key = "\x1f".join((str(row.get("vfc2_original") or ""), str(row.get("vfc3_original") or "")))
    issue_id = "VFC3-" + hashlib.sha256(issue_key.encode("utf-8")).hexdigest()
    return {"id": row["id"], "feedback_id": row["feedback_id"],
            "source_record_id": row["source_record_id"], "issue_id": issue_id,
            "properties": _plain_properties(row)}


def _clean_fact(row: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": row["id"], "source_record_id": row["source_record_id"],
            "properties": _plain_properties(row)}


def _clean_document_chunk(row: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": row["id"], "source_file_id": row["source_file_id"],
            "source_record_id": row["source_record_id"], "properties": _plain_properties(row)}


def _clean_study_mapping(row: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": row["id"], "relation": row["relation"],
            "fact_ids": list(row.get("fact_ids", [])),
            "properties": _plain_properties(row)}


def load_processed_bundle(driver: Any, processed_dir: Path,
                          audit_sink: Callable[[Any, Mapping[str, Any]], None] | None = None,
                          allow_partial: bool = False,
                          study_mappings: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """Load one complete bundle transactionally; injected audit sink shares tx."""
    processed_dir = processed_dir.resolve()
    manifest_path = processed_dir / "manifest.json"
    if not manifest_path.exists():
        raise BundleError("manifest.json is missing; run the raw converter first")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    state = manifest.get("status")
    if state == "failed" or (state != "succeeded" and not allow_partial):
        raise BundleError(f"Refusing to import bundle with status '{state}'; use --allow-partial only after reviewing the manifest")

    source_files = _read_jsonl(processed_dir / "source_files.jsonl")
    source_records = _read_jsonl(processed_dir / "source_records.jsonl")
    feedback = _read_jsonl(processed_dir / "feedback_comments.jsonl")
    annotations = _read_jsonl(processed_dir / "feedback_annotations.jsonl")
    facts = _read_jsonl(processed_dir / "facts.jsonl")
    chunks = _read_jsonl(processed_dir / "document_chunks.jsonl")
    if not source_files:
        raise BundleError("Bundle has no converted source files; an empty import is not considered success")
    if any(not source.get("id") or not source.get("sha256") for source in source_files):
        raise BundleError("Every source file needs a stable ID and SHA-256")
    source_ids = {source.get("id") for source in source_files}
    source_record_ids = {record.get("id") for record in source_records}
    if any(record.get("source_file_id") not in source_ids for record in source_records):
        raise BundleError("A source record references a missing source file")
    if any(comment.get("source_file_id") not in source_ids for comment in feedback):
        raise BundleError("A feedback comment references a missing source file")
    feedback_ids = {comment.get("id") for comment in feedback}
    if any(annotation.get("feedback_id") not in feedback_ids for annotation in annotations):
        raise BundleError("An annotation references a missing canonical comment")
    if any(annotation.get("source_record_id") not in source_record_ids for annotation in annotations):
        raise BundleError("An annotation references a missing source record")
    if any(record.get("feedback_comment_id") not in feedback_ids
           for record in source_records if record.get("feedback_comment_id")):
        raise BundleError("A feedback source record references a missing canonical comment")
    if any(fact.get("source_record_id") not in source_record_ids for fact in facts):
        raise BundleError("A fact references a missing source record")
    if any(chunk.get("source_file_id") not in source_ids or chunk.get("source_record_id") not in source_record_ids
           for chunk in chunks):
        raise BundleError("A document chunk references a missing source file or page record")
    fact_by_id = {fact.get("id"): fact for fact in facts}
    valid_relations = {"direct", "broad", "contradictory", "unmapped"}
    cleaned_mappings = []
    mapping_ids: set[tuple[str, str]] = set()
    for raw in study_mappings:
        mapping = dict(raw)
        required = ("id", "mapping_version", "topic", "relation", "reason", "reviewed_by", "reviewed_at")
        if any(not str(mapping.get(field, "")).strip() for field in required):
            raise BundleError("Study mappings require id, topic, relation, reason, reviewed_by, and reviewed_at")
        if mapping["relation"] not in valid_relations:
            raise BundleError(f"Unsupported study mapping relation: {mapping['relation']}")
        mapping_version = str(mapping["mapping_version"])
        source_mapping_id = str(mapping["id"])
        versioned_key = (source_mapping_id, mapping_version)
        if versioned_key in mapping_ids:
            raise BundleError(f"Duplicate study mapping ID/version: {source_mapping_id}/{mapping_version}")
        mapping_ids.add(versioned_key)
        fact_ids = list(mapping.get("fact_ids") or [])
        if mapping["relation"] == "unmapped" and fact_ids:
            raise BundleError("An unmapped study-topic decision cannot link fact IDs")
        if mapping["relation"] != "unmapped" and not fact_ids:
            raise BundleError("A mapped study-topic decision must cite one or more exact fact IDs")
        for fact_id in fact_ids:
            fact = fact_by_id.get(fact_id)
            if fact is None or not str(fact.get("kind", "")).startswith("survey"):
                raise BundleError(f"Study mapping references an absent or non-survey fact ID: {fact_id}")
        mapping["fact_ids"] = fact_ids
        mapping["mapping_id"] = source_mapping_id
        mapping["id"] = "MAP-" + hashlib.sha256(f"{source_mapping_id}\x1f{mapping_version}".encode("utf-8")).hexdigest()
        mapping["review_state"] = "reviewed"
        cleaned_mappings.append(mapping)

    import_material = json.dumps({
        "source_file_ids": sorted(source_ids),
        "conversion_version": manifest.get("conversion_version"),
        "counts": manifest.get("counts", {}),
        "study_mapping_versions": sorted((item["mapping_id"], item["mapping_version"])
                                          for item in cleaned_mappings),
    }, sort_keys=True, separators=(",", ":"))
    import_id = "IMPORT-" + hashlib.sha256(import_material.encode("utf-8")).hexdigest()
    counts = {"source_files": len(source_files), "source_records": len(source_records),
              "feedback_comments": len(feedback), "feedback_annotations": len(annotations),
              "facts": len(facts), "document_chunks": len(chunks),
              "study_topic_mappings": len(cleaned_mappings)}

    def write_transaction(tx: Any) -> None:
        for batch in _batches([_clean_source_file(row) for row in source_files]):
            tx.run(UPSERT_SOURCE_FILES, rows=batch)
        for batch in _batches([_clean_source_record(row) for row in source_records]):
            tx.run(UPSERT_SOURCE_RECORDS, rows=batch)
        for batch in _batches([_clean_feedback(row) for row in feedback]):
            tx.run(UPSERT_FEEDBACK, rows=batch)
        feedback_source_links = [{"id": row["id"], "feedback_comment_id": row["feedback_comment_id"]}
                                 for row in source_records if row.get("feedback_comment_id")]
        for batch in _batches(feedback_source_links):
            tx.run(LINK_FEEDBACK_SOURCE_RECORDS, rows=batch)
        for batch in _batches([_clean_annotation(row) for row in annotations]):
            tx.run(UPSERT_ANNOTATIONS, rows=batch)
        for batch in _batches([_clean_fact(row) for row in facts]):
            tx.run(UPSERT_FACTS, rows=batch)
        for batch in _batches([_clean_document_chunk(row) for row in chunks]):
            tx.run(UPSERT_DOCUMENT_CHUNKS, rows=batch)
        for batch in _batches([_clean_study_mapping(row) for row in cleaned_mappings]):
            tx.run(UPSERT_STUDY_MAPPING_NODES, rows=batch)
            links = [mapping for mapping in batch if mapping["fact_ids"]]
            if links:
                tx.run(UPSERT_STUDY_MAPPING_RELATIONSHIPS, rows=links)
        tx.run("""
            MERGE (run:ImportRun {id: $id})
            SET run.status = $status, run.manifest_json = $manifest_json,
                run.counts_json = $counts_json, run.source_file_ids_json = $source_file_ids_json
        """, id=import_id, status=state, manifest_json=json.dumps(manifest, ensure_ascii=False, sort_keys=True),
               counts_json=json.dumps(counts, sort_keys=True), source_file_ids_json=json.dumps(sorted(source_ids)))
        if audit_sink is not None:
            audit_sink(tx, {"action": "source_import", "import_run_id": import_id,
                            "id": f"SOURCE-IMPORT-{import_id}",
                            "status": state, "source_file_ids": sorted(source_ids), "counts": counts})

    try:
        with driver.session() as session:
            # Schema setup is separate from the data transaction; every
            # imported record and the injected audit event share one write tx.
            for query in (*CONSTRAINTS, *INDEXES):
                session.run(query).consume()
            session.execute_write(write_transaction)
    except Exception:
        # The transaction rolls back; callers receive the actual failure.
        raise
    return {"import_run_id": import_id, "status": state, "counts": counts,
            "source_file_ids": sorted(source_ids)}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processed-dir", type=Path,
                        help="Converted bundle directory; defaults to BMW_DATA_DIR from root configuration.")
    parser.add_argument("--allow-partial", action="store_true",
                        help="Explicitly import a partial bundle after reviewing its manifest.")
    parser.add_argument("--study-mappings", type=Path,
                        help="Optional reviewed JSON topic-to-exact-survey-fact map; see study_topic_mappings.example.json.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = get_settings()
    except ValueError as exc:
        print(f"Invalid runtime configuration: {exc}", file=sys.stderr)
        return 2
    uri, user, password = settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password.get_secret_value()
    if not all((uri, user, password)):
        print("Set NEO4J_URI, NEO4J_USER, and NEO4J_PASSWORD before loading.", file=sys.stderr)
        return 2
    mappings = []
    if args.study_mappings:
        try:
            raw_mappings = json.loads(resolve_path(args.study_mappings).read_text(encoding="utf-8"))
            if not isinstance(raw_mappings, dict) or not isinstance(raw_mappings.get("mappings"), list):
                raise BundleError("Study mapping file must be an object with a mappings array")
            mapping_version = raw_mappings.get("mapping_version")
            mappings = [{**mapping, "mapping_version": mapping.get("mapping_version", mapping_version)}
                        for mapping in raw_mappings["mappings"]]
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            print(f"Unable to read study mapping file: {exc}", file=sys.stderr)
            return 2
        except BundleError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    try:
        from neo4j import GraphDatabase
    except ImportError:
        print("Neo4j import requires neo4j driver; install pipeline/requirements-data.txt", file=sys.stderr)
        return 2
    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        driver.verify_connectivity()
        processed_dir = resolve_path(args.processed_dir) if args.processed_dir is not None else settings.data_dir
        result = load_processed_bundle(driver, processed_dir, allow_partial=args.allow_partial,
                                       study_mappings=mappings)
    except Exception as exc:
        print(f"Evidence import failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        driver.close()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
