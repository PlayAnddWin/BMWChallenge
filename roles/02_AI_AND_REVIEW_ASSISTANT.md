# Role 2 — AI Requirements Engine & Review Assistant

## Exact Role

Own model-assisted signal interpretation, evidence retrieval orchestration, structured requirement drafts, and contextual review suggestions. **No frontend implementation; no authoritative scoring or PM decision writes.**

Success: generate a cited validated draft from real evidence and answer a scoped PM challenge with a pending, previewed diff. The PM applies and approves through Role 3's backend.

Use the implemented signal/draft stages at `origin/ai-requirements-engine`, snapshot `3bd0251`. This guide embeds the relevant review findings; [the original review](../AI_ENGINE_BRANCH_REVIEW.md) is supporting history. Read [the plan](../IMPLEMENTATION_PLAN.md), Sections 2/5/6/8, and [Role 1's evidence contract](01_DATA_AND_EVIDENCE.md).

## Existing Code and Ownership

| Own | Work |
| :--- | :--- |
| `pipeline/04_extract_signals.py` | Signal extraction, source IDs/spans, keyed output validation, stable/versioned signals |
| `pipeline/05_correlate_studies.py` | Reviewed study context/mapping, scoped contradictions; confidence policy comes from Role 3 |
| Drafting portion of `pipeline/06_generate_requirements.py` | Prompts, structured output and model adapter; Role 3 owns scoring/save helpers |
| New `services/llm_client.py`, `signals.py`, `analysis.py`, `review_assistant.py`, `tool_registry.py` | Reusable validated engine stages and bounded review tools; Role 3 mounts/runs API jobs |
| `tests/intelligence/*` | Fake-model/provider tests and one live cited integration |

Split step 06's drafting and persistence boundaries with Role 3 before simultaneous edits. Shared `models.py` and `config.py` are coordinated by Role 3; supply your DTO definitions. Role 3 owns HTTP mounting/job infrastructure; you supply callable engine services. Role 4 owns every UI component.

**Shared environment:** [.env.example](../.env.example) and [CONFIGURATION.md](../CONFIGURATION.md) are canonical. Use `OPENAI_API_KEY`, `OPENAI_MODEL`, and `BMW_CACHE_DIR` through `config.py`. OpenAI is the only provider; missing key means live generation unavailable, never Anthropic fallback. Retire legacy Anthropic imports/provider branches with Role 3; do not add `LLM_API_KEY` aliases or another dotenv loader.

## Branch Repairs Assigned to This Role

Task IDs match the plan. Fix P0 identity/provenance problems before relying on a live generation output.

| Task | Priority / ownership | Observed behavior at `3bd0251` | Implementation and acceptance proof |
| :--- | :--- | :--- | :--- |
| **R05** | P0, lead | `04_extract_signals.py:136–169` stores unchecked quotes/intensity and truncates VFC IDs to 20 characters. | Validate schema/ranges and exact input-ID/span membership; stable full-scope IDs and immutable signal versions. Categories sharing their first 20 characters remain distinct; an invented quote or intensity 999 is rejected. |
| **R06** | P0, lead | Steps 04/06 attach outputs to inputs by array position. | Include input group/signal IDs in output and map by ID. Reverse output order still binds correctly; duplicate/unknown IDs fail and missing IDs produce explicit partial results. |
| **R07** | P0, support Role 1 | Signal → VFC relation cannot identify contributing comments; broad counts support narrower proposals. | Persist direct evidence assignments and separate bucket/support/cohort counts. Fixture issue support is two distinct comments, not all annotation rows or topic members. |
| **R04** | P0, support Role 3 | Step 06 resets ordinal requirement IDs and rewrites content/status on rerun. | Supply stable derivation key/context; regeneration returns a pending suggestion through the backend writer. Approved/rejected content and decisions survive reruns. |
| **R13** | P1, lead; Role 3 owns confidence policy | `05_correlate_studies.py` can upgrade WEAK with an opposing study and pools keyword matches/scales/regions. | Use reviewed scoped mappings/native scales; opposition stays explicit; no upgrade from ambiguous/contradictory context. Test `WEAK + opposing study` preserves weak support/contradiction rather than becoming stronger automatically. |
| **R15** | P1, lead; Role 3 enforces review | Model SMART/competitor/capability/date assertions are accepted without supplied sources. | Add claim citations, target/test/provenance/review fields. Unsourced numbers/feasibility/milestones stay proposed; SMART=true does not satisfy PM approval. |
| **R17** | P1, lead; Role 4 displays | Generation excludes WEAK; existing export has no WEAK/RED examples. | Generate an honest evidence-gap draft when the problem has a traceable basis, with actual weak support/gaps. No fabricated supporting count or forced high tier. |
| **R12** | P1, support Role 3 | A broad group count ≥30 produces STRONG/confidence 1.0 regardless of assignment/coverage/source concentration. | Pass actual issue counts, reviewed mapping, assignment coverage and source gaps to the backend ECI calculator; do not choose confidence solely from VFC volume. |

### What the Existing Outputs Establish

There are 250 exported proposals: F70 49, G60 102, G70 99. Of 750 quote occurrences, 212 do not exactly match original model feedback; all 750 lack exported original feedback-ID lineage. **218** proposals self-report all SMART flags true; **225** have no declared gap. These are model/output attributes, not independent validation.

Use Role 1's reconciliation to replace legacy snippets with exact referenced evidence or regenerate the draft. Unresolved/transformed/ambiguous quotes remain unverified and cannot count as verified issue support. Preserve the original artifact and transformation notes; a non-match alone does not prove fabrication.

Offline diagnostics characterized the code; live model generation and live Cypher were not checked. Your independent suite uses fake adapters, followed by source-resolved live draft/review examples.

## Interfaces to Deliver

```python
extract_signals(scope, evidence_provider) -> list[Signal]
build_evidence_pack(signal, scope) -> EvidencePack
draft_requirement(signal, pack, planning_scope) -> RequirementDraft
answer_review_turn(context, question, tools) -> ReviewSuggestion
get_pending_suggestion(turn_id, transaction)
mark_suggestion_applied(turn_id, revision, transaction)
```

Inject evidence provider, fake/real model adapter, backend scoring-preview and requirement-writer interfaces, and audit sink. Your services can run without the full API, UI, or external model in tests.

`RequirementDraft` contains title/description/problem, source and target scope, SMART metric IDs/value/unit/operator/conditions/test method, milestone and feasibility provenance/review state, finding/claim citations, factor proposals/reasons, alternatives, assumptions and uncertainty. It contains no final status/priority chosen by the model.

`ReviewSuggestion` contains cited answer, gaps, allowed proposed patch, base revision and pending state. Backend computes the score preview and validates/appends the proposal; model-supplied final numbers are not trusted.

## Implementation Details

### Extraction and retrieval

- Get topic buckets and authoritative DISTINCT counts from Role 1.
- Select bounded, documented discovery examples (up to 24 per bucket) with available scope/source diversity and counterexamples; record selection limitations.
- Include feedback IDs with original text in model input. Require one keyed output per requested input; reject unknown/duplicate IDs. Incomplete output produces partial results, not positional misassignment.
- Validate spans as exact original substrings. Transformed/translated text cannot masquerade as original verbatim.
- Save signal versions/direct evidence edges, assignment methods and source/run versions through the agreed repository interfaces.
- Keep bucket frequency, discovered issue support, and unassigned candidate counts distinct.
- Retrieve up to eight supporting/four opposing spans, mapped native study/sales facts, three equipment and three strategy passages. No unsupported competitor benchmark or architecture claim.

### Draft generation

- Use OpenAI behind the canonical configuration/adapter, with Pydantic schema validation.
- Preserve baseline JTBD/metric output where useful, but add explicit target/scope/provenance/test fields.
- Require input signal ID in each returned draft; model order has no meaning.
- Tag source-backed versus AI-proposed versus PM-entered targets. Exact latency/SOP/architecture/commercial uplift needs a source or explicit proposal/assumption label.
- Confidence/Kano/MoSCoW are assessments, not measured customer classifications. Show rationale and avoid claiming Kano survey results.
- Generation calls Role 3's writer. Regenerating a known derivation proposes a new revision/diff; it cannot overwrite approved/rejected records or reset their decisions.
- Existing 250 outputs are unverified legacy proposals; do not relabel them validated after parsing JSON.

### Study context

Use the reviewed mapping and native metrics from Role 1. Preserve source G68 separately from G60; do not pool regions/scales into an unsupported average. A high aggregate satisfaction score and a complaint can coexist. Classify broad context, direct support, contradiction, or missing evidence; let Role 3 apply the approved ECI policy.

### Contextual review and tools

Registered read-only tools: search feedback, get signal evidence, get survey facts, get sales volume, search documents, get pinned requirement context, preview allowed change.

Limit each turn to six tool calls and four model round-trips including repairs; cap result sizes. Record supplied IDs/versions, tool input/results, visible answer and model/prompt metadata. Evidence text cannot instruct tool execution.

Allowed patches: description/SMART/target scope/assumptions/evidence/factor assessments. Forbidden: status, actor, revision, source text, calculated score and audit history. Use an agreed whitelist of JSON-pointer operations; all patches receive backend validation and preview.

Each thread is bound to a signal/requirement. Reload current revision on follow-up; mark outdated patches stale. A read-only answer or discarded suggestion leaves the requirement unchanged. Applying is a separate backend request and approval is another request.

## Run, Cache and Failure Behavior

- Provide progress events/stages to Role 3's background job runner: import_check/consolidate/retrieve/draft/validate/score/completed.
- Persist queued/running/succeeded/partial/failed state and truthful errors/results.
- Cache by source/signal/scope/context/model/prompt/config versions; return existing IDs without replacing PM revisions.
- Bound provider retries and structured repair. Keep valid results when another draft fails.
- Missing credentials produces unavailable or labeled genuine cached output, never fake success.
- No network/model work inside a Neo4j write transaction. Role 3 persists the validated result atomically.

## Verify Correctness Independently

```bash
python -m pytest tests/intelligence
```

Use fixture evidence and fixed model/tool responses. The [shared fixture](01_DATA_AND_EVIDENCE.md#shared-synthetic-fixture) yields SIG-FIXTURE-001 with two supporting comments, one positive counterexample, all-powertrain cohort three. A metric of at most two actions and a fixture milestone are synthetic proposed targets, not BMW facts.

Required tests:
- Return signals/drafts in reverse model order: assignments still match their input IDs.
- Unknown/duplicate IDs and altered/invented quotes fail validation; source locators remain resolvable.
- Long category names do not collide; reruns retain previous signal/requirement versions.
- Broad-topic count cannot become narrow support; missing China evidence remains a gap.
- Proposed target remains unreviewed; unsourced competitor/architecture claims are labeled or rejected.
- Weak/contradictory evidence stays visible without unjustified upgrade.
- Tool limits terminate; model write/approval attempts cannot invoke such a tool.
- Cached generation preserves PM changes; partial errors do not claim complete success.
- Read-only review never calls mutation; stale suggestion is not applied; callback uses caller transaction.
- A legacy model SMART=true/empty-gap record remains unverified until its claims/targets receive independent evidence and PM review.
- An opposing or wrong-region/native-scale study never silently increases evidence strength; weak proposals remain discoverable.

Then run one real generation and one real challenge, resolving each citation with Role 1. Keep live model checks separate from account-free unit tests.

## Timing and Handoff

By 45 minutes: ID/keyed-output/draft/review contracts and fake model fixtures. By hour 3: one real validated draft available to backend. By hour 6: scoped context and initial sample list. By hour 8: bounded review assistant with pending diff. Final hours: verified live cases/failure handling.

Handoff: engine functions, DTO examples, model setup without secrets, fake tests, source-validated live draft/review, known limitations. Send payloads to Role 4; frontend widgets are their responsibility.
