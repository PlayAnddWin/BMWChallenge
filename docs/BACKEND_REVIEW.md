# Role 3 adversarial review

The subsequent [independent re-audit](ROLE03_REAUDIT.md) records newly reproduced defects, repairs and current verification. Historical passing rounds below do not establish defect-free implementation.

Ground truth: [Role 3](../roles/03_BACKEND_AND_GOVERNANCE.md), with coherence checked against [IMPLEMENTATION_PLAN.md](../IMPLEMENTATION_PLAN.md) and [CHALLENGE_SPECIFICATION.md](../CHALLENGE_SPECIFICATION.md). Review covered the authoritative API, contracts, governance transactions, scoring/ECI, audit, exports and runtime configuration. Review agents corrected findings; the parent independently revisited boundaries and integration evidence.

Acceptance uses explicit partitions: valid actions, every state/action pair, malformed inputs, numeric boundaries, stale versions, identical/conflicting retries, simultaneous writes, provider failures, partial completion, corruption and rollback. Coverage measures executed code; it cannot establish that every possible input combination or defect is absent. The latest measured results are in [STRICT_ROLE03_REVIEW.md](STRICT_ROLE03_REVIEW.md); counts below record earlier review rounds.

## Acceptance and regression matrix

| Role task / requirement | Concrete acceptance and regression evidence | Remaining integration boundary |
| --- | --- | --- |
| R01 human decisions and SMART | `test_decisions.py`, `test_decision_review.py`: full state/action table; AI identity, blank rationale, route/body mismatch, stale revision; nested SMART targets/provenance, zero metric target, valid scope vocabulary and source/target/assumption confirmations. | Role 4 must demonstrate these decisions with actual customer-facing content. Prototype actor inputs are not authentication. |
| R02 transactional revisions/state/rank/turn/audit | `test_neo4j_integration.py`: direct graph snapshots before/after injected decision/application and late materialization failure; one winner for concurrent expected revisions. `test_decision_review.py`: simultaneous application retry and freeze recheck after provider resolution. | Repeat against intended deployment and its actual adapters. |
| R03 overrides | `test_scoring.py`, `test_decision_review.py`: negative, out-of-policy, nonnumeric, bool, NaN/Infinity; explicit zero, calculated/effective distinction, before/after audit; edits clear override. | UI must distinguish zero from disabled override. |
| R04 stable regeneration/revisions | `test_decision_review.py`: order-independent identity, approved/rejected preservation, changed evidence refresh, stale turn cannot resurrect, identical/conflicting retries; explicit legacy import and reviewed reconciliation. | Real engine must supply semantic keys and versioned lineage. |
| R10 provider/runtime | `test_runtime.py`, `test_cli_review.py`: OpenAI-only/Anthropic-only, explicit/auto selection, missing/whitespace keys, pipeline path/config failure and truthful runner checks. | No live provider or complete raw-conversion pipeline demonstrated. |
| R09 API container address | `test_runtime.py` parses Compose provider forwarding and UI `API_BASE_URL=http://api:8000`. | API image build, API/Neo4j Compose startup and container-network HTTP passed in the final round. Actual Role 4 UI acceptance remains pending. |
| R11 scoped 2030 reach | `test_scoring.py`, `test_scoring_review.py`: golden arithmetic, G60/G68 deduplication, conflicting duplicate facts, source locator structure, wrong year/region exclusion, explicit zero forecast, missing scopes, fixed-reference validation. `test_decision_review.py`: immutable config, explicit required proxy assumption IDs and future derivative mapping. | Role 1 must establish actual source cells, fixed total and proxy semantics. |
| R12 ECI | Golden ECI, distinct traceable support IDs, ID precedence over inflated broad counts, logarithmic cap, native regional coverage, review levels, unknown count range, concentration/contradiction/proposed-target warnings and non-probability semantics in scoring suites. | Actual relevant support and reviewed study mappings are authoritative provider responsibilities. |
| R13/R15 evidence/targets | `test_decision_review.py`: source-backed target linkage, current-source revalidation, uncited strategy provisional/blocking, specific proxy IDs; API review tests reject unverified citations and malformed model patches without writes. Opposition warnings do not increase ECI. | Real mapped studies, strategy passages and model citations remain unintegrated. |
| R16 documented sensitivity | Scoring suites cover all unknown factors, known severity/unknown cohort and known mention/unknown severity, discovery candidate upper bounds, regional proportions rather than pooled counts, finite counts/weights/factors and separate legacy scale. | Synthetic cohort/forecast fixtures do not validate real uncertainty inputs. |
| R18 full payload audit and immutability | `test_audit_export.py`, `test_scoring_review.py`: altered fields, deletion/reorder/full rehash against retained checkpoint, malformed checkpoints/events, reserved metadata, serialization failure atomicity. `test_repository_review.py`: immutable revisions/config/freezes/rankings, candidate consistency, native graph index/copy tampering and coherent reads/writes. API review tests retain import, visible tool/citation, stale review and discarded suggestion history. | Source/consolidation events require actual import adapter outputs. Independently retain checkpoints. |
| R19 complete exports/freeze | Export suites exercise more than 200 rows, required BMW columns populated from actual RequirementDetail fields, Pydantic JSON/CSV roundtrip, empty export and nested arrays. Decision suites cover stale/unapproved freeze, source snapshots, policy reconstruction and immutable later edits. | Verify actual BMW locator/content completeness and private delivery package. |
| Shared API/contracts | `test_api_boundaries.py`, `test_api_review.py`: detail/evidence/history/download, configurable prefix/root aliases, sources/records/PDFs, allowlisted path and symlink escape, requirement/signal threads, partial jobs, adapter progress/cache modes, resource errors, citation resolution, provider faults and lifespan ownership. | Real Role 1/2 contracts and Role 4 client integration remain pending. |

