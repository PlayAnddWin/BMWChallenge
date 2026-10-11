"""Role 1/2 integration boundary. No invented model output or source evidence."""
from collections import defaultdict
from collections.abc import Mapping
from copy import deepcopy
from itertools import product
import json
from math import isfinite

from services.decisions import DomainError
from services.evidence import EvidenceProviderError, EvidenceScope, Neo4jEvidenceProvider
from services.repository import same_snapshot


class UnavailableEvidenceProvider:
    def resolve(self, draft):
        raise DomainError(503, "Role 1 evidence resolver is not integrated; validated persistence is unavailable")


class _DatabaseBoundDriver:
    """Bind Role 1's session() calls without changing or owning the shared driver."""

    def __init__(self, driver, database):
        self.driver, self.database = driver, database

    def session(self, **kwargs):
        return self.driver.session(**{**kwargs, "database": self.database,
                                      "default_access_mode": "READ"})


class GraphSourceProvider:
    """Read native Role 1 entities and adapt scalar/array retrieval scopes.

    Native IDs are retained. ``source_id`` aliases ``source_file_id``; JSON
    storage properties become their unsuffixed values. Records, facts and chunks
    include the owning ``source_file`` and its exact version/SHA. Facts retain
    their stored ``locator`` and expose Role 1's parent-enriched ``source_locator``
    plus ``source_record_locator``. Chunks expose the exact page locator and keep
    ``chunk_number/start_offset/end_offset`` separately, validated against the
    preserved page text. Caches are local to a single read operation.
    """

    LABELS = {"source": "SourceFile", "record": "SourceRecord",
              "chunk": "DocumentChunk", "fact": "Fact", "feedback": "CustomerFeedback",
              "signal": "Signal", "signal_revision": "SignalRevision"}
    SCALAR_SCOPE = {"model", "derivative", "vehicle_line", "market", "region",
                    "powertrain", "source_version"}
    ARRAY_SCOPE = {"derivatives": "model", "regions": "region",
                   "powertrains": "powertrain", "source_versions": "source_version"}
    JSON_SHAPES = {"locator": dict, "source_locator": dict, "raw_payload": dict,
                   "payload": dict, "distribution": dict, "evidence_links": list,
                   "category_labels": list, "cell_locators": list,
                   "annotations": list, "source_records": list}

    def __init__(self, driver, database=None):
        if database is not None and (not isinstance(database, str) or not database.strip()):
            raise ValueError("Neo4j database must be a nonempty name")
        self.driver, self.database = driver, database
        # Neo4jEvidenceProvider currently has no database constructor argument.
        self.evidence_provider = Neo4jEvidenceProvider(_DatabaseBoundDriver(driver, database))

    def _call(self, method, *args, **kwargs):
        try:
            return getattr(self.evidence_provider, method)(*args, **kwargs)
        except EvidenceProviderError as exc:
            raise DomainError(503, "Role 1 evidence graph is unavailable") from exc
        except ValueError as exc:
            raise DomainError(422, str(exc)) from exc

    def _read(self, query, **params):
        return self._call("_read", query, **params)

    def list_sources(self):
        rows = self._read("MATCH (s:SourceFile) RETURN s{.*} AS source ORDER BY s.id")
        return self._unique_list([self._source(row["source"]) for row in rows])

    def list_signals(self):
        rows = self._read("MATCH (s:Signal) RETURN s{.*} AS signal ORDER BY s.id")
        return self._unique_list([self._normalize(row["signal"]) for row in rows])

    def list_conflicts(self, model=None):
        rows = self._read("MATCH (c:ConflictNode) WHERE $model IS NULL OR c.model = $model "
                          "RETURN c{.*} AS conflict ORDER BY c.delta DESC", model=model)
        return [dict(row["conflict"]) for row in rows]

    def get(self, kind, entity_id):
        """Get one allowlisted entity; duplicate IDs and inconsistent parents fail."""
        return self._get(kind, entity_id, {})

    def _get(self, kind, entity_id, cache):
        if not isinstance(kind, str) or kind not in self.LABELS:
            raise DomainError(422, "Unsupported source entity kind")
        if not isinstance(entity_id, str) or not entity_id.strip():
            raise DomainError(422, "Source entity ID must be a nonempty string")
        key = (kind, entity_id)
        if key in cache:
            return cache[key]
        rows = self._read(f"MATCH (s:{self.LABELS[kind]} {{id:$id}}) "
                          "RETURN s{.*} AS entity LIMIT 2", id=entity_id)
        if not rows:
            raise DomainError(404, "Source entity not found")
        if len(rows) != 1:
            raise DomainError(503, "Source identity is ambiguous")
        entity = self._normalize(rows[0]["entity"])
        if entity.get("id") != entity_id:
            raise DomainError(503, "Source entity has a conflicting identity")
        if kind == "source":
            entity = self._source(entity)
        elif kind in {"fact", "chunk"} and ("source_file_id" in entity or "source_record_id" in entity):
            parent_id = entity.get("source_record_id")
            if not isinstance(parent_id, str) or not parent_id:
                raise DomainError(503, "Native evidence has no parent source record")
            parent = self._parent("record", parent_id, cache)
            if "source_file" not in parent or not parent.get("locator"):
                raise DomainError(503, "Native evidence has no source-bound parent record")
            self._bind_source(entity, parent["source_file"])
            entity["source_record"] = deepcopy(parent)
            entity["source_record_locator"] = deepcopy(parent["locator"])
            if kind == "chunk":
                stored_locator = entity.get("locator")
                self._validate_chunk(entity, parent)
                entity["locator"] = deepcopy(parent["locator"])
                entity["source_locator"] = deepcopy(parent["locator"])
                if stored_locator is not None and not self._locator_matches("chunk", entity, stored_locator):
                    raise DomainError(503, "Stored chunk locator differs from its parent page/span")
            else:
                self._validate_fact(entity, parent, cache)
                entity["source_locator"] = {**parent["locator"], **entity["locator"]}
        elif kind in {"record", "feedback"} and "source_file_id" in entity:
            self._bind_source(entity, self._parent("source", entity["source_file_id"], cache))
            if kind == "record":
                if not entity.get("locator"):
                    raise DomainError(503, "Native source record has no locator")
                entity["source_locator"] = deepcopy(entity["locator"])
        cache[key] = entity
        return entity

    def _parent(self, kind, entity_id, cache):
        if not isinstance(entity_id, str) or not entity_id.strip():
            raise DomainError(503, "Imported evidence has an invalid parent ID")
        try:
            return self._get(kind, entity_id, cache)
        except DomainError as exc:
            if exc.status == 404:
                raise DomainError(503, "Imported evidence references a missing parent") from exc
            raise

    @classmethod
    def _source(cls, properties):
        entity = cls._normalize(properties)
        identity = entity.get("id")
        if not isinstance(identity, str) or not identity.strip():
            raise DomainError(503, "Imported source has no native ID")
        if any(entity.get(key, identity) != identity for key in ("source_id", "source_file_id")):
            raise DomainError(503, "Imported source identities conflict")
        if "source_sha256" in entity and entity["source_sha256"] != entity.get("sha256"):
            raise DomainError(503, "Imported source SHA representations conflict")
        entity.setdefault("source_version", entity.get("sha256"))
        entity["source_id"] = entity["id"]
        entity["source_file_id"] = entity["id"]
        if "sha256" in entity:
            entity["source_sha256"] = entity["sha256"]
        return entity

    @staticmethod
    def _bind_source(entity, source):
        for field in ("source_id", "source_file_id"):
            if entity.get(field) is not None and entity[field] != source["id"]:
                raise DomainError(503, "Native evidence and parent source identities differ")
        version, sha = source.get("source_version"), source.get("sha256")
        if not isinstance(version, str) or not version or not isinstance(sha, str) or not sha:
            raise DomainError(503, "Native parent source has no exact version/SHA")
        for field, expected in (("source_version", version), ("source_sha256", sha)):
            if entity.get(field) is not None and entity[field] != expected:
                raise DomainError(503, "Native evidence and parent source versions/SHA differ")
            entity[field] = expected
        entity["source_id"] = entity["source_file_id"] = source["id"]
        entity["source_file"] = deepcopy(source)
        locator = entity.get("locator") or {}
        if "filename" in locator and locator["filename"] != source.get("filename"):
            raise DomainError(503, "Native evidence locator names a different source file")

    @staticmethod
    def _validate_chunk(chunk, parent):
        locator, payload = parent.get("locator") or {}, parent.get("raw_payload") or {}
        text, original = payload.get("text"), chunk.get("original_text")
        start, end = chunk.get("start_offset"), chunk.get("end_offset")
        if (type(chunk.get("page")) is not int or chunk["page"] < 1
                or not same_snapshot(chunk["page"], locator.get("page"))
                or not same_snapshot(chunk.get("printed_page"), locator.get("printed_page"))
                or type(chunk.get("chunk_number")) is not int or chunk["chunk_number"] < 1
                or type(start) is not int or type(end) is not int
                or not isinstance(text, str) or not isinstance(original, str)
                or not 0 <= start < end <= len(text) or text[start:end] != original):
            raise DomainError(503, "Document chunk differs from its preserved parent page/span")
        chunk.setdefault("printed_page", locator.get("printed_page"))

    def _validate_fact(self, fact, parent, cache):
        locator = fact.get("locator")
        if not isinstance(locator, dict) or not locator:
            raise DomainError(503, "Native fact has no stored locator")
        self._row_coordinates(locator, parent["locator"])
        cells = fact.get("cell_locators", locator.get("cells"))
        if "cell_locators" in fact and "cells" in locator and not same_snapshot(cells, locator["cells"]):
            raise DomainError(503, "Distribution cell locators have conflicting metadata")
        if cells is not None:
            if not cells:
                raise DomainError(503, "Distribution has no stored cells")
            categories = []
            for cell in cells:
                record = (self._parent("record", cell["source_record_id"], cache)
                          if cell.get("source_record_id") else parent)
                if record.get("source_id") != fact["source_id"] or record.get("source_version") != fact["source_version"]:
                    raise DomainError(503, "Distribution cell belongs to a different source/version")
                self._row_coordinates(cell, record["locator"])
                raw = self._cell_value(cell, record)
                category = cell.get("response_category", str(cell.get("column_header")))
                distribution = fact.get("distribution") or {}
                if category not in distribution:
                    raise DomainError(503, "Distribution cell has no stored response category")
                categories.append(category)
                self._numeric_cell(raw, distribution[category], cell.get("value_provenance"))
            if len(set(categories)) != len(categories) or set(categories) != set(distribution):
                raise DomainError(503, "Distribution cells do not cover the exact stored categories")
            if "category_labels" in fact and not same_snapshot(categories, fact["category_labels"]):
                raise DomainError(503, "Distribution category labels differ from stored cell metadata")
        elif "column" in locator:
            raw = self._cell_value(locator, parent)
            if "source_value" in fact and not same_snapshot(raw, fact["source_value"]):
                raise DomainError(503, "Fact source value differs from its preserved cell")
            value = fact.get("volume") if fact.get("kind") == "sales" else fact.get("value")
            self._numeric_cell(raw, value, fact.get("value_provenance"))

    @staticmethod
    def _row_coordinates(locator, parent_locator):
        for field in ("filename", "sheet", "row", "page", "printed_page"):
            if field in locator and field in parent_locator and not same_snapshot(locator[field], parent_locator[field]):
                raise DomainError(503, "Fact cell locator differs from its preserved source row")

    @staticmethod
    def _cell_value(locator, record):
        column = locator.get("column")
        columns = (record.get("raw_payload") or {}).get("columns")
        if (type(column) is not int or column < 1 or not isinstance(columns, list)
                or column > len(columns) or not isinstance(columns[column - 1], dict)
                or "value" not in columns[column - 1]):
            raise DomainError(503, "Fact cell has no preserved source column")
        return columns[column - 1]["value"]

    @staticmethod
    def _numeric_cell(raw, value, provenance):
        # Formula text is preserved separately; the imported fact explicitly
        # identifies cached results. Never reinterpret it as literal source text.
        try:
            if isinstance(raw, bool) or isinstance(value, bool) or raw is None or value is None:
                raise ValueError
            number = float(value)
            if not isfinite(number):
                raise ValueError
            if isinstance(raw, str) and raw.startswith("=") and provenance in (None, "cached_formula_result"):
                return
            source_number = float(raw.replace(",", "") if isinstance(raw, str) else raw)
            if not isfinite(source_number) or source_number != number:
                raise ValueError
        except (TypeError, ValueError, OverflowError) as exc:
            raise DomainError(503, "Fact value differs from its preserved numeric cell") from exc

    def verify_reference(self, reference):
        """Verify a row, stored fact/cell, or exact chunk (also via its parent ID).

        A fact's cell locator must be stored on a Fact; the existence of a raw
        column alone is insufficient. Chunk locators may add the exact stored
        offset pair/chunk number/quote, or a ``span`` object with that offset
        pair and optional quote. Unknown extensions and invented quotes fail.
        Legacy source_id/locator fixtures support their exact stored locator.
        """
        if (not isinstance(reference, Mapping) or reference.get("validation_state") != "verified"
                or any(not isinstance(reference.get(key), str) or not reference[key].strip()
                       for key in ("record_id", "source_id", "source_version"))
                or not self._valid_locator(reference.get("locator"))):
            return False
        candidates = []
        for kind in ("record", "chunk", "fact"):
            try:
                entity = self.get(kind, reference["record_id"])
                # Older injected get() doubles only distinguish record/chunk.
                if kind != "fact" or entity.get("kind") or entity.get("source_record_id"):
                    candidates.append((kind, entity))
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
        kind, entity = candidates[0]
        if not self._provenance_matches(entity, source, reference):
            return False
        if self._locator_matches(kind, entity, reference["locator"]):
            return True
        if kind != "record" or "source_file_id" not in entity:
            return False
        # Native DTOs cite source_record_id, with a fact/cell or chunk locator.
        # Resolve actual stored descendants instead of accepting arbitrary cells.
        for derived_kind in ("fact", "chunk"):
            label = self.LABELS[derived_kind]
            rows = self._read(f"""
                MATCH (e:{label})
                WHERE e.source_file_id = $source_id
                  AND (e.source_record_id = $record_id
                       OR e.cell_locators_json CONTAINS $record_token)
                RETURN e.id AS id ORDER BY e.id
            """, source_id=reference["source_id"], record_id=reference["record_id"],
                record_token=json.dumps(reference["record_id"]))
            for row in rows:
                derived = self.get(derived_kind, row["id"])
                belongs = (derived.get("source_record_id") == reference["record_id"]
                           or any(cell.get("source_record_id") == reference["record_id"]
                                  for cell in derived.get("cell_locators", [])))
                if (belongs and self._provenance_matches(derived, source, reference)
                        and self._locator_matches(derived_kind, derived, reference["locator"])):
                    return True
        return False

    @staticmethod
    def _provenance_matches(entity, source, reference):
        return (entity.get("source_id", entity.get("source_file_id")) == reference["source_id"]
                and source.get("id", reference["source_id"]) == reference["source_id"]
                and reference.get("source_file_id", reference["source_id"]) == reference["source_id"]
                and entity.get("source_version") == reference["source_version"]
                and source.get("source_version", source.get("sha256")) == reference["source_version"]
                and ("source_sha256" not in reference or reference["source_sha256"] == source.get("sha256"))
                and ("source_sha256" not in entity or entity["source_sha256"] == source.get("sha256")))

    @classmethod
    def _locator_matches(cls, kind, entity, locator):
        if kind == "chunk" and "source_record" in entity:
            base = dict(locator)
            span = base.pop("span", None)
            quote = base.pop("quote", None)
            number = base.pop("chunk_number", None)
            offsets = {key: base.pop(key) for key in ("start_offset", "end_offset") if key in base}
            if not same_snapshot(base, entity["locator"]):
                return False
            expected = {key: entity[key] for key in ("start_offset", "end_offset")}
            if offsets and not same_snapshot(offsets, expected):
                return False
            if number is not None and not same_snapshot(number, entity["chunk_number"]):
                return False
            if quote is not None and quote != entity["original_text"]:
                return False
            if span is not None:
                if not isinstance(span, dict) or set(span) - {"start_offset", "end_offset", "quote"}:
                    return False
                if not same_snapshot({key: value for key, value in span.items() if key != "quote"}, expected):
                    return False
                if "quote" in span and span["quote"] != entity["original_text"]:
                    return False
            return True
        locators = [entity.get("locator")]
        if kind == "fact":
            locators += [entity.get("source_locator"), *entity.get("cell_locators", [])]
        # Legacy fixtures cannot establish text-span evidence without a parent
        # page. An exact synthetic page/row locator remains compatible.
        if "source_file_id" not in entity and any(key in locator for key in ("quote", "span", "start_offset", "end_offset")):
            return False
        return any(same_snapshot(stored, locator) for stored in locators if stored is not None)

    @staticmethod
    def _valid_locator(locator):
        def valid(value, key=None):
            if value is None:
                return key == "printed_page"
            if isinstance(value, bool):
                return False
            if isinstance(value, dict):
                return bool(value) and all(isinstance(name, str) and name.strip() and valid(item, name)
                                           for name, item in value.items())
            if isinstance(value, list):
                return bool(value) and all(valid(item) for item in value)
            if key in {"row", "page", "column", "chunk_number"}:
                return type(value) is int and value > 0
            if key in {"start_offset", "end_offset"}:
                return type(value) is int and value >= 0
            if isinstance(value, str):
                return bool(value.strip())
            return type(value) is int or type(value) is float and isfinite(value)
        return isinstance(locator, dict) and valid(locator)

    @classmethod
    def _normalize(cls, properties):
        """Decode native JSON without dropping distribution-cell metadata."""
        if not isinstance(properties, Mapping):
            raise DomainError(503, "Imported source metadata has an invalid shape")
        entity = deepcopy(dict(properties))
        if any(not isinstance(key, str) for key in entity):
            raise DomainError(503, "Imported source metadata keys must be names")
        try:
            for storage_key in list(entity):
                if storage_key.endswith("_json"):
                    key = storage_key.removesuffix("_json")
                    decoded = json.loads(entity.pop(storage_key))
                    if key in entity and not same_snapshot(entity[key], decoded):
                        raise DomainError(503, "Imported JSON metadata has conflicting representations")
                    entity[key] = decoded
            for key, expected in cls.JSON_SHAPES.items():
                if isinstance(entity.get(key), str):
                    entity[key] = json.loads(entity[key])
                if key in entity and not isinstance(entity[key], expected):
                    raise DomainError(503, "Imported source metadata has an invalid shape")
            for key in ("evidence_links", "cell_locators", "annotations", "source_records"):
                if any(not isinstance(item, dict) for item in entity.get(key, [])):
                    raise DomainError(503, f"Imported {key} must contain objects")
            for key in ("locator", "source_locator"):
                if "cells" in entity.get(key, {}):
                    cells = entity[key]["cells"]
                    if not isinstance(cells, list) or any(not isinstance(cell, dict) for cell in cells):
                        raise DomainError(503, "Imported locator cells must contain objects")
            json.dumps(entity, allow_nan=False)
        except (ValueError, TypeError, OverflowError) as exc:
            raise DomainError(503, "Imported source metadata is malformed") from exc
        if "source_file_id" in entity:
            if "source_id" in entity and entity["source_id"] != entity["source_file_id"]:
                raise DomainError(503, "Imported source identities conflict")
            entity["source_id"] = entity["source_file_id"]
        if "locator" not in entity and "source_locator" in entity:
            entity["locator"] = deepcopy(entity["source_locator"])
        return entity

    @staticmethod
    def _unique_list(entities):
        ids = set()
        for entity in entities:
            identity = entity.get("id")
            if not isinstance(identity, str) or not identity or identity in ids:
                raise DomainError(503, "Source identity is missing or ambiguous")
            ids.add(identity)
        return entities

    @classmethod
    def _deduplicate(cls, entities, limit=None):
        indexed = {}
        for entity in entities:
            identity = entity.get("id")
            if not isinstance(identity, str) or not identity:
                raise DomainError(503, "Retrieved evidence has no native ID")
            if identity in indexed and not same_snapshot(indexed[identity], entity):
                raise DomainError(503, "Source identity has conflicting retrieval snapshots")
            indexed[identity] = entity
        return [indexed[key] for key in sorted(indexed)[:limit]]

    @classmethod
    def _scopes(cls, scope, operation):
        """Fan out array scopes to normalized Role 1 scalar scopes.

        Empty arrays mean no filter. Nonempty signal_ids/topics filters are
        explicitly unsupported; survey's singular topic uses reviewed Role 1
        mappings. Survey/document powertrain and non-sales vehicle_line filters
        are rejected because the native adapters cannot enforce them.
        """
        if isinstance(scope, EvidenceScope):
            values = scope.query_params()
        elif scope is None:
            values = {}
        elif isinstance(scope, Mapping):
            values = dict(scope)
        else:
            raise DomainError(422, "scope must be a scalar scope or filter object")
        if set(values) - cls.SCALAR_SCOPE - set(cls.ARRAY_SCOPE) - {"signal_ids", "topics"}:
            raise DomainError(422, "Unsupported evidence scope filters")
        arrays = {}
        for key in (*cls.ARRAY_SCOPE, "signal_ids", "topics"):
            items = values.get(key, [])
            if not isinstance(items, list) or any(not isinstance(item, str) or not item.strip() for item in items):
                raise DomainError(422, f"{key} must be a list of nonempty strings")
            arrays[key] = sorted({item.strip() for item in items})
        for key in ("signal_ids", "topics"):
            if arrays[key]:
                raise DomainError(422, f"Nonempty {key} filters are unsupported for {operation}")
        scalar = {key: values.get(key) for key in cls.SCALAR_SCOPE}
        if any(value is not None and (not isinstance(value, str) or not value.strip()) for value in scalar.values()):
            raise DomainError(422, "Scalar evidence scope filters must be nonempty strings")
        scalar = {key: value.strip() if value is not None else None for key, value in scalar.items()}
        if operation != "sales-facts" and scalar["vehicle_line"] is not None:
            raise DomainError(422, f"vehicle_line filters are unsupported for {operation}; use model/derivatives")
        if operation in {"survey-facts", "document-search"} and (scalar["powertrain"] or arrays["powertrains"]):
            raise DomainError(422, f"Powertrain filters are unsupported for native {operation}")
        for array_key, scalar_key in cls.ARRAY_SCOPE.items():
            if arrays[array_key] and (scalar[scalar_key] is not None
                                     or scalar_key == "model" and scalar["derivative"] is not None
                                     or scalar_key == "region" and scalar["market"] is not None):
                raise DomainError(422, f"Use either {array_key} or its scalar filter")
        dimensions = [(key, arrays[array_key] or [scalar[key]])
                      for array_key, key in cls.ARRAY_SCOPE.items()]
        scopes = []
        for combination in product(*(items for _, items in dimensions)):
            current = {**scalar, **dict(zip((key for key, _ in dimensions), combination))}
            params = EvidenceScope(**current).query_params()
            if params["model"] and params["derivative"] and params["model"] != params["derivative"]:
                raise DomainError(422, "Model and derivative filters conflict")
            if (current["market"] and current["region"]
                    and EvidenceScope(region=current["market"]).query_params()["region"] != params["region"]):
                raise DomainError(422, "Market and region filters conflict")
            if (operation == "sales-facts" and params["vehicle_line"]
                    and (params["model"] or params["derivative"])
                    and params["vehicle_line"] != (params["model"] or params["derivative"])):
                raise DomainError(422, "Vehicle line and model/derivative filters conflict")
            if operation == "document-search" and params["model"] is not None:
                params["derivative"] = params["model"]
            if params not in scopes:
                scopes.append(params)
        return scopes

    @staticmethod
    def _limit(limit, maximum):
        if type(limit) is not int or not 1 <= limit <= 200:
            raise DomainError(422, "limit must be an integer between 1 and 200")
        return min(limit, maximum)

    def _retrieved(self, kind, dto, cache):
        """Bind a Role 1 DTO to its exact native entity, preserving query metadata."""
        dto = self._normalize(dto)
        if not isinstance(dto.get("id"), str) or not dto["id"].strip():
            raise DomainError(503, "Retrieved evidence has no native ID")
        native = self._get(kind, dto.get("id"), cache)
        context = {"annotations", "source_records", "topic_mapping", "mapping_strength",
                   "unknowns", "source_label_semantics"}
        for key in (set(dto) & set(native)) - context:
            if not same_snapshot(dto[key], native[key]):
                raise DomainError(503, "Retrieved DTO differs from its native source snapshot")
        result = {**deepcopy(native), **dto}
        if kind == "feedback":
            for field in ("source_records", "annotations"):
                entries = []
                for raw in dto.get(field, []):
                    entry = self._normalize(raw)
                    record_id = entry.get("source_record_id", entry.get("id") if field == "source_records" else None)
                    if not record_id:
                        raise DomainError(503, "Feedback evidence has no parent source row")
                    record = self._parent("record", record_id, cache)
                    if record.get("feedback_comment_id") not in (None, result["id"]):
                        raise DomainError(503, "Feedback evidence belongs to another canonical comment")
                    if record["source_id"] != result["source_id"] or record["source_version"] != result["source_version"]:
                        raise DomainError(503, "Feedback evidence belongs to another source/version")
                    for key in ("source_id", "source_file_id", "source_sha256", "source_version", "locator", "source_locator"):
                        if key in entry and not same_snapshot(entry[key], record.get(key)):
                            raise DomainError(503, "Feedback evidence differs from its preserved source row")
                        entry[key] = deepcopy(record[key])
                    entry["source_record_id"] = record_id
                    entries.append(entry)
                result[field] = entries
        return result

    def search_feedback(self, query, scope=None, limit=20):
        """Union scalar feedback searches by canonical ID; limit is global (max 100)."""
        scopes, limit, cache = self._scopes(scope, "feedback-search"), self._limit(limit, 100), {}
        entities = []
        for scalar in scopes:
            entities.extend(self._retrieved("feedback", dto, cache)
                            for dto in self._call("search_feedback", query, scalar, limit))
        return self._deduplicate(entities, limit)

    def get_source_record(self, record_id):
        """Role 1 compatibility method: absent records return None, ambiguity fails."""
        try:
            return self.get("record", record_id)
        except DomainError as exc:
            if exc.status == 404:
                return None
            raise

    @staticmethod
    def _versions(entities):
        versions = sorted({entity["source_version"] for entity in entities if entity.get("source_version")})
        return {"source_versions": versions, "mixed_source_versions": len(versions) > 1}

    def get_survey_facts(self, topic=None, scope=None):
        """Union exact native facts, preserving G68/CN and reviewed mapping states."""
        scopes, cache, facts, responses = self._scopes(scope, "survey-facts"), {}, [], []
        for scalar in scopes:
            response = self._call("get_survey_facts", topic, scalar)
            responses.append(response)
            facts.extend(self._retrieved("fact", dto, cache) for dto in response.get("facts", []))
        facts = self._deduplicate(facts, 200)
        statuses = {response["mapping_status"] for response in responses}
        result = {"topic": topic, "facts": facts,
                  "mapping_status": next(iter(statuses)) if len(statuses) == 1 else "mixed",
                  **self._versions(facts)}
        messages = sorted({response["message"] for response in responses if response.get("message")})
        if messages:
            result["message"] = " ".join(messages)
        mappings = [self._normalize(mapping) for response in responses for mapping in response.get("mappings", [])]
        if mappings:
            result["mappings"] = self._deduplicate(mappings)
        if len(scopes) > 1:
            result["scope_results"] = [{"scope": scalar, "mapping_status": response["mapping_status"],
                                        "fact_ids": sorted({fact["id"] for fact in response.get("facts", [])})}
                                       for scalar, response in zip(scopes, responses)]
        return result

    def get_sales_facts(self, scope=None, year=2030):
        """Keep direct sales and whole-line proxy candidates separate across scopes."""
        if type(year) is not int or not 1900 <= year <= 2200:
            raise DomainError(422, "year must be an integer between 1900 and 2200")
        scopes, cache, responses, direct, proxies = self._scopes(scope, "sales-facts"), {}, [], [], []
        for scalar in scopes:
            response = self._call("get_sales_facts", scalar, year)
            responses.append(response)
            direct.extend(self._retrieved("fact", dto, cache) for dto in response.get("facts", []))
            proxies.extend(self._retrieved("fact", dto, cache)
                           for dto in response.get("possible_whole_line_proxy_candidates", []))
        direct, proxies = self._deduplicate(direct), self._deduplicate(proxies)
        retained_ids = {fact["id"] for fact in self._deduplicate(direct + proxies, 200)}
        direct, proxies = ([fact for fact in entities if fact["id"] in retained_ids] for entities in (direct, proxies))
        gaps = sorted({response["scope_gap"] for response in responses if response.get("scope_gap")})
        result = {**responses[0], "year": year, "facts": direct,
                  "possible_whole_line_proxy_candidates": proxies,
                  "proxy_candidates_are_not_direct_derivative_volumes": bool(proxies),
                  "available_lines_in_scope": sorted({line for response in responses
                                                       for line in response.get("available_lines_in_scope", [])}),
                  "scope_gap": "; ".join(gaps) if gaps else None,
                  **self._versions(direct + proxies)}
        if len(scopes) > 1:
            result["scope_results"] = [{"scope": scalar, "scope_gap": response.get("scope_gap"),
                                        "direct_fact_ids": sorted({fact["id"] for fact in response.get("facts", [])}),
                                        "proxy_fact_ids": sorted({fact["id"] for fact in response.get("possible_whole_line_proxy_candidates", [])})}
                                       for scalar, response in zip(scopes, responses)]
        return result

    def search_document_chunks(self, query, scope=None, document_kind=None, limit=6):
        """Union page-validated chunks by ID; limit is global (native max 50)."""
        scopes, limit, cache = self._scopes(scope, "document-search"), self._limit(limit, 50), {}
        entities = []
        for scalar in scopes:
            entities.extend(self._retrieved("chunk", dto, cache)
                            for dto in self._call("search_document_chunks", query, scalar, document_kind, limit))
        return self._deduplicate(entities, limit)

    def get_comment_cohort_counts(self, scope=None):
        """Count the union of canonical comments, independently of annotations.

        Use Role 1's managed read adapter with a union query: adding scalar
        cohort summaries would double-count overlapping normalized scopes.
        """
        scopes = self._scopes(scope, "cohort-counts")
        rows = self._read("""
            UNWIND $scopes AS scope
            MATCH (fb:CustomerFeedback)
            WHERE (scope.model IS NULL OR fb.model = scope.model)
              AND (scope.derivative IS NULL OR fb.derivative = scope.derivative OR fb.model = scope.derivative)
              AND (scope.region IS NULL OR fb.region = scope.region)
              AND (scope.powertrain IS NULL OR fb.powertrain = scope.powertrain)
              AND (scope.source_version IS NULL OR fb.source_version = scope.source_version)
            WITH DISTINCT fb
            OPTIONAL MATCH (fb)-[:HAS_ANNOTATION]->(a:FeedbackAnnotation)
            RETURN fb.id AS id, fb.source_version AS source_version, fb.source_label AS source_label,
                   collect(DISTINCT properties(a)) AS annotations
            ORDER BY id
        """, scopes=scopes)
        self._unique_list(rows)
        topics, issues, labels, types = (defaultdict(set) for _ in range(4))
        topic_annotations, issue_annotations, type_annotations = (defaultdict(set) for _ in range(3))
        annotation_snapshots = {}
        annotation_owners = {}
        for row in rows:
            feedback_id = row["id"]
            if row.get("source_label") is not None:
                labels[row["source_label"]].add(feedback_id)
            for raw in row.get("annotations", []):
                annotation = self._normalize(raw)
                aid = annotation.get("id")
                if not isinstance(aid, str) or not aid:
                    raise DomainError(503, "Cohort annotation has no native ID")
                if (annotation.get("feedback_id", feedback_id) != feedback_id
                        or annotation_owners.get(aid, feedback_id) != feedback_id):
                    raise DomainError(503, "Cohort annotation belongs to another canonical comment")
                if aid in annotation_snapshots and not same_snapshot(annotation_snapshots[aid], annotation):
                    raise DomainError(503, "Cohort annotation identity is ambiguous")
                annotation_snapshots[aid] = annotation
                annotation_owners[aid] = feedback_id
                topic, issue, original_type = (annotation.get(key) for key in
                                               ("vfc2_original", "vfc3_original", "feedback_type_original"))
                if topic is not None:
                    topics[topic].add(feedback_id)
                    topic_annotations[topic].add(aid)
                if issue is not None:
                    key = f"{topic or '(unclassified)'} :: {issue}"
                    issues[key].add(feedback_id)
                    issue_annotations[key].add(aid)
                if original_type is not None:
                    types[original_type].add(feedback_id)
                    type_annotations[original_type].add(aid)
        def counts(groups):
            return {key: len(groups[key]) for key in sorted(groups)}
        return {"cohort_distinct_comment_count": len(rows), "cohort_annotation_count": len(annotation_snapshots),
                **self._versions(rows), "topic_bucket_distinct_comments": counts(topics),
                "topic_annotation_counts": counts(topic_annotations), "issue_label_distinct_comments": counts(issues),
                "issue_annotation_counts": counts(issue_annotations),
                "annotation_counts_by_original_type": {key: {"distinct_comments": len(types[key]),
                                                             "annotation_count": len(type_annotations[key])}
                                                       for key in sorted(types)},
                "source_label_counts": counts(labels), "source_labels_are_opaque": True,
                "counting_rule": "DISTINCT canonical feedback IDs; annotation and source-row counts are reported separately."}
