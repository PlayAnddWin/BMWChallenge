"""Bounded, provenance-bearing query providers over the Neo4j evidence graph."""

from __future__ import annotations

from dataclasses import dataclass, asdict
import json
import re
from typing import Any, Mapping, Protocol, TypedDict


class EvidenceProviderError(RuntimeError):
    """Raised when evidence could not be read; failures are never empty success."""


def _normalize_region(value: Any) -> Any:
    if value is None:
        return None
    key = re.sub(r"[^a-z0-9]+", "", str(value).casefold())
    aliases = {
        "us": "US", "usa": "US", "unitedstates": "US", "america": "US",
        "cn": "CN", "china": "CN", "europe": "EU", "eu": "EU",
        "unitedkingdom": "EU", "uk": "EU", "gb": "EU", "norway": "EU", "no": "EU",
        "row": "RoW", "restofworld": "RoW",
    }
    return aliases.get(key, value)


def _normalize_vehicle_code(value: Any) -> Any:
    if value is None:
        return None
    text = str(value)
    if re.search(r"(?i)G60\s*[_-]\s*G68", text):
        return "G60_G68"
    if re.search(r"(?i)G68\s*[_-]\s*CN", text):
        return "G68"
    for code in ("F70", "G60", "G70", "G68"):
        if re.search(rf"(?i)(?<![A-Z0-9]){code}(?![A-Z0-9])", text):
            return code
    return value


class SourceLocator(TypedDict, total=False):
    filename: str
    sheet: str
    row: int
    column: int
    column_header: str
    page: int
    printed_page: str | None


class SourceReference(TypedDict, total=False):
    source_file_id: str
    source_record_id: str
    source_version: str
    source_sha256: str
    source_locator: SourceLocator


class SourceRecordDTO(TypedDict, total=False):
    id: str
    source_file_id: str
    source_version: str
    locator: SourceLocator
    raw_payload: dict[str, Any]
    original_record_id: str | None
    source_file: dict[str, Any] | None


class FeedbackEvidenceDTO(TypedDict, total=False):
    id: str
    original_id: str | None
    original_text: str
    source_version: str
    model: str | None
    region: str | None
    country: str | None
    powertrain_original: str | None
    powertrain: str | None
    source_label: str | None
    annotations: list[dict[str, Any]]
    source_records: list[SourceReference]
    unknowns: list[str]


class SurveyFactDTO(TypedDict, total=False):
    id: str
    kind: str
    metric: str | None
    question: str | None
    value: Any
    distribution: dict[str, Any]
    respondent_n: int | None
    scale: str | None
    derivative: str | None
    region: str | None
    mapping_strength: str
    source_version: str
    source_file_id: str
    source_record_id: str
    source_sha256: str
    source_locator: SourceLocator


class SalesFactDTO(TypedDict, total=False):
    id: str
    vehicle_line: str
    region: str
    year: int
    volume: int | float
    unit: str
    powertrain: str | None
    powertrain_split: str
    source_version: str
    source_file_id: str
    source_record_id: str
    source_sha256: str
    source_locator: SourceLocator


class DocumentChunkDTO(TypedDict, total=False):
    id: str
    document_kind: str
    page: int
    printed_page: str | None
    original_text: str
    derivative: str | None
    market: str | None
    applicability: str
    source_version: str
    source_file_id: str
    source_record_id: str
    source_sha256: str
    source_locator: SourceLocator


@dataclass(frozen=True)
class EvidenceScope:
    model: str | None = None
    derivative: str | None = None
    vehicle_line: str | None = None
    market: str | None = None
    region: str | None = None
    powertrain: str | None = None
    source_version: str | None = None

    def query_params(self) -> dict[str, Any]:
        values = asdict(self)
        values["region"] = _normalize_region(values.get("region") or values.get("market"))
        for field_name in ("model", "derivative", "vehicle_line"):
            values[field_name] = _normalize_vehicle_code(values.get(field_name))
        if str(values.get("powertrain") or "").casefold() == "beve":
            values["powertrain"] = "BEV"
        return values


