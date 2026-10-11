"""Role 03 import boundary delegating conversion and graph writes to Role 01."""
from functools import lru_cache
import importlib.util
from pathlib import Path
import tempfile

from config import get_settings, resolve_path
from services.decisions import DomainError
from services.ingestion import convert_sources, write_bundle


@lru_cache(maxsize=1)
def role01_loader():
    path = Path(__file__).resolve().parents[1] / "pipeline" / "01_load_neo4j.py"
    spec = importlib.util.spec_from_file_location("bmw_role01_loader", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Role01ImportService:
    """Prepare outside governance transactions, commit with its exact write tx.

    This imports the configured corpus as a whole. A requested subset must not
    silently broaden into a full import; filtering is not implemented by Role 01.
    """
    def __init__(self, driver, *, database=None, raw_data_dir=None, study_mappings=()):
        self.driver = driver
        self.database = database
        self.raw_data_dir = resolve_path(raw_data_dir) if raw_data_dir is not None else get_settings().raw_data_dir
        self.study_mappings = tuple(study_mappings)

    def prepare_import(self, scope, progress_callback=None):
        if scope:
            raise DomainError(422, "Role 01 imports the complete configured corpus; subset import filters are unsupported")
        bundle = convert_sources(self.raw_data_dir, check_known_feedback_counts=True)
        loader = role01_loader()
        with tempfile.TemporaryDirectory(prefix="bmw-role01-import-") as temp:
            processed = Path(temp) / "processed"
            write_bundle(bundle, processed, self.raw_data_dir)
            prepared = loader.prepare_processed_bundle(processed, study_mappings=self.study_mappings)
        # No imported evidence is written here. Schema changes cannot share a
        # Neo4j data transaction; apply before admission to the commit callback.
        loader.ensure_schema(self.driver, self.database)
        return prepared

    def commit_import(self, prepared, *, tx, audit_sink):
        if tx is None:
            raise DomainError(503, "Role 01 import requires a real Neo4j transaction")
        result = role01_loader().commit_processed_bundle(tx, prepared)
        for source in prepared["source_files"]:
            audit_sink("SOURCE_IMPORTED", source["id"], None, dict(source),
                       "Original BMW source converted and loaded by Role 01")
        return {"sources": prepared["source_files"], "result_ids": result["source_file_ids"],
                "import_run_id": result["import_run_id"], "counts": result["counts"], "failures": []}
