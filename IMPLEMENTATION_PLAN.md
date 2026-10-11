# BMW ConceptCraft AI: Core Application Implementation Plan

- **Delivery window:** 10–12 hours; four implementation owners plus one pitch/demo/submission owner; AI-assisted implementation
- **Product:** Evidence-backed product requirements workbench for BMW Product Managers
- **Process:** Market research and product planning, through G1 concept freeze
- **Scope:** F70, G60/G68, G70, and explicitly labeled successor/Neue Klasse proposals
- **Implementation:** Extend the existing Neo4j/FastAPI/Streamlit engine with validated LLM calls, versioned decisions, and explicit evidence lineage

## 1. The Tangible Product We Are Building

Build one complete decision loop:

**Import actual BMW inputs → consolidate findings → draft requirements → explain and rank them → PM review → inspect history → export the approved plan.**

The main screen is a requirements table. Clicking a row opens a workbench containing the requirement, its score explanation, original evidence, assumptions, and decision controls. This is the central product, not a dashboard around a hidden analysis script.

### 1.1 Core User Journey

1. The PM opens the app and sees the imported datasets, analysis progress, and a ranked requirements list.
2. The PM selects a derivative, target markets, and powertrains, then runs analysis or opens a previous completed run.
3. The app presents proposed requirements, each with its own evidence, score breakdown, evidence level, assumptions, and status.
4. The PM opens a requirement and follows a claim to the original spreadsheet row/cell or PDF page.
5. The PM edits a target, challenges an assumption, changes a scoring factor, approves, rejects, or defers the requirement.
6. The app saves the change, rationale, actor, time, and before/after state; relevant scores and baseline ranks update.
7. The PM selects approved requirements and creates an exportable G1 snapshot.

### 1.2 BMW Deliverables and Their Concrete Implementation

| BMW expected deliverable | What we implement | Demonstration / acceptance criterion |
| :--- | :--- | :--- |
| Concept and demonstrable AI-powered prototype processing heterogeneous inputs, deriving requirements, supporting prioritization, and integrating human review | Import pipeline, analysis service, requirements workbench, scoring service, and PM decision actions | Generate at least one new requirement from actual imported data and complete its review in the UI |
| Structured sample list with ID, actionable description, linked findings/sources, score, prioritization rationale, evidence level, future assumptions/uncertainties, and decision status | The requirements table and detail response share one structured requirement model; CSV/JSON export includes all of these fields | Every displayed/exported requirement contains these fields; evidence links resolve to stored sources |
| Defined end-to-end workflow distinguishing autonomous AI work from mandatory human review | The workflow in Section 2, analysis-run stages, and server-enforced status transitions | Explain the process from an imported record to an approved revision; AI cannot approve requirements |
| Audit trail covering sources, consolidation, scoring, prioritization, and human decisions, including changes, initiator, time, and rationale | Immutable revision snapshots plus an append-only application event stream | Show the original derivation and subsequent content, evidence, score, rank, and status changes for one requirement |

**Additional presentation output:** a 3–5 minute pitch built around the working decision loop and three verified example requirements.

### 1.3 Delivery Targets

- Import every supplied feedback row, the survey sheets, sales forecasts, and page-aware text from the option catalogs and corporate report.
- Produce an initial sample list targeting 12–18 proposed requirements across the three vehicle lines, as the evidence permits.
- Verify three showcase requirements thoroughly: strong support, a real trade-off, and an explicit evidence gap.
- Support live generation for a bounded selection of signals, with real cached results available for a reliable walkthrough.
- Have the first real end-to-end requirement working by hour 3.

The essential build is the workbench, generation, ranking, review, audit, and export. Weight sensitivity and regional comparison are additions after that loop works. Separate Kanban, graph visualization, elaborate dashboards, SSO, and full engineering feasibility automation are outside this delivery window.

### 1.4 Final Deliverable Bundles

These are the outputs to have ready for BMW at the end of implementation; this document specifies the build, rather than claiming these outputs already exist.

| Bundle | Concrete artifacts | What BMW can inspect |
| :--- | :--- | :--- |
| **1. Concept and working prototype** | Backend/frontend source, README with setup/startup instructions, configured sample analysis, and a running application | Actual imports, new AI-generated requirements, explained ranking, contextual review chat, and PM decisions |
| **2. Structured requirements sample** | In-app list plus `var/exports/requirements.csv` and `requirements.json` | Every required field from Section 6, including findings/sources, score rationale, evidence level, assumptions, uncertainty, and status |
| **3. End-to-end workflow** | Workflow diagram and AI/PM responsibility table from Section 2, included in the project documentation and presentation | Autonomous processing, persisted proposals, acceptance of edits, mandatory approval, and export gates |
| **4. Demonstrable audit trail** | In-app history, revision diffs, `var/exports/audit.json`, and the frozen review-package manifest/checkpoint | Source-to-decision lineage and changes to consolidation, content, evidence, scores, ranks, and status, with initiator/time/rationale |

The frozen G1 package is the approved-decision export within these bundles. Include a 3–5 minute pitch and three verified demonstration cases. Exported BMW evidence stays in the local/private delivery bundle, not the public repository. The 12–18 requirement target is a team goal, not a BMW-mandated minimum.

## 2. Workflow and AI/Human Responsibilities

```mermaid
flowchart LR
    A[Import sources] --> B[Canonical comments and facts]
    B --> C[Consolidate evidence into signals]
    C --> D[Retrieve supporting and opposing context]
    D --> E[AI drafts structured requirements]
    E --> F[Validate and calculate scores]
    F --> G[Ranked proposed requirements]
    G --> H[Mandatory PM review]
    H --> I[Approved revision]
    H --> J[Edit, challenge, reject, or defer]
    J --> H
    I --> K[Freeze snapshot and export]
    A -.-> L[Versioned provenance and audit events]
    C -.-> L
    E -.-> L
    F -.-> L
    H -.-> L
    K -.-> L
```

| Activity | Implementation | Authority |
| :--- | :--- | :--- |
| Parse, normalize, deduplicate comments, calculate counts | Deterministic Python code | Automatic; preserve originals and report import issues |
| Group topics, extract issue spans, retrieve relevant context | Imported annotations, explicit rules, and bounded model-assisted analysis | Automatic proposals; retain assignment method and source locations |
| Draft requirement, metric, alternatives, and assumptions | Structured LLM output | Proposed content only |
| Compute factor scores, evidence index, and ranking | Versioned deterministic scoring code | Automatic calculation; PM can override factors with rationale |
| Validate a proposed target or accept an extrapolation | PM review controls | Mandatory human judgment |
| Approve, reject, defer, reopen, or freeze | Backend decision service | PM action required |

Opening a detail panel is read-only. AI never changes an approved requirement in place. A changed requirement becomes a new revision requiring review.

### 2.1 Workflow Graph and Evidence Graph

Implement the workflow as a Python state machine in `analysis.py`, with each completed stage checkpointed in `analysis_runs`. Specialist model roles handle signal extraction, requirement drafting, and contextual review; they share the provider adapter and validated evidence contracts. Deterministic services own import, counts, score calculation, ranking, persistence, and decision transitions.

The two graphs serve different purposes:

- **Workflow graph:** `consolidate → retrieve → draft → validate → score → propose → human review → decision/export`. Initial generation follows predefined retrieval steps; contextual review can choose additional read-only tools within a bounded loop.
- **Evidence graph:** requirement revision → signal revision → feedback spans → source rows, plus requirement/score → survey/sales facts → cells, strategy/equipment chunks → PDF pages, and revision → decisions/history. Versioned Neo4j nodes and explicit evidence relationships preserve these links.

Persist run context: entity IDs/revisions, planning scope, source/config versions, retrieved evidence IDs, model/prompt version, tool calls, validation failures, and result/suggestion IDs. A PM review is a saved state awaiting a later UI action; no background worker remains blocked while the PM thinks.

### 2.2 Two Human Gates

1. **Accept an edit:** the assistant presents a cited explanation, proposed field diff, and score preview. The PM applies, edits, or discards the proposal. Applying creates an auditable new revision and may invalidate prior approval.
2. **Approve the requirement:** a separate PM action approves the current revision after reviewing targets, scope, evidence, and assumptions. Accepting an assistant suggestion does not approve it.

Read-only questions and discarded suggestions leave requirement content, scores, ranks, and decision status unchanged. Natural-language requests to approve or reject lead to the corresponding UI decision control; the assistant itself has no decision-write authority.

## 3. Architecture and Repository Structure

### 3.0 Existing Implementation Baseline

Use `origin/ai-requirements-engine` at reviewed snapshot `3bd0251` as the implementation baseline. It already contains data loading, graph statistics, batched signal/draft generation, FastAPI review routes, Streamlit screens, and 250 exported proposals. The useful review findings are integrated here and in the role guides; [AI_ENGINE_BRANCH_REVIEW.md](AI_ENGINE_BRANCH_REVIEW.md) remains the original assessment.

