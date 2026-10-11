"""Role 1/2 integration boundary. No invented model output or source evidence."""
import json
import hashlib
from functools import lru_cache
from pathlib import Path
from contextlib import contextmanager
from contextvars import ContextVar
from collections import Counter
from itertools import product

from services.evidence import EvidenceScope, Neo4jEvidenceProvider

from services.decisions import DomainError
from services.repository import same_snapshot


@lru_cache(maxsize=2)
def _original_corpus(raw_path, versions):
    """Cache only Role 01 conversion of the exact bytes fingerprinted by caller."""
    from services.ingestion import convert_sources
    bundle = convert_sources(Path(raw_path), check_known_feedback_counts=True)
    if bundle.manifest["status"] != "succeeded":
        raise DomainError(503, "Original Role 01 corpus conversion is unavailable")
    actual = tuple(sorted((row["filename"], row["sha256"]) for row in bundle.source_files))
    if actual != versions:
        raise DomainError(409, "Original source files changed during integrity verification")
    return {"source": {r["id"]: r for r in bundle.source_files},
            "record": {r["id"]: r for r in bundle.source_records},
            "chunk": {r["id"]: r for r in bundle.document_chunks},
            "fact": {r["id"]: r for r in bundle.facts},
            "feedback": {r["id"]: r for r in bundle.feedback_comments},
            "annotation": {r["id"]: r for r in bundle.feedback_annotations}}


class UnavailableEvidenceProvider:
    def resolve(self, draft):
        raise DomainError(503, "Role 1 evidence resolver is not integrated; validated persistence is unavailable")


class GraphSourceProvider:
    def __init__(self, driver, database=None):
        self.driver, self.database = driver, database

    def list_sources(self):
        with self.driver.session(database=self.database) as session:
            return [self._normalize(row["source"]) for row in session.run("MATCH (s:SourceFile) RETURN s{.*} AS source ORDER BY s.id")]

    def list_signals(self):
        with self.driver.session(database=self.database) as session:
            return [self._normalize(row["signal"]) for row in session.run("MATCH (s:Signal) RETURN s{.*} AS signal ORDER BY s.id")]

    def verify_reference(self, reference):
        """Verify exact imported row or PDF chunk provenance; ambiguous IDs fail closed."""
        candidates = []
        for kind in ("record", "chunk"):
            try:
                candidates.append(self.get(kind, reference["record_id"]))
            except DomainError as exc:
                if exc.status != 404:
                    raise
        if len(candidates) != 1:
            return False
        try:
            source = self.get("source", reference["source_id"])
        except DomainError as exc:
            if exc.status == 404:
                return False
            raise
        record = candidates[0]
        return (record.get("source_id") == reference["source_id"]
                and record.get("source_version") == reference["source_version"]
                and source.get("source_version", source.get("sha256")) == reference["source_version"]
                and same_snapshot(record.get("locator"), reference["locator"])
                and reference["validation_state"] == "verified")

    def list_conflicts(self, model=None):
        with self.driver.session(database=self.database) as session:
            rows = session.run("MATCH (c:ConflictNode) WHERE $model IS NULL OR c.model = $model RETURN c{.*} AS conflict ORDER BY c.delta DESC", model=model)
            return [dict(row["conflict"]) for row in rows]

    def get(self, kind, entity_id):
        labels = {"source": "SourceFile", "record": "SourceRecord", "chunk": "DocumentChunk", "signal": "Signal"}
        if kind not in labels:
            raise DomainError(422, "Unsupported source entity kind")
        with self.driver.session(database=self.database) as session:
            rows = list(session.run(f"MATCH (s:{labels[kind]} {{id:$id}}) RETURN s{{.*}} AS entity LIMIT 2", id=entity_id))
        if not rows:
            raise DomainError(404, "Source entity not found")
        if len(rows) != 1:
            raise DomainError(503, "Source identity is ambiguous")
        entity = self._normalize(rows[0]["entity"])
        if kind == "chunk" and "locator" not in entity and entity.get("source_record_id"):
            record = self.get("record", entity["source_record_id"])
            if (record.get("source_id") != entity.get("source_id")
                    or record.get("source_version") != entity.get("source_version")):
                raise DomainError(503, "Document chunk provenance does not match its source record")
            entity["locator"] = record.get("locator", {})
        return entity


    @staticmethod
    def _normalize(properties):
        entity = dict(properties)
        # Role 01 uses source_file_id; the backend contract uses source_id.
        if "source_id" not in entity and "source_file_id" in entity:
            entity["source_id"] = entity["source_file_id"]
        for storage_key, key in (("locator_json", "locator"), ("raw_payload_json", "raw_payload"), ("evidence_links_json", "evidence_links"),
                                 ("distribution_json", "distribution"), ("category_labels_json", "category_labels"),
                                 ("cell_locators_json", "cell_locators")):
            if storage_key in entity:
                try:
                    entity[key] = json.loads(entity.pop(storage_key))
                except (json.JSONDecodeError, TypeError) as exc:
                    raise DomainError(503, "Imported source metadata is malformed") from exc
        for key in ("locator", "evidence_links"):
            if isinstance(entity.get(key), str):
                try:
                    entity[key] = json.loads(entity[key])
                except json.JSONDecodeError as exc:
                    raise DomainError(503, "Imported source metadata is malformed") from exc
        for key, expected in (("locator", dict), ("raw_payload", dict), ("evidence_links", list)):
            if key in entity and not isinstance(entity[key], expected):
                raise DomainError(503, "Imported source metadata has an invalid shape")
        if "evidence_links" in entity and any(not isinstance(link, dict) for link in entity["evidence_links"]):
            raise DomainError(503, "Imported evidence links must be objects")
        if "locator" in entity:
            # Role 01 retains unknown optional metadata as null; backend locator
            # values describe concrete anchors only. Keep original raw payload.
            entity["locator"] = {key: value for key, value in entity["locator"].items() if value is not None}
        return entity


