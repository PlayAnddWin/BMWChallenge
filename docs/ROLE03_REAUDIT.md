# Role 03 independent re-audit — 2026-10-10–11

This review supersedes earlier claims that no further concrete backend defect was found. It checks the role assignment, implementation plan, challenge specification, all existing backend documents, production code and test behavior. Repair agents and fresh reviewers are followed by independent parent verification. Existing test totals are historical unless explicitly identified as the final rerun.

## Reproduced defects and repairs

| Severity | Defect missed by the previous suite | Repair and regression evidence |
| --- | --- | --- |
| P1 | Coordinated changes to ledger snapshots and matching graph copies passed integrity checks while disagreeing with the unchanged audit chain. | Audited revision/configuration/ranking/freeze snapshots now bind to their hashed event payloads. Frozen checkpoints and complete audit prefixes bind to the freeze event. Six actual Neo4j corruption partitions verify that reads and writes fail without repairing corrupted data. |
| P1 | Explicit overrides and weight previews dropped the provisional marker from uncited strategy assessments. | One scorer applies strategy annotations consistently across creation, overrides and previews. Enable/disable and no-write preview regressions preserve the warning. |
| P1 | Analysis released shared admission before persisting its terminal state. | Admission remains held through terminal persistence, and releases even on failure. Blocking-write tests check concurrent analysis/import refusal and subsequent recovery. |
| P2 | Legacy retries treated boolean/integer/float substitutions as identical originals. | Canonical JSON comparison rejects changed original types without writes; identical retries remain idempotent. |
| P2 | Colon-containing Neo4j namespaces collided with another namespace's prefix scans. | Invalid namespace identities fail before driver access; namespace delimiters remain unambiguous. |
| P2 | Scoring references accepted zero/negative/text page or row coordinates inconsistent with source contracts. | Page/row coordinates require positive integers. Pure scoring partitions cover customer, strategy and forecast references without unnecessary API setup. |
| P2 | Nonfinite nested run metadata reached persistence and produced misleading server/dependency errors. | Run scopes require finite JSON before admission. Raw HTTP NaN/Infinity partitions return 422 with no writes. |
| P2 | History download headers failed on Unicode legacy IDs and embedded unsafe quote/newline values. | RFC 5987 encoded filenames and a constant ASCII fallback preserve identifiers in downloaded content and valid response headers. |
| P1 | Application history omitted turn/run provenance and the proposed versus accepted patch required by plan Section 8.4. | Application revisions and their hashed events pin the turn/run, base revision and both patches, including PM modifications and generation metadata. Later revisions clear the application marker while preserving immutable history. |
| P1 | Generation had no backend channel for model/prompt metadata, unlike review. | A compatible optional analysis envelope records validated metadata in runs, derivation revisions/events and regenerated proposals/application history. Empty metadata remains an explicit absence of provider proof. |
| P2 | Markdown freeze exports supplied required review information only inside raw JSON. | The readable package now includes an executive summary, frozen priority order, SMART details, citations, scoring/evidence coverage, validations and decision history, followed by one complete manifest. Structured uncertainties and embedded backtick sequences have regression coverage. |
| P2 | Nonfinite nested scorer assumptions caused standalone preview responses to fail with HTTP 500. | Both pure scorers validate annotation JSON. Raw HTTP malformed annotations return 422 without writes; valid nested annotation data remains intact and independent of caller mutations. |
| P1 | Checking events against snapshots did not require each snapshot to have an event; unaudited approvals and other immutable additions could pass with an unchanged checkpoint. | Reverse coverage now requires every revision, configuration, ranking and freeze to match a protected event. Incomplete production-event exceptions were removed; synthetic setup now emits genuine snapshot bindings. Four actual graph addition attacks and atomic repository rejection cases cover this boundary. |
| P1 | Duplicate or reordered ranking snapshots could make a historical baseline current while all individual snapshots still matched their events. | The ranking sequence binds to chronological ranking events, preserving the meaning of the latest baseline as well as each snapshot's content. |
| P2 | The new generation envelope rejected an entire batch when one draft had a malformed shape or nonfinite value, losing valid siblings. | Envelope metadata validates separately; each draft passes through the existing independent validation/error path. Iterable/envelope parity cases preserve partial results. |
| P1 | Mutable proposal copies could be rewritten together with ledger data to forge pending content, application fingerprints/results or lifecycle state without changing the checkpoint. | Turn creation and terminal outcomes bind to protected events and legal transitions. Application results match their immutable revisions; every application requires a terminal audit event. Seven real graph corruption partitions and terminal-event failure rollback cover this boundary. |
| P1 | A requirement reconciled under an original ordinal legacy ID was duplicated when regeneration looked up only the calculated hash ID. | Generation resolves existing semantic identity before creating a new record, preserves the original approved/rejected requirement and produces pending changes against it. Ambiguous identities and colliding reconciliations fail transactionally. |
| P1 | Conversation scope/actor/time could be rewritten in matching ledger and graph copies without an audit record, redirecting subsequent turns. | Thread creation is audited in both standalone and convenience routes; complete stored thread bindings and linked turn scopes must match protected creation events. |
| P1 | Evidence adapters received mutable validated drafts and could mark assumptions/targets reviewed, bypassing the human approval guard. | Resolver inputs and outputs are detached. Adversarial creation, edit, application and preview cases preserve pending review states and reject approval without human acceptance. |
| P2 | SMART and scope validators modified caller-owned nested dictionaries, including on failed validation. | Validators copy nested values before defaulting/normalizing; valid and invalid request boundaries preserve caller input. |
| P1 | A citation verification adapter could rewrite a validated locator/source identity before review persistence. | Verification receives a detached citation; the authoritative validated review and audit retain their original content. |
| P1 | A generation adapter could mutate the validated request to increase its admitted draft limit before the backend enforced it. | Both callback and plain adapters receive detached scope inputs. Bounded-generator tests assert one persisted draft, only two consumed values, an explicit cap failure and unchanged admitted scope. |
| P1 | The legacy transition adapter discarded a supplied current-style body ID and silently preferred the legacy alias, bypassing the route/body identity guard. | Both optional `id` and `req_id` must match the route before adaptation. Conflicting/malformed aliases return 422 with exact state preservation; omitted and matching identities remain compatible. |

