# Backend runtime

See [ROLE03_REAUDIT.md](ROLE03_REAUDIT.md) for subsequent code repairs and current verification. The recorded Compose/image results below remain historical deployment evidence.

The authoritative entry point is `services.api:app`. Run from the repository root. [BACKEND_HANDOFF.md](BACKEND_HANDOFF.md) describes adapter contracts and [BACKEND_REVIEW.md](BACKEND_REVIEW.md) records acceptance evidence and remaining blockers.

## Local startup

Use Python 3.12 and the recorded tested dependency versions:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -c requirements.lock.txt
cp .env.example .env
.venv/bin/python -m uvicorn services.api:app --host 127.0.0.1 --port 8000
```

Set `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` and `NEO4J_DATABASE` for an accessible database. Environment-file paths are relative to the working directory. The default database password is a local prototype default. API lifespan creates/closes its owned driver; a caller-injected driver remains caller-owned. Startup failures do not leave the owned driver open.

Use one Uvicorn worker: admission, background jobs and interrupted-run recovery are in-process. A durable queue/lease adapter is needed before deploying multiple workers. The serialized governance ledger is a prototype correctness mechanism; load/scalability has not been assessed. Writes materialize only changed/new records; full locked integrity verification remains. Health returns 503 when persistence is unreadable.

`API_PREFIX` defaults to `/api` and is validated at composition. Configure actual adapters with `BACKEND_ADAPTER_FACTORY=package.module:function`. The synchronous factory receives `driver` and `repository` and returns any of `evidence_provider`, `source_provider`, `engine`, `import_service`, with the documented methods. A key alone does not install an evidence/model adapter. Missing integrations produce truthful unavailable responses or failed run records.

Auto provider selection prefers a nonblank OpenAI key; an explicit `LLM_PROVIDER` requires its corresponding key. Configure `OPENAI_API_KEY`/`OPENAI_MODEL` or `ANTHROPIC_API_KEY`/`CLAUDE_MODEL`. Configuration import does not itself require model credentials. Provider-selection tests use synthetic key values and do not prove live access.

`BMW_RAW_DATA_DIR` defaults to `BMW-Data`, `BMW_DATA_DIR` to `var/processed`, and `BMW_OUTPUT_DIR` to `var/exports`. Keep raw/processed feedback, extracted text, exports, credentials and database files private. Role 1 must supply conversion/import before the runner can complete the full pipeline.

```bash
PYTHON_BIN=.venv/bin/python ./run_pipeline.sh --check-config
PYTHON_BIN=.venv/bin/python ./run_pipeline.sh
```

The configuration check validates the configured provider and processed directory; it does not verify source validity, model credentials or database access. The full runner checks required scripts before running any stage and fails explicitly when Role 1/2 files are absent.

## Containers: API and Neo4j verified; actual UI acceptance pending

```bash
docker compose up --build api
docker compose --profile ui up --build
```

Compose pins `neo4j:5.20-community`, forwards provider/model configuration and adapter factory/prefix settings, mounts raw/processed input read-only and retains private exports under `var/exports`. Ports bind to localhost. The API waits for the database healthcheck. The optional UI service needs Role 4's `pipeline/08_streamlit_ui.py`; its API address is `http://api:8000`. The Dockerfile copies runtime source/dependency files without packaging private inputs.

The final isolated Compose project `bmw-role03-audit` built the Python 3.12 Linux ARM64 API image and started API/Neo4j with the database health dependency. Host HTTP and another container's HTTP request to `http://api:8000/api/health` passed. `pip check` reported no broken requirements. The freshly built API image passed **930 tests**, including all **46 real Neo4j cases**, with no skips or failures; the macOS run passed the same suite. An actual Compose database restart preserved exact approved revisions, ranks and audit checkpoint.

The database image is `neo4j:5.20-community`, digest `sha256:52d3dec8d45585b21edeca8517d752b2512e52aa94f80fc36a5788c88c95f8e4`. The dedicated Colima profile is `bmw-backend-test`; the active Docker context was not changed. Test data is synthetic. This verifies API/database deployment and container networking, while the actual Role 4 UI and Role 1/2 adapters remain unverified. See [STRICT_ROLE03_REVIEW.md](STRICT_ROLE03_REVIEW.md) for current evidence and scope.

## Repeat acceptance

Offline checks:

```bash
.venv/bin/python -m pytest tests/backend -q
```

Real graph tests require a dedicated accessible server. Supply `BACKEND_TEST_NEO4J_URI` and, if necessary, `BACKEND_TEST_NEO4J_USER`, `BACKEND_TEST_NEO4J_PASSWORD`, `BACKEND_TEST_NEO4J_DATABASE` through the environment, then run the same suite. Tests create random backend namespaces and remove only their own nodes; uniqueness constraints remain. An absent graph URI skips opt-in tests; a configured but inaccessible graph fails.

Observed graph verification used existing **Homebrew Neo4j 2026.09 and Java 21**, with an isolated database under `/private/tmp/bmw-backend-neo4j` and Bolt port 17687. Tests inspect transaction rollback after audit/materialization failures, concurrent mutations, state persistence, retained-checkpoint ledger tampering, immutable materialized copies/indexes and API lifespan/HTTP behavior. A separately recorded `tests/backend/graph_restart_probe.py prepare`/`verify` check spans an actual server STOP/START and checks exact saved state. Treat it separately from driver reconnect tests.

Provider checks cover OpenAI-only, Anthropic-only, automatic/explicit selection and missing/whitespace keys. Runner configuration and shell syntax have been exercised using synthetic configuration. `requirements.txt` pins direct dependencies; `requirements.lock.txt` records the complete installed Python 3.12 environment, including transitive versions. The constraints include macOS and Linux ARM64 dependencies; a clean Linux API image installation and its full suite passed. The Dockerfile applies the recorded versions as constraints.

Regenerate public contracts using `PYTHONPATH=.:tests/backend .venv/bin/python tests/backend/generate_contracts.py`. Examples come from actual synthetic API calls and retain verifiable audit timestamps/hashes. Contract acceptance validates the models, scoring, checkpoint and exported OpenAPI against the current application.

No successful live model call, original BMW raw-conversion run or actual UI workflow has been demonstrated. Missing production source/model adapters and actual UI acceptance remain integration blockers. An empty source/export response or passing health endpoint establishes startup/query behavior, not an imported source-to-approved-plan workflow.
