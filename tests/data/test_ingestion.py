from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from openpyxl import Workbook

from config import get_settings
from services.ingestion import (
    CONVERSION_VERSION,
    EXPECTED_FEEDBACK_COUNTS,
    _year,
    convert_feedback_workbook,
    convert_pdf,
    convert_sources,
    convert_workbook,
    discover_sources,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_DATA_DIR = get_settings().raw_data_dir


def _save_workbook(path: Path, sheet_name: str, rows: list[tuple[object, ...]]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_name
    for row in rows:
        sheet.append(row)
    workbook.save(path)


class IngestionTests(unittest.TestCase):
    def test_feedback_reuses_comment_but_preserves_multiple_annotations(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temp_dir:
            path = Path(temp_dir) / "G70_feedback_hackathon.xlsx"
            _save_workbook(path, "Feedback_Explorer", [
                ("ID", "Source", "Country", "Derivate (e-code)", "Engine Type",
                 "Feedback Type", "Customer Feedback", "Vfc level2 Name", "Vfc level3 Name"),
                ("FB-1", "A", "US", "G70", "BEVE", "Likes",
                 "I like the quiet cabin.", "Interior", "Cabin comfort"),
                ("FB-1", "A", "US", "G70", "BEVE", "DTU",
                 "I like the quiet cabin.", "Interior", "Cabin comfort"),
            ])

            bundle = convert_feedback_workbook(path)

        self.assertEqual(bundle.manifest["source_rows"], 2)
        self.assertEqual(bundle.manifest["canonical_comments"], 1)
        self.assertEqual(len(bundle.feedback_annotations), 2)
        self.assertEqual(bundle.feedback_comments[0]["model_original"], "G70")
        self.assertEqual(bundle.feedback_comments[0]["powertrain_original"], "BEVE")
        self.assertEqual(bundle.feedback_comments[0]["powertrain"], "BEV")
        self.assertEqual(
            [annotation["feedback_type_original"] for annotation in bundle.feedback_annotations],
            ["Likes", "DTU"],
        )
        self.assertTrue(all(annotation["vfc2_original"] == "Interior"
                            for annotation in bundle.feedback_annotations))
        self.assertTrue(all(annotation["vfc3_original"] == "Cabin comfort"
                            for annotation in bundle.feedback_annotations))
        self.assertEqual(bundle.source_records[-1]["locator"]["row"], 3)

    def test_conflicting_duplicate_feedback_ids_are_not_merged(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temp_dir:
            path = Path(temp_dir) / "G70_feedback_hackathon.xlsx"
            _save_workbook(path, "Feedback_Explorer", [
                ("ID", "Source", "Country", "Derivate (e-code)", "Feedback Type", "Customer Feedback"),
                ("FB-1", "A", "US", "G70", "Likes", "First text."),
                ("FB-1", "A", "US", "G70", "DTU", "Changed text."),
            ])

            bundle = convert_feedback_workbook(path)

        self.assertEqual(bundle.manifest["canonical_comments"], 2)
        self.assertEqual(bundle.manifest["duplicate_id_conflicts"], 1)
        self.assertIn("duplicate_feedback_id_conflict", {issue["code"] for issue in bundle.issues})

    def test_explicit_bmw_derivative_column_wins_over_filename(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temp_dir:
            path = Path(temp_dir) / "G60_feedback_hackathon.xlsx"
            _save_workbook(path, "Feedback_Explorer", [
                ("ID", "Derivate (e-code)", "Country", "Customer Feedback"),
                ("FB-G68", "G68", "CN", "Synthetic explicitly scoped feedback."),
            ])

            bundle = convert_feedback_workbook(path)

        self.assertEqual(bundle.feedback_comments[0]["model"], "G68")
        self.assertEqual(bundle.feedback_comments[0]["model_original"], "G68")
        self.assertEqual(bundle.feedback_comments[0]["region"], "CN")

    def test_sales_matrix_recognizes_underscored_year_headers_and_zero_values(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temp_dir:
            path = Path(temp_dir) / "sales_volumes.xlsx"
            _save_workbook(path, "G70", [
                ("market", "market_code", "volume_2024", "volume_2025", "volume_2030"),
                ("Europe", "EU", 11, 13, 17),
                ("USA", "US", 0, 0, 0),
            ])

            bundle = convert_workbook(path, "sales")

        sales = [fact for fact in bundle.facts if fact["kind"] == "sales"]
        self.assertEqual(_year("volume_2030"), 2030)
        self.assertEqual(len(sales), 6)
        us_2030 = next(fact for fact in sales if fact["region"] == "US" and fact["year"] == 2030)
        self.assertEqual(us_2030["volume"], 0)
        self.assertEqual(us_2030["vehicle_line"], "G70")

    def test_survey_scopes_each_column_model_and_merged_market_heading(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temp_dir:
            path = Path(temp_dir) / "customer_study.xlsx"
            _save_workbook(path, "CN_EU_2025", [
                (None, "Dataset country", None, None, None),
                (None, "China", None, "EU", None),
                ("Exterior styling", "BMW 5 Series G68", "BMW 7 Series G70",
                 "BMW 1 Series F70", "BMW 5 Series G60"),
                ("Mean", 8.1, 8.2, 8.3, 8.4),
            ])

            bundle = convert_workbook(path, "survey")

        facts = [fact for fact in bundle.facts if fact["kind"] == "survey_cell"]
        self.assertEqual(
            [(fact["derivative"], fact["region"]) for fact in facts],
            [("G68", "CN"), ("G70", "CN"), ("F70", "EU"), ("G60", "EU")],
        )
        self.assertTrue(all(fact["question"] == "Exterior styling" for fact in facts))

    def test_source_discovery_skips_excel_lock_files(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temp_dir:
            root = Path(temp_dir)
            (root / "~$G70_feedback_hackathon.xlsx").write_bytes(b"not an xlsx")
            (root / "G70_feedback_hackathon.xlsx").write_bytes(b"source")

            found = discover_sources(root)

        self.assertEqual([path.name for path in found], ["G70_feedback_hackathon.xlsx"])

    def test_aes_encrypted_pdf_preserves_exact_page_text(self) -> None:
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temp_dir:
            path = Path(temp_dir) / "synthetic_report.pdf"
            writer = PdfWriter()
            page = writer.add_blank_page(width=612, height=792)
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({
                    NameObject("/F1"): DictionaryObject({
                        NameObject("/Type"): NameObject("/Font"),
                        NameObject("/Subtype"): NameObject("/Type1"),
                        NameObject("/BaseFont"): NameObject("/Helvetica"),
                    }),
                }),
            })
            stream = DecodedStreamObject()
            stream.set_data(b"BT /F1 12 Tf 20 40 Td (Synthetic encrypted report passage.) Tj ET")
            page[NameObject("/Contents")] = writer._add_object(stream)
            writer.encrypt("", owner_password="synthetic-owner", algorithm="AES-256")
            writer.write(path)

            bundle = convert_pdf(path)

        self.assertEqual(bundle.issues, [])
        self.assertEqual(bundle.source_records[0]["locator"]["page"], 1)
        self.assertEqual(bundle.document_chunks[0]["original_text"], "Synthetic encrypted report passage.")
        chunk = bundle.document_chunks[0]
        page_text = bundle.source_records[0]["raw_payload"]["text"]
        self.assertEqual(page_text[chunk["start_offset"]:chunk["end_offset"]], chunk["original_text"])

    def test_real_bmw_data_matches_feedback_counts_and_page_citations(self) -> None:
        if not REAL_DATA_DIR.is_dir():
            self.skipTest("private BMW-Data directory is not present")

        bundle = convert_sources(REAL_DATA_DIR, check_known_feedback_counts=True)
        self.assertEqual(bundle.manifest["conversion_version"], CONVERSION_VERSION)

        report_present = any(source.get("document_kind") == "corporate_report"
                             for source in bundle.source_files)
        self.assertEqual(bundle.manifest["status"], "succeeded")
        self.assertEqual(bundle.manifest["missing_document_kinds"],
                         [])
        self.assertEqual(bundle.manifest["counts"]["source_files"], len(discover_sources(REAL_DATA_DIR)))
        self.assertEqual(bundle.manifest["counts"]["feedback_comments"], 10_128)
        self.assertEqual(bundle.manifest["counts"]["feedback_annotations"], 13_371)
        self.assertTrue(any(annotation.get("vfc2_original") for annotation in bundle.feedback_annotations))
        self.assertTrue(any(annotation.get("vfc3_original") for annotation in bundle.feedback_annotations))
        self.assertEqual(sum(bool(annotation.get("vfc2_original"))
                             for annotation in bundle.feedback_annotations), 13_371)
        self.assertEqual(sum(bool(annotation.get("vfc3_original"))
                             for annotation in bundle.feedback_annotations), 11_289)
        if report_present:
            report_chunks = [chunk for chunk in bundle.document_chunks
                             if chunk.get("document_kind") == "corporate_report"]
            self.assertTrue(report_chunks, "Corporate report was discovered but yielded no searchable text chunks")
            self.assertTrue(all(chunk.get("page") for chunk in report_chunks))
        else:
            self.assertEqual(bundle.manifest["counts"]["document_chunks"], 78)
        self.assertEqual(
            {model: value["observed"] for model, value in bundle.manifest["known_feedback_counts"].items()},
            EXPECTED_FEEDBACK_COUNTS,
        )
        self.assertEqual(
            {issue["code"] for issue in bundle.issues if issue.get("severity") == "error"},
            set(),
        )
        self.assertFalse(any(source["filename"].startswith("~$") for source in bundle.source_files))

        records = {record["id"]: record for record in bundle.source_records}
        for chunk in bundle.document_chunks:
            page_text = records[chunk["source_record_id"]]["raw_payload"]["text"]
            self.assertEqual(page_text[chunk["start_offset"]:chunk["end_offset"]], chunk["original_text"])

        sales = [fact for fact in bundle.facts if fact["kind"] == "sales"]
        self.assertEqual(len(sales), 36)
        formula_fact = next(fact for fact in sales if fact.get("value_provenance") == "cached_formula_result")
        self.assertEqual(formula_fact["source_value"], "=-D3-C10")
        self.assertEqual(formula_fact["volume"], 0)

        distributions = [fact for fact in bundle.facts if fact["kind"] == "survey_distribution"]
        g70_us = [fact for fact in distributions
                  if fact.get("derivative") == "G70" and fact.get("region") == "US"]
        self.assertEqual(len(g70_us), 103)
        self.assertTrue(all(len(fact["distribution"]) == 7 for fact in g70_us))
        self.assertTrue(all(fact["respondent_n"] is None for fact in g70_us))
        self.assertTrue(all(len(fact["cell_locators"]) == 7 for fact in g70_us))


if __name__ == "__main__":
    unittest.main()