The initial repair agent was followed by seven fresh reviewers, with parent verification and additional reproduced findings between rounds. The first repair round's focused governance/repository verification passed 118 tests including real graph cases; its full graph run passed 1,000 tests. Later reviewers reproduced additional defects despite passing suites. The seventh fresh review found no further concrete production defect, independently passed 315 targeted tests, then added seven behavioral acceptance cases for malformed generation envelopes/metadata and admission recovery. Their focused module passed 23 tests. These are intermediate counts; the final verification below covers all repairs together.

## Coverage and redundancy assessment

Coverage counts executed statements/branches; it cannot prove every possible value, combination, timing or external failure. Tests need distinct oracles for policy arithmetic, HTTP contracts, concurrency, actual database rollback and corruption. Overlap across those boundaries is useful; repeated setup around a pure scorer is unnecessary and was removed from the new coordinate tests. A duplicated import was also removed. Full serialized ledger verification remains a documented prototype cost, not demonstrated scalability.

### Role 03 acceptance boundaries

| Required behavior | Behavioral test evidence / remaining boundary |
| --- | --- |
| Validated drafts, SMART and real evidence; human-only decisions and assumption/target review | `test_decisions.py`, `test_decision_review.py`, `test_metric_operator_contract.py`, `test_resolver_isolation_review.py`; synthetic evidence providers do not establish original BMW lineage. |
| Stale revision, legal transitions, edit invalidation, route/body identity and bounded run admission | `test_api_boundaries.py`, `test_api_review.py`, `test_governance_reaudit.py`, `test_resolver_isolation_review.py`, `test_legacy_transition_identity_review.py`. |
| Idempotent suggestion application, regeneration preserving decisions, proposal provenance | `test_review.py`, `test_regeneration_api_lifecycle.py`, `test_turn_integrity_review.py`, `test_identity_thread_review.py`; actual Role 2 provider integration remains unsigned. |
| Exact golden priority/ECI, factor/override validation, uncertainty, forecast scope and stable rank | `test_scoring.py`, `test_scoring_properties.py`, scoring review modules, `test_override_disable_contract.py`; forecasts in acceptance fixtures are synthetic. |
| Transactional revision/audit/rank/turn persistence and protected checkpoints | `test_neo4j_integration.py`, `test_repository_review.py`, `test_fresh_audit_completeness.py`, `test_turn_integrity_review.py`; includes real Neo4j rollback and coordinated corruption. |
| Full exports beyond list cap, original fields and immutable complete freezes | `test_audit_export.py`, `test_fresh_export_review.py`, `test_governance_reaudit.py`; legacy original data is preserved rather than asserted source-validated. |
| Source retrieval/import contracts, startup/provider/driver lifecycle, CLI and published contracts | `test_import_retrieval_contract.py`, `test_runtime.py`, `test_cli_review.py`, `test_contract_artifacts.py`; live model/cache, Compose rerun, actual adapters and UI reachability are separate integration evidence. |
| Durability across actual server restart | `graph_restart_probe.py` prepares an approved synthetic record/checkpoint and compares exact persisted state after stopping and restarting the dedicated server; final execution recorded below. |

