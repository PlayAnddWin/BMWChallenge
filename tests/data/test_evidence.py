from __future__ import annotations

import unittest

from services.evidence import (
    EvidenceProviderError,
    EvidenceScope,
    Neo4jEvidenceProvider,
)


class _Record:
    def __init__(self, value):
        self.value = value

    def data(self):
        return self.value


class _Transaction:
    def __init__(self, records, error=None):
        self.records = records
        self.error = error

    def run(self, query, **params):
        if self.error:
            raise self.error
        return iter(_Record(record) for record in self.records)


class _Session:
    def __init__(self, transaction):
        self.transaction = transaction

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute_read(self, work):
        return work(self.transaction)


class _Driver:
    def __init__(self, transaction):
        self.transaction = transaction

    def session(self):
        return _Session(self.transaction)


class EvidenceProviderTests(unittest.TestCase):
    def test_scope_normalization_is_explicit_and_scoped(self) -> None:
        params = EvidenceScope(model="G70", market="United States", powertrain="BEVE").query_params()
        self.assertEqual(params["model"], "G70")
        self.assertEqual(params["region"], "US")
        self.assertEqual(params["powertrain"], "BEV")

    def test_source_record_decodes_locator_and_payload(self) -> None:
        provider = Neo4jEvidenceProvider(_Driver(_Transaction([{
            "record": {
                "id": "SR-1",
                "locator_json": '{"filename":"G70.xlsx","sheet":"Feedback_Explorer","row":9}',
                "raw_payload_json": '{"text":"original"}',
            },
            "source_file": {"id": "SRC-1", "sha256": "abc"},
        }])))

        record = provider.get_source_record("SR-1")

        self.assertEqual(record["locator"]["row"], 9)
        self.assertEqual(record["raw_payload"]["text"], "original")
        self.assertEqual(record["source_file"]["sha256"], "abc")

    def test_query_failures_are_not_returned_as_empty_evidence(self) -> None:
        provider = Neo4jEvidenceProvider(_Driver(_Transaction([], RuntimeError("database unavailable"))))

        with self.assertRaisesRegex(EvidenceProviderError, "database unavailable"):
            provider.get_source_record("SR-1")

    def test_sales_fact_citation_keeps_exact_column_and_zero_volume(self) -> None:
        provider = Neo4jEvidenceProvider(_Driver(_Transaction([{
            "fact": {
                "id": "FACT-SALES", "kind": "sales", "vehicle_line": "G70",
                "region": "US", "year": 2030, "volume": 0,
                "locator_json": '{"sheet":"G70","row":3,"column":5,"column_header":"volume_2030"}',
            },
            "source_record": {
                "id": "SR-SALES",
                "locator_json": '{"filename":"sales.xlsx","sheet":"G70","row":3}',
            },
            "source_file": {"id": "SRC-SALES", "sha256": "synthetic-hash"},
            "mapping": None,
        }])))

        fact = provider.get_sales_facts(EvidenceScope(model="G70", region="US"))["facts"][0]

        self.assertEqual(fact["volume"], 0)
        self.assertEqual(fact["source_locator"], {
            "filename": "sales.xlsx", "sheet": "G70", "row": 3,
            "column": 5, "column_header": "volume_2030",
        })

    def test_reviewed_survey_fact_keeps_cell_and_mapping_relation(self) -> None:
        provider = Neo4jEvidenceProvider(_Driver(_Transaction([{
            "fact": {
                "id": "FACT-SURVEY", "kind": "survey_cell", "derivative": "G68",
                "region": "CN", "value": 8.1, "mapping_strength": "unmapped",
                "locator_json": '{"sheet":"CN_EU_2025","row":4,"column":2}',
            },
            "source_record": {
                "id": "SR-SURVEY",
                "locator_json": '{"filename":"study.xlsx","sheet":"CN_EU_2025","row":4}',
            },
            "source_file": {"id": "SRC-SURVEY", "sha256": "synthetic-hash"},
            "mapping": {"relation": "direct", "review_state": "reviewed"},
        }])))

        result = provider.get_survey_facts("synthetic topic", EvidenceScope(model="G68", region="CN"))
        fact = result["facts"][0]

        self.assertEqual(result["mapping_status"], "reviewed")
        self.assertEqual(fact["source_locator"]["column"], 2)
        self.assertEqual(fact["source_locator"]["filename"], "study.xlsx")
        self.assertEqual(fact["mapping_strength"], "direct")
        self.assertIsNone(fact["respondent_n"])


if __name__ == "__main__":
    unittest.main()
