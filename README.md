# BMW ConceptCraft — Role 2 scaffold

This branch provides the AI signal extraction, requirement drafting, and contextual review
services from [Role 2](roles/02_AI_AND_REVIEW_ASSISTANT.md). It starts from `main` and reuses
the reviewed engine's useful prompt/JTBD and batching concepts. Scoring, persistence,
HTTP jobs, decisions and UI integrate through the other roles' adapters.

## Run the verified account-free slice

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements-role2.lock
.venv/bin/python -m pytest tests/intelligence
.venv/bin/python -m tests.intelligence.demo
```

Tested with Python **3.14.7**. The lock contains only the Role 2 runtime/test dependencies.
The demo uses the explicitly synthetic shared fixture, fixed model responses, and fake
backend previews; it makes no network calls and is not a BMW-generated requirement.

## Test real OpenAI with the synthetic fixture

Set `OPENAI_API_KEY` in the private root `.env`, then run:

```bash
.venv/bin/python -m pytest tests/intelligence/test_live_openai_fixture.py --live-openai-fixture -v -s
```

This makes real model calls for signal extraction, requirement drafting, and read-only tool-assisted
review. Evidence is synthetic and persistence/score-preview boundaries are in-memory test doubles.
It needs no BMW importer, Neo4j, API server, or external backend adapter. Real provider response IDs,
token usage and outputs are recorded under `BMW_CACHE_DIR/live-fixture-checks/`.

Default model: **`gpt-5.4` with low reasoning**, using OpenAI's Responses API for generation and
reasoning/tool turns. Verified: **4 live tests passed**, including exact extraction, a cited HVAC
draft, a pending scoped review, and a weak F70 storage draft with unknown strategy/effort. The
account-free suite has **129 passing tests**; 15 live checks are skipped unless explicitly enabled.
See [the output-quality assessment](docs/ROLE2_OUTPUT_QUALITY.md) for the before/after findings.

The four checks are core-flow smoke tests. Nine additional curated real-model evaluations cover
batch/source binding, mixed feedback, injected instructions, read-only/approval boundaries, study
scope/scale reasoning and revision-aware follow-ups. Run them independently:

```bash
.venv/bin/python -m pytest tests/intelligence/test_live_evaluation.py --live-openai-evaluation --live-no-retries -v -s
```

See [the live coverage matrix](docs/ROLE2_LIVE_COVERAGE.md): 13 synthetic live scenarios are available,
while two separate real-BMW integration hooks still require source/backend adapters. These curated
checks improve coverage but do not establish accuracy on all BMW data or a statistical reliability rate.

## Application agents and service boundaries

| Agent / service | Entry point | Responsibility |
| --- | --- | --- |
| Signal extraction | `services.signals.extract_signals` | Keyed issue discovery, exact original spans, distinct support counts |
| Evidence retrieval | `services.retrieval.build_evidence_pack` | Bounded support/opposition, reviewed native study context, scoped facts/passages |
| Requirement drafting | `services.drafting.draft_requirement` / `draft_requirements` | Cited structured proposals, explicit target provenance and validation |
| Analysis workflow | `services.analysis.run_analysis` | Progress events, partial failures, versioned caches, backend writer handoff |
| Review assistant | `services.review_assistant.answer_review_turn` | Bounded read-only tools, cited answer, pending/stale patch and backend preview |

`contracts/intelligence.py` supplies strict DTOs. `services/interfaces.py` defines Role 1/3
ports. `contracts/fixture_case.json` is the shared synthetic source/target fixture.

Canonical agent descriptions, instructions, output contracts and budgets live in `services/agents.py`.
The seven tool use-cases, argument descriptions and interpretation limits live in
`services/tool_registry.py`. Extraction/drafting use Pydantic-derived **strict JSON Schemas**, augmented
with requested IDs/scope/provenance constraints. Review uses JSON mode plus local typed patch/citation
validation because JSON-pointer values are dynamic. Python additionally validates evidence and proposal
quality; schema conformance alone cannot prove source entailment or engineering feasibility.

OpenAI is the only live model adapter. Root `config.py` reads the canonical environment
contract from [.env.example](.env.example) / [CONFIGURATION.md](CONFIGURATION.md). Credentials
are required only at the live-model boundary. `FileDraftCache.from_settings(settings)` uses
`BMW_CACHE_DIR`; authoritative cache identity and current PM state remain in the backend.

See [the Role 2 handoff](docs/ROLE2_HANDOFF.md) for exact contracts, live-check setup,
backend obligations, verification coverage, and current integration limits.
