"""Idempotently load a converted evidence bundle into Neo4j."""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import get_settings, resolve_path  # noqa: E402


class BundleError(RuntimeError):
    pass


class PreparedBundle:
    """Validated, in-memory import input; holds no driver, session or transaction."""

    def __init__(self, processed_dir: Path, manifest: dict[str, Any],
                 tables: dict[str, list[dict[str, Any]]], import_run_id: str,
                 counts: dict[str, int], source_snapshots: dict[str, dict[str, Any]],
                 entity_sources: dict[str, dict[str, set[str]]]):
        self.processed_dir = processed_dir
        self.manifest = manifest
        self.tables = tables
        self.import_run_id = import_run_id
        self.status = manifest["status"]
        self.counts = counts
        self.source_snapshots = source_snapshots
        self.source_file_ids = sorted(source_snapshots)
        self.entity_sources = entity_sources
        self.evidence_manifest = _evidence_manifest_properties(
            list(source_snapshots.values()), manifest.get("conversion_version"),
            [(row["properties"]["mapping_id"], row["properties"]["mapping_version"])
             for row in tables["study_topic_mappings"]], import_run_id,
        )


def _finite_json(value: Any, description: str) -> None:
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise BundleError(f"{description} must contain finite JSON values") from exc


def _read_json(path: Path) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BundleError(f"Unable to read {path.name}: {exc}") from exc
    _finite_json(value, path.name)
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise BundleError(f"Required processed table is missing: {path.name}")
    rows = []
    try:
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
                _finite_json(value, f"{path.name}:{line_number}")
                rows.append(value)
    except (OSError, UnicodeError) as exc:
        raise BundleError(f"Unable to read {path.name}: {exc}") from exc
    return rows


def _plain_properties(row: Mapping[str, Any], json_fields: set[str] | None = None) -> dict[str, Any]:
    json_fields = {"locator", "raw_payload", "distribution", "category_labels", "cell_locators", "pages"} | (json_fields or set())
    result: dict[str, Any] = {}
    for key, value in row.items():
        if key in json_fields and value is None:
            continue
        nested = key in json_fields or isinstance(value, (dict, list, tuple, set))
        storage_key = f"{key}_json" if nested else key
        if storage_key in result:
            raise BundleError(f"Processed fields collide with Neo4j storage property: {storage_key}")
        result[storage_key] = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
                               if nested else value)
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
    "CREATE CONSTRAINT evidence_manifest_id_unique IF NOT EXISTS FOR (n:EvidenceManifest) REQUIRE n.id IS UNIQUE",
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

INITIALIZE_EVIDENCE_MANIFEST = """
MERGE (manifest:EvidenceManifest {id: 'role1'})
ON CREATE SET manifest.lock_version = 0, manifest._role1_seed_token = $token
SET manifest.lock_version = coalesce(manifest.lock_version, 0)
RETURN manifest{.*} AS evidence_manifest,
       coalesce(manifest._role1_seed_token = $token, false) AS created
"""

READ_EXISTING_SOURCE_IDENTITIES = """
MATCH (sf:SourceFile)
RETURN sf.id AS id, coalesce(sf.source_version, sf.sha256) AS source_version,
       sf.conversion_version AS conversion_version
ORDER BY id
"""

SEED_EVIDENCE_MANIFEST = """
MATCH (manifest:EvidenceManifest {id: 'role1'})
WHERE manifest._role1_seed_token = $token
SET manifest += $properties
REMOVE manifest._role1_seed_token
RETURN manifest{.*} AS evidence_manifest
"""

# Importers and governance readers must lock this same node before selecting or
# changing evidence. The property dependency acquires a Neo4j write lock before
# the manifest (or any SourceFile) is read. It remains held until the caller's
# transaction commits or rolls back, including its audit/governance writes.
LOCK_EVIDENCE_MANIFEST = """
MERGE (manifest:EvidenceManifest {id: 'role1'})
ON CREATE SET manifest.lock_version = 0
SET manifest.lock_version = coalesce(manifest.lock_version, 0) + 1
RETURN manifest{.*} AS evidence_manifest
"""

