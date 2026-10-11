# Backend runtime

The authoritative entry point is `services.api:app`. Root `config.py` supplies
`Settings`, `get_settings()` and `resolve_path()`: it loads the repository's
private `.env` with process-environment precedence. Relative environment and CLI
paths resolve against the repository root, including when a launcher/export/
migration command is invoked from another working directory.

See [CONFIGURATION.md](../CONFIGURATION.md) for the environment contract,
[BACKEND_HANDOFF.md](BACKEND_HANDOFF.md) for adapter contracts and
[ROLE03_REAUDIT.md](ROLE03_REAUDIT.md) for governance repairs and acceptance
evidence. **OpenAI is the only live model provider.** `OPENAI_API_KEY` is needed
at an actual generation/review boundary; configuration checks, conversion,
imports, API startup and exports do not require it. A key alone does not install
a model adapter.

## Start the API against the existing imported graph

Both `docker-compose.yml` and `compose.data.yaml` use project
**`bmwchallenge-data`**, service **`neo4j`**, and volume key **`neo4j_data`**
(Docker volume **`bmwchallenge-data_neo4j_data`**). Their Neo4j service definitions
match: `neo4j:5.26-community`, 3 GiB container limit, 512 MiB initial / 1536 MiB
maximum heap, 256 MiB page cache and 1 GiB transaction-memory budget. The full
application therefore joins the Role 1 network and imported database.

From the repository root, with the existing data service already running:

```bash
docker compose config --quiet
docker compose up -d --build --no-deps api
```

`--no-deps` starts only the API on the existing network. Ordinary
`docker compose up -d --build api` uses the same Neo4j definition and waits for
its healthcheck. Keep the root credentials consistent with the existing database;
Neo4j's initialization environment does not change an existing user's password.
Use the declared project name to retain the existing network and volume.

The API connects to `bolt://neo4j:7687` inside Compose and uses
`NEO4J_DATABASE` from canonical settings. Its command reads `API_HOST` and
`API_PORT` through `get_settings()`, with one worker; the published localhost
port also follows `API_PORT`. OpenAI/model, adapter factory and prefix settings
are forwarded. Raw input is mounted read-only; processed conversion output,
exports and cache are writable, using the host's `BMW_RAW_DATA_DIR`,
`BMW_DATA_DIR`, `BMW_OUTPUT_DIR` and `BMW_CACHE_DIR` locations. Private inputs
are bind-mounted rather than included in the image.

The optional `ui` profile is wired for `API_BASE_URL=http://api:${API_PORT}` and
`API_PREFIX`. Native `STREAMLIT_SERVER_ADDRESS` / `STREAMLIT_SERVER_PORT` are
present before the Streamlit CLI starts, and its published port follows that
port. There are no hard-coded server CLI overrides or model credentials in the
UI service. `pipeline/08_streamlit_ui.py` is currently absent; actual UI startup
and acceptance require Role 4's implementation.

## Local launcher and raw-only startup

Create a private root `.env` from `.env.example` if needed. Set the graph
connection variables for an accessible server and install the recorded runtime
dependencies in the project environment:

```bash
.venv/bin/python -m pip install -r requirements.txt -c requirements.lock.txt
./run_pipeline.sh --check-config
./run_pipeline.sh --data-only
```

The launcher uses `.venv/bin/python` when available, otherwise `python3`;
`PYTHON_BIN` can select another interpreter. It works from any directory.
`BMW_RAW_DATA_DIR` defaults to `BMW-Data`, `BMW_DATA_DIR` to `var/processed`,
`BMW_OUTPUT_DIR` to `var/exports`, and `BMW_CACHE_DIR` to `var/cache`.
**The processed directory need not exist:** the actual converter creates it.
Destinations must be outside the read-only raw directory.

| Invocation | Behavior |
| --- | --- |
| `./run_pipeline.sh` | Keeps the full-pipeline intent: Role 1, Role 2 generation, then foreground API startup. Missing Role 2 scripts fail explicitly before conversion or database access. Once supplied, generation requires an OpenAI key. |
| `./run_pipeline.sh --data-only` | Runs `00_preprocess.py`, `01_load_neo4j.py`, `02_build_kg.py`, `03_vfc_analysis.py`, then starts the foreground backend. No generation or model key is required. |
| `./run_pipeline.sh --check-config` | Offline check of canonical settings, raw-directory existence, destination layout and stage availability. Reports absent Role 2 scripts without pretending the default pipeline is runnable. |
| `./run_pipeline.sh --data-only --check-config` | The same offline check for the explicitly selected data/backend mode. |

The default is deliberately **not an implicit data-only run**. Role 2's
`04_extract_signals.py`, `05_correlate_studies.py` and
`06_generate_requirements.py` are currently absent; no replacement generation
or prompt scripts are fabricated. Use the explicit data-only mode for today's
available data/backend workflow.

Before executing stages, the launcher verifies graph connectivity and performs
a read-only query in the configured database. The converter retains its complete
corpus requirements: an incomplete/failed manifest returns nonzero and stops
before import. Any failed stage stops subsequent stages and API startup. The
launcher does not start Docker. The offline configuration check does not connect
to the graph, call a model, convert data, create destinations or establish raw
source validity. Native Streamlit settings are exported from canonical settings
before subprocess startup; the launcher itself starts the backend only.

To serve the already imported graph locally without rerunning conversion, run
from the repository root:

```bash
.venv/bin/python -c "from config import get_settings; import uvicorn; s = get_settings(); uvicorn.run('services.api:app', host=s.api_host, port=s.api_port, workers=1)"
```

