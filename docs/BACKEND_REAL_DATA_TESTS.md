# Backend acceptance with the supplied BMW corpus

`tests/backend/test_real_bmw_data.py` runs Role 03 against the original eight
files: five Excel workbooks and three option-catalog PDFs. A corporate report
is optional. It uses Role 01 conversion and its transactional Neo4j loader.

The checks cover original workbook/PDF references and rejection of altered
hashes, source IDs and locators; source APIs and byte-identical PDF downloads;
retrieval APIs with actual counts and scoped survey/cohort provider queries;
priority reach using sourced 2030 sales cells; and requirement creation,
approval, freeze, JSON export, audit-chain integrity and repository reload
with original feedback snapshots. The suite verifies 10,128 distinct comments
and 13,371 annotations through the loader's known-count validation.

Governance tests use explicitly authored proposals, signal identifiers, SMART
targets and PM actions. The production `RealEvidenceResolver` builds snapshots,
distinct support and cohort counts through Role 01's provider. It leaves
unsupported strategy/severity/complexity assessments unknown. Reach remains
unknown unless a specific PM-authored whole-line or successor proxy policy is
supplied; those policies require explicit assumption acceptance and retain
provisional bounds rather than inventing a derivative or powertrain split. The
production `Role01SourceProvider` adapts HTTP scopes to Role 01 queries;
`Role01ImportService` delegates conversion and transactional writes to Role 01.
These checks establish the Role 03/01 integration contract. They do not certify
production Role 02 generation, automatic finding consolidation, strategy
inference, live/cached model behavior, or a Role 04 browser workflow.

The real reference tests exposed a contract mismatch: Role 01 nodes carry
`source_file_id`, while Role 03 expects `source_id`; PDF chunk locators reside
on their parent source records. `GraphSourceProvider` now normalizes the source
identifier and reads the parent locator after checking source/version identity.

Use disposable loopback Neo4j servers. Keep backend corruption tests on a
separate database from the imported corpus: the data loader creates uniqueness
constraints, while corruption tests intentionally insert duplicate IDs.
Provide enough transaction memory for the complete idempotent reimport
(the acceptance run uses a 2 GiB heap and a 1536 MiB transaction pool).

```sh
BACKEND_TEST_NEO4J_URI=bolt://127.0.0.1:17691 \
BMW_NEO4J_TEST_URI=bolt://127.0.0.1:17690 \
BMW_BACKEND_REAL_DATA_URI=bolt://127.0.0.1:17690 \
BMW_BACKEND_IMPORT_URI=bolt://127.0.0.1:17692 \
.venv/bin/python -m pytest tests -q --tb=short
```

Credentials default to the shared settings for the real-data tests and to
`neo4j`/`bmwpassword` for existing backend graph tests. The real-data server
retains imported evidence; governance test nodes use UUID namespaces and are
removed after the test. Never point these checks at an application database.

Historical six-case smoke verification on 2026-10-11: **1,128 passed, zero
skipped or failed in 49.41s**. That total predates the expanded acceptance below;
the expanded final verification is recorded below.

## Expanded acceptance matrix

This matrix follows [Role 03](../roles/03_BACKEND_AND_GOVERNANCE.md),
[Role 01](../roles/01_DATA_AND_EVIDENCE.md), [the implementation
plan](../IMPLEMENTATION_PLAN.md) Sections 4, 7–10 and 12, and the
[challenge specification](../CHALLENGE_SPECIFICATION.md).

| Requirement | Real-source acceptance and independent oracle | Tests |
| --- | --- | --- |
| R07/R08/R14: authoritative conversion and lineage | Production conversion/import, original source records, exact extracted page spans, original PDFs and hashes; native G68/CN and US distributions preserve values and unknown samples. | `test_real_bmw_data.py`, `test_real_retrieval.py`, Role 01 `tests/data` |
| R11: 2030 reach and honest proxies | Every supplied sales region/year resolves original cells. Fixed normalization uses the complete supplied 2030 total independent of request. G60/G68 share one line; derivative/powertrain proxies remain explicit candidates. | `test_real_retrieval.py`, `test_real_scoring.py`, `test_real_governance.py` |
| R12: distinct counts and ECI | All F70/G60/G70 × US/EU/CN × all/BEV/ICE scopes compare provider aggregates to original IDs/annotations. Every populated topic bucket independently recomputes customer bounds and ECI. Opaque labels remain opaque; empty CN cohorts remain gaps. | `test_real_scoring.py`, `test_real_retrieval.py` |
| R13/R15/R16: honest uncertainty | Native study cells/distributions remain unmapped without reviewed mappings. Unknown severity, strategy, complexity and unsupported reach remain provisional. Real evidence does not certify proposed targets; bounds are sensitivity, not statistical confidence. | `test_real_scoring.py`, `test_real_governance.py` |
| R01/R03: human review and overrides | Real sources across five populated model/region cohorts require PM identity, current revision, source/target review and assumption acceptance. Invalid/nonfinite overrides fail without writes; zero is an explicit override. | `test_real_governance.py` |
| R02: atomic mutations | Decision, edit, suggestion application and freeze audit failures preserve complete graph state. Concurrent PM actions have one winner. Application retries do not duplicate revisions. | `test_real_governance.py` |
| R04: preserved decisions/revisions | Authored regeneration with real references retains approved content; pending suggestions require explicit application and cannot approve. Same-version graph corruption cannot be laundered by refreshing a revision; only authoritative Role 01 reimport restores admissible evidence. | `test_real_governance.py` |
| R18: reconstructable audit | Original snapshots survive approval, export, reload and immutable freeze. Checkpoint verification rejects altered events. HTTP history retains explicit PM actions. | `test_real_bmw_data.py`, `test_real_governance.py` |
| R19: complete exports | Real-source JSON/Markdown freezes, CSV fields, decision/history and stored snapshot equality. A 250-original-comment batch proves complete API JSON/CSV export beyond the 200-row list limit. This bulk export uses MemoryRepository for bounded setup cost; the other governance mutation cases use live Neo4j. | `test_real_governance.py`, existing export regressions |
| Source/retrieval API | Plural scopes, alias deduplication, original annotations, unsupported-filter rejection, exact source retrieval and truthful unknown PDF applicability delegate to Role 01. | `test_real_retrieval.py` |
| Import transaction | `Role01ImportService` prepares outside the governance transaction and uses the exact supplied transaction for Role 01 writes. Unsupported subset imports fail explicitly. | `test_real_import.py` (seven live cases verified), `services/role01_import.py` |

