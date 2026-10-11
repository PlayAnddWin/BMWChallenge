"""Canonical runtime checks use synthetic inputs, never a live model or graph."""
import importlib
import json
import os
import subprocess
import sys
import textwrap
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from config import get_settings, select_llm_provider


ROOT = Path(__file__).resolve().parents[2]
DATA_STAGES = ['00_preprocess', '01_load_neo4j', '02_build_kg', '03_vfc_analysis']
GENERATION_STAGES = ['04_extract_signals', '05_correlate_studies', '06_generate_requirements']


class RuntimeTests(unittest.TestCase):
    def test_openai_only(self):
        self.assertEqual(select_llm_provider({'OPENAI_API_KEY': 'key'}, required=True), 'openai')

    def test_explicit_openai_compatibility(self):
        self.assertEqual(select_llm_provider({'OPENAI_API_KEY': ' key ', 'LLM_PROVIDER': ' OpenAI '}), 'openai')
        self.assertIsNone(select_llm_provider({'LLM_PROVIDER': 'openai'}))

    def test_missing_and_whitespace_keys(self):
        self.assertIsNone(select_llm_provider({}))
        for env in ({}, {'OPENAI_API_KEY': '  '}, {'ANTHROPIC_API_KEY': 'legacy-key'}):
            self.assertIsNone(select_llm_provider(env))
            with self.assertRaisesRegex(ValueError, 'Set OPENAI_API_KEY for live generation'):
                select_llm_provider(env, required=True)

    def test_unsupported_legacy_provider_is_rejected(self):
        for provider in ('anthropic', 'unknown'):
            with self.assertRaisesRegex(ValueError, 'OpenAI is the only supported provider'):
                select_llm_provider({'LLM_PROVIDER': provider, 'OPENAI_API_KEY': 'key'})

    def test_compose_contract(self):
        import yaml
        full = yaml.safe_load((ROOT / 'docker-compose.yml').read_text())
        data = yaml.safe_load((ROOT / 'compose.data.yaml').read_text())
        self.assertEqual(full['name'], 'bmwchallenge-data')
        self.assertEqual(full['name'], data['name'])
        self.assertEqual(full['services']['neo4j'], data['services']['neo4j'])
        self.assertEqual(full['volumes'], data['volumes'])
        neo4j = full['services']['neo4j']
        self.assertEqual(neo4j['image'], 'neo4j:5.26-community')
        self.assertEqual(neo4j['mem_limit'], '3g')
        self.assertEqual(neo4j['environment']['NEO4J_server_memory_heap_max__size'], '1536M')
        self.assertEqual(neo4j['environment']['NEO4J_dbms_memory_transaction_total_max'], '1G')
        self.assertEqual(neo4j['volumes'], ['neo4j_data:/data'])
        services = full['services']
        self.assertIn('${API_PORT:-8000}:${API_PORT:-8000}', services['api']['ports'][0])
        self.assertEqual(services['api']['environment']['NEO4J_URI'], 'bolt://neo4j:7687')
        for key in ('OPENAI_API_KEY', 'OPENAI_MODEL', 'API_HOST', 'API_PORT', 'BMW_CACHE_DIR'):
            self.assertIn(key, services['api']['environment'])
        for key in ('ANTHROPIC_API_KEY', 'CLAUDE_MODEL', 'LLM_PROVIDER'):
            self.assertNotIn(key, services['api']['environment'])
            self.assertNotIn(key, services['ui']['environment'])
        mounts = services['api']['volumes']
        for variable, target in (('BMW_DATA_DIR', '/data/processed'), ('BMW_OUTPUT_DIR', '/app/var/exports'),
                                 ('BMW_CACHE_DIR', '/app/var/cache')):
            mount = next(value for value in mounts if value.startswith('${' + variable + ':'))
            self.assertTrue(mount.endswith(':' + target) or mount.endswith(':' + target + ':rw'))
        self.assertTrue(next(value for value in mounts if value.startswith('${BMW_RAW_DATA_DIR:')).endswith(':/data/raw:ro'))
        ui = services['ui']
        self.assertEqual(ui['environment']['API_BASE_URL'], 'http://api:${API_PORT:-8000}')
        self.assertIn('STREAMLIT_SERVER_ADDRESS', ui['environment'])
        self.assertIn('STREAMLIT_SERVER_PORT', ui['environment'])
        self.assertNotIn('--server.', ui['command'])
        self.assertIn('${STREAMLIT_SERVER_PORT:-8501}:${STREAMLIT_SERVER_PORT:-8501}', ui['ports'][0])