## Findings corrected

- Malformed nested SMART/scope/provenance fields, nonfinite payloads and truthy fake traceability flags could reach governance boundaries. Validation now rejects them before writes; source/future derivative mapping and numeric zero targets have explicit coverage.
- Regeneration, current evidence, configuration, stale suggestion reuse and concurrent retries needed stronger semantics. Refreshes remain pending; approval/freeze revalidate current evidence and baseline; retries preserve actor identity and commit once.
- Audit/repository verification previously allowed inconsistent candidates or malformed ledger inputs and did not protect every materialized copy/index. Append-only candidate validation and graph-copy verification now fail corruption rather than repairing history silently.
- Unknown customer subcomponents discarded known information; invalid counts could hide behind a missing partner; whitespace scopes and malformed source references misrepresented reach. Known subcomponents retain documented bounds and malformed input shapes/counts/references fail validation.
- ECI accepted malformed traceability/concentration inputs. Explicit support IDs, region coverage, review collections and concentration counts are validated; opposing evidence does not add corroboration automatically.
- CSV had BMW headers whose values were blank for real backend field names. Actual contract aliases now populate those fields, and real DTO roundtrips verify preservation.
- Review/job/source/runtime edges lacked guard coverage: malformed citations/patches, orphan threads, stale model bases, progress failures, partial adapters, oversized generators, source path escape, driver ownership and unavailable dependencies now have acceptance cases.
- Freeze accepted incomplete opposing/context evidence snapshots. Every evidence relation now requires an exact pinned source version/locator and nonempty raw payload; rejected freezes leave state unchanged.
- Malformed source-provider responses and legacy status objects could produce 500 errors. Invalid adapter shapes/nonfinite metadata now return 503 and invalid transitions return 422. Admission persistence faults release locks and allow a later retry.
- Materialized revision native IDs, revision numbers and ownership relationships were not verified. Real graph tampering tests now establish that reads and writes fail closed, without silently restoring altered lineage.

## Observed runtime and limits

Real graph acceptance uses an isolated **Homebrew Neo4j 2026.09 with Java 21**, not the Compose image. Tests inspect rollback, exact persistence, concurrency, ledger/checkpoint tampering and materialized copies. The separate `graph_restart_probe.py` records exact state across an actual server STOP/START; driver reconnection alone is weaker evidence. Actual API lifespan and HTTP checks are distinct from source import success.

At the original review, the Neo4j 5.20 Compose image was untested because Docker was unavailable. The later Docker verification below supersedes that database-image limitation. Role 4's UI is unavailable; configuration parsing does not establish UI→API networking. Actual Role 1/2 adapters, original-source reconciliation, live model generation and full raw-data conversion are absent. Those are sprint integration blockers, not passing backend tests.

All fixtures used for review are synthetic. Credentials, original feedback, extracted documents, private exports and database files must stay outside committed review artifacts.

Separately, the original baseline's private 250-record `requirements_full.json` passed the explicit legacy migration dry-run. Its summary reported 250 **unverified** records. This verifies artifact compatibility, not source reconciliation, PM approval or live migration. No original artifact contents are included here.

## Fresh review: correctness and unnecessary work

A fresh three-agent review and parent verification found additional defects after the earlier 675-test run. All findings below were reproduced and corrected; earlier passing totals were not treated as sign-off.