The branch has not been merged/fixed by this documentation task. The structures below are extensions/adapters to existing code, not a claim that all planned features are implemented. Keep Neo4j as the single authoritative store and Streamlit as the primary UI for the delivery window; avoid a simultaneous SQLite/React rewrite. The exported proposals require source/target/governance validation before being presented as validated requirements.

**Reuse inventory:** steps 00–03 provide enrichment/loading/graph-statistic skeletons; steps 04–06 provide batched provider calls, signal/draft schemas and legacy pure score helpers; step 07 provides list/detail/transition/history routes; step 08 provides four UI pages; step 09 provides JSON/CSV/summary export. Extend these boundaries with the assigned repairs in Section 11.4.

#### Reviewed Artifact and Test Baseline

| Observation at `3bd0251` | What implementation must establish |
| :--- | :--- |
| 250 proposals with unique IDs: F70 49, G60 102, G70 99 | Preserve legacy candidates and validate selected revisions; count alone does not establish requirement quality. |
| All 250 histories empty | Add actual derivation/change/decision events; legacy artifacts do not demonstrate human review. |
| 750 quote occurrences: 538 exact model-text substrings and 212 non-matches; zero exported original feedback IDs | Resolve original IDs/spans/locators and issue membership. Transformed or ambiguous evidence remains unverified; non-match alone does not prove fabrication. |
| 218 self-report all SMART flags true; 225 declare no gaps | Targets, feasibility, dates and assumptions require independent source/proposal provenance and PM review. |
| Zero WEAK/RED proposals | Keep an actual evidence-gap case visible; the current filter is not proof of honest uncertainty handling. |
| All 250 priorities match legacy arithmetic | Preserve labeled arithmetic compatibility, then recompute the new scoped policy from validated inputs. |
| 16 offline diagnostics characterized/reproduced behavior; no branch test suite or tested dependency lock found | Implement corrected regression tests and pin a tested environment. Passing counterexample reproductions are not passing correctness acceptance tests. |
| No live Cypher/container/raw-conversion/model run validated during review | Close these specific verification gaps before presenting a working deployment. |

### 3.1 Runtime

- **Backend:** extend the existing FastAPI/Pydantic/Uvicorn API and Neo4j driver; add OpenPyXL and a PDF parser for raw conversion.
- **Frontend:** complete/polish `pipeline/08_streamlit_ui.py`; all UI work belongs to Owner 4. Rendering calls HTTP, not Cypher/model/scoring logic.
- **LLM:** OpenAI only, using `OPENAI_API_KEY`/`OPENAI_MODEL` from the shared root configuration. Remove the legacy Anthropic prerequisite/fallback and repair runner/Compose wiring before relying on startup.
- **Retrieval:** Neo4j full-text indexes or a bounded lexical provider behind the evidence interface. Counts/citations come from authoritative nodes, not search hits or model prose.
- **Execution:** one API process with persisted background run state and bounded engine calls; no separate queue infrastructure.
- **Python:** the branch Dockerfile uses 3.12; local review imports worked on 3.14. Pin a tested project environment rather than relying on unconstrained lower-bound dependencies.

Use Neo4j managed write transactions for state, revision, ranking and audit atomicity. Store nested immutable snapshots as JSON properties; native Neo4j properties cannot store arbitrary nested dictionaries.

**Canonical environment:** root [.env.example](.env.example) and [CONFIGURATION.md](CONFIGURATION.md) define all runtime variable names/defaults. Owner 3 maintains the single `config.py` reader; other modules import its typed settings without redeclaring env variables, loading their own dotenv files, or copying defaults. Data paths, OpenAI, graph credentials, API origin and Streamlit settings use this same contract in local and container processes.

### 3.2 Proposed Files

```text
models.py, config.py          # Owner 3 coordinates shared contracts/runtime
pipeline/
  00_preprocess.py            # Owner 1: raw conversion/processed-format contract
  01_load_neo4j.py            # Owner 1: versioned evidence/facts
  02_build_kg.py              # Owner 1: evidence relationships/statistics
  03_vfc_analysis.py          # Owner 1: distinct cohort/topic counts
  04_extract_signals.py       # Owner 2: keyed/validated signal extraction
  05_correlate_studies.py     # Owner 2: reviewed/scoped study context
  06_generate_requirements.py # Owner 2 drafting; Owner 3 score/write helpers
  07_pm_review_api.py         # Owner 3: all API/decision composition
  08_streamlit_ui.py          # Owner 4: complete frontend
  09_export_report.py         # Owner 3: full sample/audit/freeze exports
services/                    # Extract reusable helpers without replacing all scripts
  evidence.py, ingestion.py  # Owner 1
  signals.py, llm_client.py   # Owner 2
  analysis.py                # Owner 2: bounded engine stages; Owner 3 mounts jobs
  review_assistant.py         # Owner 2
  tool_registry.py           # Owner 2
  scoring.py, decisions.py    # Owner 3
  audit.py, export.py         # Owner 3
ui/
  client.py, components.py    # Owner 4: HTTP adapter/rendering
  fixtures.py                # Owner 4: labeled synthetic preview
tests/
  data/                      # Owner 1
  intelligence/              # Owner 2
  backend/                   # Owner 3
  frontend/                  # Owner 4
contracts/                   # Shared DTO examples/linked synthetic fixture
docs/                        # Owner 5: narrative/workflow/demo/QA/checklist
var/                         # Private processed inputs/caches/exports/presentation
```

Agree the step-06 helper extraction before concurrent edits: Owner 2 owns prompts/model validation, Owner 3 owns scoring/revision writes. Other owners request shared contract/runtime changes from Owner 3.

Keep raw BMW data and derived quotes, databases, caches, and exports out of public commits. Extend `.gitignore` for `var/` and credentials. Frontend development fixtures are synthetic and explicitly labeled.

### 3.3 Development Commands to Provide

After the engine branch is integrated, dependencies installed, and Owner 1's raw-conversion extension and Owner 3's startup fixes are complete:

```bash
python pipeline/00_preprocess.py
python pipeline/07_pm_review_api.py
streamlit run pipeline/08_streamlit_ui.py
```

The reviewed step 00 only enriches existing external JSON; its extension reads `BMW_RAW_DATA_DIR` and writes `BMW_DATA_DIR` through shared config. The fixed runner uses OpenAI credentials and the same processed path. Backend uses configured API_HOST/API_PORT; frontend uses API_BASE_URL. The launcher exports native STREAMLIT_SERVER_* settings before starting the CLI, as defined in CONFIGURATION.md. Owner 3 documents the tested local/Compose startup path.

### 3.4 Integration and Legacy-Artifact Treatment

1. Owner 3 coordinates bringing the reviewed engine code into the team's shared integration baseline, preserving current documentation and the union of ignore rules. Split step 06's drafting versus scoring/write helpers before parallel edits.
2. Agree the processed-format manifest, source/claim IDs, strict keyed model outputs, legacy/v1 score versions, uppercase states, and expected-revision mutation contracts with Owners 1/2/4.
3. Fix startup: runner uses OpenAI only; Compose forwards OPENAI_API_KEY/OPENAI_MODEL to the API, graph credentials use shared settings, and UI receives API_BASE_URL=http://api:${API_PORT} plus native Streamlit settings. Missing input/OpenAI/server states stay explicit. CONFIGURATION.md defines the complete path/network contract.
4. Preserve the 250 exported candidates as legacy/unverified. Owner 1 resolves raw source versions/locations; Owner 2 validates exact spans, assignment and proposed claims; Owner 3 creates new validated/reviewable revisions. Source matching must not silently select an ambiguous first hit or replace the original artifact.
5. Block approval/freeze while the underlying basis remains unverified. A model SMART flag, GREEN gate, matched arithmetic or empty gap array cannot satisfy review guards. Preserve PM-approved/rejected revisions when generation runs again.
6. Store future processed files/caches/customer-quote exports in private `var/` storage. Integrate `.gitignore` as a union retaining existing `.venv`/data exclusions; ignoring a path does not remove tracked content. Repository-history rewriting is outside this implementation task.
7. Use fixture tests for independent work, then prove real Neo4j rollback/queries, actual container UI networking, raw conversion and a cited live generation/review. Record these results separately from the review's offline diagnostics.

## 4. Data Model and Real Input Handling

### 4.1 Observed Data Constraints

These counts come from inspecting the supplied workbooks and must be checked again by the importer:

| File | Raw rows | Distinct feedback IDs |
| :--- | ---: | ---: |
| `F70_feedback_hackathon.xlsx` | 4,076 | 2,128 |
| `G60_feedback_hackathon.xlsx` | 5,005 | 4,000 |
| `G70_feedback_hackathon.xlsx` | 4,690 | 4,000 |
| **Total** | **13,771** | **10,128** |

