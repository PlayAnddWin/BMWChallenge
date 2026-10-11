# Final Role 03 review — 2026-10-10

Subsequent independent review reproduced additional defects and supersedes this round's no-further-defect conclusion. See [ROLE03_REAUDIT.md](ROLE03_REAUDIT.md) for current findings and verification; the execution and coverage totals below remain historical.

The review used all three backend documents in `/docs`, `roles/03_BACKEND_AND_GOVERNANCE.md`, `IMPLEMENTATION_PLAN.md`, `CHALLENGE_SPECIFICATION.md` and the interfaces to Roles 01/02/04. Earlier execution counts in BACKEND_REVIEW.md are historical. Multiple delegated repair rounds were independently reviewed and tested again. No further concrete defect was found in the final reviewed scope; this is not a claim of exhaustive correctness or sprint integration sign-off.

## Problems found and corrected

| Problem | Correction and acceptance evidence |
| --- | --- |
| Engine suggestions could mark assumptions/targets accepted and bypass PM review. A mutable engine input could also change the authoritative review base. | Engine inputs are copied; changed/new content starts pending. Only unchanged canonical content retains previous human review. Validation applies at proposal creation, application and regeneration. Governance/review contract tests cover bypass attempts. |
| Python equality conflated boolean, integer and floating-point source coordinates/content. | Canonical JSON comparisons preserve exact types for provenance, evidence, configurations and review content. Locators reject missing/empty/invalid coordinates. Final partition tests cover exact membership. |
| Import adapters could commit independently of governance audit; thin sink events could conceal returned source snapshots. | Neo4j imports use a shared managed transaction and audit sink; unsafe independently committing adapters fail before invocation. Exact deduplication preserves differing metadata. Graph tests inspect actual source/audit/result rollback and successful commits. Role 01 still supplies its production adapter. |
| PDF citations only resolved SourceRecord, and duplicate identities were ambiguous. | SourceRecord and DocumentChunk references resolve with exact source/version/locator; missing or ambiguous identities fail closed. Four added real graph cases check these boundaries. |
| Regenerated proposals lacked retrieval/discard lifecycle; persisted stale flags could mislead clients. | Analysis returns distinct result and pending proposal IDs. Read-only paginated list/detail and guarded, audited, idempotent discard are available without creating unnecessary threads. Reads derive staleness from current revision. Regeneration lifecycle tests check retries and historical outcomes. |
| Requirement lists omitted authoritative ranks and mixed legacy/v1 score scales. | Ranks come from the same baseline snapshot; policy groups remain separate, filters retain global baseline rank, ties use stable identities. |
| SMART metrics lacked the Role 02 comparator contract. | Operator is required and validated; ordered targets must be finite numeric values/text. Equality supports explicit categorical targets. Legacy content remains readable without silently passing current validation. Metric operator tests include 19 cases. |
| Explicit null override did not disable an existing override. | Omitted override preserves it; null clears it; zero enables a zero override. Recalculation, ranking and before/after audit remain coherent. Four HTTP cases verify behavior. |
| Role 01 retrieval contracts and runtime checks were incomplete. | Injected retrieval wrappers validate request/output boundaries and report unavailable capabilities truthfully. Real runner subprocess tests cover both providers, other working directories and missing configuration/data. No Role 01 retrieval implementation or Role 02 model implementation was added. |
| Contracts/deployment evidence overstated what had actually been exercised. | Public synthetic examples/OpenAPI now include analysis/proposal lifecycle and comparators. Linux dependency constraints, clean image build, Compose API/database startup and container HTTP were exercised. Actual adapters/UI remain explicitly pending. |

Earlier corrections also cover the state/action matrix, revision conflicts, atomic decision/application retries, evidence revalidation, scoring/ECI sensitivity, immutable configuration/revisions/freezes, checkpoint tampering, complete exports and private source path boundaries; see the task-by-task matrix in [BACKEND_REVIEW.md](BACKEND_REVIEW.md).

## Final verification

- macOS Python 3.12: **930 passed in 33.45 seconds**, with all **46 real Neo4j tests**, zero skips/failures.
- Fresh Linux ARM64 API image: **930 passed in 12.87 seconds**, using Compose Neo4j 5.20, zero skips/failures. Tests mounted only public test/contracts/runtime files, not private data or credentials.
- Clean API image dependency installation/build, `pip check`, database health dependency, host API HTTP and service-network HTTP to `http://api:8000/api/health` passed.
- Final actual Compose Neo4j restart preserved exact approved revisions, rankings and audit checkpoint. The probe removed only its synthetic namespace.
- Instrumented production sources: **2171/2181 statements (99.54%)**, **848/858 branches (98.83%)**, no exclusions. Scoring, audit, export, providers, configuration and models have full measured statement/branch execution.
- AST inspection found no identical bodies among 312 test functions after ignoring names/docstrings. Independent review found domain, HTTP and graph checks exercise distinct boundaries; no defensible deletion was identified. This does not prove absence of semantic overlap.

Ten statements and ten branch destinations remain unexecuted: CLI module dispatch in export/migration, defensive proposal-list errors/draft iteration, malformed preserved legacy SMART/normalization and compatibility-policy paths, freeze baseline guard, and repository revision-consistency guard. These are disclosed rather than excluded. Coverage cannot prove all possible combinations of values, timing, external faults or future adapter behavior.

The ledger still serializes governance writes and performs full integrity verification. This favors prototype correctness; load/scalability is unmeasured. Use one worker because job admission/recovery is in-process. Removing duplicate citation validation and computing the pinned evidence set once reduces redundant work without weakening boundaries.

## Role boundaries and merge assessment

### Additional independent review

The next fresh review found two remaining contract defects. Application audit events omitted the turn/run reference and proposed versus accepted patches. Resulting immutable revisions now retain `revision_application` with those fields and the base revision; later edits/decisions clear this revision-specific marker while history retains it. HTTP acceptance covers a partial proposal with PM-modified values and an identical no-write retry. The existing real graph rollback test also checks the protected application provenance.

The Markdown export previously showed only titles/descriptions before raw JSON, omitting the readable package required by plan Section 10.2. It now includes executive summary, frozen priority ordering, SMART metrics/milestone, source citations, scoring rationale, evidence coverage, open validations/assumptions and decision history. It retains one exact complete JSON manifest. A synthetic frozen two-record acceptance checks ordering and readable sections independently of the manifest.

Follow-up offline suite: **950 passed, 52 explicitly opt-in graph skips**. The focused real Neo4j application rollback/provenance test passed against localhost 17687. These follow-up runs do not refresh coverage measurements or deployment/live-adapter evidence.

Role 03 implements governance, API orchestration, scoring/ECI, persistence/audit, freeze/export and runtime contracts. Role 01 owns conversion, real evidence/cohort/study/forecast mappings and the transaction-aware source writer. Role 02 owns generation/review, semantic identity, model/tool output and metadata. Role 04 owns rendering ranks, sensitivity, evidence, stale proposals and explicit PM decisions through these contracts. Synthetic adapters verify the Role 03 boundaries; they do not implement the other roles' systems.

The identified Role 03 fixes are implemented and independently verified. Waiting for a merge would not have corrected the defects above. The current backend can proceed to integration review, but complete challenge acceptance requires actual Role 01/02 adapters, original-source reconciliation, live/cached model evidence and the Role 04 workflow. A default health response with unavailable providers or an empty list proves startup, not import success. Original BMW content, actual end-to-end UI use and delivery remain unverified. No commit or merge was performed.
