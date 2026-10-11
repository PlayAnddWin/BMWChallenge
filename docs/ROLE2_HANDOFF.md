# Role 2 implementation and integration handoff

## Branch and reuse

`role-2-ai-review-assistant` was created from `main`. No engine branch merge was performed.
The implementation retains the useful extraction/drafting batching boundaries, BMW analyst
prompt context, and JTBD structure from reviewed snapshot `3bd0251`. It replaces positional
binding, quote-only snippets, ordinal identities, automatic evidence-tier upgrades, and
model SMART self-certification with validated callable stages.

The application specialist agents are the extraction, drafting and review prompt/service
paths. The generation workflow is deterministic Python orchestration; contextual review
can choose registered read-only tools within a bounded loop.

## Shared-contract handoff

DTOs currently live in `contracts/intelligence.py` so Role 3 can coordinate their eventual
integration into `models.py`. Root `config.py` is the initial canonical reader, ready for
Role 3's shared runtime integration. It loads root `.env` with process precedence, resolves
relative paths from the repository root, validates ports/API URL, and hides secrets in repr.
It does not fail fixture/UI imports because the OpenAI key is empty.

### Role 1 evidence and signal storage

Implement `EvidenceProvider` from `services/interfaces.py`:

- `get_topic_buckets(scope, limit=24)` returns distinct canonical original comments, source
  versions/locators, authoritative bucket/cohort counts, and selection limitations. The
  limit is **per bucket**; examples are capped at 24. Selection should include available
  source/scope diversity and counterexamples. The engine never describes it as representative.
- `get_survey_facts(topic, scope)` returns `StudyContext`: reviewed relation, reason, mapping
  version, source derivative/region and native scale/distribution. Unknown sample size stays null.
- `get_sales_facts(scope, year=2030)` returns actual facts, with shared G60_G68 line identity
  and absent powertrain splits. The backend deduplicates vehicle-line/region/year keys.
- `search_document_chunks(query, scope, document_kind, limit=3)` returns exact text with
  original language and page/market applicability, not generated summaries.

`SignalRepository.save_version` reuses identical immutable content or appends a new version.
It persists direct evidence assignments and their method/review state, and preserves earlier
versions. Source-to-row/annotation relationships must remain resolvable. The in-memory
repository used in tests demonstrates this port, not a production Neo4j implementation.

Provider schemas require null quote offsets. The engine resolves a unique exact original-text match
into stored start/end positions deterministically. Repeated phrases require a longer unique quote
or fail as ambiguous. Repository/fixture DTOs can still supply explicit offsets; incorrect supplied
offsets and altered/invented text are rejected. Polarity is relative
to the stated issue: complaints support its existence and positive counterexamples oppose it.

Signal IDs hash the **full normalized source scope, full category and semantic issue key**.
They never depend on model output order or category prefixes. Agree reviewed issue-key reuse
and reconciliation before production extraction: a model choosing a different semantic key
for the same issue can still create a new identity. No fuzzy title-based merging is performed.

Extraction returns `BatchResult[Signal]`, rather than a bare list, to retain missing/failed
input IDs and `succeeded/partial/failed`. Each group has a keyed result with zero or more
issues; an explicit empty issue list is different from an omitted result. Unknown/duplicate
group IDs invalidate that batch. Invalid spans/ranges invalidate their group; other valid
groups are retained. The discovery counts are lower bounds, not exhaustive frequencies.

### Role 3 generation/scoring/jobs

Inject `RequirementWriter`, `AuditSink` and `ProgressSink` into `run_analysis`:

```python
from config import get_settings
from services.analysis import run_analysis
from services.cache import FileDraftCache
from services.llm_client import OpenAIAdapter

settings = get_settings()
result = run_analysis(
    signals, planning_scope, run_id,
    evidence_provider=evidence_provider,
    model=OpenAIAdapter(settings),
    writer=requirement_writer,
    audit=audit_sink,
    progress=checkpoint_progress,
    config_version=scoring_config_version,
    draft_cache=FileDraftCache.from_settings(settings),
)
```