| Priority | Problem found | Correction and acceptance proof |
| --- | --- | --- |
| P0 | Engine-supplied resolved/accepted assumptions bypassed human acceptance. | Engine-origin target/assumption review states start pending; explicit PM resolution remains supported. `test_fresh_governance_review.py`. |
| P1 | Each small mutation rematerialized unrelated requirements and every historical snapshot. | Only changed current/mutable records and newly appended immutable records are materialized. Real graph recorder proves identical retries make zero materialization writes and changed progress writes only its job and ledger. `test_repository_review.py`. |
| P1 | Python equality allowed boolean/integer/float payload substitutions to appear unchanged. | Canonical JSON comparisons protect exact immutable values/types and detect mutable changes; graph tampering and rollback cases pass. |
| P1 | Deleting committed turns/runs/threads left orphan materialized graph copies. | Deletion is rejected before commit; tests verify unchanged state. |
| P1 | Multiple reads in one API response could combine different revisions, rankings or history. | Detail/history/thread/preview/dashboard/freeze-audit use one governance snapshot each. `test_fresh_api_review.py`. |
| P1 | Failed convenience challenge admission left an orphan thread. | Thread, challenge run and requested event admit together in one transaction; injected audit failure leaves none. |
| P1 | Import results accepted malformed/versionless snapshots; nonfinite metadata contaminated even the failed run. | Complete results pass finite JSON validation before run assignment; IDs, source versions, signal revisions and duplicate identities are checked. Rejection persists a clean failed run with no provenance events and permits the next job. |
| P1 | Duplicate shared-line forecasts dropped a source reference and depended on iteration order. | Shared volume counts once, every distinct reference survives in deterministic order, and outputs copy caller metadata. `test_fresh_scoring_review.py`. |
| P1 | Accepted near-unity weights could put priority above 100; known-input score and range differed by a rounding step. | Normalize accepted weights, store actual policy weights, use consistent contribution arithmetic and bound the scale. One seeded property test exercises 1,000 mixed inputs, bounds and override independence. |
| P2 | Unchanged generation after human review created redundant suggestions/events. | Comparison separates human review state from proposed content; approved/rejected/postponed and PM-resolved unchanged reruns preserve the entire state. |
| P2 | String flags such as contradiction='false' created false warnings; whitespace-only scoring assumptions passed. | Explicit boolean flags and nonblank assumption descriptions are required. |
| P2 | Health advertised success despite unreadable persistence. | Repository failure returns 503; optional missing model adapters remain separately reported. |
| P2 | Markdown repeated complete requirement JSON before repeating it in the full manifest. | Human headings/descriptions plus one complete manifest preserve reconstructability with one machine-readable copy. `test_fresh_export_review.py`. |

### Redundancy assessment

AST examination of **254 test functions** found no identical bodies after ignoring function names and docstrings. This is an exact-duplication check, not proof that all behavioral overlap is useful. Manual review retained unit/API/real-graph overlap because it checks different boundaries: formula/domain correctness, request serialization, transactional isolation and actual rollback. Negative type partitions and the 25 state/action combinations protect separate contract cases. The 1,000 scoring probes run within one seeded test rather than inflating the case count.

Measured actual Neo4j transactions: identical generation retry or repeated progress uses **10 integrity queries and zero materialization writes**; changed progress uses **12 queries**, including only its changed job and ledger. An unrelated five-revision history remains unchanged. Tampering with unrelated history still blocks a no-op retry without repair. API responses avoid duplicate ledger reads. Full locked graph-copy verification, whole-ledger serialization and ranking snapshots for exact active revision baselines remain deliberate prototype integrity costs; scalability is not established.

## Final verification — fresh review, 2026-10-10

**790 tests passed with Neo4j configured; no skips or failures.** The offline repeat passed 752 tests and skipped the 38 explicitly opt-in graph cases. Source files were frozen during the final instrumented run.

Statement coverage: **99.59%** (1925/1933); branch coverage: **99.05%** (727/734). Combined: 99.44%.

| Component | Covered statements | Covered branches | Combined coverage |
| --- | ---: | ---: | ---: |
| `config.py` | 54/56 | 8/8 | 96.88% |
| `models.py` | 375/375 | 86/86 | 100.00% |
| `pipeline/07_pm_review_api.py` | 0/1 | 0/0 | 0.00% |
| `pipeline/09_export_report.py` | 33/34 | 11/12 | 95.65% |
| `services/api.py` | 527/528 | 177/178 | 99.72% |
| `services/audit.py` | 51/51 | 22/22 | 100.00% |
| `services/decisions.py` | 349/350 | 159/162 | 99.22% |
| `services/export.py` | 52/52 | 22/22 | 100.00% |
| `services/migration.py` | 39/40 | 9/10 | 96.00% |
| `services/providers.py` | 57/57 | 20/20 | 100.00% |
| `services/repository.py` | 126/127 | 67/68 | 98.97% |
| `services/scoring.py` | 262/262 | 146/146 | 100.00% |

Eight unexecuted statements remain: the legacy normalization helper, legacy startup import, CLI module dispatches, and three defensive engine/ranking/revision failures. Seven branches remain, including malformed preserved legacy SMART and the compatibility-policy path. They are disclosed without coverage exclusions. Passing coverage and a finite test suite cannot establish every possible input combination.