class EvidenceProvider(Protocol):
    def search_feedback(self, query: str, scope: EvidenceScope | Mapping[str, Any] | None = None,
                        limit: int = 20) -> list[FeedbackEvidenceDTO]: ...
    def get_source_record(self, record_id: str) -> SourceRecordDTO | None: ...
    def get_survey_facts(self, topic: str | None, scope: EvidenceScope | Mapping[str, Any] | None = None) -> dict[str, Any]: ...
    def get_sales_facts(self, scope: EvidenceScope | Mapping[str, Any] | None, year: int = 2030) -> dict[str, Any]: ...
    def search_document_chunks(self, query: str, scope: EvidenceScope | Mapping[str, Any] | None = None,
                              document_kind: str | None = None, limit: int = 6) -> list[DocumentChunkDTO]: ...
    def get_comment_cohort_counts(self, scope: EvidenceScope | Mapping[str, Any] | None) -> dict[str, Any]: ...


def _scope_params(scope: EvidenceScope | Mapping[str, Any] | None) -> dict[str, Any]:
    if scope is None:
        return EvidenceScope().query_params()
    if isinstance(scope, EvidenceScope):
        return scope.query_params()
    values = dict(scope)
    values["region"] = _normalize_region(values.get("region") or values.get("market"))
    for field_name in ("model", "derivative", "vehicle_line"):
        values[field_name] = _normalize_vehicle_code(values.get(field_name))
    if str(values.get("powertrain") or "").casefold() == "beve":
        values["powertrain"] = "BEV"
    allowed = {"model", "derivative", "vehicle_line", "market", "region", "powertrain", "source_version"}
    return {key: values.get(key) for key in allowed}


def _decode_json(value: Any, default: Any = None) -> Any:
    if not isinstance(value, str):
        return value if value is not None else default
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default


def _clean_terms(query: str) -> list[str]:
    return list(dict.fromkeys(term.casefold() for term in re.findall(r"[\w'-]+", query, flags=re.UNICODE)
                              if len(term.strip("'-")) >= 2))[:12]


def _bounded_limit(limit: int, maximum: int = 100) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer") from exc
    if value < 1:
        raise ValueError("limit must be at least 1")
    return min(value, maximum)


