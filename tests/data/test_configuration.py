from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from openpyxl import Workbook
from pydantic import ValidationError

from config import load_settings


REPO_ROOT = Path(__file__).resolve().parents[2]


class ConfigurationTests(unittest.TestCase):
    def test_root_dotenv_paths_and_port_interpolation(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temp_dir:
            root = Path(temp_dir)
            (root / ".env").write_text(
                "BMW_RAW_DATA_DIR=./inputs\n"
                "BMW_DATA_DIR=./converted\n"
                "API_PORT=8123\n"
                "API_BASE_URL=http://localhost:${API_PORT}\n"
                "NEO4J_PASSWORD=synthetic-password\n",
                encoding="utf-8",
            )
            with patch("config.REPO_ROOT", root), patch.dict(os.environ, {}, clear=True):
                settings = load_settings()

        self.assertEqual(settings.raw_data_dir, root / "inputs")
        self.assertEqual(settings.data_dir, root / "converted")
        self.assertEqual(settings.api_port, 8123)
        self.assertEqual(settings.api_base_url, "http://localhost:8123")
        self.assertEqual(settings.neo4j_password.get_secret_value(), "synthetic-password")
        self.assertNotIn("synthetic-password", repr(settings))
        self.assertEqual(settings.openai_api_key.get_secret_value(), "")

    def test_process_overrides_win_over_dotenv_and_interpolation(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temp_dir:
            root = Path(temp_dir)
            (root / ".env").write_text(
                "API_PORT=8123\n"
                "API_BASE_URL=http://localhost:${API_PORT}\n"
                "NEO4J_PASSWORD=dotenv-password\n",
                encoding="utf-8",
            )
            with patch("config.REPO_ROOT", root), patch.dict(os.environ, {
                "API_PORT": "8345", "NEO4J_PASSWORD": "process-password",
                "BMW_DATA_DIR": str(root / "explicit-output"),
            }, clear=True):
                settings = load_settings()

        self.assertEqual(settings.api_port, 8345)
        self.assertEqual(settings.api_base_url, "http://localhost:8345")
        self.assertEqual(settings.neo4j_password.get_secret_value(), "process-password")
        self.assertEqual(settings.data_dir, root / "explicit-output")

    def test_invalid_graph_uri_and_ports_fail_configuration(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temp_dir:
            root = Path(temp_dir)
            invalid_values = (
                {"NEO4J_URI": "http://localhost:7474"},
                {"NEO4J_URI": "bolt://user:password@localhost:7687"},
                {"NEO4J_URI": "bolt://localhost:70000"},
                {"API_PORT": "not-a-port"},
                {"API_PORT": "0"},
                {"STREAMLIT_SERVER_PORT": "70000"},
            )
            for environment in invalid_values:
                with self.subTest(environment=environment):
                    with patch("config.REPO_ROOT", root), patch.dict(os.environ, environment, clear=True):
                        with self.assertRaises(ValidationError):
                            load_settings()

    def test_validation_errors_do_not_echo_uri_credentials(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temp_dir:
            root = Path(temp_dir)
            for variable, scheme in (("NEO4J_URI", "bolt"), ("API_BASE_URL", "http")):
                with self.subTest(variable=variable):
                    with patch("config.REPO_ROOT", root), patch.dict(os.environ, {
                        variable: f"{scheme}://user:synthetic-uri-secret@localhost:7687",
                    }, clear=True):
                        with self.assertRaises(ValidationError) as error:
                            load_settings()

                    message = str(error.exception)
                    self.assertIn("must not contain credentials", message)
                    self.assertNotIn("synthetic-uri-secret", message)

    def test_preprocess_cli_uses_configured_paths_from_another_directory(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temp_dir:
            root = Path(temp_dir)
            raw_dir = root / "inputs"
            raw_dir.mkdir()
            output_dir = root / "converted"
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "Feedback_Explorer"
            sheet.append(("ID", "Customer Feedback", "Model", "Country", "Feedback Type"))
            sheet.append(("CLI-1", "Synthetic CLI feedback.", "G70", "US", "DTU"))
            workbook.save(raw_dir / "G70_feedback.xlsx")

            environment = {
                **os.environ,
                "BMW_RAW_DATA_DIR": str(raw_dir.relative_to(REPO_ROOT)),
                "BMW_DATA_DIR": str(output_dir.relative_to(REPO_ROOT)),
                "OPENAI_API_KEY": "",
            }
            result = subprocess.run([
                sys.executable, str(REPO_ROOT / "pipeline" / "00_preprocess.py"),
                "--required-kind", "feedback",
                "--required-document-kind", "document",
            ], cwd=root, env=environment, capture_output=True, text=True, timeout=30)

            # A missing required PDF is correctly partial, but the configured
            # workbook and destination must still resolve against the repo.
            self.assertEqual(result.returncode, 1, result.stderr)
            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["counts"]["feedback_comments"], 1)
            self.assertEqual(manifest["missing_kinds"], [])
            self.assertEqual(manifest["missing_document_kinds"], ["document"])
            self.assertFalse((root / raw_dir.relative_to(REPO_ROOT)).exists())


if __name__ == "__main__":
    unittest.main()