Actual server STOP/START after the optimization preserved exact approved revisions, rankings and checkpoint. Refreshed public API fixtures validate current models/OpenAPI and frozen audit hashes. Compilation, shell syntax and diff whitespace checks pass.

Final independent re-review found no further concrete defect within the implemented backend boundaries. Overall Role 3 integration is **not signed off**: actual Role 1/2 adapters, reconciled BMW sources, live generation, Docker deployment and Role 4 UI acceptance are still unavailable/unverified.

Repeat with a dedicated graph server:

```bash
BACKEND_TEST_NEO4J_URI=bolt://127.0.0.1:17687 \
COVERAGE_FILE=/private/tmp/backend-review-rerun.coverage \
.venv/bin/python -m coverage run --branch --source=services,models,config,pipeline \
  -m pytest tests/backend -q --tb=short
COVERAGE_FILE=/private/tmp/backend-review-rerun.coverage .venv/bin/python -m coverage report -m
```

## Role 03 contract follow-up — 2026-10-10

Review turns now retain validated coverage gaps and finite model/prompt metadata in the turn and completion audit; successful runs also retain model/prompt metadata. Existing adapters receive empty defaults. Malformed optional results fail without a successful turn. The shared engine-result schema and public synthetic examples document this boundary.

Suggestion previews retain the existing priority `score_preview` and add complete `change_preview`: authoritative ECI, proposed content, old/new priority and ranks, rank movements, base revision/ranking, and descriptive unresolved source/target/assumption/strategy checks. The same helper serves patch-based `/scoring/preview` without writes. Preview scoring uses the evidence resolver and one coherent governance snapshot; concurrent edits flag saved review results stale. Human confirmation is still required and apply still invalidates approval.

`test_review_contract_fixes.py` adds 12 acceptance cases, including malformed metadata/gaps, audited metadata, read-only/backwards-compatible defaults, no-write coherent previews, stale revision refusal, authoritative score/ECI recalculation, and two-record rejected/postponed rank reentry compared with actual application. Existing stale-during-model-call acceptance also passes. Final full offline follow-up passed **764 tests with 38 graph skips**; the focused contract/regression run also passed. No new real-Neo4j/container/live-provider acceptance was performed for this follow-up; earlier measured coverage totals predate these changes.

Role 1 retrieval signatures and the required shared import/audit transaction are documented in BACKEND_HANDOFF.md. Atomic source import remains unimplemented/unverified: the existing external import adapter and later backend provenance persistence are separate transactions. This follow-up adds no Role 1 retrieval/parser, Role 2 model/prompt or Role 4 UI implementation.

### Graph-enabled follow-up verification — 2026-10-10

The full backend suite passed **802 tests in 12.70 seconds**, with no skips or failures, using `BACKEND_TEST_NEO4J_URI=bolt://127.0.0.1:17687`. All 38 previously skipped graph cases executed against the isolated Homebrew Neo4j 2026.09 server with Java 21. This covers actual transaction rollback, concurrency, persistence, tampering detection, source-provider queries, API lifespan and legacy migration boundaries. The server ran in a persistent foreground session because detached startup did not remain available; localhost access required execution outside the sandbox. Earlier setup attempts failed due to sandbox permissions and unavailable detached-server connections, before graph assertions ran.

This run does not refresh coverage measurements or repeat the separate process STOP/START probe, and does not establish Compose Neo4j 5.20, live Role 1/2 adapters, model generation or UI acceptance. The isolated test server was stopped after verification.

### Docker Neo4j 5.20 verification — 2026-10-10

Installed Docker CLI and Colima, then started isolated profile `bmw-backend-test` without changing the active Docker context. Container `bmw-neo4j-520-test` ran the exact `neo4j:5.20-community` Linux ARM64 image (digest `sha256:52d3dec8d45585b21edeca8517d752b2512e52aa94f80fc36a5788c88c95f8e4`), with authentication enabled, separate localhost ports 17688/17474 and the Compose HTTP healthcheck. Startup logs confirmed Neo4j 5.20.0 and the container became healthy.

With `BACKEND_TEST_NEO4J_URI=bolt://127.0.0.1:17688`, the full backend suite passed **802 tests in 13.31 seconds**, with zero skips or failures. All 38 graph cases executed. The separate `graph_restart_probe.py` prepare/verify sequence spanned a real `docker restart`; exact approved revisions, ranks and audit checkpoint survived. Test fixtures and the probe clean only their synthetic namespaces. The container and dedicated VM were stopped after verification.

This establishes backend compatibility with the pinned database image, health and restart persistence. It does not establish the API Dockerfile build, complete Compose stack, UI networking, actual Role 1/2 adapters or live model generation. Coverage measurements were not refreshed.