UPDATE_EVIDENCE_MANIFEST = """
MATCH (manifest:EvidenceManifest {id: 'role1'})
SET manifest += $properties
RETURN manifest{.*} AS evidence_manifest
"""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _evidence_manifest_properties(sources: Sequence[Mapping[str, Any]], conversion_version: Any,
                                  study_mapping_versions: Sequence[tuple[str, Any]] = (),
                                  import_run_id: str | None = None) -> dict[str, Any]:
    """Stable fingerprints exclude administrative locks and import timestamps.

    source_manifest_hash hashes sorted [id, source_version, conversion_version]
    tuples. manifest_hash additionally binds the bundle conversion version and
    sorted [mapping_id, mapping_version] tuples (versions represented as text).
    """
    identities = [(source.get("id"), source.get("source_version", source.get("sha256")), source.get("conversion_version"))
                  for source in sources]
    if (any(not isinstance(identity[0], str) or not identity[0].strip()
            or not isinstance(identity[1], str) or not identity[1].strip() for identity in identities)
            or len({identity[0] for identity in identities}) != len(identities)):
        raise BundleError("Evidence manifest requires unique source IDs and immutable source versions")
    identities.sort(key=lambda identity: identity[0])
    mapping_versions = sorted((identity, str(version)) for identity, version in study_mapping_versions)
    material = {"source_identities": identities, "conversion_version": conversion_version,
                "study_mapping_versions": mapping_versions}
    properties = {
        "id": "role1", "active_source_ids_json": _canonical_json([identity[0] for identity in identities]),
        "conversion_version": conversion_version,
        "source_manifest_hash": hashlib.sha256(_canonical_json(identities).encode("utf-8")).hexdigest(),
        "manifest_hash": hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest(),
        "study_mapping_versions_json": _canonical_json(mapping_versions),
    }
    if import_run_id is not None:
        properties["import_run_id"] = import_run_id
    return properties


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


SOURCE_IMPORT_METADATA = """
UNWIND $rows AS row
MATCH (sf:SourceFile {id: row.id})
SET sf.last_imported_at = $recorded_at, sf.last_import_run_id = $import_run_id,
    sf.import_snapshot_json = coalesce(row.snapshot_json, sf.import_snapshot_json)
"""

# Only native evidence and ownership are immutable. Converter scope, quality,
# applicability and other interpretation metadata may legitimately improve.
IMMUTABLE_PROPERTIES = {
    "SourceFile": ("sha256", "source_version"),
    "SourceRecord": ("source_file_id", "source_version", "locator_json", "raw_payload_json"),
    "CustomerFeedback": ("source_file_id", "source_version", "original_text", "original_id"),
    "FeedbackAnnotation": ("feedback_id", "source_record_id", "source_file_id"),
    "Fact": ("source_file_id", "source_record_id", "source_version", "locator_json",
             "source_value", "value", "volume", "distribution_json"),
    "DocumentChunk": ("source_file_id", "source_record_id", "source_version", "original_text",
                      "page", "chunk_number", "start_offset", "end_offset"),
    "StudyTopicMapping": ("mapping_id", "mapping_version", "topic", "relation", "reason",
                          "reviewed_by", "reviewed_at", "fact_ids_json", "review_state"),
}

SOURCE_RESERVED_PROPERTIES = {
    "first_imported_at", "last_imported_at", "first_import_run_id", "last_import_run_id",
    "import_snapshot", "import_snapshot_json", "_role1_import_token",
}


def _index_rows(rows: Sequence[dict[str, Any]], table: str) -> dict[str, dict[str, Any]]:
    indexed = {}
    for row in rows:
        identity = row.get("id")
        if not isinstance(identity, str) or not identity.strip():
            raise BundleError(f"Every {table} row needs a nonempty stable ID")
        if identity in indexed:
            raise BundleError(f"Duplicate ID in {table}: {identity}")
        indexed[identity] = row
    return indexed


def _source_version(row: dict[str, Any], source: Mapping[str, Any]) -> None:
    if "source_version" not in row:
        row["source_version"] = source["source_version"]
    if row["source_version"] != source["source_version"]:
        raise BundleError(f"Source version differs from the owning source file: {row['id']}")


def normalize_study_mappings(value: Any) -> list[dict[str, Any]]:
    """Accept a mapping list or the converter's versioned mapping-file envelope."""
    version = None
    if isinstance(value, Mapping):
        if set(value) - {"mapping_version", "mappings"} or "mappings" not in value:
            raise BundleError("Study mapping file must contain only mapping_version and a mappings array")
        version = value.get("mapping_version")
        value = value["mappings"]
    if (not isinstance(value, Sequence) or isinstance(value, (str, bytes))
            or any(not isinstance(row, Mapping) for row in value)):
        raise BundleError("Study mappings must be an array of objects")
    result = [deepcopy(dict(row)) for row in value]
    for row in result:
        if "mapping_version" not in row and version is not None:
            row["mapping_version"] = version
    _finite_json(result, "Study mappings")
    return result