The writer receives the validated draft, complete signal assignments/counts, bounded evidence
pack, stable derivation key, context cache key and run ID. Counts in a snippet pack are not
scoring counts; calculate target/cohort-specific counts from authoritative assignments.
The backend owns ECI, scores, ranking, stable requirement IDs and decisions.

Updated proposal DTOs expose operational metric definitions/rationales, provenance-tagged numeric test
parameters, claim relations (`supports/contradicts/context`) and owned assumptions. `FactorProposal.value`
can be null when genuinely unassessed; do not coerce it into a model-chosen or verified midpoint.
The backend applies its own provisional midpoint/range policy. Native facts have `reviewed_usage` and
passages have `reviewed_claim_types`: these are supplied review metadata, never model-created authority.
Without those tags, observed survey/sales values cannot become sourced acceptance targets and general
feedback/strategy cannot establish capability, architecture or planning commitments.

- `get_cached(cache_key, run_id)` returns current identity/application information, not a
  cached requirement snapshot that would overwrite PM changes.
- `submit(...)` calculates authoritative scores and atomically creates a draft or stores a
  pending regeneration diff. A known derivation must not overwrite approved/rejected content.
- Derivation identity includes signal ID, target scope and purpose. Source/signal revisions,
  retrieved context, model, prompt, engine and config versions go into the cache key.
- File caches contain validated proposed drafts with model/prompt/run metadata; they are
  revalidated on read and never grant PM review. Fixture caches retain the fixture model label.
  The backend writer remains responsible for current revision/decision preservation.
- Stages emit `import_check/consolidate/retrieve/draft/validate/score/completed`. Input signals
  are already consolidated; the first stages indicate entry into this generation workflow,
  not a performed raw import. Default cap is 12 drafts, hard cap 18. Exceeding a requested cap
  produces explicit partial results rather than silently omitting signals.
- The job runner persists queued/running/terminal states, checkpoints and interruption
  recovery; the standalone engine returns terminal results and emits events.
- All model/retrieval work completes before entering the writer's managed transaction.
  Signal version and audit atomicity are also the repository/backend implementation's obligation.

Drafts have no model-selected final status, priority or confidence. Generated targets,
milestones and feasibility assertions must stay unreviewed; `PMEntered` cannot be generated.
Source-backed metrics match a supplied native fact value/unit. Documentary capability,
competitor, packaging and strategy assertions require applicable documentary references.
Unprovided numeric source-backed claim assertions are rejected; proposed numbers remain
explicitly unreviewed. Citation/number checks establish traceability, **not semantic entailment
or engineering feasibility**. Human review remains necessary.

Study context never calculates or upgrades evidence strength. Wrong-scope/unreviewed mappings
remain unmapped, opposition remains explicit, G68 remains G68, and scales/regions are not averaged.
Successor carryover, missing regional evidence, missing forecasts and whole-line powertrain
proxies are surfaced as limitations. The draft retains known gaps even if model output omits them.

### Role 3 review and transactional application

Implement `ReviewBackend` and inject a `ToolRegistry` of real read-only handlers:

1. Reload thread binding/current revision at each turn and mark older pending suggestions stale.
2. Run at most six tool invocations and four model rounds, including validation repairs.
3. Persist cited answer, model/prompt/context, tool inputs/results/source versions, errors,
   budget limits, proposed patch and backend preview before returning to the UI.
4. A read-only answer records a turn only. A patch is pending; a mid-turn revision change
   makes it stale. Backend validation/preview is mandatory, even if the model used no tools.
5. Applying is a later backend request, separate from approval.

The registered names are `search_feedback`, `get_signal_evidence`, `get_survey_facts`,
`get_sales_volume`, `search_documents`, `get_requirement_context`, and
`preview_requirement_change`. Handlers return `ToolResult` with bounded data, resolvable
evidence IDs and source versions. They must implement parameterized/read-only retrieval;
the registry does not itself implement Cypher or prove a handler's purity. Pinned requirement
context/preview calls must use the selected ID and revision. Tool results are capped at 16 KB.

The local patch policy permits add/replace/remove under `title`, `description`, `smart`,
`target_scope`, `assumptions`, `evidence_links`, and `factor_proposals`; it rejects protected
fields, metadata paths and nested authority claims. Title follows plan Section 5.6; coordinate
this whitelist with the backend and Role 2 guide. The backend must additionally validate its
exact revision payload schema, resolve new evidence, reset affected review state, and calculate
the preview. These root checks do not replace authoritative JSON-pointer/schema validation.