class Role01SourceProvider(GraphSourceProvider):
    """HTTP scope adapter over Role 01's authoritative graph query implementation.

    Plural scopes expand into disjoint canonical cohorts. Unsupported filters fail
    explicitly rather than silently widening evidence retrieval.
    """

    def __init__(self, driver, database=None, raw_data_dir=None):
        super().__init__(driver, database)
        self._source_versions = Neo4jEvidenceProvider(driver, database)
        self._integrity_session_active = ContextVar("role01_integrity_session", default=False)
        self._session_originals = ContextVar("role01_original_registry", default=None)
        self.evidence = Neo4jEvidenceProvider(driver, database, integrity_check=self.validate_graph_integrity)
        if raw_data_dir is None:
            from config import get_settings
            raw_data_dir = get_settings().raw_data_dir
        self.raw_data_dir = Path(raw_data_dir)
        self._original_registry = None

    def transaction_view(self, driver, originals):
        """Bind already byte-verified originals to a caller-held graph transaction."""
        view = Role01SourceProvider(driver, self.database, self.raw_data_dir)
        view._original_registry = originals
        return view

    def _originals(self):
        if self._original_registry is not None:
            return self._original_registry
        if self._session_originals.get() is not None:
            return self._session_originals.get()
        try:
            from services.ingestion import discover_sources
            versions = tuple(sorted((p.name, hashlib.sha256(p.read_bytes()).hexdigest())
                                    for p in discover_sources(self.raw_data_dir)))
            return _original_corpus(str(self.raw_data_dir.resolve()), versions)
        except DomainError:
            raise
        except OSError as exc:
            raise DomainError(503, "Original source files are unavailable for integrity verification") from exc

    def validate_corpus_versions(self):
        """Retained versions require explicit selection; never pool one workbook twice."""
        originals = self._originals()
        sources = [self._normalize(row) for row in self._source_versions.get_source_versions()]
        versions = {}
        for source in sources:
            filename = source.get("filename")
            versions.setdefault(filename, set()).add(source.get("source_version", source.get("sha256")))
        if any(len(values) != 1 for values in versions.values()):
            raise DomainError(409, "Multiple imported versions of one logical source require explicit corpus selection")
        if {source["id"] for source in sources} != set(originals["source"]):
            raise DomainError(409, "Imported source registry differs from the current original corpus")
        for source in sources:
            if source["id"] in originals["source"]:
                self._assert_original("source", source, originals)
        return originals

    def validate_graph_integrity(self):
        """Compare queried graph content to Role 01 conversion; never return cache as evidence."""
        if self._integrity_session_active.get():
            return
        originals = self.validate_corpus_versions()
        seen = {kind: set() for kind in ("feedback", "annotation", "fact", "chunk")}
        for row in self._source_versions.get_integrity_entities():
            kind, entity = row["kind"], self._normalize(row["entity"])
            self._assert_original(kind, entity, originals)
            if entity["id"] in seen[kind]:
                raise DomainError(409, "Imported evidence identity is ambiguous")
            seen[kind].add(entity["id"])
        if any(ids != set(originals[kind]) for kind, ids in seen.items()):
            raise DomainError(409, "Authoritative evidence graph is incomplete relative to original corpus")
        record_ids = set()
        for anchor in self._source_versions.get_integrity_record_anchors():
            anchor = self._normalize(anchor)
            expected = originals["record"].get(anchor["id"])
            if expected is None or anchor["id"] in record_ids:
                raise DomainError(409, "Source record identity differs from original corpus")
            expected = self._normalize(expected)
            for key in ("source_file_id", "source_version", "locator", "feedback_comment_id", "record_kind", "original_record_id"):
                if not same_snapshot(anchor.get(key), expected.get(key)):
                    raise DomainError(409, "Imported source anchors differ from original Role 01 conversion")
            record_ids.add(anchor["id"])
        if record_ids != set(originals["record"]):
            raise DomainError(409, "Imported original source records are incomplete")
        expected_edges = []
        for kind in ("record", "feedback"):
            for row in originals[kind].values():
                expected_edges.append((row["id"], "FROM_SOURCE_FILE", row["source_file_id"]))
                if kind == "record" and row.get("feedback_comment_id"):
                    expected_edges.append((row["id"], "DESCRIBES", row["feedback_comment_id"]))
        for kind in ("annotation", "fact", "chunk"):
            for row in originals[kind].values():
                expected_edges.append((row["id"], "FROM_SOURCE_RECORD", row["source_record_id"]))
                if kind == "annotation":
                    expected_edges.append((row["feedback_id"], "HAS_ANNOTATION", row["id"]))
        actual_edges = [(row["source"], row["relation"], row["target"])
                        for row in self._source_versions.get_integrity_relationships()]
        if Counter(actual_edges) != Counter(expected_edges):
            raise DomainError(409, "Imported evidence memberships differ from original Role 01 lineage")

    @contextmanager
    def integrity_session(self):
        """Validate a bounded preflight or a view within a caller-held transaction barrier."""
        originals_token = self._session_originals.set(self._originals())
        active_token = None
        try:
            self.validate_graph_integrity()
            active_token = self._integrity_session_active.set(True)
            yield
        finally:
            if active_token is not None:
                self._integrity_session_active.reset(active_token)
            self._session_originals.reset(originals_token)

    def _assert_original(self, kind, entity, originals):
        original = originals[kind].get(entity["id"])
        if original is None:
            raise DomainError(409, "Imported evidence is not part of the current original corpus")
        expected = self._normalize(original)
        actual = {key: entity.get(key) for key in expected}
        if not same_snapshot(actual, expected):
            raise DomainError(409, "Imported source content differs from original Role 01 conversion")

    def get(self, kind, entity_id):
        entity = super().get(kind, entity_id)
        if kind in {"source", "record", "chunk"}:
            originals = self.validate_corpus_versions()
            self._assert_original(kind, entity, originals)
        return entity

    def list_sources(self):
        originals = self.validate_corpus_versions()
        sources = super().list_sources()
        for source in sources:
            self._assert_original("source", source, originals)
        return sources

    def validate_original_snapshot(self, snapshot):
        """Validate persisted content against original bytes and current imported entity."""
        try:
            record = self.get("record", snapshot["record_id"])
            payload = record["raw_payload"]
        except DomainError as exc:
            if exc.status != 404:
                raise
            chunk = self.get("chunk", snapshot["record_id"])
            record = self.get("record", chunk["source_record_id"])
            payload = {"text": chunk["original_text"], "page_payload": record["raw_payload"]}
        if (record["source_id"] != snapshot["source_id"]
                or record["source_version"] != snapshot["source_version"]
                or not same_snapshot(record["locator"], snapshot["locator"])
                or not same_snapshot(payload, snapshot["raw_payload"])):
            raise DomainError(409, "Evidence snapshot differs from original source content")
        return True

    def validate_original_fact(self, fact):
        self._assert_original("fact", self._normalize(fact), self.validate_corpus_versions())
        self.get("record", fact["source_record_id"])
        return True

    def validate_original_feedback(self, feedback):
        self._assert_original("feedback", self._normalize(feedback), self.validate_corpus_versions())
        return True

    @staticmethod
    def _scopes(scope):
        if scope is None or isinstance(scope, EvidenceScope):
            return [scope or EvidenceScope()]
        if not isinstance(scope, dict):
            raise DomainError(422, "Evidence scope must be an object")
        plural = {"regions": "region", "derivatives": "derivative", "powertrains": "powertrain"}
        if not set(scope) & (set(plural) | {"signal_ids", "topics"}):
            if set(scope) - set(EvidenceScope.__dataclass_fields__):
                raise DomainError(422, "Unsupported evidence scope filter")
            return [EvidenceScope(**scope)]
        if set(scope) - (set(plural) | {"signal_ids", "topics"}):
            raise DomainError(422, "Cannot mix singular and plural evidence scopes")
        if scope.get("signal_ids") or scope.get("topics"):
            raise DomainError(422, "Signal and topic filters are not supported by Role 01 retrieval")
        values = []
        for key, field in plural.items():
            supplied = scope.get(key, [])
            if not isinstance(supplied, list) or any(not isinstance(v, str) or not v.strip() for v in supplied):
                raise DomainError(422, "Scope filters must be lists of nonempty strings")
            canonical = [EvidenceScope(**{field: value}).query_params()[field] for value in supplied]
            values.append(list(dict.fromkeys(canonical)) or [None])
        if len(values[0]) * len(values[1]) * len(values[2]) > 100:
            raise DomainError(422, "Evidence scope has too many cohort combinations")
        return [EvidenceScope(region=r, derivative=d, powertrain=p) for r, d, p in product(*values)]

    def _search(self, method, query, scope, limit, **kwargs):
        with self.integrity_session():
            return self._search_verified(method, query, scope, limit, **kwargs)

    def _search_verified(self, method, query, scope, limit, **kwargs):
        self.validate_corpus_versions()
        rows = {}
        for selected in self._scopes(scope):
            for row in getattr(self.evidence, method)(query, selected, limit=limit, **kwargs):
                rows[row["id"]] = row
        return [rows[key] for key in sorted(rows)][:limit]

    def search_feedback(self, query, scope=None, limit=20):
        return self._search("search_feedback", query, scope, limit)

    def search_document_chunks(self, query, scope=None, document_kind=None, limit=6):
        return self._search("search_document_chunks", query, scope, limit, document_kind=document_kind)

    def get_source_record(self, record_id):
        record = self.evidence.get_source_record(record_id)
        if record is not None:
            self.get("record", record_id)
        return record

    def _facts(self, method, scope, **kwargs):
        with self.integrity_session():
            return self._facts_verified(method, scope, **kwargs)

    def _facts_verified(self, method, scope, **kwargs):
        self.validate_corpus_versions()
        results = [getattr(self.evidence, method)(scope=selected, **kwargs) for selected in self._scopes(scope)]
        if len(results) == 1:
            return results[0]
        merged = dict(results[0])
        for key in ("facts", "possible_whole_line_proxy_candidates"):
            rows = {row["id"]: row for result in results for row in result.get(key, [])}
            if key in merged:
                merged[key] = [rows[rid] for rid in sorted(rows)]
        versions = sorted({v for result in results for v in result.get("source_versions", [])})
        merged.update(source_versions=versions, mixed_source_versions=len(versions) > 1,
                      scope_results=results)
        if "available_lines_in_scope" in merged:
            merged["available_lines_in_scope"] = sorted({v for result in results for v in result["available_lines_in_scope"]})
            merged["scope_gap"] = None if merged["facts"] else "No directly applicable sales facts for the requested scopes"
            merged["proxy_candidates_are_not_direct_derivative_volumes"] = bool(merged["possible_whole_line_proxy_candidates"])
        return merged

    def get_survey_facts(self, topic=None, scope=None):
        return self._facts("get_survey_facts", scope, topic=topic)

    def get_sales_facts(self, scope=None, year=2030):
        return self._facts("get_sales_facts", scope, year=year)

    def get_comment_cohort_counts(self, scope=None):
        with self.integrity_session():
            return self._cohort_counts_verified(scope)

    def _cohort_counts_verified(self, scope=None):
        self.validate_corpus_versions()
        results = [self.evidence.get_comment_cohort_counts(selected) for selected in self._scopes(scope)]
        if len(results) == 1:
            return results[0]
        merged = dict(results[0])
        for key in ("cohort_distinct_comment_count", "cohort_annotation_count"):
            merged[key] = sum(result[key] for result in results)
        for key in ("topic_bucket_distinct_comments", "topic_annotation_counts", "issue_label_distinct_comments",
                    "issue_annotation_counts", "source_label_counts"):
            merged[key] = {}
            for result in results:
                for label, count in result[key].items():
                    merged[key][label] = merged[key].get(label, 0) + count
        merged["annotation_counts_by_original_type"] = {}
        for result in results:
            for label, counts in result["annotation_counts_by_original_type"].items():
                dest = merged["annotation_counts_by_original_type"].setdefault(label, {"distinct_comments": 0, "annotation_count": 0})
                for key, count in counts.items():
                    dest[key] += count
        versions = sorted({v for result in results for v in result["source_versions"]})
        merged.update(source_versions=versions, mixed_source_versions=len(versions) > 1)
        return merged