class Neo4jEvidenceProvider:
    """Read-only repository adapter; accepts a shared Neo4j driver."""

    def __init__(self, driver: Any):
        if driver is None:
            raise ValueError("A configured Neo4j driver is required")
        self.driver = driver

    def _read(self, query: str, **params: Any) -> list[dict[str, Any]]:
        try:
            with self.driver.session() as session:
                result = session.execute_read(lambda tx: list(tx.run(query, **params)))
            return [record.data() if hasattr(record, "data") else dict(record) for record in result]
        except Exception as exc:
            raise EvidenceProviderError(f"Neo4j evidence query failed: {type(exc).__name__}: {exc}") from exc

    @staticmethod
    def _scope_where(variable: str = "fb") -> str:
        return f"""($model IS NULL OR {variable}.model = $model)
            AND ($derivative IS NULL OR {variable}.derivative = $derivative OR {variable}.model = $derivative)
            AND ($region IS NULL OR {variable}.region = $region)
            AND ($powertrain IS NULL OR {variable}.powertrain = $powertrain)
            AND ($source_version IS NULL OR {variable}.source_version = $source_version)"""

    def search_feedback(self, query: str, scope: EvidenceScope | Mapping[str, Any] | None = None,
                        limit: int = 20) -> list[dict[str, Any]]:
        limit = _bounded_limit(limit)
        terms = _clean_terms(query)
        if not terms:
            raise ValueError("query must contain at least one searchable term")
        params = {**_scope_params(scope), "terms": terms, "limit": limit}
        rows = self._read(f"""
            MATCH (fb:CustomerFeedback)
            WHERE {self._scope_where('fb')}
              AND ($vehicle_line IS NULL OR fb.vehicle_line = $vehicle_line)
              AND (size($terms) = 0 OR any(term IN $terms
                   WHERE toLower(coalesce(fb.original_text, '')) CONTAINS term))
            OPTIONAL MATCH (fb)<-[:DESCRIBES]-(source_row:SourceRecord)-[:FROM_SOURCE_FILE]->(source_file:SourceFile)
            WITH fb, collect(DISTINCT CASE WHEN source_row IS NULL THEN NULL ELSE {{
                id: source_row.id, locator_json: source_row.locator_json,
                source_file_id: source_file.id, source_sha256: source_file.sha256
            }} END) AS source_records
            OPTIONAL MATCH (fb)-[:HAS_ANNOTATION]->(a:FeedbackAnnotation)
            OPTIONAL MATCH (a)-[:FROM_SOURCE_RECORD]->(sr:SourceRecord)
            OPTIONAL MATCH (sr)-[:FROM_SOURCE_FILE]->(sf:SourceFile)
            WITH fb, source_records, collect(DISTINCT CASE WHEN a IS NULL THEN NULL ELSE {{
                id: a.id, feedback_type_original: a.feedback_type_original,
                vfc2_original: a.vfc2_original, vfc3_original: a.vfc3_original,
                polarity: a.polarity, assignment_method: a.assignment_method,
                assignment_review: a.assignment_review,
                source_record_id: sr.id, locator_json: sr.locator_json,
                source_file_id: sf.id, source_sha256: sf.sha256
            }} END) AS annotations
            RETURN properties(fb) AS feedback, source_records, annotations
            ORDER BY feedback.id
            LIMIT $limit
        """, **params)
        output: list[dict[str, Any]] = []
        for row in rows:
            feedback = dict(row.get("feedback") or {})
            source_records = []
            for raw_source in row.get("source_records") or []:
                if not raw_source:
                    continue
                source_record = dict(raw_source)
                source_record["source_locator"] = _decode_json(source_record.pop("locator_json", None), {})
                source_records.append(source_record)
            annotations = []
            for raw in row.get("annotations") or []:
                if not raw:
                    continue
                annotation = dict(raw)
                annotation["source_locator"] = _decode_json(annotation.pop("locator_json", None), {})
                annotations.append(annotation)
            output.append({
                "id": feedback.get("id"),
                "original_id": feedback.get("original_id"),
                "source_version": feedback.get("source_version"),
                "source_file_id": feedback.get("source_file_id"),
                "original_text": feedback.get("original_text"),
                "model": feedback.get("model"),
                "model_original": feedback.get("model_original"),
                "region": feedback.get("region"),
                "country": feedback.get("country"),
                "powertrain_original": feedback.get("powertrain_original"),
                "powertrain": feedback.get("powertrain"),
                "source_label": feedback.get("source_label"),
                "text_quality": feedback.get("text_quality"),
                "annotations": annotations,
                "source_records": source_records,
                "source_label_semantics": "opaque",
                "unknowns": [name for name in ("region", "powertrain", "source_label")
                             if feedback.get(name) in (None, "")],
            })
        return output

    def get_source_record(self, record_id: str) -> dict[str, Any] | None:
        rows = self._read("""
            MATCH (r:SourceRecord {id: $record_id})
            OPTIONAL MATCH (r)-[:FROM_SOURCE_FILE]->(sf:SourceFile)
            RETURN properties(r) AS record, properties(sf) AS source_file
            LIMIT 1
        """, record_id=record_id)
        if not rows:
            return None
        record = dict(rows[0].get("record") or {})
        source_file = rows[0].get("source_file")
        record["locator"] = _decode_json(record.pop("locator_json", None), {})
        record["raw_payload"] = _decode_json(record.pop("raw_payload_json", None), {})
        record["source_file"] = dict(source_file) if source_file else None
        return record

    def get_survey_facts(self, topic: str | None,
                         scope: EvidenceScope | Mapping[str, Any] | None = None) -> dict[str, Any]:
        params = _scope_params(scope)
        if topic:
            mappings = self._read("""
                MATCH (mapping:StudyTopicMapping {topic: $topic})-[:MAPS_TO]->(fact:Fact)
                WHERE fact.kind STARTS WITH 'survey'
                  AND ($model IS NULL OR fact.derivative = $model OR fact.model = $model)
                  AND ($derivative IS NULL OR fact.derivative = $derivative)
                  AND ($region IS NULL OR fact.region = $region)
                  AND ($source_version IS NULL OR fact.source_version = $source_version)
                OPTIONAL MATCH (fact)-[:FROM_SOURCE_RECORD]->(record:SourceRecord)
                OPTIONAL MATCH (record)-[:FROM_SOURCE_FILE]->(source:SourceFile)
                RETURN properties(fact) AS fact, properties(mapping) AS mapping,
                       properties(record) AS source_record, properties(source) AS source_file
                ORDER BY fact.id
            """, topic=topic, **params)
            if not mappings:
                mapping_rows = self._read("""
                    MATCH (mapping:StudyTopicMapping {topic: $topic})
                    RETURN properties(mapping) AS mapping ORDER BY mapping.id
                """, topic=topic)
                if mapping_rows:
                    mapping_values = [dict(row.get("mapping") or {}) for row in mapping_rows]
                    all_unmapped = all(mapping.get("relation") == "unmapped" for mapping in mapping_values)
                    return {"topic": topic, "facts": [],
                            "mapping_status": "reviewed_unmapped" if all_unmapped else "mapping_scope_gap",
                            "mappings": mapping_values,
                            "message": ("The reviewed mapping marks this topic as unmapped; no study fact was treated as support."
                                        if all_unmapped else "Reviewed mappings exist, but none has a fact in the requested scope.")}
                return {"topic": topic, "facts": [], "mapping_status": "unmapped",
                        "message": "No reviewed topic-to-study mapping exists; no study value was treated as relevant evidence."}
            facts = [self._fact_result(row) for row in mappings]
            source_versions = sorted({fact.get("source_version") for fact in facts if fact.get("source_version")})
            return {"topic": topic, "mapping_status": "reviewed", "facts": facts,
                    "source_versions": source_versions,
                    "mixed_source_versions": len(source_versions) > 1}

        rows = self._read("""
            MATCH (fact:Fact)
            WHERE fact.kind STARTS WITH 'survey'
              AND ($model IS NULL OR fact.derivative = $model OR fact.model = $model)
              AND ($derivative IS NULL OR fact.derivative = $derivative)
              AND ($region IS NULL OR fact.region = $region)
              AND ($source_version IS NULL OR fact.source_version = $source_version)
            OPTIONAL MATCH (fact)-[:FROM_SOURCE_RECORD]->(record:SourceRecord)
            OPTIONAL MATCH (record)-[:FROM_SOURCE_FILE]->(source:SourceFile)
            RETURN properties(fact) AS fact, null AS mapping,
                   properties(record) AS source_record, properties(source) AS source_file
            ORDER BY fact.id LIMIT 200
        """, **params)
        facts = [self._fact_result(row) for row in rows]
        source_versions = sorted({fact.get("source_version") for fact in facts if fact.get("source_version")})
        return {"topic": None, "facts": facts, "mapping_status": "not_requested",
                "source_versions": source_versions, "mixed_source_versions": len(source_versions) > 1}

    @staticmethod
    def _fact_result(row: Mapping[str, Any]) -> dict[str, Any]:
        fact = dict(row.get("fact") or {})
        for name in ("locator_json", "distribution_json", "category_labels_json", "cell_locators_json"):
            if name in fact:
                target = name.removesuffix("_json")
                fact[target] = _decode_json(fact.pop(name), None)
        record = row.get("source_record")
        source_file = row.get("source_file")
        source_locator = dict(fact.get("locator") or {})
        if record:
            record = dict(record)
            source_locator = {**_decode_json(record.get("locator_json"), {}), **source_locator}
            fact["source_record_id"] = record.get("id")
        fact["source_locator"] = source_locator
        if source_file:
            source_file = dict(source_file)
            fact["source_file_id"] = source_file.get("id")
            fact["source_sha256"] = source_file.get("sha256")
        mapping = row.get("mapping")
        fact["topic_mapping"] = dict(mapping) if mapping else None
        fact["mapping_strength"] = mapping.get("relation", "unmapped") if mapping else "unmapped"
        fact.setdefault("respondent_n", None)
        unknowns = []
        for field_name in ("respondent_n", "scale", "unit", "region", "derivative"):
            if fact.get(field_name) in (None, ""):
                unknowns.append(field_name)
        fact["unknowns"] = unknowns
        return fact

    def get_sales_facts(self, scope: EvidenceScope | Mapping[str, Any] | None,
                        year: int = 2030) -> dict[str, Any]:
        params = _scope_params(scope)
        rows = self._read("""
            MATCH (fact:Fact {kind: 'sales'})
            WHERE fact.year = $year
              AND ($region IS NULL OR fact.region = $region)
              AND ($source_version IS NULL OR fact.source_version = $source_version)
            OPTIONAL MATCH (fact)-[:FROM_SOURCE_RECORD]->(record:SourceRecord)
            OPTIONAL MATCH (record)-[:FROM_SOURCE_FILE]->(source:SourceFile)
            RETURN properties(fact) AS fact, null AS mapping,
                   properties(record) AS source_record, properties(source) AS source_file
            ORDER BY fact.vehicle_line, fact.region, fact.id LIMIT 200
        """, year=int(year), **params)
        requested_line = params.get("vehicle_line")
        requested_model = params.get("model") or params.get("derivative")
        all_facts = [self._fact_result(row) for row in rows]
        if requested_line:
            facts = [fact for fact in all_facts if fact.get("vehicle_line") == requested_line]
        elif requested_model:
            # Never split a combined G60_G68 line into derivative volumes.
            facts = [fact for fact in all_facts if fact.get("vehicle_line") == requested_model]
        else:
            facts = all_facts
        proxy_candidates = []
        if requested_model in {"G60", "G68"} and not facts:
            proxy_candidates = [fact for fact in all_facts if fact.get("vehicle_line") == "G60_G68"]
        evidence_rows = facts + proxy_candidates
        return {
            "year": int(year),
            "facts": facts,
            "possible_whole_line_proxy_candidates": proxy_candidates,
            "proxy_candidates_are_not_direct_derivative_volumes": bool(proxy_candidates),
            "source_versions": sorted({fact.get("source_version") for fact in evidence_rows if fact.get("source_version")}),
            "mixed_source_versions": len({fact.get("source_version") for fact in evidence_rows if fact.get("source_version")}) > 1,
            "available_lines_in_scope": sorted({fact.get("vehicle_line") for fact in all_facts if fact.get("vehicle_line")}),
            "powertrain_split": "unknown",
            "whole_line_proxy_warning": "Whole-line sales are not a BEV-specific volume.",
            "scope_gap": ("no sales facts are loaded for this year/region" if not all_facts else
                          "requested derivative is not available as a separate sales line" if requested_model and not facts else
                          "requested vehicle line is unavailable" if requested_line and not facts else None),
            "unknowns": ["powertrain_split"],
        }

    def search_document_chunks(self, query: str,
                               scope: EvidenceScope | Mapping[str, Any] | None = None,
                               document_kind: str | None = None, limit: int = 6) -> list[dict[str, Any]]:
        limit = _bounded_limit(limit, maximum=50)
        terms = _clean_terms(query)
        if not terms:
            raise ValueError("query must contain at least one searchable term")
        params = {**_scope_params(scope), "terms": terms,
                  "document_kind": document_kind, "limit": limit}
        rows = self._read("""
            MATCH (chunk:DocumentChunk)
            WITH chunk, properties(chunk) AS chunk_properties
            WHERE ($document_kind IS NULL OR chunk.document_kind = $document_kind)
              AND ($derivative IS NULL OR chunk.derivative = $derivative)
              AND ($region IS NULL OR chunk_properties.market = $region OR chunk_properties.region = $region)
              AND ($source_version IS NULL OR chunk.source_version = $source_version)
              AND (size($terms) = 0 OR any(term IN $terms
                   WHERE toLower(coalesce(chunk.original_text, '')) CONTAINS term))
            OPTIONAL MATCH (chunk)-[:FROM_SOURCE_RECORD]->(record:SourceRecord)
            OPTIONAL MATCH (record)-[:FROM_SOURCE_FILE]->(source:SourceFile)
            RETURN properties(chunk) AS chunk, properties(record) AS source_record,
                   properties(source) AS source_file
            ORDER BY chunk.id LIMIT $limit
        """, **params)
        output = []
        for row in rows:
            chunk = dict(row.get("chunk") or {})
            record = row.get("source_record") or {}
            source = row.get("source_file") or {}
            chunk["source_locator"] = {"filename": source.get("filename"), "page": chunk.get("page"),
                                       "printed_page": chunk.get("printed_page")}
            chunk["source_record_id"] = record.get("id")
            chunk["source_file_id"] = source.get("id")
            chunk["source_sha256"] = source.get("sha256")
            chunk["unknowns"] = ["market_applicability", "derivative_applicability"]
            output.append(chunk)
        return output

    def get_comment_cohort_counts(self,
                                 scope: EvidenceScope | Mapping[str, Any] | None) -> dict[str, Any]:
        params = _scope_params(scope)
        cohort = self._read(f"""
            MATCH (fb:CustomerFeedback)
            WHERE {self._scope_where('fb')}
              AND ($vehicle_line IS NULL OR fb.vehicle_line = $vehicle_line)
            OPTIONAL MATCH (fb)-[:HAS_ANNOTATION]->(cohort_annotation:FeedbackAnnotation)
            RETURN count(DISTINCT fb.id) AS distinct_comments,
                   count(DISTINCT cohort_annotation.id) AS annotation_count,
                   collect(DISTINCT fb.source_version) AS source_versions
        """, **params)
        topic_rows = self._read(f"""
            MATCH (fb:CustomerFeedback)-[:HAS_ANNOTATION]->(a:FeedbackAnnotation)
            WHERE {self._scope_where('fb')}
              AND ($vehicle_line IS NULL OR fb.vehicle_line = $vehicle_line)
              AND a.vfc2_original IS NOT NULL
            RETURN a.vfc2_original AS topic,
                   count(DISTINCT fb.id) AS distinct_comments,
                   count(DISTINCT a.id) AS annotation_count
            ORDER BY topic
        """, **params)
        issue_rows = self._read(f"""
            MATCH (fb:CustomerFeedback)-[:HAS_ANNOTATION]->(a:FeedbackAnnotation)
            WHERE {self._scope_where('fb')}
              AND ($vehicle_line IS NULL OR fb.vehicle_line = $vehicle_line)
              AND a.vfc3_original IS NOT NULL
            RETURN a.vfc2_original AS topic, a.vfc3_original AS issue,
                   count(DISTINCT fb.id) AS distinct_comments,
                   count(DISTINCT a.id) AS annotation_count
            ORDER BY issue
        """, **params)
        source_rows = self._read(f"""
            MATCH (fb:CustomerFeedback)
            WHERE {self._scope_where('fb')}
              AND ($vehicle_line IS NULL OR fb.vehicle_line = $vehicle_line)
              AND fb.source_label IS NOT NULL
            RETURN fb.source_label AS source_label, count(DISTINCT fb.id) AS distinct_comments
            ORDER BY source_label
        """, **params)
        type_rows = self._read(f"""
            MATCH (fb:CustomerFeedback)-[:HAS_ANNOTATION]->(a:FeedbackAnnotation)
            WHERE {self._scope_where('fb')}
              AND ($vehicle_line IS NULL OR fb.vehicle_line = $vehicle_line)
              AND a.feedback_type_original IS NOT NULL
            RETURN a.feedback_type_original AS annotation_type,
                   count(DISTINCT fb.id) AS distinct_comments,
                   count(DISTINCT a.id) AS annotation_count
            ORDER BY annotation_type
        """, **params)
        topic_counts = {row["topic"]: int(row["distinct_comments"] or 0) for row in topic_rows}
        topic_annotation_counts = {row["topic"]: int(row["annotation_count"] or 0) for row in topic_rows}
        issue_counts = {f"{row.get('topic') or '(unclassified)'} :: {row['issue']}":
                        int(row["distinct_comments"] or 0) for row in issue_rows}
        issue_annotation_counts = {f"{row.get('topic') or '(unclassified)'} :: {row['issue']}":
                                   int(row["annotation_count"] or 0) for row in issue_rows}
        source_counts = {row["source_label"]: int(row["distinct_comments"] or 0) for row in source_rows}
        annotation_types = {row["annotation_type"]: {
            "distinct_comments": int(row["distinct_comments"] or 0),
            "annotation_count": int(row["annotation_count"] or 0),
        } for row in type_rows}
        return {
            "cohort_distinct_comment_count": int(cohort[0]["distinct_comments"]) if cohort else 0,
            "cohort_annotation_count": int(cohort[0].get("annotation_count") or 0) if cohort else 0,
            "source_versions": sorted(version for version in (cohort[0].get("source_versions") or []) if version) if cohort else [],
            "mixed_source_versions": len([version for version in (cohort[0].get("source_versions") or []) if version]) > 1 if cohort else False,
            "topic_bucket_distinct_comments": topic_counts,
            "topic_annotation_counts": topic_annotation_counts,
            "issue_label_distinct_comments": issue_counts,
            "issue_annotation_counts": issue_annotation_counts,
            "annotation_counts_by_original_type": annotation_types,
            "source_label_counts": source_counts,
            "source_labels_are_opaque": True,
            "counting_rule": "DISTINCT canonical feedback IDs; annotation and source-row counts are reported separately.",
        }