Repeated IDs have the same text and different category/type annotations. Preserve the rows and annotations; count a canonical comment once per signal. These are distinct comments, not proven distinct customers.

Other important constraints:

- No China free-text feedback is supplied. China evidence comes from studies and other applicable sources.
- About 90.2% of distinct G60 comments and 94.8% of distinct G70 comments are US comments.
- F70 is ICE-only in this feedback dataset, and its supplied US/CN forecast volumes are zero.
- China 5 Series studies refer to G68; EU studies refer to G60. Regional differences may also reflect derivative differences.
- `BEVE` is a source value; normalize it to `BEV` while retaining the original.
- Feedback lacks dates. The core app shows coverage and sentiment/type distribution, not sentiment velocity.

### 4.2 Storage Contract

Use stable identifiers, explicit Neo4j relationships, and JSON strings for complex immutable snapshots. The following logical entity names define the data contract; map them to graph labels such as SourceFile, SourceRecord, CustomerFeedback, FeedbackAnnotation, SignalRevision, RequirementRevision, AuditEvent, and FreezeSnapshot. Reuse existing study/sales/equipment labels while adding provenance.

| Logical entity | Essential fields / purpose |
| :--- | :--- |
| `source_files` | `id`, filename, SHA-256, kind, imported timestamp, import summary; same file hash is idempotent |
| `source_records` | `id`, `source_file_id`, locator JSON, raw payload JSON, optional `feedback_id`; one record per spreadsheet row or extracted fact location |
| `feedback_comments` | `id`, source version, original ID, source label, derivative, country, region, normalized/original powertrain, original text |
| `feedback_annotations` | `id`, `feedback_id`, `source_record_id`, original feedback type and VFC level 2/3 labels |
| `facts` | `id`, kind (`survey`, `sales`, `option`), derivative/line, region, powertrain if known, year, metric, value/distribution, unit/scale metadata, source record references |
| `document_chunks` | `id`, source file, 1-based PDF page, optional printed page label, chunk text, derivative/market metadata; full-text index |
| `signals` | Composite key `(id, revision)`, title, category, source scope, consolidation method/configuration, summary JSON, analysis run ID |
| `signal_links` | Signal ID/revision, feedback ID, original-text span, polarity, assignment method, annotation/source references; distinct comment counting despite multiple spans |
| `requirements` | `id`, current revision, current status, approved revision or null; compact current-state index |
| `requirement_revisions` | Composite key `(req_id, revision)`, full requirement payload, score breakdown, evidence links, source/signal versions, actor, timestamp, rationale |
| `analysis_runs` | `id`, kind (`import`, `analysis`, `challenge`), requested scope, source/config versions, stage, state, progress counts, failures, timings, model/prompt version, result IDs |
| `review_threads` | `id`, entity kind (`signal`, `requirement`), entity ID, created actor/time; conversation bound to one selected insight or requirement |
| `review_turns` | `id`, thread ID, expected entity revision, run ID, PM question/rationale, cited answer, tool/context references, optional field diff/score preview, proposal state, applied revision or null |
| `scoring_configs` | `id`, formula version, factor weights, thresholds, normalization reference, forecast year; immutable once used |
| `ranking_snapshots` | `id`, scoring config, requirement revisions, baseline ID-to-rank/score mapping, timestamp, triggering event |
| `audit_events` | `id`, entity type/ID/revision, UTC time, actor kind/ID, action, before/after JSON, rationale, run/config references, previous/current hash |
| `freeze_snapshots` | `id`, selected approved revisions, source/config versions, accepted assumptions, ranking snapshot, audit checkpoint, actor, timestamp, complete export payload |

Add graph uniqueness constraints for IDs/composite revision keys and indexes for scope/source/status/time. Use an injected driver and managed write transactions; every related requirement/score/rank/audit write uses the same transaction. Serialize ledger-head updates and validate/lock current revisions so concurrent mutations cannot fork history or silently overwrite newer work.

### 4.3 Feedback Import

1. Read the `Feedback_Explorer` sheet and map columns by header name, not position.
2. Save every original row with a locator such as `{"sheet":"Feedback_Explorer","row":3}`; row numbers include the header.
3. Canonicalize by source-file version plus supplied feedback ID. Verify that repeated IDs have consistent text and metadata; report collisions instead of silently merging inconsistent records.
4. Save each annotation separately. A comment may contain both positive and negative annotations.
5. Map `BEVE → BEV`, countries to the agreed commercial regions, and VFC categories to the topic taxonomy. Store mapping versions.
6. Preserve missing labels. Any added label records its method and is stored separately from the imported annotation.
7. Flag empty/non-informative text for analysis exclusion while retaining the source. Do not discard meaningful short text solely because it is under 12 characters.

The sales workbook calls its Europe region `EU`. Document the commercial mapping for GB, Norway, and other European countries rather than treating the label as strict EU membership.

### 4.4 Studies and Sales Import

- **CN/EU studies:** walk category/`Mean` row pairs; identify derivative and region from the multi-row headers. Preserve each numeric cell, metric, and locator.
- **US studies:** parse question blocks and seven rating categories into distributions. `Sample total = 1` is a 100% distribution total, not one respondent. Preserve unknown respondent counts as null.
- Keep native scales. Never equate a CN/EU mean with a US distribution without an explicitly recorded transformation. Broad satisfaction measures cannot prove a specific interface preference.
- Map topics to related study questions through a small reviewed mapping file. Mark the relation `direct`, `broad`, `contradictory`, or `unmapped`, with a reason.
- **Sales:** read F70, G60_G68, and G70 sheets into line/region/year facts for 2024, 2025, and 2030. Treat G60_G68 as one sales line, not two volumes to sum.
- Sales do not supply a powertrain split. A BEV-only proposal using whole-line volume must label it as an upper-bound proxy; no invented BEV share.

### 4.5 PDF Import and Citation Retrieval

- Extract each page from the three option PDFs and `BMW-Group-Report-2025-en.pdf`; record extraction failures/empty pages.
- Chunk within pages, approximately 600–1,000 words per chunk, keeping the page boundary and exact original text.
- For showcase requirements, verify the actual option codes/package facts and strategy passages used. Store selected equipment facts with source-page references; general-purpose table reconstruction is not required.
- German catalog facts retain the original wording. Translations are separate derived text, and German packaging is not automatically asserted for other markets.
- `GET /api/sources/{id}` returns file metadata; `/api/source-records/{id}` returns the exact workbook row/cells or extracted fact, and `/api/document-chunks/{id}` returns cited page text. The PDF file endpoint supports opening the cited page with `#page=N`.

## 5. How the Analysis Produces Requirements

### 5.1 Signals: A Bounded, Implementable First Pass

Start with approximately 8–12 in-scope topics: HVAC operation, infotainment/navigation, connectivity, seating/comfort, storage/space, visibility, charging UX, perceived range, driving experience, and equipment packaging. Final names come from inspected VFC categories.

1. Map imported annotations into topic buckets per derivative. Keep positive, negative, and request evidence distinct.
2. Calculate distinct-comment bucket counts and regional/powertrain coverage through parameterized Cypher/provider queries.
3. Select up to 24 comments per bucket for initial discovery: different sources/markets/powertrains where available, positive counterexamples, and recurring friction. Record the selection method and cap; do not call it statistically representative.
4. Ask the model to propose issue-specific signals and return exact original-text spans with feedback IDs and polarity.
5. Validate that each span is a substring of the referenced original comment. Invalid citations are rejected, not repaired by inventing text.
6. Persist the validated issue evidence as `signal_links`. Wider bucket members remain retrieval candidates until actually assigned to that issue.
7. Where useful, an explicit phrase rule can scan the full bucket to add matches with the rule/version recorded. Show whether membership is model-assigned, rule-assigned, imported, or PM-confirmed.

**Two counts must stay separate:** `topic_bucket_comment_count` describes the broad topic; `issue_support_comment_count` describes validated evidence for the specific issue. Never attach the entire HVAC bucket count to one narrower physical-button proposal.

Initial generation processes at most four selected issue signals per vehicle line, targeting 12 drafts. A subsequent run can extend the list toward 18 after review. This keeps latency, model cost, and citation review bounded while every raw source remains available for search. Counts from bounded discovery are lower bounds, not an exhaustive estimate of issue frequency; retain the unassigned candidate counts and mark this limitation.

### 5.2 Context Retrieval

For each issue signal, construct an evidence pack containing:

- Up to eight supporting and four opposing issue spans, selected with scope/source diversity, with their source IDs and scope. Counts come from all persisted assignments, not only the snippets in this pack.
- The mapped survey facts and mapping strength.
- Applicable sales facts for the proposed scope.
- Up to three relevant option chunks and three strategy chunks, filtered by derivative where appropriate.
- Missing-market, missing-scale, packaging, and future-platform limitations.

