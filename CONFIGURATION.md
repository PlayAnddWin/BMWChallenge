# Shared Environment Configuration

## One Configuration Contract

[.env.example](.env.example) defines the canonical runtime variables for all branches. Each developer copies it to a private root `.env` and supplies the OpenAI key. All application modules use root `config.py`; they do not create their own env files, aliases, dotenv loading, or copied endpoint/model/password/path defaults.

**OpenAI is the only LLM provider.** No `ANTHROPIC_API_KEY`, `CLAUDE_MODEL`, `LLM_API_KEY`, or provider-selection environment variable is required. A missing OpenAI key means live generation/review is unavailable; fixture tests, UI against a configured API, and non-model operations can still work. Do not silently select another provider.

This file and the template are the integration contract. Root `config.py` now implements the shared reader, and Role 1's four pipeline commands consume it. [`compose.data.yaml`](compose.data.yaml) starts the verified local Neo4j service using the root credentials. Role 3 coordinates this reader and service with the application runner, API/UI and full Compose setup as the remaining branches are integrated. See [`pipeline/README_DATA.md`](pipeline/README_DATA.md) for the working data-layer setup and verification commands.

## Variables and Consumers

| Variable | Template default | Consumer / purpose |
| :--- | :--- | :--- |
| `OPENAI_API_KEY` | Empty; fill privately | Role 2's backend model adapter; required for live calls |
| `OPENAI_MODEL` | `gpt-4o-mini` | Model adapter; selected model recorded in run provenance |
| `BMW_RAW_DATA_DIR` | `./BMW-Data` | Role 1 raw XLSX/PDF conversion |
| `BMW_DATA_DIR` | `./var/processed` | Converter output and existing processed-input loader |
| `BMW_OUTPUT_DIR` | `./var/exports` | Role 3 sample/audit/freeze exports; Role 5 private delivery artifacts |
| `BMW_CACHE_DIR` | `./var/cache` | Role 2 private model/retrieval cache |
| `NEO4J_URI` | `bolt://localhost:7687` | Backend/data graph connection |
| `NEO4J_USER` | `neo4j` | Graph authentication; shared with Compose initialization |
| `NEO4J_PASSWORD` | `bmwpassword` | Existing local development password; shared with Compose |
| `API_HOST` | `0.0.0.0` | Backend listen address, not a UI destination |
| `API_PORT` | `8000` | Backend listen/published port |
| `API_BASE_URL` | `http://localhost:${API_PORT}` | Frontend API origin; dotenv resolves the port reference |
| `STREAMLIT_SERVER_ADDRESS` | `0.0.0.0` | Streamlit listen address |
| `STREAMLIT_SERVER_PORT` | `8501` | Streamlit listen/published port |

Weights, evidence thresholds, forecast/reference policy, allowed statuses and tool budgets belong to versioned application policy, not duplicate per-developer environment knobs. Actual source values are read from imported data.

## Local Setup

1. Create `.env` from `.env.example` at the repository root if a private `.env` does not already exist.
2. Set `OPENAI_API_KEY` for the processes performing live AI calls. Frontend-only work can leave it empty and connect to a team backend through `API_BASE_URL`.
3. Put the supplied inputs in `BMW_RAW_DATA_DIR`; conversion produces files under `BMW_DATA_DIR`.
4. Run `docker compose --file compose.data.yaml up -d --wait`, then the pipeline commands documented in `pipeline/README_DATA.md`. The application launcher/backend/UI are integrated through Role 3; jobs/exporters use the same root-resolved paths.

Root `config.py` loads the root `.env` with process-environment precedence (`override=False`). Relative paths resolve against the repository root, never a caller's current directory. Ports and graph/API URLs are parsed/validated centrally, secret fields have masked representations, and validation errors omit rejected input values. Require OpenAI credentials at the live-model boundary rather than breaking unrelated data/UI/fixture imports. `tests/data/test_configuration.py` verifies environment precedence, port interpolation, path resolution, credential-safe errors and CLI execution from another directory.

Do not load a separate `.env` from `pipeline/`, `services/`, or `ui/`. The loader may run once in each process; application modules import the resulting settings. Never include the key/password in diagnostic output.

## Container Wiring — Same Names, Different Network

Owner 3 adapts the existing Compose file using the root `.env`:

- API receives `OPENAI_API_KEY`, `OPENAI_MODEL`, Neo4j credentials, and data/output/cache settings. It connects to `NEO4J_URI=bolt://neo4j:7687` inside Compose.
- Neo4j initialization uses the same `NEO4J_USER`/`NEO4J_PASSWORD`; no second hard-coded credential declaration.
- UI receives `API_BASE_URL=http://api:${API_PORT}` plus native Streamlit server settings. It does not receive OpenAI credentials.
- Backend/Streamlit commands and published ports use the canonical values instead of conflicting hard-coded CLI overrides.
- Raw/processed/export/cache paths are mounted and overridden to the corresponding container paths using these same variable names. Raw files may be read-only; conversion/export/cache directories must be writable by their owning processes.
- Streamlit reads native server environment before its CLI initializes. The local launcher must export `STREAMLIT_SERVER_*` before starting it; loading dotenv only inside the already-running UI script is too late for custom server ports.

Compose service-host overrides describe container topology. They are not new configuration aliases or a second team env file. An API URL may intentionally point to a remote/shared backend, independently of the local backend bind address.

## Role Ownership

- **Role 1:** consumes raw/processed settings through config; supplies converter/manifest behavior.
- **Role 2:** consumes OpenAI/model/cache settings through config; removes legacy provider fallback/imports with Role 3.
- **Role 3:** owns the template, root config reader, startup validation, runner, Compose and environment integration tests. Changes to variable names go through this owner.
- **Role 4:** consumes API settings through the shared reader/client; renders UI and uses native Streamlit startup settings. No model keys or direct graph connection.
- **Role 5:** uses delivered export/presentation locations and startup instructions; no separate application settings.

## Integration Acceptance Checks

1. Template has one assignment per key and no real API key; `.env` and private outputs are ignored by Git.
2. Root config resolves the template paths from any working directory; process overrides win.
3. Changing `API_PORT` updates the local interpolated API origin unless an explicit remote `API_BASE_URL` is supplied.
4. OpenAI-only configuration produces a live model call after valid credentials are supplied. Empty credentials produce a clear unavailable result, never Anthropic fallback.
5. Native Streamlit address/port apply before server startup; UI connects locally and through the Compose service name.
6. Compose graph credentials match backend settings, and input/output/cache mounts have the intended read/write access.
7. All pipeline/API/UI modules import canonical settings; no `load_dotenv` or duplicated `os.getenv`/defaults appears outside the config/launcher boundary.