_default_provider: EvidenceProvider | None = None


def configure_evidence_provider(provider: EvidenceProvider) -> None:
    """Set an application-scoped provider for the function-style API below."""
    global _default_provider
    _default_provider = provider


def _provider(provider: EvidenceProvider | None) -> EvidenceProvider:
    selected = provider or _default_provider
    if selected is None:
        raise EvidenceProviderError("No evidence provider configured; connect Neo4j or pass provider= explicitly")
    return selected


def search_feedback(query: str, scope: EvidenceScope | Mapping[str, Any] | None = None,
                    limit: int = 20, *, provider: EvidenceProvider | None = None) -> list[dict[str, Any]]:
    return _provider(provider).search_feedback(query, scope, limit)


def get_source_record(record_id: str, *, provider: EvidenceProvider | None = None) -> dict[str, Any] | None:
    return _provider(provider).get_source_record(record_id)


def get_survey_facts(topic: str | None, scope: EvidenceScope | Mapping[str, Any] | None = None,
                     *, provider: EvidenceProvider | None = None) -> dict[str, Any]:
    return _provider(provider).get_survey_facts(topic, scope)


def get_sales_facts(scope: EvidenceScope | Mapping[str, Any] | None, year: int = 2030,
                    *, provider: EvidenceProvider | None = None) -> dict[str, Any]:
    return _provider(provider).get_sales_facts(scope, year)


def search_document_chunks(query: str, scope: EvidenceScope | Mapping[str, Any] | None = None,
                          document_kind: str | None = None, limit: int = 6,
                          *, provider: EvidenceProvider | None = None) -> list[dict[str, Any]]:
    return _provider(provider).search_document_chunks(query, scope, document_kind, limit)


def get_comment_cohort_counts(scope: EvidenceScope | Mapping[str, Any] | None,
                              *, provider: EvidenceProvider | None = None) -> dict[str, Any]:
    return _provider(provider).get_comment_cohort_counts(scope)