Use FTS search terms from the topic and issue title plus reviewed bilingual aliases for German catalogs. Return source IDs, exact text, and metadata, not an untraceable generated summary. The model receives only these supplied identifiers and may cite only them.

### 5.3 Structured Requirement Drafting

`draft_requirement(signal, evidence_pack, planning_scope)` returns validated JSON containing:

- Title and clear customer-facing requirement description.
- The evidence-backed problem, kept separate from the suggested solution.
- SMART fields: specific scope, measurable target/unit/test conditions, attainability rationale, relevance, and target milestone.
- Claim-to-evidence references for the problem, rationale, strategy, and packaging statements.
- Alternative solution suggestions when the evidence supports more than one approach.
- Initial severity/strategic-fit/effort proposals with reasons, not a model-generated final priority number.
- Future assumptions, limitations, contradictory evidence, and unresolved validations.

Every metric, milestone, architecture assertion, and package suggestion has provenance: `SourceBacked`, `AIProposed`, or `PMEntered`, plus a review state. AI-proposed targets are displayed as proposals, not engineering facts.

For example, complaints about difficult access to temperature controls can justify a usability requirement. A suggested maximum interaction count is a proposed acceptance target until reviewed. They do not establish an exact latency threshold, SOP year, existing camera architecture, or commercial uplift.

### 5.4 Draft Validation and Persistence

Pydantic validates the output shape. Service validation checks:

1. Each output includes its requested input group/signal ID and cites supplied immutable evidence/source versions; map by input ID rather than array position. Unknown/duplicate IDs fail validation; missing outputs create explicit partial results.
2. Quoted spans are exact; fact values match the stored facts.
3. Supporting customer evidence exists for the problem statement, or the draft explicitly identifies a study-only basis.
4. Markets, powertrains, source derivatives, and target derivatives are explicit.
5. A narrow requirement does not claim the full topic count as support.
6. Unsupported quantitative claims and future assumptions are labeled.
7. Regulatory engineering and detailed component/BOM specifications do not replace the customer-facing requirement.

Use collision-resistant full-scope signal/derivation IDs rather than VFC prefixes or per-run ordinals. Persist new signal/requirement revisions without overwriting human decisions. Generation can include a traceable weak-evidence draft with explicit limitations; the legacy STRONG/MODERATE-only filter must not hide the uncertainty demonstration.

Citation/number checks establish traceability, not semantic proof that a passage entails the claim. The draft records its interpretation for PM review.

Then the backend assigns a stable unique requirement/derivation ID, calculates scores and evidence quality, saves revision 1 with status `DRAFT`, records the derivation, and refreshes the baseline ranking. Invalid drafts are retained as run failures for inspection but do not appear as valid proposed requirements. Drafting belongs to Owner 2; persistence/scoring belongs to Owner 3.

Implementation sequence inside `analysis.py`:

```python
async def derive_one(signal, scope, run_id):
    pack = retrieval.build_evidence_pack(signal, scope)
    draft = await llm.draft_requirement(signal, pack, scope)
    validated = validate_draft(draft, pack, signal)
    scores = scoring.calculate(validated, signal, baseline_config)
    evidence = scoring.assess_evidence(validated, signal, pack)
    # Backend writer uses one managed Neo4j transaction for revision/rank/audit.
    # All network/model calls above complete before that transaction starts.
    return requirement_writer.create_draft(validated, scores, evidence, run_id)
```

This shows the path for a new requirement; the service checks cached/existing derivations before entering it. Imports, model calls, and evidence retrieval remain independently callable/testable modules.

### 5.5 Run Behavior and Failure Handling

Run stages: `import_check → consolidate → retrieve → draft → validate → score → completed`.

Run states: `queued`, `running`, `succeeded`, `partial`, `failed`. Return a run ID immediately, show progress, and poll while running. Execute one generation run at a time in the first build.

- Cache by source/config versions, signal revision, scope, model, prompt version, and retrieved-context hash.
- Cached derivations return existing requirement IDs without duplicating or overwriting them. Regenerating an existing requirement produces a pending suggestion/diff for PM application, preserving the current revision and decision.
- Use a bounded concurrency limit and at most one structured-output repair attempt per draft; transient provider errors receive bounded backoff.
- Save valid requirements even if another signal fails, and report partial results.
- On restart, mark interrupted runs `failed` with reason `interrupted`, rather than leaving them permanently running.
- Missing credentials or unavailable providers produce a visible failure/configuration state. Cached results are labeled cached; no fabricated successful AI run.

### 5.6 Tool-Enabled Review Assistant

Expose typed function tools through `tool_registry.py`; the server dispatches them to existing services. Tools return structured identifiers, source locations, exact text/facts, counts, and explicit missing-data results.

| Tool | Inputs | Output / service |
| :--- | :--- | :--- |
| `search_feedback` | Query, derivative/region/powertrain scope, limit | Original comments, matching spans, source references, and candidate/membership distinction |
| `get_signal_evidence` | Signal ID/revision | Supporting/opposing evidence, distinct counts, assignment coverage, and gaps |
| `get_survey_facts` | Topic and scope | Native study values/distributions, cells, mapping strength, unknown sample/scale metadata |
| `get_sales_volume` | Vehicle lines, regions, forecast year | Deterministic applicable volume, fact references, missing splits, proxy assumptions |
| `search_documents` | Query, document kind, derivative/market scope, limit | Equipment/strategy chunks with exact text and PDF page references |
| `get_requirement_context` | Requirement ID/revision | Current reviewed content, scores, assumptions, evidence, and selected history |
| `preview_requirement_change` | Requirement ID/expected revision and allowed field patch | Validated hypothetical content, recalculated score/ECI/rank effects, and unresolved checks; no writes |

The review service loads the selected entity and current revision before a model turn. It sends the PM question, relevant prior visible conversation, pinned context, and tool schemas. The model can request tools, receive results, and produce an answer or pending suggestion.

Implementation limits:

- At most six tool invocations and four model round-trips per turn, including repair attempts; bounded result sizes. Return a partial answer with its limitation if the budget is exhausted.
- Server validates tool arguments and uses parameterized queries and registered source IDs. Tools cannot execute arbitrary SQL, access arbitrary files, or modify requirements.
- Record tool inputs/results and their source versions with the turn/run so the answer is reconstructable.
- Feedback/documents are evidence content, not instructions for the assistant. Only the registered tool schemas and service policy determine execution.
- The model can propose edits to title/description, SMART fields, target scope, assumptions, evidence links, and scoring-factor assessments. It cannot patch source text, calculated scores, status, actor, revision number, or audit history.
- Every suggested patch is validated and previewed by the backend, even if the model did not call the preview tool itself. New evidence must resolve to real stored identifiers.

Final review result: cited `answer`, `coverage_gaps`, optional `proposed_patch`, server-calculated `score_preview`, `base_revision`, and proposal state (`none`, `pending`, `applied`, `discarded`, `stale`). Persist it before displaying it. Model failure does not prevent ordinary form-based PM edits.

## 6. The Requirement Contract

The backend owns this contract; the frontend client validates/normalizes responses against the shared OpenAPI/Pydantic models. Complex fields live in the revision payload so history can reconstruct exactly what the PM reviewed.

| Field | Required contents |
| :--- | :--- |
| `id`, `revision` | Stable unique identifier and version number |
| `title`, `description`, `problem_statement` | Actionable customer-facing wording and the originating need |
| `source_scope` | Source derivatives, regions, powertrains, and relevant signal revisions |
| `target_scope` | Target derivative/vehicle line, regions, powertrains; successor mapping if applicable |
| `smart` | Specific, measurable target/unit/conditions/verification method, attainability, relevance, milestone; provenance and review state for targets/assertions |
| `finding_links` | Signal versions and issue-level evidence identifiers |
| `evidence_links` | Source/record/chunk IDs, locators, claim key, relation (`supports`, `contradicts`, `context`), and validation state |
| `score` | Calculated and effective priority, factor values/contributions, input references, override if any, configuration ID, provisional flag, range |
| `prioritization_rationale` | Deterministic explanation of main score drivers plus grounded trade-off notes |
| `evidence` | ECI value/level, component values, distinct supporting comments, coverage, and explicit gaps |
| `assumptions`, `uncertainties` | Future-facing statements, missing evidence, owner/review state, and any PM acceptance rationale |
| `status`, `approved_revision` | Decision state and the exact revision approved, if any |
| `run_id`, `created_at`, `updated_at` | Derivation/run references and timestamps |

Source scope and target scope are separate. A G60 complaint can inform a successor proposal, but the app must label the carryover assumption rather than claiming successor architecture compatibility.

