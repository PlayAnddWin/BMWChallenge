"""Role 1/2 integration boundary. No invented model output or source evidence."""
import json

from services.decisions import DomainError
from services.repository import same_snapshot


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
        return self._normalize(rows[0]["entity"])

    @staticmethod
    def _normalize(properties):
        entity = dict(properties)
        for storage_key, key in (("locator_json", "locator"), ("raw_payload_json", "raw_payload"), ("evidence_links_json", "evidence_links")):
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
        return entity
