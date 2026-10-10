"""Provider checks need neither a live model nor a database."""
import unittest
import os
import subprocess
import sys
import importlib
from pathlib import Path
import pytest
from config import select_llm_provider


class RuntimeTests(unittest.TestCase):
    def test_openai_only(self):
        self.assertEqual(select_llm_provider({'OPENAI_API_KEY': 'key'}, required=True), 'openai')

    def test_anthropic_only(self):
        self.assertEqual(select_llm_provider({'ANTHROPIC_API_KEY': 'key'}, required=True), 'anthropic')

    def test_auto_selection_and_explicit_override(self):
        env = {'OPENAI_API_KEY': 'a', 'ANTHROPIC_API_KEY': 'b'}
        self.assertEqual(select_llm_provider(env), 'openai')
        env['LLM_PROVIDER'] = 'anthropic'
        self.assertEqual(select_llm_provider(env), 'anthropic')

    def test_missing_and_whitespace_keys(self):
        self.assertIsNone(select_llm_provider({}))
        with self.assertRaisesRegex(ValueError, 'OPENAI_API_KEY or ANTHROPIC_API_KEY'):
            select_llm_provider({'OPENAI_API_KEY': '  '}, required=True)

    def test_explicit_provider_without_key(self):
        with self.assertRaisesRegex(ValueError, 'ANTHROPIC_API_KEY is required'):
            select_llm_provider({'LLM_PROVIDER': 'anthropic', 'OPENAI_API_KEY': 'key'})
        with self.assertRaises(ValueError):
            select_llm_provider({'LLM_PROVIDER': 'unknown'})

    def test_compose_contract(self):
        import yaml
        root = Path(__file__).resolve().parents[2]
        services = yaml.safe_load((root / 'docker-compose.yml').read_text())['services']
        self.assertEqual(services['ui']['environment']['API_BASE_URL'], 'http://api:8000')
        self.assertIn('services.api:app', services['api']['command'])
        for key in ('OPENAI_API_KEY', 'OPENAI_MODEL', 'ANTHROPIC_API_KEY', 'CLAUDE_MODEL'):
            self.assertIn(key, services['api']['environment'])


@pytest.mark.parametrize('provider,key', [('openai', 'OPENAI_API_KEY'), ('anthropic', 'ANTHROPIC_API_KEY')])
def test_actual_runner_accepts_each_provider_from_another_working_directory(tmp_path, provider, key):
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ, OPENAI_API_KEY='', ANTHROPIC_API_KEY='', LLM_PROVIDER='',
               BMW_DATA_DIR=str(tmp_path), PYTHON_BIN=sys.executable)
    env[key] = 'synthetic-runtime-test-key'
    result = subprocess.run(['bash', str(root / 'run_pipeline.sh'), '--check-config'],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert f'Provider: {provider}' in result.stdout
    assert 'synthetic-runtime-test-key' not in result.stdout + result.stderr


@pytest.mark.parametrize('has_key,has_data,error', [
    (False, True, 'Set OPENAI_API_KEY or ANTHROPIC_API_KEY'),
    (True, False, 'Processed data directory does not exist'),
])
def test_actual_runner_rejects_missing_prerequisites(tmp_path, has_key, has_data, error):
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ, OPENAI_API_KEY='synthetic' if has_key else '', ANTHROPIC_API_KEY='',
               LLM_PROVIDER='', BMW_DATA_DIR=str(tmp_path if has_data else tmp_path / 'absent'),
               PYTHON_BIN=sys.executable)
    result = subprocess.run(['bash', str(root / 'run_pipeline.sh'), '--check-config'],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode != 0
    assert error in result.stderr


def test_legacy_api_entrypoint_delegates_to_authoritative_app():
    from services.api import app
    assert importlib.import_module('pipeline.07_pm_review_api').app is app


def test_legacy_reach_helper_remains_explicitly_legacy_arithmetic():
    from config import market_reach_norm
    assert market_reach_norm('F70', 'EU') == 7.39
    assert market_reach_norm('unknown', 'unknown') == 0