## 7. Deterministic Scoring and Evidence Quality

### 7.1 Priority Formula

Normalize all four factors to `[0,1]`:

```text
Priority = 100 × (0.40 × CustomerValue
                + 0.25 × AddressableReach
                + 0.20 × StrategicFit
                + 0.15 × EaseOfImplementation)
```

Weights are an explicit PM policy, not learned truth. Store them with the formula version. The core build uses one immutable baseline config; what-if previews do not silently change the baseline or approvals.

The branch's legacy calculation uses model impact, max hard-coded 2025 market volume and inverse effort, with G60/G70 RoW zeroed. Correct arithmetic under that policy does not validate scope or inputs. Owner 3 replaces the reach inputs with Owner 1's sourced 2030 facts and records the new policy version; do not simply multiply old scores by ten and call them v1.

**Customer value:**

- Severity levels 1–4: cosmetic/minor friction; repeated usability friction; core experience unreliable or difficult; core experience unavailable/major recurring blocker.
- `severity = level / 4`, with source-linked rationale and an explicit provisional marker for AI estimation.
- For each applicable observed region, `p_r = distinct issue-support comments / distinct available comments in the matching source-derivative/powertrain cohort`.
- `mention = sqrt(mean(p_r))` across observed target regions. Show per-region denominators; missing regions are gaps, not zero demand.
- `CustomerValue = 0.60 × severity + 0.40 × mention`.
- This is a within-dataset mention signal, not population prevalence. Study-only drafts have a provisional customer factor until PM judgment; survey measures appear as corroboration rather than being silently mixed across scales.
- If issue assignment covered only a discovery subset, treat `p_r` as a lower bound and flag the customer factor provisional. Its upper bound includes still-unassigned candidate comments in that topic/cohort; expose the resulting score range rather than implying an exhaustive count.

**Addressable reach:**

- Sum applicable 2030 sales facts over unique `(vehicle_line, region)` keys.
- `AddressableReach = target_volume / reference_volume`.
- `reference_volume` is the fixed total of the supplied 2030 forecast across all supported lines/regions, saved in the config; it does not depend on the currently visible requirements.
- Mark future-line carryover forecasts and whole-line powertrain proxies as assumptions. Zero-volume F70 US/CN scope is surfaced explicitly.

**Strategic fit:** levels 0–4, mapped to `level / 4`: explicit conflict, limited alignment, partial alignment, strong alignment, direct strategic contribution. Require a cited strategy passage and a rationale. Missing assessment is unknown, not verified neutral alignment.

**Ease of implementation:** complexity levels 1–5: software/UI change; calibration/configuration; existing hardware/packaging change; new subsystem integration; major architecture change. `Ease = (5 - complexity) / 4`. This is a rough proposal, not an engineering feasibility certificate.

Entirely unknown factors use a clearly marked provisional midpoint of `0.5`; expose a score range calculated with their lower/upper values of 0/1. Partially observed customer factors use the lower-bound/range rule above. Do not display an unknown factor as a sourced fact. Targets with no valid comparable volume require a provisional reach factor.

The baseline ranks current `DRAFT`, `PM_REVIEW`, and `APPROVED` requirements. `REJECTED` and `POSTPONED` retain scores/history but have no active rank. Sort by effective score descending, then requirement ID for deterministic ties. Keep calculated score and any PM score override separately. Content/evidence changes clear an old override unless the PM explicitly reaffirms it with a new rationale. This is the new versioned 0–100 policy; existing branch 0–10 scores remain labeled legacy until recalculated from validated inputs.

### 7.2 Evidence Confidence Index

Implement a transparent rubric, not a probability of correctness:

```text
ECI = 100 × (0.35 × support
           + 0.25 × coverage
           + 0.25 × corroboration
           + 0.15 × assignment_validation)
```

- `support = min(1, log(1 + distinct_support_comments) / log(51))`. For study-only cases with unknown respondent counts, use a conservative 0 in the headline index, label the component unknown, and show its possible contribution range; do not manufacture comment counts.
- `coverage` is the fraction of target regions with applicable customer feedback or a relevant mapped study. Show which type each region has.
- `corroboration`: 0 for no independent relevant support, 0.5 for broad same-topic support, 1 for directly relevant corroboration; contradictory evidence is separately flagged.
- `assignment_validation`: mean membership-review value, 0.5 for traceable but unreviewed imported/rule/model assignment and 1 for PM-confirmed assignment. For study-only evidence use the mapping-review value instead; untraceable assignments are rejected.
- Evidence levels: `Low < 40`, `Moderate 40 ≤ ECI < 70`, `High ≥ 70`; thresholds and components are versioned policy choices.

Always show low-support warnings, unknown respondent counts, source concentration, future assumptions, and regional gaps alongside the index. High ECI does not establish an AI-proposed target or architecture claim. Priority and ECI are independent; do not hide promising low-evidence requirements by multiplying the scores.

Remove legacy automatic STRONG/confidence=1.0 from broad count alone and automatic upgrade from ambiguous/opposing studies. Opaque Source A–D labels remain opaque unless semantics are documented. Contradiction and coverage are explicit inputs; native study scales/regions/derivatives cannot be pooled into an unsupported average. Legacy ±15% impact bounds are labeled sensitivity assumptions, never a statistical confidence interval.

### 7.3 Regional Differences and Optional Sensitivity

- Different satisfaction means produce a `satisfaction_difference` observation, not an inferred preference for touch versus physical controls.
- Explicit opposing preferences produce a `supported_conflict` with both evidence sets.
- Missing regional evidence produces a `coverage_gap`.
- G60 versus G68 comparisons include a derivative-confounding note.
- Where a real conflict exists, draft alternative regional scopes for PM review.
- A preview endpoint accepts alternate factor weights and returns old/new scores, rank movement, and drivers. Preview results are labeled and excluded from baseline audit/approval mutations.

## 8. PM Review, Revisions, and Audit

### 8.1 Status Model

Use the existing uppercase status vocabulary: `DRAFT`, `PM_REVIEW`, `APPROVED`, `REJECTED`, `POSTPONED`. UI labels can be Proposed, Under review, Approved, Rejected, Deferred. Adapt legacy `CONFLICTED` to `PM_REVIEW` plus an explicit conflict flag; conflict and human decision are separate dimensions.

```text
DRAFT → PM_REVIEW                         explicit Start review
DRAFT / PM_REVIEW → APPROVED              PM approval, validation, rationale
DRAFT / PM_REVIEW → REJECTED              PM rationale
DRAFT / PM_REVIEW → POSTPONED             PM rationale
APPROVED / REJECTED / POSTPONED → PM_REVIEW   explicit reopen/edit
```

Challenge is an action with a separate run/result, not a new decision status. G1 inclusion is a freeze-snapshot selection, not another ambiguous status.

### 8.2 Editing and Decisions

Every requirement content/evidence/score/status mutation includes `expected_revision`, actor identity, and rationale. The server reads the current revision, validates the action, builds a complete new revision, recomputes affected factors, records the event, and refreshes baseline ranking within one transaction. Challenge requests record an event/run but do not create a content revision until applied.

Return HTTP 409 on stale revisions and let the UI reload/compare. Return 422 for missing rationale or invalid transitions. Use server-confirmed updates for approval/edit actions; do not show an optimistic approval before it succeeds.

Reject AI_SYSTEM decision authority and route/body ID mismatch; trim/validate rationale for every decision/override. Validate finite values and override bounds against the active score policy, including explicit zero. Backend uses one managed Neo4j write callback for revision/status/score/ranking/suggestion/audit changes and records actual before/after values. Injected audit failure must roll back all mutations, closing the reviewed auto-commit defect.

Approval requires:

- Complete, actionable SMART fields, including a measurable acceptance target and milestone.
- At least one valid underlying finding/study source link.
- PM review of proposed targets and milestone, and explicit acceptance or resolution of feasibility/scope assumptions.
- The current reviewed revision; any later content, evidence, or score edit clears approval and creates a `PM_REVIEW` revision.

The prototype uses a named PM selector as actor identity. AI/system events identify the model/run or service. This supports the demonstrable workflow; enterprise authentication is a later integration.

### 8.3 Contextual Chat and Challenge Action

Each signal or requirement has a contextual review thread. The PM can ask a question, challenge evidence, request a narrower scope, or suggest a target change. The service retrieves pinned context and uses the tools in Section 5.6, then returns cited reasoning, coverage gaps, and an optional proposed diff.

Record the request and response. Do not mutate the requirement until the PM selects **Apply suggestion**, confirms the diff, and supplies a rationale. Applying uses the original expected revision; a stale result must be reviewed again.

Example interaction, with actual retrieved citations replacing the example's generic descriptions:

