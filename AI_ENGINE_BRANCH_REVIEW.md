# Review of `ai-requirements-engine`

- **Reviewed snapshot:** `origin/ai-requirements-engine` at `3bd0251`
**Purpose:** reuse implemented work, identify correctness gaps, and assign specialized responsibilities before further implementation.

## 1. Verdict

The branch is a substantial prototype baseline, not just an AI experiment. Keep its Neo4j/FastAPI/Streamlit architecture for this delivery window and repair/extend it. Its 250 exported requirements are **unverified proposals**, not 250 evidence-validated SMART requirements.

The main gaps are source lineage, validation of generated content, preservation of PM decisions on reruns, approval guards, transactional audit, complete exports, and runtime configuration. Creating a second persistence stack or giving every builder frontend/backend/AI tasks would add avoidable integration work.

This review changes documentation and assignments. It does not merge or fix the branch's application code.

## 2. What Already Exists

| Code | Implemented capability | Reuse / extension owner |
| :--- | :--- | :--- |
| `pipeline/00_preprocess.py`, `01_load_neo4j.py` | Enrichment of preprocessed JSON; loading feedback, studies, sales, equipment | Role 1: raw-file conversion, source references, annotations, correct scope |
| `pipeline/02_build_kg.py`, `03_vfc_analysis.py` | Cross-source pain differences, cross-model VFC links, VFC statistics | Role 1: statistics/membership correctness; Role 2 consumes reviewed evidence |
| `pipeline/04_extract_signals.py` | Batched OpenAI/Anthropic signal extraction | Role 2: IDs/spans, output validation, stable mapping and signal versions |
| `pipeline/05_correlate_studies.py` | Study matching and heuristic tier/conflict updates | Role 2: mapped context; Role 3: confidence policy approval/calculation |
| `pipeline/06_generate_requirements.py` | Batched drafts, JTBD/metric fields, deterministic score helpers, graph persistence | Role 2: drafting; Role 3: scoring and authoritative requirement persistence |
| `pipeline/07_pm_review_api.py` | List/detail, status transition, history, dashboard/conflict/gap endpoints | Role 3: hardened review/edit API, revisions, transactionality, extra endpoints |
| `pipeline/08_streamlit_ui.py` | Dashboard, requirements/detail, decisions, conflicts/gaps, list export | Role 4: all frontend behavior and polish |
| `pipeline/09_export_report.py` | Full/list/summary export | Role 3: completeness, audit reconstruction, approved freeze |
| `models.py`, `config.py`, Docker/runner | DTOs and execution skeleton | Role 3 coordinates shared contracts/runtime; domain owners supply definitions |

Role 5 owns presentation, demo, artifact verification, and submission.

## 3. Verification Performed

- Inspected the branch in a detached temporary worktree; the main working tree and remote branch were not modified.
- Ran 16 offline diagnostic checks with mocked Neo4j sessions/model calls. All 16 passed **as reproductions/characterizations of current behavior**, including undesirable behavior. This is not a claim that correctness acceptance tests pass.
- Confirmed Python syntax and imported the core pipeline/API modules with compatible dependencies in a temporary environment.
- Recomputed the exported priority using the branch's existing policy; all 250 records match that formula.
- Compared exported quotes to original feedback text in the supplied Excel files, scoped by vehicle model.
- No Docker containers were running locally. Live Cypher execution, the complete container stack, processed-input conversion, and live model generation were not validated.
- No test suite was found on the reviewed branch. Dependencies have lower bounds rather than a tested lock; the temporary environment is not a reproducible project environment.

### Artifact Results

| Check | Result |
| :--- | ---: |
| Exported requirements / unique requirement IDs | 250 / 250 |
| Distribution F70 / G60 / G70 | 49 / 102 / 99 |
| Empty requirement histories | 250 |
| All five SMART flags set true | 218 |
| Records with no declared data gaps | 225 |
| Exported quote occurrences | 750 |
| Exact original-text substring matches within the stated model | 538 |
| Quotes without an exact original-text match | 212 |
| Source-signal exports containing original feedback IDs | 0 |
| WEAK / RED requirements in the export | 0 / 0 |

The 212 non-matches need source validation. Preprocessing/translation could explain some differences; this check does not prove fabrication. All quotes still need IDs/locators and assignment validation, including the 538 matches.

## 4. Correctness Gaps and Assigned Fixes

