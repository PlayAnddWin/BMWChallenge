"""Role 1's processed-bundle importer for the backend-owned Neo4j transaction.

Startup: Role1ImportAdapter(driver, database=settings.neo4j_database) initializes
the evidence schema in a separate session. prepare_import never writes data;
commit_import neither opens sessions nor commits the supplied transaction.
It locks EvidenceManifest {id: 'role1'} before source access and publishes the
active selection in that transaction. Governance readers must use the same
manifest's property write lock before checking their current evidence snapshot.

Request fields are optional processed_dir and study_mappings. The latter accepts
a reviewed mapping list, a {mapping_version, mappings} envelope, or a JSON file
path. An empty request imports the configured BMW_DATA_DIR, never raw files.
"""

from __future__ import annotations

from copy import deepcopy
from importlib import import_module
from pathlib import Path
from typing import Any, Callable

from config import get_settings, resolve_path


_loader = import_module("pipeline.01_load_neo4j")
BundleError = _loader.BundleError
PreparedBundle = _loader.PreparedBundle


def _reviewed_mappings(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, (str, Path)):
        if not str(value).strip():
            raise BundleError("study_mappings must name a reviewed mapping file")
        return _loader.read_study_mappings(value)
    return _loader.normalize_study_mappings(value)


class Role1ImportAdapter:
    def __init__(self, driver: Any = None, database: str | None = None, *,
                 processed_dir: Path | str | None = None, study_mappings: Any = (),
                 settings: Any = None):
        settings = settings if settings is not None else get_settings()
        self.driver = driver
        self.database = database if database is not None else settings.neo4j_database
        if not isinstance(self.database, str) or not self.database.strip():
            raise BundleError("Neo4j database must be a nonempty name")
        self.processed_dir = resolve_path(processed_dir if processed_dir is not None else settings.data_dir)
        self.study_mappings = _reviewed_mappings(study_mappings)
        if driver is not None:
            self.initialize()

    def initialize(self, driver: Any = None) -> dict[str, Any]:
        """Initialize schema at startup, optionally attaching a driver then."""
        if driver is not None:
            self.driver = driver
        if self.driver is None:
            raise BundleError("A Neo4j driver is required to initialize the evidence schema")
        return _loader.initialize_schema(self.driver, database=self.database)

    def prepare_import(self, body: dict[str, Any], progress_callback: Callable[..., None] | None = None) -> PreparedBundle:
        if not isinstance(body, dict):
            raise BundleError("Import request must be an object")
        unexpected = set(body) - {"processed_dir", "study_mappings"}
        if unexpected:
            raise BundleError("Unexpected import fields: " + ", ".join(sorted(unexpected)))
        processed_dir = self.processed_dir
        if "processed_dir" in body:
            path = body["processed_dir"]
            if not isinstance(path, str) or not path.strip():
                raise BundleError("processed_dir must be a nonempty processed bundle path")
            processed_dir = resolve_path(path)
        mappings = _reviewed_mappings(body["study_mappings"]) if "study_mappings" in body else deepcopy(self.study_mappings)
        if progress_callback is not None:
            progress_callback("read_processed_bundle", 0.1, "live")
        # Backend imports require a completed converter run. Partial imports are
        # intentionally an explicit standalone-loader capability only.
        prepared = _loader.prepare_processed_bundle(processed_dir, study_mappings=mappings)
        if progress_callback is not None:
            progress_callback("prepared_import", 0.8, "live")
        return prepared

    def commit_import(self, prepared: PreparedBundle, tx: Any,
                      audit_sink: Callable[[str, str, Any, dict[str, Any], str], None]) -> dict[str, Any]:
        if not callable(audit_sink):
            raise BundleError("Backend import requires a transactional source audit sink")
        if not isinstance(prepared, PreparedBundle) or prepared.status != "succeeded":
            raise BundleError("Backend import requires a prepared, completed processed bundle")
        result = _loader.write_prepared_bundle(tx, prepared)
        for change in result["source_changes"]:
            snapshot = change["after"]
            rationale = ("Existing source evidence reconciled with validated converter and mapping metadata"
                         if snapshot["reconciliation"]["already_imported"]
                         else "Processed source evidence imported with validated provenance")
            # This matches services.api.execute_import's sink directly. Its
            # closure appends the ledger event inside the same caller-owned tx.
            audit_sink("SOURCE_IMPORTED", snapshot["id"], deepcopy(change["before"]), deepcopy(snapshot), rationale)
        return {"result_ids": list(result["source_file_ids"]), "failures": [],
                "sources": deepcopy(result["sources"]), "signals": [],
                "import_run_id": result["import_run_id"], "counts": result["counts"],
                "evidence_manifest": deepcopy(result["evidence_manifest"])}