1. PM asks: **“Does this evidence justify a global requirement?”**
2. Assistant examines supporting comments, regional coverage, and mapped study facts. If only US evidence supports the specific preference, it explains the gap rather than inventing a China preference.
3. Assistant proposes narrowing target scope and adding a regional validation assumption. The UI shows before/after fields and a backend-calculated priority preview.
4. PM selects **Apply**, optionally edits the proposal, and enters the rationale.
5. Backend checks the revision, validates the allowed patch and evidence, creates a new revision, recomputes affected scores/ECI/ranking, and records changes in one transaction. An approved item becomes `PM_REVIEW`.
6. PM separately selects **Approve** after reviewing the resulting revision and assumptions.

Signal-only conversations can request **Create requirement draft**, which submits the selected signal to the normal analysis/validation/scoring path. Read-only insight discussion does not mutate signal membership. Requirement conversations can modify a pending suggestion; direct form edits remain available for simple changes.

A follow-up turn retains previous visible conversation but reloads the current entity revision. If the requirement changed, flag older patches as stale and regenerate their previews. Selecting **Discard** records the suggestion outcome without changing the requirement.

### 8.4 Events and Reconstructability

Record these event categories:

| Event | What must be reconstructable |
| :--- | :--- |
| `SOURCE_IMPORTED` | File version, parser/mapping version, row counts, exclusions/errors |
| `SIGNAL_CONSOLIDATED` / `SIGNAL_REVISED` | Membership, consolidation method, counts, source scope, before/after signal snapshots |
| `REQUIREMENT_DERIVED` | Signal/source versions, retrieved evidence, run/model/prompt references, initial content |
| `CONTENT_CHANGED` / `EVIDENCE_CHANGED` | Previous/new revision and exact field/evidence changes |
| `SCORE_CALCULATED` / `SCORE_OVERRIDDEN` | Inputs, formula/config, factor contributions, original/effective score, rationale |
| `RANKING_RECALCULATED` | Previous/new baseline ID-to-rank map, scores, context, trigger; visible moves for affected requirements |
| `STATUS_CHANGED` | Actor, previous/new status, approved revision, decision rationale |
| `CHALLENGE_REQUESTED` / `CHALLENGE_COMPLETED` | PM question, cited response, proposed diff, application state |
| `SUGGESTION_APPLIED` / `SUGGESTION_DISCARDED` | Turn/run reference, proposed versus accepted patch, PM rationale, resulting revision or no-op outcome |
| `FREEZE_CREATED` | Approved revisions, source/config versions, accepted assumptions, actor, checkpoint |

Every event has UTC time, initiator kind/ID, entity/version, and a non-empty human or generated rationale. Store model output/context references locally for AI derivations; generation is not claimed to be mathematically deterministic.

Audit view combines requirement events with its linked signal/run/source events so the PM can follow the complete chain. Ranking history uses the baseline full-list context; sorting/filtering a table changes only the view and is not a governance event.

For tamper evidence, hash canonical event JSON plus the previous event hash. Provide a verifier and include the checkpoint in the exported package. This is a tamper-evident local ledger relative to a trusted checkpoint, not a claim that a database administrator cannot rewrite history.

## 9. API and Frontend Contracts

### 9.1 API Endpoints

| Endpoint | Purpose / payload |
| :--- | :--- |
| `GET /api/sources` | Import state, file hashes, row/comment counts, parsing issues |
| `GET /api/sources/{id}` | Source file metadata and import state |
| `GET /api/source-records/{id}` | Exact workbook row/cell or extracted fact with original locator |
| `GET /api/document-chunks/{id}` | Exact cited PDF text and page metadata |
| `GET /api/sources/{id}/file` | Allowlisted imported PDF download/view |
| `POST /api/import-runs` | Import the configured local BMW dataset; returns an analysis-compatible run ID |
| `POST /api/analysis-runs` | Requested scope, selected signals/topics, max drafts, refresh/cache option; returns run ID |
| `GET /api/analysis-runs/{id}` | State/stage, progress, failures, model/cache mode, resulting requirement IDs |
| `GET /api/signals` | Consolidated findings with scope, distinct counts, and evidence links |
| `POST /api/review-threads` | Bind a contextual conversation to a signal or requirement ID |
| `GET /api/review-threads/{id}` | Visible turns, citations, suggestion states, and current entity revision |
| `POST /api/review-threads/{id}/turns` | Question, expected entity revision, actor, rationale; returns a challenge run ID |
| `POST /api/review-threads/{id}/turns/{turn_id}/discard` | Mark a pending suggestion discarded, actor and rationale; no requirement mutation |
| `GET /api/requirements` | Paginated/searchable table; derivative, region, powertrain, status, evidence filters |
| `GET /api/requirements/{id}` | Complete current revision, score, uncertainty, and decision controls |
| `GET /api/requirements/{id}/evidence` | Supporting/opposing/context evidence, locators, facts, regional coverage |
| `PATCH /api/requirements/{id}` | SMART/scope/evidence/factor edits plus expected revision, actor, rationale |
| `POST /api/requirements/{id}/decisions` | Start review, approve, reject, defer, or reopen; expected revision and rationale |
| `POST /api/requirements/{id}/challenges` | Convenience wrapper creating/reusing a requirement thread and submitting a turn; returns run ID |
| `POST /api/requirements/{id}/challenge-applications` | Turn/run reference, expected revision, accepted allowed diff, actor, rationale; apply one proposal once |
| `GET /api/requirements/{id}/history` | Revision diffs and linked consolidation/scoring/ranking/decision events |
| `GET /api/requirements/{id}/history/export?format=json` | Download the reconstructable requirement history and referenced context |
| `GET /api/requirements/export?format=csv\|json` | Structured sample list, including linked evidence, score rationale, ECI, assumptions, status |
| `POST /api/freezes` | Selected requirement IDs/revisions, expected baseline ranking ID, actor, rationale |
| `GET /api/freezes/{id}` | Immutable review-package snapshot |
| `GET /api/freezes/{id}/export?format=markdown\|json` | G1 package from the stored snapshot |
| `GET /api/freezes/{id}/audit` | Audit bundle with revision/tool references and complete chain prefix through the snapshot checkpoint |
| `POST /api/scoring/preview` | Optional sensitivity preview; no baseline writes |

Register the static `/api/requirements/export` route before the `/{id}` route. Run polling uses a common response shape for import, analysis, and challenge jobs.

All contextual chat uses the same review service. The convenience challenge route is an adapter, not a second implementation. Applying a proposal is idempotent by turn ID; an identical repeat request returns the original application result rather than creating another revision. A conflicting reapplication returns 409. Record the application result and requirement mutation in the same transaction.

Example approval request:

```json
{
  "action": "approve",
  "expected_revision": 3,
  "actor_id": "pm-demo-1",
  "actor_role": "PM_USER",
  "rationale": "Reviewed linked evidence and accepted the proposed usability target for concept planning.",
  "reviewed_target_ids": ["metric-1", "milestone-1"],
  "accepted_assumption_ids": ["assumption-1"]
}
```

The server determines the new revision, status, timestamp, score, and events. The client cannot submit a fabricated approval state or final calculated score.

### 9.2 Main Screen

Header: dataset/run state, **Run analysis**, **Export sample list**, **Create review package**.

Filters: source/target derivative, region, powertrain, status, evidence level, text search.

Table columns: rank, ID/title, target scope, effective priority, evidence level, uncertainty/conflict badges, status. Row selection opens the workbench. Empty, failed, partial, and cached states are explicit.

### 9.3 Workbench

Use tabs or sections within one large drawer/panel:

- **Requirement:** problem, description, editable SMART fields, target provenance, alternatives, assumptions.
- **Why this priority:** four factor values, weighted contributions, input references, provisional range, override controls, generated score rationale.
- **Evidence:** verbatim spans, source/type, worksheet/cell/page link, support/opposition label, bucket-versus-issue counts, regional coverage and survey context.
- **Review assistant:** scoped conversation, clickable citations, visible retrieval progress, coverage gaps, and a change-preview card with Apply/Edit/Discard controls. Applying and approving are separate buttons/actions.
- **History:** chronological events and revision diff, including score/rank/status moves.

Persistent actions: **Start review**, **Edit**, **Challenge**, **Approve**, **Reject**, **Defer**. A decision dialog requires rationale and shows outstanding targets/assumptions. Invalid actions are disabled and server-enforced.

After a confirmed mutation, refresh server detail/list/history/ranking and invalidate matching Streamlit cache/session data. Preserve unsaved editor state if an API request fails. Show a reload/compare prompt for HTTP 409. Rendering/rerunning the UI must not itself submit mutations.