The missing integration cases are acceptance gaps: actual Role 1 import/evidence/source adapters and BMW source reconciliation, actual Role 2 generation/review and model/cache evidence, and the Role 4 UI workflow. Passing synthetic adapters, database transactions and health endpoints cannot establish these cases. Backend code is reviewable independently, but the complete role integration and challenge definition of done remain unsigned.

The final structural redundancy check inspected 363 test functions and found no identical AST bodies after stripping docstrings. This is a narrow duplication check, not proof of no semantic overlap. Pure-function/API/actual-database tests retain separate behavioral oracles. `append_event` independently verifies its chain for safe standalone use; its overlap with repository verification remains an explicit cost. Removing that check merely to reduce execution time would weaken the standalone integrity boundary. Full-ledger locking/copies and single-process background jobs remain documented prototype limitations.

Coverage-gap review added reachable malformed generation boundaries rather than tests that fabricate impossible state to execute every defensive line. Unexecuted guards include internal semantic event/index validation, invariant-protected branches, and script entry guards. This review establishes no new concrete production defect in those branches; it does not assert exhaustive possible-state coverage.

## Compatibility requiring explicit reconciliation

Older prototype ledgers with applied review turns lack the newly required terminal application audit event; older conversation threads also lack protected creation events. Such ledgers now fail closed on reads/writes. Original legacy imports without conversations remain compatible. Upgrading an older conversation ledger requires reconciliation against independently retained checkpoints and verified request/application/thread provenance. Silently generating trusted events from mutable stored copies would recreate the corruption vulnerability. A fresh synthetic test namespace is appropriate for new fixture demonstrations, not a substitute for preserving real decision history.

## Final independent verification

- Complete suite after all repairs and the final seven acceptance partitions: **1,097 passed, zero skipped, zero failed**, in 42.07 seconds under coverage instrumentation. All **68 opt-in graph cases** ran against the isolated Homebrew Neo4j 2026.09 / Java 21 server on Bolt port 17687.
- Branch-instrumented production coverage (`services`, `models`, `config`, `pipeline`): **2,364 / 2,380 statements (99.33%)**, **946 / 962 branches (98.34%)**. No source changes occurred during this final measurement. Remaining defensive/entry branches are explicitly not claimed covered.
- Independent focused parent rerun of adapter isolation, legacy-ID guards and published artifacts: **29 passed**. Seventh review's independent targeted suite passed **315 tests**; its two opt-in graph skips were covered by the final complete graph-enabled run.
- `graph_restart_probe.py prepare` saved an approved synthetic requirement; the dedicated server stopped fully and restarted; `verify` matched the exact complete saved state, approved revisions, ranks and audit checkpoint. The probe removed only its synthetic UUID namespace. The test server was then stopped after verification.
- Python compilation, shell syntax, tracked diff whitespace and explicit Python trailing-whitespace checks passed. Published OpenAPI/fixture validation passed in the full and focused suites. The final AST duplication scan inspected **363 test functions** and found no identical bodies.

Reproduction of the complete test/coverage run with an available dedicated graph server:

```sh
BACKEND_TEST_NEO4J_URI=bolt://127.0.0.1:17687 \
  COVERAGE_FILE=/private/tmp/role03-final.coverage \
  .venv/bin/python -m coverage run --branch \
  --source=services,models,config,pipeline -m pytest tests/backend -q --tb=short
```

All **23 reproduced defect groups** above were repaired and verified. The last fresh reviewer established no further concrete defect within the implemented backend boundaries. This is a scoped review conclusion, not an assertion that all possible inputs are tested or that pending full challenge integration is complete.

## Documentation interpretation

BACKEND_REVIEW.md contains dated historical rounds, including limitations later superseded by Docker checks. BACKEND_HANDOFF.md and BACKEND_RUNTIME.md describe the current adapter/runtime contract. STRICT_ROLE03_REVIEW.md describes the preceding review and its measured results; its no-further-defect conclusion is superseded by the findings above. This turn's Homebrew test-server execution does not repeat or certify the previous Docker/Compose evidence.
