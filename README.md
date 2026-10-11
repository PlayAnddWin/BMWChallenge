<div align="center">

# 🚗✨ BMW ConceptCraft ✨🚀

### 🧠 AI-powered. Cloud-adjacent. Vibe-engineered. Held together by hopes and prayers💅

![Vibes](https://img.shields.io/badge/vibes-peer_reviewed_by_vibes-ff69b4?style=for-the-badge)
![Slop](https://img.shields.io/badge/slop-MAXIMUM-blueviolet?style=for-the-badge)
![ROI](https://img.shields.io/badge/ROI-see_pitch_deck-00d4ff?style=for-the-badge)

**Because apparently reading customer feedback required a digital transformation.** 🚗📈

</div>

> 💡 What if Excel had an API bill? Finally, someone brave enough to ask. 🤯💸

## 🔥 The vision (you are not ready for this)

The mission: turn BMW feedback, surveys, catalogs, and strategy into **evidence-backed, prioritized SMART requirements**. The pitch: say “agentic” until someone releases a budget. 🚀

- 📊 **Data ingestion** — liberate spreadsheets from folders. Imprison them in a pipeline.
- 🕸️ **Knowledge graphs** — connect the dots. Call the dots an ecosystem. 🫶
- 🤖 **AI-powered insights** — “the seat is uncomfortable,” now with token usage.
- 🧾 **Traceable evidence** — because “the chatbot seemed confident” is a bold engineering methodology.
- 🧑‍💼 **Human-in-the-loop** — someone still clicks approve. We call that a platform. ✨

If it's not `agentic` then I DON'T want it!💅

## 🛠️ Our proprietary secret sauce™ (it's dependencies)

**Python 🐍 + Neo4j 🕸️ + OpenAI 🤖 + caffeine ☕**. Add `ENTERPRISE-GRADE` and `SCALABLE` to taste. No measurements necessary just trust me bro. 📈

## ⚡ Frictionless onboarding™ (friction included)

Requires Python **3.12+** and Docker with Compose. Local development uses Python
3.14; the API image uses Python 3.12. All runtime and test dependencies, including
their supporting version pins, are in **`requirements.txt`**.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows, activate with `.venv\Scripts\Activate.ps1`.

Create a private root `.env` from [`.env.example`](.env.example) if needed. Set
`OPENAI_API_KEY` for AI generation/review and the Neo4j credentials for your local
database. Data import, API startup and exports work without a model key.

### Import the source data

Place the supplied XLSX/PDF files in `BMW-Data/`, including
`BMW-Group-Report-2025-en.pdf`. The converter requires feedback, customer studies,
sales forecasts, option catalogs and the corporate report.

```bash
docker compose up -d --wait neo4j
python pipeline/00_preprocess.py --check-known-feedback-counts
python pipeline/01_load_neo4j.py
python pipeline/03_vfc_analysis.py
python pipeline/02_build_kg.py
```

Conversion writes `var/processed/manifest.json` and source-preserving JSONL
tables. A complete manifest reports `succeeded`; failed or incomplete conversion
returns a nonzero exit code. Source files are read-only inputs. Reimport uses
stable source-versioned identities, preserves original row/cell/page locators,
and rejects conflicting evidence.

Reviewed study-topic mappings can be imported using
`python pipeline/01_load_neo4j.py --study-mappings <file>`; start from
[`pipeline/study_topic_mappings.example.json`](pipeline/study_topic_mappings.example.json).
Unreviewed mappings remain explicitly unmapped.

### Start the API

```bash
docker compose up -d --build api
```

- API docs: **http://localhost:8000/docs**
- API health: **http://localhost:8000/api/health**
- Neo4j Browser: **http://localhost:7474/browser/**

For an existing imported graph, the API can be rebuilt independently with
`docker compose up -d --build --no-deps api`. Both Compose configurations use the
`bmwchallenge-data` project and persistent `bmwchallenge-data_neo4j_data` volume.
Neo4j has a 3 GiB container limit and a 1 GiB transaction budget for full imports.
Changing `.env` credentials also requires updating the existing Neo4j user's
password; initialization only sets a password for a new volume.

To serve the imported graph directly from your Python environment:

```bash
python -c "from config import get_settings; import uvicorn; s = get_settings(); uvicorn.run('services.api:app', host=s.api_host, port=s.api_port, workers=1)"
```

`./run_pipeline.sh --data-only` converts/imports data and starts the local API.
`--check-config` performs an offline configuration check. Generation is exposed
through the API; the launcher's legacy full mode expects standalone generation
scripts that are not part of this integrated implementation.

### Start the PM workbench

With the API running, start the frontend locally:

```bash
python -m ui
```

The launcher loads the shared `.env` and applies the configured Streamlit address
and port before startup. Open **http://localhost:8501** with the default settings.
The client uses `API_BASE_URL` and `API_PREFIX` for all reads, actions and downloads.

To start only the UI container against the existing API service:

```bash
docker compose --profile ui up -d --build --no-deps ui
```

The workbench includes the ranked portfolio, requirement/evidence/priority/history
panels, cited review assistant, scoped analysis and immutable review packages.
All writes go through the API with explicit PM actions and revision checks.

An explicit read-only snapshot can be opened after exporting the full list:

```bash
python pipeline/09_export_report.py
python -m ui -- --snapshot
```

Synthetic previews are available through `python -m ui -- --fixtures` or
`--mock`; each is labelled and never selected automatically after an API failure.
Frontend checks: `python -m pytest tests/frontend -q`.

## Configuration

`config.py` is the single settings reader. It loads root `.env` with process
environment precedence and resolves relative paths against the repository root.

| Setting | Default / purpose |
| --- | --- |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | Private key; model defaults to `gpt-5.4` |
| `BMW_RAW_DATA_DIR` | `./BMW-Data` |
| `BMW_DATA_DIR` | `./var/processed` |
| `BMW_OUTPUT_DIR` | `./var/exports` |
| `BMW_CACHE_DIR` | `./var/cache` |
| `NEO4J_URI` | `bolt://localhost:7687` locally; `bolt://neo4j:7687` inside Compose |
| `NEO4J_USER`, `NEO4J_PASSWORD`, `NEO4J_DATABASE` | Graph credentials; database defaults to `neo4j` |
| `API_HOST`, `API_PORT`, `API_PREFIX` | `0.0.0.0`, `8000`, `/api` |
| `API_BASE_URL` | `http://localhost:${API_PORT}` for clients/export CLI |
| `BACKEND_ADAPTER_FACTORY` | Optional trusted custom adapter factory; built-in adapters are the default |
| `STREAMLIT_SERVER_ADDRESS`, `STREAMLIT_SERVER_PORT` | `0.0.0.0`, `8501`; optional frontend profile |

Compose forwards model credentials only to the API, mounts raw data read-only,
and mounts processed data, caches and exports writable. The optional `ui` profile
runs the frontend using the API service hostname.

## Generate and review requirements

Start with a narrow scope and a small draft limit:

```bash
curl -X POST http://localhost:8000/api/analysis-runs \
  -H 'Content-Type: application/json' \
  -d '{"derivatives":["G60"],"regions":["US"],"powertrains":["BEV"],"topics":["Air conditioning, setting"],"query":"temperature","max_drafts":1}'
```

Poll `GET /api/analysis-runs/{id}` for progress, results and failures. The default
uses cached signals/drafts when their source/model/prompt identities match;
`refresh=true` requests fresh interpretation. The integrated engine caps requests
at 18 drafts. Actual scope counts, forecasts and citation validation come from
the imported graph, not model-generated totals.

| Operation | Endpoint |
| --- | --- |
| List / inspect requirements | `GET /api/requirements`, `GET /api/requirements/{id}` |
| Inspect original evidence | `GET /api/sources`, `/source-records/{id}`, `/facts/{id}`, `/document-chunks/{id}` |
| Retrieve scoped evidence | `POST /api/evidence/{feedback-search,survey-facts,sales-facts,document-search,cohort-counts}` |
| Ask the assistant | `POST /api/requirements/{id}/challenges` |
| Inspect proposed changes | `GET /api/requirements/{id}/proposals` |
| Apply a reviewed proposal | `POST /api/requirements/{id}/challenge-applications` |
| Edit / make a PM decision | `PATCH /api/requirements/{id}`, `POST /api/requirements/{id}/decisions` |
| Inspect history / freeze approved revisions | `GET /api/requirements/{id}/history`, `POST /api/freezes` |

OpenAPI in `/docs` describes the request bodies. PM mutations require a named
actor, rationale and expected revision. Applying an assistant proposal leaves
the requirement in `PM_REVIEW`; approval is a separate action confirming targets,
source assignments and assumptions. Regeneration preserves existing PM content
and decisions, and produces a pending diff. Freezes retain immutable approved
revisions, source snapshots, scoring/ranking configuration and an audit checkpoint.

Whole-line forecasts have no powertrain split; proxy reach remains provisional.
Distinct comments are not verified customer counts or population prevalence.
Study sample totals are not respondent N, and missing regional data is a coverage
gap. Documentary market/derivative applicability stays unknown until reviewed.

### Export

```bash
python pipeline/09_export_report.py
python pipeline/09_export_report.py --format csv
python pipeline/09_export_report.py --kind freeze --id <freeze-id> --format markdown
```

Exports use `API_BASE_URL`, `API_PREFIX` and `BMW_OUTPUT_DIR` and include the full
server result independently of list pagination. Legacy migration is available
through `python -m services.migration --help`.

## Repository layout

- `pipeline/`: raw conversion, graph import/summaries and export CLI.
- `services/`: evidence retrieval, AI agents, API, scoring and transactional governance.
- `ui/`, `pipeline/08_streamlit_ui.py`: the thin Streamlit workbench and HTTP adapter.
- `models.py`, `contracts/intelligence.py`: backend and AI DTOs.
- `contracts/openapi.json`: published HTTP schema; regenerate with
  `PYTHONPATH=.:tests/backend python tests/backend/generate_contracts.py`.
- `tests/{data,backend,intelligence,frontend}/`: parser, backend, AI and workbench checks.
- `var/`: ignored processed data, caches and exports. `BMW-Data/` and `.env` are also ignored.

Unit checks can be selected explicitly with
`python -m pytest tests/data tests/backend tests/intelligence tests/frontend`. Live graph/model
checks are opt-in; graph checks use disposable test instances and model checks
may make external calls. `tests/backend/graph_restart_probe.py` is the retained
dedicated-graph restart verification utility.

The original scope, judging rubric and deliverables are in
[CHALLENGE_SPECIFICATION.md](CHALLENGE_SPECIFICATION.md).

---

<div align="center">

**Built with 💖, fueled by ☕, and optimized for ✨ shareholder value ✨.**<br>
🚗💨 _The badges are decorative. Just like half the adjectives._ 🌈

</div>