`get_pending_suggestion(turn_id, transaction, backend=...)` and
`mark_suggestion_applied(turn_id, revision, transaction, backend=...)` use the **caller-provided
transaction**. They create no session/transaction and make no model calls. The backend must
validate/lock the requirement revision, reject stale or conflicting applications, provide
idempotent retries and write suggestion/revision/score/ranking/audit changes atomically.

The OpenAI SDK has a 45-second single-item request timeout and a 90-second multi-item allowance,
with at most two transient retries. OpenAI drafting isolates one signal per request, even when the
caller submits multiple signals; this prevents cross-signal evidence vocabulary leakage and preserves
aggregate partial results. Batch extraction remains keyed and bounded. Extraction allows
one malformed-output repair. Single-draft generation allows one proposal-quality repair with all
detected omissions recorded through the audit sink; batch callers opt in with `repair_quality=True`.
The quality path reserves at most two model calls per draft. Invalid IDs, invented evidence and
claims of PM entry/completed review fail without quality repair. Every repaired draft is fully
revalidated; unresolved failures stay failures. Review repairs share its four-round budget.
Missing credentials fail explicitly; validated existing caches remain usable and are labeled cached.

Closed extraction/draft DTOs use OpenAI strict structured outputs, with requested ID vocabularies
and batch-size limits. Draft source/target scopes are constrained in the provider schema where
applicable and checked locally. Review's dynamic JSON-pointer values use JSON mode plus local
patch/citation validation. Registered read-tool arguments use strict provider schemas where closed;
dynamic preview patches remain locally/backend validated. Classic chat models use temperature zero.
The default `gpt-5.4` uses low reasoning via Responses API, `store=False`, and stateless encrypted
tool-round continuity held only in adapter memory. Medium reasoning exceeded the request budget in
the evaluated draft case. This policy improves the observed quality/latency trade-off without
making model interpretation deterministic. Model/API policy is part of generation cache identity.

All three application agent descriptions/instructions are centralized in `services/agents.py`;
all seven tool descriptions specify when to use them, required scope/version, result interpretation,
limits and read-only behavior in `services/tool_registry.py`.

## Verification

```bash
python -m pytest tests/intelligence
python -m tests.intelligence.demo
python -m pip check
```

The account-free suite covers reverse order, unknown/duplicate/missing IDs, exact offsets and
source locators, invalid/nonfinite ranges, same-prefix category identities, immutable signal
versions, bucket/support/cohort separation, weak/opposing/native-scale context, China gaps,
target provenance, unsupplied claims, partial runs, PM-preserving cache handoffs, corrupt caches,
protected/nested patches, tool limits, unknown execution tools, citation validation, stale
suggestions, caller transactions, config precedence and an end-to-end synthetic slice.

Current default verification: **129 passed, 15 opt-in live tests skipped** on Python 3.14.7. Fixtures and fake
backend tests verify engine behavior and handoff contracts; they do not prove Neo4j rollback,
real source ingestion, backend scoring or HTTP/UI integration. Real OpenAI has separately been
checked on the synthetic fixture, as described below.

### Standalone live OpenAI checks with synthetic evidence

```bash
python -m pytest tests/intelligence/test_live_openai_fixture.py --live-openai-fixture -v -s
```

Uses only the canonical private OpenAI key/model, synthetic source fixture and in-memory Role 2
test boundaries. It does not require a backend adapter, API server or database. It exercises real
extraction, real structured drafting and real tool-assisted review. Live provider metadata and
outputs are saved privately under `BMW_CACHE_DIR/live-fixture-checks/`, including outputs from
failed validation attempts.