| Priority | Finding and code reference | Required fix | Owner |
| :--- | :--- | :--- | :--- |
| P0 | `07_pm_review_api.py:143–190` accepts AI_SYSTEM approval with no rationale, arbitrary state transitions, and a body ID different from the route ID | PM-only decision authority, state machine, pinned revision, required rationale and target/assumption review; remove redundant ID or enforce equality | 3 |
| P0 | Status/history/override/audit use separate `session.run` auto-commit transactions; an injected audit failure leaves the new status applied | One `session.execute_write` callback for revision/state/score/ranking/events | 3 |
| P0 | Overrides have no range validation; 999 was accepted. History stores `score_before=None` | Bound overrides to the active policy scale; retain calculated/effective scores and actual before/after values | 3 |
| P0 | `06_generate_requirements.py:186–218` resets ordinal IDs per run and MERGEs over existing requirement content/status | Stable derivation IDs, immutable revisions, pending suggestions on regeneration, no overwriting approved/rejected decisions | 3 with 2 |
| P0 | `04_extract_signals.py:136–169` accepts unvalidated quotes/intensity; IDs truncate VFC names to 20 characters and collide | Validate against supplied feedback IDs/spans, use collision-resistant identifiers and signal versions | 2 |
| P0 | Both generation stages pair returned model output to input by array position | Require input IDs in output; reject missing/duplicate/unknown IDs and map by ID, never model order | 2 |
| P0 | Signals link to a broad VFC rather than exact contributing comments; loader stores one feedback_type on a multi-topic comment | Preserve rows/annotations and direct signal-to-evidence assignments; count distinct issue comments | 1 with 2 |
| P0 | The branch assumes external `BMW-Processed` JSON; no raw XLSX/PDF converter or corporate-report ingestion is included | Provide conversion from supplied inputs, immutable source versions/locators, and strategy passages | 1 |
| P0 | `08_streamlit_ui.py:26` uses localhost in the UI container; compose supplies API_HOST but UI ignores it | Explicit API_BASE_URL, fixture-backed client adapter, container-host verification | 4 with 3 |
| P0 | Runner requires ANTHROPIC_API_KEY even though OpenAI is primary; compose API does not forward OpenAI credentials/model | Apply the team's OpenAI-only configuration decision: canonical OPENAI_API_KEY/OPENAI_MODEL, no Anthropic prerequisite/fallback, and tested startup | 3 |
| P1 | Generator uses max hard-coded 2025 market volume, not actual requested region/powertrain/2030 scope; G60/G70 RoW volume is zeroed | Source-linked scoped forecasts, fixed normalization, explicit proxy/missing-data assumptions | 3 using 1 |
| P1 | STRONG implies confidence 1.0 solely from group count; source-channel semantics/bias are assumed; imported bias adjustment is unused | Transparent reviewed evidence rubric, concentration/coverage gaps, no unsupported source-channel truth | 3 with 1/2 |
| P1 | Step 05 uses keyword matching, pooled markets, assumed 1–10 scales and can upgrade WEAK even when opposed | Reviewed metric mapping/native scales/scope; contradictory evidence separate from strength; no automatic unjustified upgrade | 2 with 1/3 |
| P1 | Valid G68 study input is skipped by allowed model scope; study records omit original cells/distributions | Preserve source derivative G68 and native study evidence; deliberate G60/G68 sales-line mapping | 1 |
| P1 | SMART flags and competitor/architecture/milestone claims are model assertions; strategy/report sources are not supplied to generation | Proposed target/feasibility/milestone provenance, claim citations, acceptance-test method, PM validation | 2 with 3 |
| P1 | Claimed priority “confidence interval” is an arbitrary ±15% impact perturbation | Label sensitivity honestly, or compute documented assumption-based bounds; no statistical CI claim | 3/4 |
| P1 | Weak signals are excluded from requirement generation; exported honesty demo has no WEAK/RED case | Keep an evidence-gap case visible without inventing support; do not hide weak proposals | 2/4 |
| P1 | Audit hash covers only entity/action/time, not payload or previous hash; creation/consolidation/content evidence history is missing | Payload-complete chronological events, revisions, ranking context, chain/checkpoint verifier | 3 |
| P1 | UI export downloads only summary fields and caps at 200 although there are 250; CSV report omits linked sources/gaps/assumptions | Complete server export and pagination independent of table limit; every BMW-required field included | 3/4 |

P0 items block a trustworthy end-to-end demonstration. P1 items are implemented next, prioritizing the verified showcase cases.

## 5. Specialized Ownership and Integration

1. **Data engineering:** source conversion, provenance, annotations, facts, evidence queries. No frontend or model prompting.
2. **AI engine:** signal/draft/review reasoning, tool orchestration, strict model-output validation. No frontend or authoritative decisions/scoring.
3. **Backend and governance:** API contracts, persistence/revisions, deterministic scoring, PM guards, transactions, audit/export, runtime integration. No frontend rendering or model prompt development.
4. **Frontend:** one Streamlit PM experience, evidence/score/history UI, edit/chat/diff controls, server-backed export. No database writes, scoring calculation, or LLM calls.
5. **Pitch/demo:** verified story, presentation, acceptance walkthrough, backup recording, artifact packaging.

Keep root `models.py`/`config.py` coordinated by Role 3 and let Roles 1/2 supply their definitions. Split score/persistence helpers from step 06 without rewriting the entire pipeline. Existing root URLs/status vocabulary can be adapted to the richer planned API; add `/api` aliases and typed responses deliberately.

The team subsequently selected OpenAI as the only provider. Root `.env.example` and `CONFIGURATION.md` are the shared environment contract; the observed Anthropic behavior is legacy code to retire, not a required team setting.

Use Neo4j as the single authoritative store and Neo4j transactions for state/audit atomicity. The logical entities/revision DTOs in the implementation plan remain valid; store structured snapshots as JSON properties on versioned nodes. The planned 0–100 score is a new versioned policy; existing 0–10 exports must remain labeled legacy rather than silently mixed.

Resolve `.gitignore` as a union when integrating the branch: retain `.venv`/data exclusions and add private processed/output/cache storage. Committed exports contain customer quotes; treat them as internal artifacts, not public code fixtures. Do not rewrite repository history as part of this task.