Owner 4 reads explicit API_BASE_URL instead of hard-coded localhost, normalizes legacy `gate` list versus `gate_color` detail fields, and shows score policy/scale. An override-enabled control distinguishes zero from disabled. Empty legacy histories, unresolved citations and model SMART flags have accurate validation labels; GREEN is an evidence/gate display, not PM approval. Empty API results remain distinct from failures.

### 9.4 Sources and Review Package Screens

The sources screen shows actual imported counts, source errors, region/powertrain coverage, and source previews. The review-package screen shows selected approved revisions, unresolved-but-accepted assumptions, baseline scores, and snapshot/download controls.

Keep the BMW-inspired styling consistent: navy `#061a38`, blue `#1c69d4`, neutral slate, amber for uncertainty. Prioritize legible evidence and review controls over decorative charts.

## 10. Export Behavior

### 10.1 Structured Sample List

CSV/JSON export includes ID, revision, description/SMART fields, linked finding/source identifiers and locators, score and factor breakdown, prioritization rationale, evidence index/level, future assumptions, uncertainties, and decision status.

JSON retains nested structures. CSV flattens the main fields and serializes evidence/assumption arrays predictably; it must not silently omit them.

Generate complete exports server-side, independent of table pagination/limit. The reviewed UI downloads summary rows capped at 200 despite 250 proposals; the reviewed CSV omits linked sources/gaps/assumptions. A 250-record fixture must export all 250 with the full BMW field set. Frontend requests that endpoint rather than building files from its displayed page.

### 10.2 Frozen G1 Package

`POST /api/freezes` validates that each supplied revision is current and approved and that the baseline ranking context has not changed. It captures full requirement payloads, score/configuration, source/signal versions, decisions, accepted assumptions, and an audit checkpoint in one transaction.

Generate Markdown and JSON from that stored snapshot. Later edits to a requirement do not change an existing package. PDF can be added using browser print after the core exports work.

The Markdown package contains an executive summary, prioritized approved list, SMART descriptions, source references, scoring rationale, confidence/coverage, open validations, and decision history. Include a machine-readable manifest and checkpoint for verification.

### 10.3 Audit Export

Provide `GET /api/freezes/{id}/audit` and a JSON download on the history view. For a freeze, export the relevant chronological source/run/signal/requirement/ranking events, revision snapshots, and visible tool/assistant records needed to reconstruct each selected decision.

Hash-chain verification requires the complete event prefix through the stored checkpoint, or an explicit proof linking omitted events. The core build exports the complete prefix plus selected-requirement references so a verifier does not mistake a filtered event list for a complete chain. Write `audit.json` and the manifest beside the review package in the private export bundle.

## 11. Four Specialized Builders + One Pitch Owner

Everyone has a specialty, independent tests, and fixed file ownership. Only Owner 4 builds frontend; only Owner 2 develops model prompts/tool reasoning. Owner 3 coordinates API/runtime integration. Each role has a separate detailed assignment:

| Owner / guide | Exact scope | Independent verification |
| :--- | :--- | :--- |
| **[1 — Data engineering](roles/01_DATA_AND_EVIDENCE.md)** | Raw conversion, source versions/annotations, native facts, graph statistics and evidence-provider functions | Synthetic XLSX/PDF → exact evidence/counts; real import and query checks |
| **[2 — AI engine](roles/02_AI_AND_REVIEW_ASSISTANT.md)** | Existing extraction/drafting, keyed/cited validation, reviewed study context, bounded review tools and suggestions | Fake model/evidence → valid draft/diff; invalid IDs/order/quotes rejected; one live cited check |
| **[3 — Backend/governance](roles/03_BACKEND_AND_GOVERNANCE.md)** | API/contracts, deterministic scoring, PM guards, revisions, transactions, audit, complete export/freeze, runtime | Fake-provider API tests; real test-graph atomicity; score fixtures and immutable exports |
| **[4 — Frontend](roles/04_FRONTEND_AND_PM_WORKBENCH.md)** | Existing Streamlit screens/client, evidence/score/history/edit/chat/diff controls and server-backed downloads | Mock API → complete interaction; no rendering mutations; live/container walkthrough |
| **[5 — Pitch/demo/submission](roles/05_PITCH_DEMO_AND_SUBMISSION.md)** | Narrative, deck, workflow explanation, manual acceptance, verified cases, timing, recording/Q&A and private artifact checklist | Rehearsed 3–5 minute story with an actual artifact for every BMW deliverable |

### 11.1 Ownership Rules and Shared Contracts

Owner 3 coordinates root `models.py`/`config.py`, constraints, dependencies, API entry points and runner/Compose. Owner 1 supplies source/evidence definitions; Owner 2 supplies signal/draft/review definitions; Owner 3 supplies requirement/score/event/freeze/error contracts. Owner 4 supplies client-facing payload requirements, not backend implementations.

Split `pipeline/06_generate_requirements.py` early: Owner 2 owns model/prompt/validation, Owner 3 owns score/save helpers. Extract reusable helpers instead of simultaneously editing the same block. Owner 1 owns steps 00–03, Owner 2 owns 04–05 and draft logic, Owner 3 owns 07/09 and persistence, Owner 4 owns 08/UI. No other builder implements UI screens.

Agree IDs, locators, source/scope distinctions, uppercase status/version semantics, score scales, revision checks and errors in the first 45 minutes. Use one linked synthetic fixture defined in Role 1's guide and assembled in `contracts/fixture_case.json`; fixture IDs resolve across all modules.

### 11.2 Independent Work and Handoffs

- Data provider tests use synthetic input files and fake repositories, then isolated Neo4j query checks.
- AI tests inject fixture evidence/model/scorer/writer interfaces; no model account or UI required.
- Backend tests inject fake evidence/engine and use TestClient plus isolated graph transaction checks.
- Frontend injects mock HTTP responses; it never implements database/scoring/LLM fallbacks.
- Pitch owner validates the narrative/checklist first, then conducts manual real-app acceptance from hour 3 onward.

Planned test targets after integrating the scaffold:

```bash
python -m pytest tests/data
python -m pytest tests/intelligence
python -m pytest tests/backend
python -m pytest tests/frontend
```

Replace test providers with real providers through the same interfaces at integration. Publish fixture/OpenAPI payloads immediately. A handoff includes the callable module/client, fixture proof, passing meaningful tests, one real-provider check when available and explicit remaining gaps.

Use branches/worktrees and bounded agents inside each specialty. Backend scoring/revision/audit/export subtasks can be delegated separately, while Owner 3 coordinates their shared transaction and schema. Integration starts with one validated requirement, not after all code is finished.

### 11.3 Milestones

| Time | Four builders | Pitch/demo owner |
| :--- | :--- | :--- |
| **0–0.75h** | Agree reviewed branch baseline, fix startup ownership, freeze contracts/fixtures and split step 06 | Draft narrative, checklist and case-selection questions |
| **0.75–3h** | Data provider, validated real draft, guarded transactional decision and fixture UI; connect the first real loop | Prepare workflow/deck; begin acceptance as soon as the loop appears |
| **3–6h** | Native studies/sales/strategy evidence, direct quote IDs, versioned scores/revisions and complete workbench | Verify showcase sources, measure activities and report blockers |
| **6–8h** | Bounded review chat/apply, full history/rank changes, complete sample export and frozen package | Lock demo route, capture actual outputs and map four deliverable bundles |
| **8–10h** | Atomicity/citation/contract checks, Docker/live UI walkthrough and polish | Rehearse, record truthful backup, prepare Q&A and artifact bundle |
| **10–12h** | Buffer; differentiation only once P0 review defects and core loop are resolved | Final rehearsal/submission readiness |

First-loop target remains hour 3. Keep a real evidence-gap example visible and source-check the three showcase requirements. Existing legacy outputs or hand-written fixture cards do not establish validated live generation or approval governance.

### 11.4 Branch Repair Backlog

All items below derive from reviewed snapshot `3bd0251`; role guides include their owned tasks and specific tests. **P0 blocks the trustworthy judged demonstration.** The hour-3 vertical slice first closes safety/provenance for one real requirement; complete heterogeneous context and the remaining P0 repairs before the final walkthrough. P1 completes decision quality, sample coverage and reconstruction. Work on independent fixes in parallel.