API lifespan creates/closes its owned driver; an injected driver remains
caller-owned. Startup failures close the owned driver. Admission, background
jobs and interrupted-run recovery are in-process, so use one worker. A durable
queue/lease adapter is needed for multiple workers. Health returns 503 when
persistence is unreadable.

`API_PREFIX` defaults to `/api`. `BACKEND_ADAPTER_FACTORY=package.module:function`
selects a synchronous factory receiving `driver` and `repository`, returning
any of `evidence_provider`, `source_provider`, `engine`, `import_service` with
the documented methods. Missing integrations produce explicit unavailable
responses or failed run records.

## Export and legacy migration

The export CLI reads canonical `API_BASE_URL`, `API_PREFIX` and `BMW_OUTPUT_DIR`.
With no output argument it writes `<BMW_OUTPUT_DIR>/<kind>.<format>`. Explicit
`--output` / `--output-dir` paths also resolve from the repository root. HTTP
failure produces no export file; the payload is the complete backend export,
independent of UI pagination.

```bash
.venv/bin/python pipeline/09_export_report.py
.venv/bin/python pipeline/09_export_report.py --format csv
.venv/bin/python pipeline/09_export_report.py --kind freeze --id FREEZE_ID --format markdown
.venv/bin/python pipeline/09_export_report.py --kind audit --id FREEZE_ID
.venv/bin/python pipeline/09_export_report.py --kind history --id REQUIREMENT_ID
.venv/bin/python -m services.migration --from-json var/legacy/requirements.json --dry-run
```

Migration reads canonical graph credentials and database settings and resolves
JSON paths from the root. JSON dry-run uses memory and makes no database
connection; legacy-graph dry-run reads the explicitly configured application
database. Persisted legacy candidates retain their original payload and remain
unverified/unapproved. Existing callers/tests that override root graph
compatibility constants remain supported.

## Repeat focused acceptance

```bash
.venv/bin/python -m pytest tests/backend/test_runtime.py tests/backend/test_cli_review.py tests/backend/test_audit_export.py tests/backend/test_fresh_export_review.py -q -ra
bash -n run_pipeline.sh
docker compose config --quiet
docker compose --file compose.data.yaml config --quiet
```

Runtime tests cover OpenAI-only selection, optional model keys, raw-only startup,
real partial conversion stopping before import, stage/preflight failures,
canonical bind/port/path behavior, shared Compose data-service identity, and
export/migration defaults. Model/graph/server doubles validate launcher wiring;
they do not establish live model access or real graph transaction acceptance.

The focused run on 2026-10-11 passed **76 tests**, with **2 opt-in real-Neo4j
migration tests skipped** because `BACKEND_TEST_NEO4J_URI` was not configured.
Bash/Python syntax checks and both quiet Compose validations passed, including
application Compose validation with custom API/Streamlit ports. No Docker
service was started or restarted for these checks.

Real graph tests require a **dedicated accessible test server**. Supply
`BACKEND_TEST_NEO4J_URI`, `BACKEND_TEST_NEO4J_DATABASE` and test credentials, then
run the graph-enabled suites. Random namespaces isolate test nodes; cleanup
retains constraints. An absent test URI skips opt-in cases with an explicit
reason; a configured inaccessible server fails. Report skips as unverified
graph cases rather than successful graph acceptance.

For a manual server-restart proof, explicitly set both the test URI and database
and invoke:

```bash
.venv/bin/python tests/backend/graph_restart_probe.py prepare
# Stop/start only the dedicated test server separately.
.venv/bin/python tests/backend/graph_restart_probe.py verify
```

The probe has no production URI/database fallback. Its default manifest is
`/tmp/opencode/bmw-backend-restart.json`; `--manifest` can select another private
path. The manifest binds the synthetic namespace and exact state to the chosen
URI/database, and verification checks the full persisted state before deleting
only that synthetic namespace. This server-restart proof is distinct from a
driver reconnect or the offline probe tests.

## Existing evidence and remaining integration

[pipeline/README_DATA.md](../pipeline/README_DATA.md) records the complete real
corpus conversion/import and Neo4j 5.26 acceptance: 9 source files, 10,128
canonical feedback comments, 13,371 annotations, 2,075 facts and 605 document
chunks, plus idempotence, citation, rollback and restart checks. Preserve that
native imported graph when starting the API.

Earlier isolated `bmw-role03-audit` / `bmw-backend-test` verification used Neo4j
5.20 and synthetic data. It recorded 930 passing tests, including 46 real graph
cases, and an actual dedicated server restart. Those are historical results,
not a statement that the current full Compose stack or live model/UI workflow
has been rerun. See [STRICT_ROLE03_REVIEW.md](STRICT_ROLE03_REVIEW.md) for scope.

The default application wires `GraphSourceProvider`, `Role1EvidenceResolver`
and `Role1ImportAdapter` to the existing imported graph. The graph pipeline
commands honor `NEO4J_DATABASE`. The full API image was built and started on the
existing Role 1 network/volume; the actual local HTTP provider returned nine
sources, the 1,271-comment G60/US/BEV cohort and the 31,000-vehicle G60_G68/RoW
proxy. Real-corpus integration checks also exercised draft scoring, PM approval,
immutable freeze/export, regeneration preservation and audit rollback.

No stored mock JSON is loaded or required. The former synthetic contract file
was removed; test-only examples remain generated in memory. Role 2's actual
generation/review adapter and Role 4's UI are integrated in their subsequent
branches. Current analysis/review endpoints report that engine as unavailable
instead of returning fabricated model output.