def read_study_mappings(path: Path | str) -> list[dict[str, Any]]:
    return normalize_study_mappings(_read_json(resolve_path(path)))


def prepare_processed_bundle(processed_dir: Path | str, allow_partial: bool = False,
                             study_mappings: Sequence[Mapping[str, Any]] = ()) -> PreparedBundle:
    """Read, validate and clean a bundle without touching the graph or raw inputs."""
    processed_dir = Path(processed_dir).expanduser().resolve()
    if not processed_dir.is_dir():
        raise BundleError(f"Processed bundle directory does not exist: {processed_dir}")
    manifest_path = processed_dir / "manifest.json"
    if not manifest_path.is_file():
        raise BundleError("manifest.json is missing; run the raw converter first")
    manifest = _read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise BundleError("manifest.json must be an object")
    state = manifest.get("status")
    if state not in {"succeeded", "partial"} or (state == "partial" and not allow_partial):
        raise BundleError(f"Refusing to import bundle with status '{state}'; use --allow-partial only after reviewing the manifest")
    declared_counts = manifest.get("counts", {})
    if not isinstance(declared_counts, dict):
        raise BundleError("Manifest counts must be an object")
    issues = manifest.get("issues", [])
    if not isinstance(issues, list) or any(not isinstance(issue, dict) for issue in issues):
        raise BundleError("Manifest issues must be an array of objects")
    if state == "succeeded" and (manifest.get("missing_kinds") or manifest.get("missing_document_kinds")
                                  or any(issue.get("severity") == "error" for issue in issues)):
        raise BundleError("A succeeded manifest cannot contain unresolved conversion errors or missing inputs")

    source_files = _read_jsonl(processed_dir / "source_files.jsonl")
    source_records = _read_jsonl(processed_dir / "source_records.jsonl")
    feedback = _read_jsonl(processed_dir / "feedback_comments.jsonl")
    annotations = _read_jsonl(processed_dir / "feedback_annotations.jsonl")
    facts = _read_jsonl(processed_dir / "facts.jsonl")
    chunks = _read_jsonl(processed_dir / "document_chunks.jsonl")
    if not source_files:
        raise BundleError("Bundle has no converted source files; an empty import is not considered success")
    raw_tables = {"source_files": source_files, "source_records": source_records,
                  "feedback_comments": feedback, "feedback_annotations": annotations,
                  "facts": facts, "document_chunks": chunks}
    indexes = {name: _index_rows(rows, name) for name, rows in raw_tables.items()}
    sources_by_id = indexes["source_files"]
    records_by_id = indexes["source_records"]
    feedback_by_id = indexes["feedback_comments"]
    source_ids = set(sources_by_id)
    source_record_ids = set(records_by_id)
    for source in source_files:
        if any(not isinstance(source.get(field), str) or not source[field].strip()
               for field in ("sha256", "filename", "kind")):
            raise BundleError("Every source file needs a SHA-256, filename and kind")
        if set(source) & SOURCE_RESERVED_PROPERTIES:
            raise BundleError("Source import history is server-owned, not processed input")
        source.setdefault("source_version", source["sha256"])
        if not isinstance(source["source_version"], str) or not source["source_version"].strip():
            raise BundleError("Every source file needs an immutable source version")
        if "conversion_version" not in source and manifest.get("conversion_version") is not None:
            source["conversion_version"] = manifest["conversion_version"]
    if any(record.get("source_file_id") not in source_ids for record in source_records):
        raise BundleError("A source record references a missing source file")
    for record in source_records:
        if not isinstance(record.get("locator"), dict) or not record["locator"]:
            raise BundleError(f"Source record has no original locator: {record['id']}")
        if not isinstance(record.get("raw_payload"), dict) or not record["raw_payload"]:
            raise BundleError(f"Source record has no raw row/page payload: {record['id']}")
        _source_version(record, sources_by_id[record["source_file_id"]])
    if source_ids - {record["source_file_id"] for record in source_records}:
        raise BundleError("Every converted source must have at least one preserved source record")
    if any(comment.get("source_file_id") not in source_ids for comment in feedback):
        raise BundleError("A feedback comment references a missing source file")
    for comment in feedback:
        if not isinstance(comment.get("original_text"), str):
            raise BundleError(f"Feedback comment has no original text: {comment['id']}")
        _source_version(comment, sources_by_id[comment["source_file_id"]])
    feedback_ids = set(feedback_by_id)
    if any(annotation.get("feedback_id") not in feedback_ids for annotation in annotations):
        raise BundleError("An annotation references a missing canonical comment")
    if any(annotation.get("source_record_id") not in source_record_ids for annotation in annotations):
        raise BundleError("An annotation references a missing source record")
    if any(record.get("feedback_comment_id") not in feedback_ids
           for record in source_records if record.get("feedback_comment_id")):
        raise BundleError("A feedback source record references a missing canonical comment")
    described_comments = set()
    for record in source_records:
        if record.get("feedback_comment_id"):
            comment = feedback_by_id[record["feedback_comment_id"]]
            if comment["source_file_id"] != record["source_file_id"]:
                raise BundleError("A feedback source record and its comment belong to different files")
            described_comments.add(comment["id"])
    if feedback_ids - described_comments:
        raise BundleError("A feedback comment has no preserved source record")
    if any(fact.get("source_record_id") not in source_record_ids for fact in facts):
        raise BundleError("A fact references a missing source record")
    if any(chunk.get("source_file_id") not in source_ids or chunk.get("source_record_id") not in source_record_ids
           for chunk in chunks):
        raise BundleError("A document chunk references a missing source file or page record")
    for table in (annotations, facts, chunks):
        for row in table:
            record = records_by_id[row["source_record_id"]]
            row.setdefault("source_file_id", record["source_file_id"])
            if row["source_file_id"] != record["source_file_id"]:
                raise BundleError(f"Evidence and its source record belong to different files: {row['id']}")
            _source_version(row, sources_by_id[row["source_file_id"]])
    for annotation in annotations:
        record = records_by_id[annotation["source_record_id"]]
        if record.get("feedback_comment_id") != annotation["feedback_id"]:
            raise BundleError("An annotation does not describe its source record's canonical comment")
    for fact in facts:
        if not isinstance(fact.get("kind"), str) or not fact["kind"].strip():
            raise BundleError(f"Fact has no source kind: {fact['id']}")
    for chunk in chunks:
        if not isinstance(chunk.get("original_text"), str):
            raise BundleError(f"Document chunk has no original text: {chunk['id']}")
    for name, rows in raw_tables.items():
        if name in declared_counts and (type(declared_counts[name]) is not int or declared_counts[name] != len(rows)):
            raise BundleError(f"Manifest count for {name} does not match the processed table")
    if "issues" in declared_counts and (type(declared_counts["issues"]) is not int or declared_counts["issues"] != len(issues)):
        raise BundleError("Manifest issue count does not match its issues array")

    fact_by_id = indexes["facts"]
    valid_relations = {"direct", "broad", "contradictory", "unmapped"}
    cleaned_mappings = []
    mapping_ids: set[tuple[str, str]] = set()
    for raw in normalize_study_mappings(study_mappings):
        mapping = dict(raw)
        required = ("id", "topic", "relation", "reason", "reviewed_by", "reviewed_at")
        version = mapping.get("mapping_version")
        if (any(not isinstance(mapping.get(field), str) or not mapping[field].strip() for field in required)
                or isinstance(version, bool) or not isinstance(version, (str, int)) or not str(version).strip()):
            raise BundleError("Study mappings require id, mapping_version, topic, relation, reason, reviewed_by, and reviewed_at")
        if mapping.get("review_state", "reviewed") != "reviewed":
            raise BundleError("Study topic mappings must be reviewed before import")
        if mapping["relation"] not in valid_relations:
            raise BundleError(f"Unsupported study mapping relation: {mapping['relation']}")
        mapping_version = str(mapping["mapping_version"])
        source_mapping_id = str(mapping["id"])
        versioned_key = (source_mapping_id, mapping_version)
        if versioned_key in mapping_ids:
            raise BundleError(f"Duplicate study mapping ID/version: {source_mapping_id}/{mapping_version}")
        mapping_ids.add(versioned_key)
        fact_ids = mapping.get("fact_ids", [])
        if (not isinstance(fact_ids, (list, tuple))
                or any(not isinstance(fact_id, str) or not fact_id.strip() for fact_id in fact_ids)):
            raise BundleError("Study mapping fact_ids must be an array of nonempty exact IDs")
        fact_ids = list(fact_ids)
        if len(set(fact_ids)) != len(fact_ids):
            raise BundleError("Study mapping fact_ids must not contain duplicates")
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
    summaries = manifest.get("sources", [])
    if not isinstance(summaries, list) or any(not isinstance(summary, dict) for summary in summaries):
        raise BundleError("Manifest sources must be an array of parser summaries")
    summary_by_source = {}
    for summary in summaries:
        identity = summary.get("source_file_id")
        if identity not in source_ids or identity in summary_by_source:
            raise BundleError("Manifest parser summaries reference an absent or duplicate source")
        summary_by_source[identity] = summary

    raw_tables["study_topic_mappings"] = cleaned_mappings
    entity_sources = {}
    for name, rows in raw_tables.items():
        entity_sources[name] = {}
        for row in rows:
            if name == "source_files":
                owners = {row["id"]}
            elif name == "study_topic_mappings":
                owners = {fact_by_id[identity]["source_file_id"] for identity in row["fact_ids"]}
            else:
                owners = {row["source_file_id"]}
            entity_sources[name][row["id"]] = owners
    source_snapshots = {}
    for source in source_files:
        identity = source["id"]
        source_counts = {name: sum(identity in owners for owners in entities.values())
                         for name, entities in entity_sources.items()}
        native_versions: dict[str, set[str]] = {}
        for name, rows in raw_tables.items():
            if name == "study_topic_mappings":
                continue
            for row in rows:
                if identity not in entity_sources[name][row["id"]]:
                    continue
                for field, value in row.items():
                    if (field == "mapping_version" or field.endswith("_mapping_version")) and value is not None:
                        native_versions.setdefault(field, set()).add(str(value))
        source_snapshots[identity] = {
            **deepcopy(source), "import_run_id": import_id, "import_status": state,
            "counts": source_counts, "counts_scope": "prepared_bundle",
            "parser_version": source.get("conversion_version", manifest.get("conversion_version")),
            "parser_snapshot": {"summary": deepcopy(summary_by_source.get(identity)),
                                "issues": [deepcopy(issue) for issue in issues if issue.get("source_file_id") == identity]},
            "mapping_snapshot": {
                "native_versions": {field: sorted(versions) for field, versions in native_versions.items()},
                "reviewed_study_mappings": [deepcopy(row) for row in cleaned_mappings
                                            if identity in entity_sources["study_topic_mappings"][row["id"]]],
                "bundle_unmapped_decisions": [deepcopy(row) for row in cleaned_mappings if not row["fact_ids"]],
            },
        }
    cleaners = {"source_files": _clean_source_file, "source_records": _clean_source_record,
                "feedback_comments": _clean_feedback, "feedback_annotations": _clean_annotation,
                "facts": _clean_fact, "document_chunks": _clean_document_chunk,
                "study_topic_mappings": _clean_study_mapping}
    tables = {name: [cleaners[name](row) for row in rows] for name, rows in raw_tables.items()}
    return PreparedBundle(processed_dir, manifest, tables, import_id, counts, source_snapshots, entity_sources)