| ID | Priority | Current defect / code | Lead and supporting roles | Required corrected proof |
| :--- | :--- | :--- | :--- | :--- |
| **R01** | P0 | Step 07 accepts AI approval without rationale, arbitrary transitions and mismatched route/body IDs | 3; 4 collects review inputs | AI/blank-rationale/invalid-state/ID-mismatch requests fail without writes; reviewed PM decision succeeds on the current revision. |
| **R02** | P0 | Separate status/history/override/audit auto-commit writes | 3 | Injected audit failure leaves revision/status/score/rank/turn state unchanged in real Neo4j. |
| **R03** | P0 | Override 999 accepted; score_before is null | 3; 4 renders scale/toggle | Finite policy-bounded override; actual before/after and calculated/effective scores; explicit zero works. |
| **R04** | P0 | Step 06 resets ordinal IDs and MERGEs over prior content/status | 3 with 2 | Stable derivation identity, immutable revisions and pending regeneration; approved/rejected content survives rerun/order changes. |
| **R05** | P0 | Step 04 accepts unvalidated spans/intensity; 20-character category IDs collide | 2 | Reject invalid quote/range; input-ID/span validation and full-scope versioned IDs; same-prefix categories stay distinct. |
| **R06** | P0 | Steps 04/06 pair outputs by position | 2 | Reverse output order still maps by ID; unknown/duplicate IDs fail and missing entries report partial results. |
| **R07** | P0 | Scalar feedback type erases annotation polarity; Signal only links broad VFC | 1 with 2; 4 renders evidence | Mixed annotations retained; exact source rows/spans/direct membership; distinct issue counts separate from bucket/annotation counts. |
| **R08** | P0 | External clean JSON assumed; no raw conversion/corporate-report ingestion | 1; 3 wires runs | From supplied files produce versioned manifest/evidence; quote/cell/page resolves; missing required inputs cannot masquerade as success. |
| **R09** | P0 | Streamlit calls its own localhost inside Docker | 4 with 3 | Configured API_BASE_URL and actual container UI → API smoke check. |
| **R10** | P0 | Runner requires Anthropic; Compose omits OpenAI configuration | 3 | Canonical OpenAI-only configuration; key/model forwarded to API, no Anthropic prerequisite/fallback, missing key gives truthful unavailable state. |
| **R11** | P1 | Max hard-coded 2025 reach, ignored target scope, zeroed G60/G70 RoW | 3 using 1 | Sourced scoped 2030 volumes, G60_G68 deduplication, fixed reference and explicit proxy assumptions. |
| **R12** | P1 | Broad count implies STRONG=1.0; assumed channel semantics/bias | 3 with 1/2 | Versioned issue-based ECI, coverage/concentration/review gaps; no unsupported channel truth or calibrated-probability claim. |
| **R13** | P1 | Keyword/native-scale/region pooling and upgrades despite study opposition | 2 with 1/3 | Reviewed metric scope/scale mapping; contradiction remains explicit; ambiguous/opposing study does not inflate strength. |
| **R14** | P1 | Valid G68 input skipped; study cells/distributions omitted | 1 | G68_CN preserved as source G68, native distribution/cells/unknown N retained; deliberate shared sales-line mapping. |
| **R15** | P1 | Model SMART/competitor/architecture/date assertions lack evidence | 2 with 3; 4 displays | Source/proposed target provenance, acceptance-test method and PM review; self-checked SMART cannot authorize approval. |
| **R16** | P1 | Arbitrary ±15% impact range called confidence interval | 3; 4 labels | Documented sensitivity/provisional bounds and range kind; no unsupported statistical interval. |
| **R17** | P1 | WEAK excluded; no WEAK/RED in existing output | 2; 4 displays | Actual traceable weak/evidence-gap draft visible without invented support or forced high confidence. |
| **R18** | P1 | Hash omits payload/chain; derivation/content/evidence history missing | 3 | Full chronological versioned events and checkpoint verifier; content/actor/rationale/score tampering detectable; source-to-decision reconstruction. |
| **R19** | P1 | UI export capped at 200/summary only; CSV omits required evidence/gaps | 3 with 4 | Full 250-record fixture export with every BMW field, independent of pagination; approved snapshot stable after live edit. |

Owner 5 tracks proof of these repairs, not code ownership. Record task ID, implementation/revision reference, fixture regression result, real integration result where required, and remaining limitation in the team's QA/handoff notes. A passed counterexample-characterization test is not closure; assert the corrected result.

## 12. Verification and Demonstration

### 12.1 Meaningful Checks

- **Import correctness:** expected raw/distinct counts; repeated-ID annotations survive; mixed positive/negative comments remain multi-topic; rerunning the same import is idempotent.
- **Provenance:** sampled comments, survey cells, volume facts, and cited PDF passages match the actual source. All generated citation IDs resolve.
- **Signal counts:** duplicate annotations and multiple spans do not inflate distinct-comment support; broad bucket counts cannot become narrow-issue counts.
- **Scoring:** factors stay bounded, weights sum to one, missing inputs remain provisional, same inputs/config reproduce scores, volume keys are not double-counted, complexity importance affects scores as specified.
- **Workflow:** AI cannot approve; a blank rationale fails; stale revision fails; editing an approved requirement invalidates approval; failed writes cannot leave an unlogged state change.
- **Review assistant:** retrieval uses real identifiers; read-only chat/discard cannot mutate requirements; pending edits have validated score previews; applying once creates one revision; stale suggestions cannot overwrite newer work; applying never implicitly approves.
- **History:** derivation, consolidation, evidence edits, factor/score changes, resulting rank moves, and PM decisions are reconstructable with actors/times/reasons.
- **Freeze:** unapproved/stale revisions cannot be frozen; changing the live requirement does not change an existing package; all required sample-list fields survive export.
- **Recovery:** cached/live runs are distinguished, partial failures display, saved decisions/history survive restart, and changed audit events fail verification against the trusted checkpoint.

Use focused backend/integration tests for these invariants and one browser walkthrough. Avoid spending the window on exhaustive UI unit tests.

### 12.1.1 Integration Gates Based on the Review

| Gate | Required proof | Owners |
| :--- | :--- | :--- |
| **Baseline/contracts, first 45 minutes** | Reviewed code integrated deliberately; shared model/step-06 ownership agreed; tested environment/paths/provider selection and API payloads documented | 3 coordinates 1/2/4 |
| **One trustworthy vertical slice, target hour 3** | Real original IDs/spans; keyed valid generation; score policy labeled; guarded PM decision and atomic history; UI opens without mutation | 1/2/3/4, witnessed by 5 |
| **Source/sample readiness, target hour 6** | Native study/G68/sales/strategy/equipment evidence; quote reconciliation for every showcase claim; complete field contract and source/target assumptions | 1/2/3 |
| **Decision/export readiness, target hour 8** | Reruns preserve decisions; apply and approve separate; complete exports/history/freeze; source gaps/weak case visible | 2/3/4 |
| **Judged demonstration, hours 8–10** | All P0 repair proofs; real Neo4j rollback/query checks, actual container networking, raw conversion and cited live model run; three verified cases, honest cache/fixture labels and private artifact bundle | 3 coordinates all; 5 rehearses/checks |

For regression tests, use the recorded counterexamples: AI_SYSTEM approval with no rationale; conflicting route/body IDs; override 999; audit failure after status write; same-prefix signal names; reversed model output; rerun over approved data; G68_CN/native distribution; WEAK plus opposing study; and a 250-record export. Preserve the distinction between synthetic/mocked proof and real graph/container/source/model checks.

The reviewed export's 218 SMART-true flags, 225 empty-gap arrays and 538 text matches are not acceptance evidence by themselves. Showcase requirements need independently resolved sources/assignment, reviewed target provenance and a real chronological decision history.

### 12.2 Three Showcase Cases

Select cases after inspecting actual evidence; do not hard-code assumed complaint counts or regional preferences.

1. **Strong support:** recurring issue with valid quotes and relevant corroboration.
2. **Trade-off:** a genuine conflict or two plausible solutions with traceable evidence and effort differences.
3. **Evidence gap:** a valuable successor/regional proposal whose applicability or target needs validation.

The demonstration follows one case through drafting, score explanation, original evidence, PM modification, approval, history, and export. The other two show why uncertainty and trade-offs are useful decision outputs.

### 12.3 Definition of Done

- [ ] Prototype imports the supplied heterogeneous data and exposes actual import/coverage state.
- [ ] A new proposed requirement can be generated from real findings in the app.
- [ ] The structured sample list contains every BMW-required field and exports successfully.
- [ ] Every requirement has traceable findings/sources and a reproducible score rationale.
- [ ] Proposed targets, future assumptions, evidence gaps, and conflicts are visible.
- [ ] PM edit, challenge, approve, reject, and defer work with revisions and rationale.
- [ ] Contextual chat retrieves cited evidence and presents changes that a PM explicitly applies, edits, or discards before a separate approval.
- [ ] Audit history traces sources → consolidation → requirement → score/rank → human decision.
- [ ] Relevant content, evidence, score, priority, and status changes record initiator, timestamp, and rationale.
- [ ] Approved revisions can be frozen/exported without later edits altering the snapshot.
- [ ] Three verified examples and a rehearsed 3–5 minute pitch demonstrate the complete core loop.

**Measure the prototype's actual contribution:** time to first cited draft, time to review a requirement, and traceability coverage. Present acceleration of these activities rather than claiming the entire two-year development process has been eliminated.