Verified on 2026-10-10 with `gpt-5.4` (provider snapshot `gpt-5.4-2026-03-05`), low reasoning:
**4 passed in 104.05 seconds**. That run used six real responses, including one successful bounded
quality repair for a missing storage-test dwell-time parameter. The HVAC extraction assigned two
supporting comments and one counterexample; drafting retained the China gap, owned assumptions,
unreviewed targets and unknown effort; review returned a cited pending US-only patch without
mutation. An additional F70/EU/ICE storage case checked weak support, unassigned candidates, missing
forecast/strategy/capability and a non-HVAC verification protocol. Its memberships were supplied
deterministically; drafting was real OpenAI. Diagnostic SDK retries were disabled for the recorded run
using `--live-no-retries`; production remains bounded at two transient retries.

The earlier mini-model's structural smoke pass was not a quality acceptance result: manual review
found unsupported feasibility, a past milestone, uncited factor estimates and compound test criteria.
See `docs/ROLE2_OUTPUT_QUALITY.md` for the assessment and corrected verification criteria.

### Curated robustness evaluations

```bash
python -m pytest tests/intelligence/test_live_evaluation.py --live-openai-evaluation --live-no-retries -v -s
```

Nine additional scenarios have passed in focused verification runs: batched extraction, mixed-topic
praise/friction, feedback injection, isolated bulk drafting with disjoint evidence, read-only questions,
approval/score-write requests, tool-result injection, G68/CN/native-scale reasoning, and revision-aware
follow-up with stale suggestions. Reports are checkpointed under `BMW_CACHE_DIR/live-evaluations/`.
The evaluations exposed a batch timeout/cross-citation problem, ambiguous preview instructions and
intermittent offset guessing; the corrections are documented in `docs/ROLE2_LIVE_COVERAGE.md`.

Core-flow checks plus curated evaluations provide 13 synthetic scenarios. They are not a claim that
every model behavior or BMW topic is covered. The two source-resolved hooks below remain separate.

Earlier live attempts exposed duplicate issue/group outputs, guessed offsets, omitted target
provenance, scope drift, an uncited counterexample and a survey-distribution component incorrectly
claimed as a source-backed acceptance target. Validators rejected invalid outputs. Fixes clarify
issue-relative polarity/consolidation, resolve unique quote offsets deterministically, enforce
closed provider schemas/identity/scope constraints, and distinguish observed survey context from
proposed future acceptance targets. The final passing run and failed attempts remain inspectable
in private reports; they are not evidence of real BMW-source or authoritative scoring validation.

### Source-resolved live checks

After Roles 1/3 provide real adapters and the private canonical OpenAI key:

```bash
python -m pytest tests/intelligence/test_live.py --live --live-factory team_integration:role2_bundle
```

The trusted factory supplies a dict containing:

- `scope`: DTO/dict planning scope.
- `evidence_provider`, `signal_repository`, `requirement_writer`, `audit`: real adapters.
- `resolve_source(source_reference)`: independently reads the original row/cell/page and returns
  `{"source": reference.model_dump(), "text": original_text}` or the original native `value`.
- `review_for(requirement_id)`: returns `(review_backend, bound_review_context, tool_registry)`.
- `resolve_citation(id)`: resolves a stored cited item to its source, including `source_version`
  and `locator`.

The tests bound discovery to one real bucket, generate one live draft, resolve spans/facts/pages,
then request a scoped live challenge with a pending backend preview and resolve its citations.
They are skipped unless explicitly enabled; enabling without adapters fails instead of falling
back to fixtures. A shared live case may reuse the generated requirement between the two tests.

## Remaining integration work

Role 1 implements real evidence/reconciliation and reviewed mappings. Role 3 coordinates
shared DTO/runtime/dependency integration, durable jobs, Neo4j repositories, scoring, revision
writes and transaction proofs. Role 4 consumes backend payloads. The legacy 250 exported
proposals remain legacy/unverified; this branch imports or relabels none of them.

Adapters for `pipeline/04_extract_signals.py`, `05_correlate_studies.py` and step 06 should be
added after the shared baseline/provider/writer contracts are wired, keeping drafting and
scoring/write ownership separate. No second database or frontend is introduced here.
# Integrated runtime

The real-data adapters and default API composition are now implemented in
`services/intelligence_backend.py`. See [ROLE2_INTEGRATION.md](ROLE2_INTEGRATION.md)
for startup and the small real OpenAI/Neo4j checks. The original independent-role
contract and verification notes below are retained as implementation history.