Topic buckets exercise policy arithmetic; they do not automatically become
narrow issue assignments or prove customer preferences. Distinct comments are
not proven distinct customers. The corpus has no CN free-text cohort and no
reviewed native study-topic mappings. Whole-line forecasts have no derivative
or powertrain split. PDF catalog text does not establish target-market
applicability. The optional strategy report is absent in the supplied eight-file
corpus, so strategy remains unknown. Tests preserve these gaps.

Runtime/Compose/networking/restart verification in other documents retains its
original date and scope. The historical R10 provider-migration requirement is
separate from this real-data task; this acceptance pass does not change provider
selection merely to increase real-source test counts.

Focused scoring/provider verification on 2026-10-11: **39 passed, zero failures
or skips**, in 7.76 seconds on the isolated corpus server. This covers 27 cohort,
four sales-region and eight native-survey scope partitions. All work
remains local and uncommitted; nothing is pushed.

## Final verification

On 2026-10-11, the complete serial suite ran with all four Neo4j environment
variables enabled: **1,430 passed, one failed, zero skipped**, in 8,297.38 seconds.
The single failure was the lifecycle test's old expectation of HTTP 200 for an
unimported Role 01 corpus. The strict adapter correctly returns HTTP 409; the
test now asserts that contract. The updated lifecycle test and the Office
temporary-file discovery regression both passed in a final **two-test rerun**
(12.71 seconds). The latter was added after the full run's collection.

Thus **all 1,432 currently collected tests have passed**, including **310
expanded real-data backend acceptance cases**, with no remaining failures or
skips. This result combines the full run and the two focused reruns; it is not
a claim of a subsequent single clean full-suite run. Live import, authoritative
reimport races, corruption rejection, governance rollback and exports all ran.


## Repair and independent review rounds

Four fresh review agents compared implementation and acceptance with Role 03,
Role 01, the plan and repository documents. Earlier synthetic suites remain
unit and adversarial regression coverage; their count is not presented as
real-source acceptance.

1. The first review found import schema setup used the default database while
   source queries/commit could use another repository database. Database routing
   now reaches Role 01 schema setup and is regression checked. It also flagged
   unreachable direct powertrain reach: the explicit proxy policy now handles
   reviewed whole-line/successor assumptions, required IDs and provisional bounds.
2. The second review found same-hash source payload corruption could be refreshed
   and approved, a gap between source preflight and governance commit, and pooling
   retained logical-workbook versions. Production checks now compare graph data,
   original locators and lineage against Role 01 conversion of SHA256-verified
   files, reject ambiguous versions, and reject malformed/missing/duplicate native
   evidence. Role 01 imports and source-dependent governance commits share the
   EvidenceCorpus lock/generation. Transaction-bound providers use staged originals
   and perform no original-file reads or conversion inside the write transaction.
   Four real reimport race cases cover create/edit/approve/freeze admission.
3. The third review found no additional actionable production defect and
   independently passed 117 scoring/export regressions. It required documentation
   to distinguish the real 250-source export's in-memory governance setup from
   actual Neo4j mutation/reload/rollback coverage.
4. The fourth review found no additional actionable defect in source integrity,
   corpus admission, shared transactions, forecast proxies or exports, and
   independently passed 44 offline scoring/export tests.

The acceptance oracles compare imported outputs to Role 01 conversion and direct
source hashes/page spans/cells. This establishes integration and lossless lineage;
it does not independently prove every parser interpretation. Role 01's own
conversion tests remain the parser checks. Scoped tests cannot prove every possible
state/timing or privileged database tampering schedule.

## Runtime integration

Set `BACKEND_ADAPTER_FACTORY=services.role01_runtime:build_adapters` to wire the
production Role 01 source/retrieval, evidence resolver and transactional import
adapters. No Role 02 generator is fabricated. The import adapter converts via
Role 01 before the transaction, then delegates to Role 01's public commit API
using the backend's exact transaction/audit sink. Import failure retains a failed
run without source/audit/results; successful runs retain counts and provenance
under `import_metadata`. Subset imports fail explicitly because Role 01 imports
the configured corpus as a whole.

Backend-owned additions are in `services/real_evidence.py`, `role01_import.py`,
`role01_runtime.py`, `evidence_transaction.py`, source adapters, API/history exports,
scoring/governance guards and `tests/backend/test_real*`. Role 01 integration edits
are confined to transaction preparation/commit/generation in its existing loader,
public evidence query/provenance/integrity APIs, the three-PDF required-input fix,
and related data tests/documentation. Its parser and graph model remain authoritative.