def _session(driver: Any, database: str | None) -> Any:
    if database is not None and (not isinstance(database, str) or not database.strip()):
        raise BundleError("Neo4j database must be a nonempty name")
    return driver.session(database=database) if database is not None else driver.session()


def _transaction_rows(result: Any, allow_unreadable: bool) -> list[dict[str, Any]] | None:
    if callable(getattr(result, "data", None)):
        records = result.data()
    elif hasattr(result, "__iter__"):
        records = list(result)
    elif allow_unreadable:
        # Only the standalone compatibility path permits the original loader's
        # consume-only test doubles. Public helpers always inspect real results.
        result.consume()
        return None
    else:
        raise BundleError("Import transaction must return readable Neo4j results")
    if not isinstance(records, list) or any(not isinstance(record, Mapping) for record in records):
        raise BundleError("Import transaction must return an array of readable Neo4j records")
    return records


def _manifest_result(rows: list[dict[str, Any]] | None,
                     expected: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    if rows is None:
        return None
    if len(rows) != 1 or not isinstance(rows[0].get("evidence_manifest"), dict):
        raise BundleError("Transaction must resolve a single role1 evidence manifest")
    manifest = rows[0]["evidence_manifest"]
    if (manifest.get("id") != "role1" or type(manifest.get("lock_version")) is not int
            or manifest["lock_version"] < 0):
        raise BundleError("Evidence manifest identity or administrative lock is invalid")
    if expected is not None and any(_canonical_json(manifest.get(key)) != _canonical_json(value)
                                    for key, value in expected.items()):
        raise BundleError("Evidence manifest differs from the selected source preparation")
    return manifest


def _initialize_evidence_manifest(tx: Any, allow_unreadable: bool) -> dict[str, Any] | None:
    token = uuid4().hex
    rows = _transaction_rows(tx.run(INITIALIZE_EVIDENCE_MANIFEST, token=token), allow_unreadable)
    manifest = _manifest_result(rows)
    if rows is None:
        return None
    if type(rows[0].get("created")) is not bool:
        raise BundleError("Evidence manifest initialization must report whether it was created")
    if not rows[0]["created"]:
        # Do not reactivate historic SourceFile nodes on application restart.
        return manifest
    sources = _transaction_rows(tx.run(READ_EXISTING_SOURCE_IDENTITIES), allow_unreadable)
    if sources is None:
        return None
    versions = {source.get("conversion_version") for source in sources}
    conversion_version = next(iter(versions)) if len(versions) == 1 else None
    properties = _evidence_manifest_properties(sources, conversion_version)
    # The token was set ON CREATE, and is checked under the shared write lock.
    # Thus bootstrap metadata is seeded only for a newly created manifest, even
    # when startup and a source import run concurrently in different processes.
    return _manifest_result(_transaction_rows(tx.run(SEED_EVIDENCE_MANIFEST, token=token, properties=properties),
                                              allow_unreadable), properties)


def _initialize_schema(driver: Any, database: str | None,
                       allow_unreadable: bool) -> dict[str, Any] | None:
    with _session(driver, database) as session:
        for query in (*CONSTRAINTS, *INDEXES):
            session.run(query).consume()
        return session.execute_write(lambda tx: _initialize_evidence_manifest(tx, allow_unreadable))


def initialize_schema(driver: Any, database: str | None = None) -> dict[str, Any]:
    """Initialize schema and on-create active-source selection outside data imports.

    Returns the exact EvidenceManifest properties. Existing selections are never
    replaced; legacy graphs are bootstrapped without inventing an import time.
    """
    manifest = _initialize_schema(driver, database, allow_unreadable=False)
    if manifest is None:
        raise BundleError("Schema initialization did not resolve a role1 evidence manifest")
    return manifest


def _lock_existing(tx: Any, label: str, rows: Sequence[Mapping[str, Any]],
                   token: str, recorded_at: str, import_id: str,
                   allow_unreadable: bool) -> dict[str, dict[str, Any] | None] | None:
    # MERGE plus the property-dependent SET serializes conflicting reimports,
    # including concurrently created identities. The temporary marker identifies
    # newly created nodes without inventing history for legacy imported sources.
    on_create = "n._role1_import_token = $token"
    if label == "SourceFile":
        on_create += ", n.first_imported_at = $recorded_at, n.first_import_run_id = $import_id"
    query = f"""
        UNWIND $rows AS row
        MERGE (n:{label} {{id: row.id}})
        ON CREATE SET {on_create}
        SET n.id = n.id
        WITH row, n, CASE WHEN n._role1_import_token = $token THEN null ELSE properties(n) END AS before
        REMOVE n._role1_import_token
        RETURN row.id AS id, before
    """
    records = _transaction_rows(tx.run(query, rows=rows, token=token, recorded_at=recorded_at, import_id=import_id),
                                allow_unreadable)
    if records is None:
        return None
    existing = {}
    for record in records:
        identity, before = record["id"], record["before"]
        if identity in existing or (before is not None and (not isinstance(before, dict) or before.get("id") != identity)):
            raise BundleError(f"Ambiguous or invalid existing {label} identity: {identity}")
        existing[identity] = before
    if set(existing) != {row["id"] for row in rows}:
        raise BundleError(f"Import transaction did not resolve every {label} identity")
    return existing


def _evidence_value(field: str, value: Any) -> str:
    if field.endswith("_json") and value is not None:
        try:
            value = json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise BundleError(f"Stored immutable evidence has invalid {field}") from exc
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _check_immutable(label: str, rows: Sequence[Mapping[str, Any]],
                     existing: Mapping[str, dict[str, Any] | None]) -> None:
    for row in rows:
        before = existing[row["id"]]
        if before is None:
            continue
        for field in IMMUTABLE_PROPERTIES[label]:
            if before.get(field) is not None and _evidence_value(field, before[field]) != _evidence_value(field, row["properties"].get(field)):
                raise BundleError(f"Conflicting reimport of immutable {label} {row['id']}: {field}")


def _previous_source_snapshot(properties: dict[str, Any] | None) -> dict[str, Any] | None:
    if properties is None:
        return None
    if properties.get("import_snapshot_json") is not None:
        try:
            snapshot = json.loads(properties["import_snapshot_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise BundleError("Stored source import snapshot is malformed") from exc
        if (not isinstance(snapshot, dict) or snapshot.get("id") != properties["id"]
                or snapshot.get("source_version") != properties.get("source_version", properties.get("sha256"))):
            raise BundleError("Stored source import snapshot has a conflicting identity or source version")
        _finite_json(snapshot, "Stored source import snapshot")
        return snapshot
    # A source loaded before backend integration has no historical count/parser
    # snapshot or import timestamp. Current input must not be passed off as one.
    return {**deepcopy(properties), "source_version": properties.get("source_version", properties.get("sha256")),
            "counts": None, "parser_version": properties.get("conversion_version"),
            "parser_snapshot": None, "mapping_snapshot": None,
            "first_imported_at": properties.get("first_imported_at"),
            "historical_snapshot_available": False}


def _write_prepared_bundle(tx: Any, prepared: PreparedBundle,
                           audit_sink: Callable[[Any, Mapping[str, Any]], None] | None,
                           allow_unreadable: bool) -> dict[str, Any]:
    if not isinstance(prepared, PreparedBundle):
        raise BundleError("Import requires a bundle returned by prepare_processed_bundle")
    if tx is None or not callable(getattr(tx, "run", None)):
        raise BundleError("Import requires the caller's Neo4j write transaction")
    # This must be the first graph operation: otherwise governance could select
    # evidence while a different process is replacing the active bundle.
    _manifest_result(_transaction_rows(tx.run(LOCK_EVIDENCE_MANIFEST), allow_unreadable))
    recorded_at = datetime.now(timezone.utc).isoformat()
    token = uuid4().hex
    previous_sources = {}
    matched = {identity: {name: 0 for name in prepared.counts} for identity in prepared.source_file_ids}
    created = deepcopy(matched)
    readable = True
    operations = (
        ("source_files", "SourceFile", UPSERT_SOURCE_FILES),
        ("source_records", "SourceRecord", UPSERT_SOURCE_RECORDS),
        ("feedback_comments", "CustomerFeedback", UPSERT_FEEDBACK),
        ("feedback_annotations", "FeedbackAnnotation", UPSERT_ANNOTATIONS),
        ("facts", "Fact", UPSERT_FACTS),
        ("document_chunks", "DocumentChunk", UPSERT_DOCUMENT_CHUNKS),
        ("study_topic_mappings", "StudyTopicMapping", UPSERT_STUDY_MAPPING_NODES),
    )
    for name, label, query in operations:
        for batch in _batches(prepared.tables[name]):
            existing = _lock_existing(tx, label, batch, token, recorded_at, prepared.import_run_id, allow_unreadable)
            if existing is None:
                readable = False
            else:
                _check_immutable(label, batch, existing)
                if name == "source_files":
                    previous_sources.update(existing)
                for row in batch:
                    target = created if existing[row["id"]] is None else matched
                    for identity in prepared.entity_sources[name][row["id"]]:
                        target[identity][name] += 1
            tx.run(query, rows=batch).consume()
            if name == "study_topic_mappings":
                links = [row for row in batch if row["fact_ids"]]
                if links:
                    tx.run(UPSERT_STUDY_MAPPING_RELATIONSHIPS, rows=links).consume()
    feedback_source_links = [{"id": row["id"], "feedback_comment_id": row["properties"]["feedback_comment_id"]}
                             for row in prepared.tables["source_records"] if row["properties"].get("feedback_comment_id")]
    for batch in _batches(feedback_source_links):
        tx.run(LINK_FEEDBACK_SOURCE_RECORDS, rows=batch).consume()

    sources, source_changes = [], []
    metadata_rows = []
    for identity in prepared.source_file_ids:
        snapshot = None
        if readable:
            previous = previous_sources[identity]
            snapshot = deepcopy(prepared.source_snapshots[identity])
            snapshot.update(
                first_imported_at=previous.get("first_imported_at") if previous is not None else recorded_at,
                first_import_run_id=previous.get("first_import_run_id") if previous is not None else prepared.import_run_id,
                last_imported_at=recorded_at,
                reconciliation={
                    "already_imported": previous is not None,
                    "previous_import_run_id": previous.get("last_import_run_id") if previous is not None else None,
                    "previous_imported_at": previous.get("last_imported_at") if previous is not None else None,
                    "historical_import_time_known": previous is not None and previous.get("first_imported_at") is not None,
                    "previous_parser_version": previous.get("conversion_version") if previous is not None else None,
                    "previous_snapshot_available": previous is not None and previous.get("import_snapshot_json") is not None,
                    "matched_existing_counts": matched[identity], "created_counts": created[identity],
                },
            )
            sources.append(snapshot)
            source_changes.append({"before": _previous_source_snapshot(previous), "after": snapshot})
        metadata_rows.append({"id": identity, "snapshot_json": json.dumps(snapshot, ensure_ascii=False, sort_keys=True, allow_nan=False)
                              if snapshot is not None else None})
    for batch in _batches(metadata_rows):
        tx.run(SOURCE_IMPORT_METADATA, rows=batch, recorded_at=recorded_at,
               import_run_id=prepared.import_run_id).consume()
    tx.run("""
        MERGE (run:ImportRun {id: $id})
        ON CREATE SET run.first_imported_at = $recorded_at
        SET run.status = $status, run.manifest_json = $manifest_json,
            run.counts_json = $counts_json, run.source_file_ids_json = $source_file_ids_json,
            run.last_imported_at = $recorded_at
    """, id=prepared.import_run_id, status=prepared.status, recorded_at=recorded_at,
           manifest_json=json.dumps(prepared.manifest, ensure_ascii=False, sort_keys=True, allow_nan=False),
           counts_json=json.dumps(prepared.counts, sort_keys=True),
           source_file_ids_json=json.dumps(prepared.source_file_ids)).consume()
    evidence_manifest = _manifest_result(
        _transaction_rows(tx.run(UPDATE_EVIDENCE_MANIFEST, properties=prepared.evidence_manifest), allow_unreadable),
        prepared.evidence_manifest,
    )
    if audit_sink is not None:
        audit_sink(tx, {"action": "source_import", "import_run_id": prepared.import_run_id,
                        "id": f"SOURCE-IMPORT-{prepared.import_run_id}", "status": prepared.status,
                        "source_file_ids": list(prepared.source_file_ids), "counts": dict(prepared.counts)})
    return {"import_run_id": prepared.import_run_id, "status": prepared.status,
            "counts": dict(prepared.counts), "source_file_ids": list(prepared.source_file_ids),
            "sources": sources, "source_changes": source_changes, "evidence_manifest": evidence_manifest}


def write_prepared_bundle(tx: Any, prepared: PreparedBundle,
                          audit_sink: Callable[[Any, Mapping[str, Any]], None] | None = None) -> dict[str, Any]:
    """Write and inspect evidence using only tx; caller owns commit and rollback.

    The shared EvidenceManifest lock is held throughout the caller's transaction.
    The optional legacy audit sink receives (tx, event). Returned source_changes
    contain accurate before/after snapshots for a backend's per-source sink, and
    evidence_manifest contains the exact locked, updated manifest properties.
    """
    return _write_prepared_bundle(tx, prepared, audit_sink, allow_unreadable=False)


def load_processed_bundle(driver: Any, processed_dir: Path,
                          audit_sink: Callable[[Any, Mapping[str, Any]], None] | None = None,
                          allow_partial: bool = False,
                          study_mappings: Sequence[Mapping[str, Any]] = (),
                          database: str | None = None) -> dict[str, Any]:
    """Standalone compatibility API; schema and data use separate sessions."""
    prepared = prepare_processed_bundle(processed_dir, allow_partial=allow_partial, study_mappings=study_mappings)
    _initialize_schema(driver, database, allow_unreadable=True)
    with _session(driver, database) as session:
        result = session.execute_write(lambda tx: _write_prepared_bundle(tx, prepared, audit_sink, allow_unreadable=True))
    # Preserve the stable original return value, including equality on reimport.
    return {key: result[key] for key in ("import_run_id", "status", "counts", "source_file_ids")}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processed-dir", type=Path,
                        help="Converted bundle directory; defaults to BMW_DATA_DIR from root configuration.")
    parser.add_argument("--allow-partial", action="store_true",
                        help="Explicitly import a partial bundle after reviewing its manifest.")
    parser.add_argument("--study-mappings", type=Path,
                        help="Optional reviewed JSON topic-to-exact-survey-fact map; see study_topic_mappings.example.json.")
    parser.add_argument("--database", help="Neo4j database; defaults to NEO4J_DATABASE from root configuration.")
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
            mappings = read_study_mappings(args.study_mappings)
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
                                       study_mappings=mappings,
                                       database=args.database if args.database is not None else settings.neo4j_database)
    except Exception as exc:
        print(f"Evidence import failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        driver.close()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
