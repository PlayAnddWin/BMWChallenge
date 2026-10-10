from __future__ import annotations

import json
from pathlib import Path
import runpy
import tempfile
import unittest

from config import get_settings
from services.ingestion import convert_sources, write_bundle


REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_DATA_DIR = get_settings().raw_data_dir
_loader = runpy.run_path(str(REPO_ROOT / "pipeline" / "01_load_neo4j.py"))
load_processed_bundle = _loader["load_processed_bundle"]
BundleError = _loader["BundleError"]


class _Result:
    def consume(self):
        return None


class _Transaction:
    def __init__(self):
        self.calls = []

    def run(self, query, **params):
        self.calls.append((query, params))
        return _Result()


class _Session:
    def __init__(self):
        self.tx = _Transaction()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def run(self, query, **params):
        return self.tx.run(query, **params)

    def execute_write(self, work):
        return work(self.tx)


class _Driver:
    def __init__(self):
        self.session_value = _Session()

    def session(self):
        return self.session_value


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class LoaderTests(unittest.TestCase):
    def _bundle_dir(self, root: Path, status: str = "succeeded") -> Path:
        bundle_dir = root / "processed"
        bundle_dir.mkdir()
        (bundle_dir / "manifest.json").write_text(json.dumps({
            "status": status,
            "conversion_version": "test-1",
            "counts": {"source_files": 1},
        }), encoding="utf-8")
        _write_jsonl(bundle_dir / "source_files.jsonl", [{
            "id": "SRC-1", "sha256": "abc", "source_version": "abc",
            "filename": "fixture.xlsx", "kind": "feedback",
        }])
        _write_jsonl(bundle_dir / "source_records.jsonl", [{
            "id": "SR-1", "source_file_id": "SRC-1", "source_version": "abc",
            "locator": {"sheet": "Feedback_Explorer", "row": 2},
            "raw_payload": {"text": "A synthetic example."},
            "feedback_comment_id": "FB-1",
        }])
        _write_jsonl(bundle_dir / "feedback_comments.jsonl", [{
            "id": "FB-1", "source_file_id": "SRC-1", "source_version": "abc",
            "model": "G70", "region": "US", "original_text": "A synthetic example.",
        }])
        _write_jsonl(bundle_dir / "feedback_annotations.jsonl", [{
            "id": "ANN-1", "feedback_id": "FB-1", "source_record_id": "SR-1",
            "feedback_type_original": "DTU",
        }])
        _write_jsonl(bundle_dir / "facts.jsonl", [{
            "id": "FACT-1", "source_record_id": "SR-1", "kind": "sales", "volume": 0,
        }])
        _write_jsonl(bundle_dir / "document_chunks.jsonl", [{
            "id": "DOC-1", "source_file_id": "SRC-1", "source_record_id": "SR-1",
            "original_text": "synthetic",
        }])
        return bundle_dir

    def test_bundle_import_batches_and_audit_share_one_transaction(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temp_dir:
            bundle_dir = self._bundle_dir(Path(temp_dir))
            driver = _Driver()
            audit_events = []

            result = load_processed_bundle(
                driver, bundle_dir,
                audit_sink=lambda tx, event: audit_events.append((tx, event)),
            )

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["counts"]["source_files"], 1)
        self.assertEqual(result["counts"]["feedback_comments"], 1)
        self.assertEqual(result["counts"]["feedback_annotations"], 1)
        self.assertEqual(result["counts"]["facts"], 1)
        self.assertEqual(result["counts"]["document_chunks"], 1)
        self.assertEqual(len(audit_events), 1)
        self.assertIs(audit_events[0][0], driver.session_value.tx)
        self.assertEqual(audit_events[0][1]["action"], "source_import")
        self.assertGreater(len(driver.session_value.tx.calls), 1)

    def test_partial_bundle_is_refused_without_explicit_override(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temp_dir:
            bundle_dir = self._bundle_dir(Path(temp_dir), status="partial")

            with self.assertRaisesRegex(BundleError, "Refusing to import"):
                load_processed_bundle(_Driver(), bundle_dir)

    def test_real_bmw_bundle_serializes_and_passes_import_validation(self) -> None:
        if not REAL_DATA_DIR.is_dir():
            self.skipTest("private BMW-Data directory is not present")

        bundle = convert_sources(REAL_DATA_DIR, check_known_feedback_counts=True)
        self.assertEqual(
            bundle.manifest["status"],
            "succeeded" if not bundle.manifest["missing_document_kinds"] else "partial",
        )
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temp_dir:
            bundle_dir = Path(temp_dir) / "bundle"
            write_bundle(bundle, bundle_dir, REAL_DATA_DIR)
            driver = _Driver()
            audit_events = []
            result = load_processed_bundle(
                driver, bundle_dir,
                audit_sink=lambda tx, event: audit_events.append((tx, event)),
                allow_partial=True,
            )

        self.assertEqual(result["counts"]["source_files"], bundle.manifest["counts"]["source_files"])
        self.assertEqual(result["counts"]["source_records"], bundle.manifest["counts"]["source_records"])
        self.assertEqual(result["counts"]["feedback_comments"], 10_128)
        self.assertEqual(result["counts"]["feedback_annotations"], 13_371)
        self.assertEqual(result["counts"]["facts"], bundle.manifest["counts"]["facts"])
        self.assertEqual(result["counts"]["document_chunks"], bundle.manifest["counts"]["document_chunks"])
        self.assertEqual(len(audit_events), 1)
        self.assertIs(audit_events[0][0], driver.session_value.tx)


if __name__ == "__main__":
    unittest.main()