def runner_environment(tmp_path):
    raw = tmp_path / 'raw corpus'
    raw.mkdir()
    return dict(os.environ, OPENAI_API_KEY='', LLM_PROVIDER='',
                BMW_RAW_DATA_DIR=str(raw), BMW_DATA_DIR=str(tmp_path / 'processed'),
                BMW_OUTPUT_DIR=str(tmp_path / 'exports'), BMW_CACHE_DIR=str(tmp_path / 'cache'),
                NEO4J_URI='bolt://127.0.0.1:17687', NEO4J_USER='synthetic-user',
                NEO4J_PASSWORD='synthetic-runtime-password', NEO4J_DATABASE='synthetic-runtime-db',
                API_HOST='127.0.0.7', API_PORT='8127', API_BASE_URL='http://localhost:8127',
                API_PREFIX='/runtime', BACKEND_ADAPTER_FACTORY='',
                STREAMLIT_SERVER_ADDRESS='127.0.0.8', STREAMLIT_SERVER_PORT='8620',
                PYTHON_BIN=sys.executable)


def run_launcher(tmp_path, env, *args):
    return subprocess.run(['bash', str(ROOT / 'run_pipeline.sh'), *args], cwd=tmp_path,
                          env=env, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize('key', ['', 'synthetic-runtime-test-key'])
@pytest.mark.parametrize('mode', [[], ['--data-only']])
def test_actual_runner_checks_raw_only_configuration_from_another_cwd(tmp_path, key, mode):
    env = runner_environment(tmp_path)
    env['OPENAI_API_KEY'] = key
    env['BMW_RAW_DATA_DIR'] = os.path.relpath(env['BMW_RAW_DATA_DIR'], ROOT)
    result = run_launcher(tmp_path, env, *mode, '--check-config')
    assert result.returncode == 0, result.stderr
    assert 'Configuration valid (offline' in result.stdout
    assert str(tmp_path / 'raw corpus') in result.stdout
    assert 'Backend listen: 127.0.0.7:8127' in result.stdout
    assert 'synthetic-runtime-test-key' not in result.stdout + result.stderr
    assert env['NEO4J_PASSWORD'] not in result.stdout + result.stderr
    for directory in ('processed', 'exports', 'cache'):
        assert not (tmp_path / directory).exists()


@pytest.mark.parametrize('invalid,error', [
    ('raw', 'Raw data directory does not exist'),
    ('nested', 'Processed directory must be outside the raw data directory'),
    ('file', 'Processed destination is not a directory'),
    ('port', 'api_port'),
    ('credentials', 'Set NEO4J_USER and NEO4J_PASSWORD'),
    ('factory', 'BACKEND_ADAPTER_FACTORY must be module:function'),
])
def test_actual_runner_rejects_invalid_prerequisites_without_exposing_credentials(tmp_path, invalid, error):
    env = runner_environment(tmp_path)
    if invalid == 'raw':
        env['BMW_RAW_DATA_DIR'] = str(tmp_path / 'absent')
    elif invalid == 'nested':
        env['BMW_DATA_DIR'] = str(Path(env['BMW_RAW_DATA_DIR']) / 'processed')
    elif invalid == 'file':
        Path(env['BMW_DATA_DIR']).write_text('not a directory')
    elif invalid == 'port':
        env['API_PORT'] = '70000'
    elif invalid == 'credentials':
        env['NEO4J_PASSWORD'] = ''
    else:
        env['BACKEND_ADAPTER_FACTORY'] = 'missing-separator'
    result = run_launcher(tmp_path, env, '--check-config')
    assert result.returncode != 0
    assert error in result.stderr
    assert 'synthetic-runtime-password' not in result.stdout + result.stderr


@pytest.fixture
def runner_spy(tmp_path):
    """Observe the real launcher while keeping graph, model and server calls offline."""
    env = runner_environment(tmp_path)
    hooks = tmp_path / 'hooks'
    hooks.mkdir()
    log = tmp_path / 'runtime.jsonl'
    env['PYTHONPATH'] = os.pathsep.join([str(hooks), str(ROOT), env.get('PYTHONPATH', '')])
    env['BMW_RUNTIME_TEST_LOG'] = str(log)
    (hooks / 'sitecustomize.py').write_text(textwrap.dedent('''
        import json
        import os
        from pathlib import Path
        import subprocess
        from neo4j import GraphDatabase
        import uvicorn
        from config import REPO_ROOT

        def record(*event):
            with Path(os.environ['BMW_RUNTIME_TEST_LOG']).open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(event) + '\\n')

        class Result:
            def consume(self):
                return None

        class Session:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def run(self, query):
                record('probe', query)
                return Result()

        class Driver:
            def verify_connectivity(self):
                record('connectivity')
                if os.environ.get('BMW_RUNTIME_TEST_FAIL') == 'preflight':
                    raise RuntimeError(os.environ['NEO4J_PASSWORD'])
            def session(self, database):
                record('session', database)
                return Session()
            def close(self):
                record('closed')

        def driver(uri, auth):
            record('driver', uri)
            return Driver()
        GraphDatabase.driver = driver

        original_run = subprocess.run
        def run(command, *args, **kwargs):
            if len(command) > 1 and Path(command[1]).parent == REPO_ROOT / 'pipeline':
                stage = Path(command[1]).stem
                record('stage', stage)
                if os.environ.get('BMW_RUNTIME_TEST_FAIL') == stage:
                    raise subprocess.CalledProcessError(9, command)
                if stage == '00_preprocess' and os.environ.get('BMW_RUNTIME_TEST_RUN_CONVERTER'):
                    return original_run(command, *args, **kwargs)
                return subprocess.CompletedProcess(command, 0)
            return original_run(command, *args, **kwargs)
        subprocess.run = run

        def serve(app, **kwargs):
            record('api', app, kwargs, os.environ['STREAMLIT_SERVER_ADDRESS'],
                   os.environ['STREAMLIT_SERVER_PORT'])
        uvicorn.run = serve

        if os.environ.get('BMW_RUNTIME_TEST_ROLE2_AVAILABLE'):
            original_is_file = Path.is_file
            def is_file(path):
                if path.parent == REPO_ROOT / 'pipeline' and path.stem in (
                        '04_extract_signals', '05_correlate_studies', '06_generate_requirements'):
                    return True
                return original_is_file(path)
            Path.is_file = is_file
    '''), encoding='utf-8')
    return env, log


def runtime_events(log):
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def test_default_runner_keeps_full_pipeline_intent_and_names_missing_role2(tmp_path, runner_spy):
    env, log = runner_spy
    result = run_launcher(tmp_path, env)
    assert result.returncode != 0
    assert 'Full pipeline unavailable: missing Role 2 stages' in result.stderr
    assert '--data-only' in result.stderr
    assert all(stage in result.stderr for stage in GENERATION_STAGES)
    assert runtime_events(log) == []
    assert not Path(env['BMW_DATA_DIR']).exists()


def test_check_config_is_offline_even_with_available_generation_stages(tmp_path, runner_spy):
    env, log = runner_spy
    env['BMW_RUNTIME_TEST_ROLE2_AVAILABLE'] = '1'
    result = run_launcher(tmp_path, env, '--check-config')
    assert result.returncode == 0, result.stderr
    assert runtime_events(log) == []


def test_data_only_runs_all_available_role1_stages_then_canonical_backend(tmp_path, runner_spy):
    env, log = runner_spy
    result = run_launcher(tmp_path, env, '--data-only')
    assert result.returncode == 0, result.stderr
    events = runtime_events(log)
    assert events[:5] == [['driver', env['NEO4J_URI']], ['connectivity'],
                         ['session', env['NEO4J_DATABASE']], ['probe', 'RETURN 1 AS ready'], ['closed']]
    assert [event[1] for event in events if event[0] == 'stage'] == DATA_STAGES
    assert events[-1] == ['api', 'services.api:app', {'host': env['API_HOST'], 'port': 8127, 'workers': 1},
                          env['STREAMLIT_SERVER_ADDRESS'], env['STREAMLIT_SERVER_PORT']]
    assert 'synthetic-runtime-password' not in result.stdout + result.stderr


@pytest.mark.parametrize('failure', ['preflight', '00_preprocess', '01_load_neo4j', '03_vfc_analysis'])
def test_runner_failure_stops_later_stages_and_backend(tmp_path, runner_spy, failure):
    env, log = runner_spy
    env['BMW_RUNTIME_TEST_FAIL'] = failure
    result = run_launcher(tmp_path, env, '--data-only')
    assert result.returncode == (1 if failure == 'preflight' else 9)
    events = runtime_events(log)
    assert ['closed'] in events
    assert not any(event[0] == 'api' for event in events)
    ran = [event[1] for event in events if event[0] == 'stage']
    assert ran == ([] if failure == 'preflight' else DATA_STAGES[:DATA_STAGES.index(failure) + 1])
    assert 'synthetic-runtime-password' not in result.stdout + result.stderr


@pytest.mark.parametrize('key', ['', 'synthetic-runtime-test-key'])
def test_generation_requires_openai_key_only_when_scripts_will_actually_run(tmp_path, runner_spy, key):
    env, log = runner_spy
    env.update(BMW_RUNTIME_TEST_ROLE2_AVAILABLE='1', OPENAI_API_KEY=key)
    result = run_launcher(tmp_path, env)
    if key:
        assert result.returncode == 0, result.stderr
        events = runtime_events(log)
        assert [event[1] for event in events if event[0] == 'stage'] == DATA_STAGES + GENERATION_STAGES
        assert events[-1][0] == 'api'
    else:
        assert result.returncode != 0
        assert 'Set OPENAI_API_KEY for live generation' in result.stderr
        assert runtime_events(log) == []
    assert 'synthetic-runtime-test-key' not in result.stdout + result.stderr


def test_raw_only_runner_really_converts_and_refuses_partial_corpus_before_import(tmp_path, runner_spy):
    from openpyxl import Workbook
    env, log = runner_spy
    env['BMW_RUNTIME_TEST_RUN_CONVERTER'] = '1'
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Feedback_Explorer'
    sheet.append(('ID', 'Customer Feedback', 'Model', 'Country', 'Feedback Type'))
    sheet.append(('CLI-1', 'Synthetic launcher feedback.', 'G70', 'US', 'DTU'))
    source = Path(env['BMW_RAW_DATA_DIR']) / 'G70_feedback.xlsx'
    workbook.save(source)
    original = source.read_bytes()
    assert not Path(env['BMW_DATA_DIR']).exists()
    result = run_launcher(tmp_path, env, '--data-only')
    assert result.returncode == 1, result.stdout + result.stderr
    manifest = json.loads((Path(env['BMW_DATA_DIR']) / 'manifest.json').read_text())
    assert manifest['status'] == 'partial'
    assert manifest['counts']['feedback_comments'] == 1
    assert {'survey', 'sales', 'document'} <= set(manifest['missing_kinds'])
    assert [event[1] for event in runtime_events(log) if event[0] == 'stage'] == ['00_preprocess']
    assert not any(event[0] == 'api' for event in runtime_events(log))
    assert source.read_bytes() == original


def test_compose_api_command_uses_canonical_settings(monkeypatch):
    import config
    import uvicorn
    import yaml
    settings = get_settings().model_copy(update={'api_host': '127.0.0.9', 'api_port': 8239})
    monkeypatch.setattr(config, 'get_settings', lambda: settings)
    observed = []
    monkeypatch.setattr(uvicorn, 'run', lambda app, **kwargs: observed.append((app, kwargs)))
    command = yaml.safe_load((ROOT / 'docker-compose.yml').read_text())['services']['api']['command']
    assert command[:2] == ['python', '-c']
    exec(command[2], {})
    assert observed == [('services.api:app', {'host': '127.0.0.9', 'port': 8239, 'workers': 1})]


def test_export_defaults_to_canonical_endpoint_prefix_and_output_dir(tmp_path, monkeypatch):
    module = importlib.import_module('pipeline.09_export_report')
    settings = get_settings().model_copy(update={'api_base_url': 'http://canonical.test:8239',
                                                'api_prefix': '/canonical', 'output_dir': tmp_path / 'exports'})
    monkeypatch.setattr(module, 'get_settings', lambda: settings)
    monkeypatch.setenv('API_BASE_URL', 'http://wrong.test')
    monkeypatch.setenv('BMW_OUTPUT_DIR', str(tmp_path / 'wrong'))
    observed = []
    payload = 'Complete canonical export\n'
    def get(url, params, timeout):
        observed.append((url, params, timeout))
        return httpx.Response(200, text=payload, request=httpx.Request('GET', url))
    monkeypatch.setattr(module.httpx, 'get', get)
    module.main([])
    assert observed == [('http://canonical.test:8239/canonical/requirements/export', {'format': 'json'}, 60)]
    assert (settings.output_dir / 'requirements.json').read_text() == payload
    assert not (tmp_path / 'wrong').exists()


@pytest.mark.parametrize('flag,value', [('--output', 'reports/exact.json'), ('--output-dir', 'reports')])
def test_export_explicit_relative_paths_resolve_against_repo_root(tmp_path, monkeypatch, flag, value):
    import config
    module = importlib.import_module('pipeline.09_export_report')
    root = tmp_path / 'repo'
    root.mkdir()
    monkeypatch.setattr(config, 'REPO_ROOT', root)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(module.httpx, 'get', lambda url, **kwargs:
                        httpx.Response(200, text='Exact export\n', request=httpx.Request('GET', url)))
    module.main([flag, value])
    target = root / value if flag == '--output' else root / value / 'requirements.json'
    assert target.read_text() == 'Exact export\n'
    assert not (tmp_path / 'reports').exists()


def test_export_direct_cli_from_another_cwd_uses_canonical_defaults(tmp_path):
    env = runner_environment(tmp_path)
    requested = []
    payload = '{"complete": "synthetic export"}\n'
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requested.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(payload.encode('utf-8'))
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env['API_BASE_URL'] = f'http://127.0.0.1:{server.server_port}'
    try:
        result = subprocess.run([sys.executable, str(ROOT / 'pipeline/09_export_report.py')],
                                cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert result.returncode == 0, result.stderr
    assert requested == ['/runtime/requirements/export?format=json']
    assert (Path(env['BMW_OUTPUT_DIR']) / 'requirements.json').read_text() == payload


@pytest.mark.parametrize('source', ['json', 'graph'])
@pytest.mark.parametrize('compatibility_override', [False, True])
def test_migration_uses_canonical_connection_and_preserves_constant_overrides(tmp_path, monkeypatch, capsys,
                                                                           source, compatibility_override):
    import config
    from neo4j import GraphDatabase
    from services import migration
    from services.repository import MemoryRepository
    settings = get_settings().model_copy(update={'neo4j_uri': 'bolt://canonical.test:17687',
                                                'neo4j_user': 'canonical-user',
                                                'neo4j_password': SecretStr('canonical-password'),
                                                'neo4j_database': 'canonical-database'})
    monkeypatch.setattr(config, 'get_settings', lambda: settings)
    if compatibility_override:
        for name, value in (('NEO4J_URI', 'bolt://override.test:17687'), ('NEO4J_USER', 'override-user'),
                            ('NEO4J_PASSWORD', 'override-password'), ('NEO4J_DATABASE', 'override-database')):
            monkeypatch.setattr(config, name, value)
    expected = ('bolt://override.test:17687', ('override-user', 'override-password'), 'override-database') if compatibility_override else (
        settings.neo4j_uri, (settings.neo4j_user, settings.neo4j_password.get_secret_value()), settings.neo4j_database)
    original = {'req_id': 'synthetic-legacy', 'title': 'Historical proposal', 'status': 'APPROVED',
                'impact': 5, 'market_reach_norm': 4, 'effort': 3}
    connections, sessions, repositories, closed = [], [], [], []
    class Transaction:
        def run(self, query):
            assert 'r.backend_key IS NULL' in query
            return [{'original': original}]
    class Session:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def execute_read(self, callback):
            return callback(Transaction())
    class Driver:
        def session(self, database):
            sessions.append(database)
            return Session()
        def close(self):
            closed.append(True)
    def driver(uri, auth):
        connections.append((uri, auth))
        return Driver()
    class Repository(MemoryRepository):
        def __init__(self, driver, database, namespace):
            super().__init__()
            repositories.append((database, namespace, self))
        def initialize(self):
            pass
    monkeypatch.setattr(GraphDatabase, 'driver', driver)
    monkeypatch.setattr(migration, 'Neo4jRepository', Repository)
    if source == 'json':
        path = tmp_path / 'legacy.json'
        path.write_text(json.dumps([original]))
        args = ['--from-json', str(path)]
    else:
        args = ['--from-legacy-graph', '--dry-run']
    migration.main(args)
    assert connections == [(expected[0], expected[1])]
    assert closed == [True]
    assert json.loads(capsys.readouterr().out)['count'] == 1
    if source == 'graph':
        assert sessions == [expected[2]]
        assert repositories == []
    else:
        assert repositories[0][:2] == (expected[2], 'backend-v1')
        imported = list(repositories[0][2].read()['requirements'].values())
        assert len(imported) == 1
        assert imported[0]['legacy_original'] == original
        assert imported[0]['validation_state'] == 'unverified'
        assert imported[0]['approved_revision'] is None


def test_migration_direct_dry_run_resolves_relative_input_from_another_cwd(tmp_path):
    env = runner_environment(tmp_path)
    source = tmp_path / 'legacy.json'
    source.write_text(json.dumps([{'req_id': 'legacy-cli', 'title': 'Synthetic historical proposal',
                                   'impact': 5, 'market_reach_norm': 4, 'effort': 3}]))
    result = subprocess.run([sys.executable, str(ROOT / 'services/migration.py'), '--from-json',
                             os.path.relpath(source, ROOT), '--dry-run'], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'count': 1, 'dry_run': True, 'validation_state': 'unverified', 'namespace': 'backend-v1'}
    assert env['NEO4J_PASSWORD'] not in result.stdout + result.stderr


@pytest.mark.parametrize('missing', ['BACKEND_TEST_NEO4J_URI', 'BACKEND_TEST_NEO4J_DATABASE'])
def test_restart_probe_requires_explicit_test_server_and_database(tmp_path, monkeypatch, capsys, missing):
    probe = importlib.import_module('graph_restart_probe')
    monkeypatch.setenv('BACKEND_TEST_NEO4J_URI', 'bolt://synthetic.test:17687')
    monkeypatch.setenv('BACKEND_TEST_NEO4J_DATABASE', 'synthetic-database')
    monkeypatch.delenv(missing)
    monkeypatch.setattr(probe.GraphDatabase, 'driver', lambda *a, **k: pytest.fail('Missing test target reached a graph'))
    manifest = tmp_path / 'restart.json'
    with pytest.raises(SystemExit) as error:
        probe.main(['prepare', '--manifest', str(manifest)])
    assert error.value.code == 2
    assert 'BACKEND_TEST_NEO4J_URI and BACKEND_TEST_NEO4J_DATABASE' in capsys.readouterr().err
    assert not manifest.exists()


@pytest.mark.parametrize('field,value', [('uri', 'bolt://other.test:17687'), ('database', 'other-db'), ('namespace', 'backend-v1')])
def test_restart_probe_rejects_changed_target_or_nonsynthetic_cleanup(tmp_path, monkeypatch, field, value):
    probe = importlib.import_module('graph_restart_probe')
    monkeypatch.setenv('BACKEND_TEST_NEO4J_URI', 'bolt://synthetic.test:17687')
    monkeypatch.setenv('BACKEND_TEST_NEO4J_DATABASE', 'synthetic-database')
    saved = {'uri': 'bolt://synthetic.test:17687', 'database': 'synthetic-database',
             'namespace': 'backend-restart-' + 'a' * 32, 'state': {}}
    saved[field] = value
    manifest = tmp_path / 'restart.json'
    manifest.write_text(json.dumps(saved))
    monkeypatch.setattr(probe.GraphDatabase, 'driver', lambda *a, **k: pytest.fail('Invalid manifest reached a graph'))
    with pytest.raises(SystemExit) as error:
        probe.main(['verify', '--manifest', str(manifest)])
    assert error.value.code == 2


def test_restart_probe_uses_selected_database_for_both_phases_and_exact_cleanup(tmp_path, monkeypatch, capsys):
    probe = importlib.import_module('graph_restart_probe')
    from services.repository import MemoryRepository
    monkeypatch.setenv('BACKEND_TEST_NEO4J_URI', 'bolt://synthetic.test:17687')
    monkeypatch.setenv('BACKEND_TEST_NEO4J_DATABASE', 'synthetic-database')
    monkeypatch.setenv('BACKEND_TEST_NEO4J_PASSWORD', 'synthetic-probe-password')
    stores, databases, cleanup, closed = {}, [], [], []
    class Result:
        def consume(self):
            pass
    class Session:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def run(self, query, **params):
            cleanup.append((query, params))
            return Result()
    class Driver:
        def verify_connectivity(self):
            pass
        def close(self):
            closed.append(True)
    class Repository:
        def __init__(self, driver, database, namespace):
            databases.append(database)
            self.namespace = namespace
            self.store = stores.setdefault(namespace, MemoryRepository())
        def initialize(self):
            pass
        def __getattr__(self, name):
            return getattr(self.store, name)
        def session(self):
            return Session()
    monkeypatch.setattr(probe.GraphDatabase, 'driver', lambda *a, **k: Driver())
    monkeypatch.setattr(probe, 'Neo4jRepository', Repository)
    manifest = tmp_path / 'private' / 'restart.json'
    probe.main(['prepare', '--manifest', str(manifest)])
    saved = json.loads(manifest.read_text())
    assert saved['database'] == 'synthetic-database'
    assert saved['state']['requirements']
    assert all(record['approved_revision'] is not None for record in saved['state']['requirements'].values())
    probe.main(['verify', '--manifest', str(manifest)])
    assert databases == ['synthetic-database', 'synthetic-database']
    assert closed == [True, True]
    assert len(cleanup) == 1
    assert cleanup[0][1] == {'key': saved['namespace'], 'prefix': saved['namespace'] + ':'}
    assert 'synthetic-probe-password' not in manifest.read_text() + capsys.readouterr().out


def test_legacy_api_entrypoint_delegates_to_authoritative_app():
    from services.api import app
    assert importlib.import_module('pipeline.07_pm_review_api').app is app


def test_legacy_reach_helper_remains_explicitly_legacy_arithmetic():
    from config import market_reach_norm
    assert market_reach_norm('F70', 'EU') == 7.39
    assert market_reach_norm('unknown', 'unknown') == 0
