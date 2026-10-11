# Role 3 — Backend, Decisions, Scoring & Governance

## Exact Role

Own the authoritative API and decision state: validated requirement persistence, scoring, human review guards, revisions, transactional audit and export. Coordinate shared contracts and runtime integration. **No frontend rendering or AI prompt development.**

Success: the API accepts a validated engine draft, supports safe review/edit/apply/approve, and exports a fully reconstructable approved snapshot. Verify independently using fake evidence/model providers and a test-only graph.

Use [the implementation plan](../IMPLEMENTATION_PLAN.md) with the repair instructions below. They embed the relevant findings from [the original review](../AI_ENGINE_BRANCH_REVIEW.md). Existing baseline is `origin/ai-requirements-engine` at `3bd0251`; extending the current Neo4j/FastAPI service is the implementation target.

## Existing Code and Ownership

- `pipeline/07_pm_review_api.py`: all API composition and hardened decisions; preserve/adapt existing URLs intentionally.
- Scoring/persistence helpers of `pipeline/06_generate_requirements.py`: extract into backend services, agreed with Role 2.
- `pipeline/09_export_report.py`: complete sample and freeze/audit exports.
- Root `models.py`, `config.py`, `requirements.txt`, `docker-compose.yml`, `Dockerfile`, `run_pipeline.sh`, shared constraints and `.gitignore` integration.
- New `services/scoring.py`, `decisions.py`, `audit.py`, `export.py`, API adapters/job infrastructure; `tests/backend/*`.

Own authoritative Requirement/RequirementRevision, scoring config/ranking snapshot, audit and freeze nodes. Roles 1/2 supply their node/DTO definitions and service interfaces. Role 4 owns Streamlit and all UI/client code.

**Shared configuration ownership:** maintain root [.env.example](../.env.example), [CONFIGURATION.md](../CONFIGURATION.md), and the single `config.py` reader. Load root dotenv with process precedence, validate types and resolve paths from the repository root. Wire every declared setting to its consumer; runtime defaults/names must not be copied into team modules. OpenAI is the only LLM provider. Forward API keys only to backend/model processes, and native Streamlit variables before CLI startup.

## Branch Repair Backlog — P0 First

Task IDs match the plan. Keep this priority ordering when delegating backend work.

| Task | Priority / ownership | Observed behavior at `3bd0251` | Implementation and acceptance proof |
| :--- | :--- | :--- | :--- |
| **R01** | P0, lead | `07_pm_review_api.py:143–190` accepts AI_SYSTEM approval with no rationale, arbitrary transitions and conflicting route/body IDs. | Human PM decision guard, state machine, expected revision, trimmed rationale, target/assumption confirmations. AI approval/blank rationale/ID mismatch/invalid transition fail; valid reviewed PM action succeeds. |
| **R02** | P0, lead | Separate auto-commit writes leave status applied when audit fails. | One `session.execute_write` callback for revision/state/score/rank/turn/audit. Inject failure and inspect real graph: all related changes roll back. |
| **R03** | P0, lead | Override 999 is accepted; history writes score_before=None. | Finite/ranged override by score-policy version, actual before/after and effective/calculated values. Reject invalid values; a valid zero override is distinct from disabled override. |
| **R04** | P0, lead with Role 2 | Step 06 resets ordinal IDs and overwrites content/status. | Stable derivation identity, immutable revisions, pending regenerated diff. Changed batch order/rerun leaves approved/rejected decisions and previous content intact. |
| **R10** | P0, lead | Runner demands Anthropic despite primary OpenAI; Compose omits OpenAI variables. | Use canonical OpenAI-only config, forward OPENAI_API_KEY/OPENAI_MODEL and remove Anthropic prerequisite/fallback. Valid OpenAI configuration works; missing key is explicit without selecting another provider. |
| **R09** | P0, support Role 4 | UI container calls its own localhost. | Supply API_BASE_URL=http://api:${API_PORT} from canonical Compose settings; validate actual UI → API networking with Role 4. |
| **R11** | P1, lead using Role 1 | Max hard-coded 2025 market reach and zeroed RoW ignore requested scope. | Source-linked 2030 forecasts, unique G60_G68 keys, fixed reference and proxy notes; scoped fixture reproduces source values. |
| **R12** | P1, lead with Roles 1/2 | Count-based STRONG=1.0 and assumed channel bias miss reliability/coverage. | Versioned ECI inputs for issue support, coverage, corroboration, review and explicit concentration gaps; no probability or unsupported channel-semantics claim. |
| **R13/R15** | P1, policy/review support | Study opposition can inflate strength; model SMART=true is treated as validation. | Consume reviewed mappings and require source/target/assumption review before approval. Keyword match or self-checked SMART cannot satisfy these guards. |
| **R16** | P1, lead; Role 4 labels | Legacy low/high bounds are arbitrary ±15% impact perturbations. | Record sensitivity assumptions or compute documented provisional bounds; response specifies range kind, never statistical confidence interval. |
| **R18** | P1, lead | Audit hash omits payload/previous hash; creation/consolidation/content/evidence events are absent. | Full payload events, immutable versions, protected ledger head and checkpoint verification. Changing actor/rationale/content/score invalidates the chain; creation-to-decision history reconstructs. |
| **R19** | P1, lead with Role 4 | UI exports only summary rows ≤200; CSV omits sources/gaps/assumptions. | Complete server-side export independent of list pagination, full BMW fields and approved immutable snapshot. A 250-record fixture exports 250 with every required field. |

### Baseline Evidence and Validation Limits

The 250 exported priorities recompute correctly under the **legacy formula**; this establishes arithmetic consistency, not a defensible policy or validated inputs. All 250 histories are empty, so the artifact does not demonstrate PM review or derivation/change history. No original feedback-ID lineage is exported; unresolved legacy evidence must block approval/freeze until a real basis is verified.

Sixteen offline diagnostics reproduced current behavior, including defects. Live Neo4j queries/rollback, complete containers, processed-input conversion and live model generation were not validated; no test suite or dependency lock was found. Establish corrected acceptance tests, pin a tested environment, and perform actual graph/Compose checks before signing off the integration.

## Shared Contracts and Integration

Coordinate these in the first 45 minutes in root `models.py` and a linked `contracts/fixture_case.json`:

- Role 1: SourceReference, native facts, EvidencePack, cohort counts.
- Role 2: Signal, RequirementDraft, ReviewSuggestion, progress callbacks.
- Backend: RequirementDetail/Revision, mutation request, ScoreContext/Result, EvidenceResult, AuditEvent, FreezeSnapshot, errors.
- Frontend: sample list/detail/evidence/history/run/suggestion/decision/export payloads.

Use uppercase persisted/API states DRAFT, PM_REVIEW, APPROVED, REJECTED, POSTPONED. Treat existing CONFLICTED as a legacy state to adapt to PM_REVIEW plus has_conflict; conflict and decision status are separate. UI may show friendly labels. Preserve legacy consumers while changing schemas with explicit adapters.

Generation functions and source queries remain behind injected interfaces. Mount backend with fake providers for tests. Publish OpenAPI/example payloads to Role 4 immediately, not after all implementation finishes.

## Persistence and Decision Details

Represent source/run/signal/revision IDs explicitly. Neo4j snapshot properties use JSON strings where nested maps cannot be stored natively. Add uniqueness constraints for stable IDs and composite revision keys.

```python
create_requirement(validated_draft, evidence_context, run_id)
edit_requirement(id, expected_revision, patch, actor, rationale)
apply_suggestion(id, turn_id, expected_revision, accepted_patch, actor, rationale)
decide_requirement(id, expected_revision, action, actor, rationale)
```

- Read current state and acquire/validate revision within the write transaction. Serialize/lock the current requirement as needed; a stale mutation returns 409, not last-write-wins.
- Approved/rejected/postponed records require reopening before further approval; edits create PM_REVIEW revisions and clear current approval.
- Approval requires complete customer-facing SMART content, real underlying evidence, explicit review of proposed targets/milestone, and acceptance/resolution of outstanding assumptions. Model SMART=true is not human validation.
- Pending suggestion is revalidated and applied once by turn ID. Identical retries return the same result; conflicting retries/stale bases fail. Mark the turn applied and save the revision/events in the same transaction using Role 2's repository interface.
- Model calls happen outside database transactions. Backend calculates final scores from authoritative evidence inputs, not submitted model counts.
- Save immutable revision JSON and before/after/rationale metadata. Do not reuse ordinal IDs for unrelated derivations on a rerun.
- Named PM identity is a prototype actor mechanism, not enterprise authentication; avoid claiming otherwise.
- Mark imported legacy records with their provenance/validation state and legacy score version. Preserve originals; source reconciliation/regeneration produces reviewed revisions, never a blanket conversion of all 250 proposals to validated status.

## Scoring and Evidence Policy

Reuse the legacy pure helpers only as legacy-v0 compatibility. The planned policy is versioned v1, 0–100; old 0–10 scores are labeled and never silently mixed.

```text
customer = 0.60 * (severity_level / 4) + 0.40 * sqrt(mean(support_r / cohort_r))
reach = applicable unique 2030 volume / fixed reference volume
strategy = strategic_fit_level / 4
ease = (5 - complexity_level) / 4
priority = 100 * (0.40*customer + 0.25*reach + 0.20*strategy + 0.15*ease)
```

Validate counts, ranges/weights and finite values. Missing regions are gaps; zero/missing cohort is unavailable. Unknown factors use labeled midpoint/range; partial discovery has observed lower bounds and unassigned-candidate upper bounds. Native survey context supports evidence quality, not undocumented cross-scale averaging.

Use sourced forecasts, correct G60_G68 deduplication, explicit powertrain/successor proxy assumptions, and fixed config normalization. Store factor values/contributions/references/reasons and calculated/effective score. Stable tie-break is ID; rejected/postponed items have no active rank.

ECI follows plan Section 7: 35% logarithmic distinct support, 25% regional coverage, 25% direct/broad corroboration, 15% assignment review. Separate contradiction/proposed-target/unknown-sample warnings. A ±15% impact perturbation is a sensitivity assumption, not a statistical confidence interval.

Golden [fixture](01_DATA_AND_EVIDENCE.md#shared-synthetic-fixture): support 2/cohort 3, severity 2, strategy 2, complexity 3, target volume 100/reference 200 → customer **0.6265986324**, priority **55.0639452948**. Coverage/direct corroboration 1 and assignment review 0.5 → ECI **67.2795359447**, Moderate. Display rounding is not stored calculation precision.

## Audit and Exports

Audit covers source import, consolidation, derivation, content/evidence/factors/scores/ranks/status, visible assistant/tool records, applied/discarded suggestions and freeze. Each event includes actor, UTC time, rationale, versions and before/after references. Rank snapshots state their baseline context; UI filtering is not an audited change.

Chain canonical full event payloads with previous hash. Serialize ledger-head updates in the transaction so concurrent writes cannot fork the chain; protect sequence and all decision fields, not only entity/action/time. Provide checkpoint verification; call it tamper-evident relative to a retained checkpoint.

Exports must include every BMW field. Do not use the summary list endpoint or its 200-row cap to export. CSV serializes linked evidence/assumption arrays predictably; JSON retains structure.

Freeze in one transaction: validate selected current approved revisions and baseline context, copy their complete payload/source/config/decision versions, append freeze event and checkpoint, store immutable snapshot. Later live edits do not change it. Complete audit verification uses the full prefix through checkpoint, not only filtered events.

## API Surface and Runtime

Supply list/detail/evidence/edit/decision/history, sources/records/pages, analysis/review job start/status, preview, complete exports and freeze endpoints from plan Section 9. Wrap Role 1/2 services; do not reimplement their parser/prompts. Register static export routes before dynamic ID routes and use a configurable /api alias for the new contract.

Driver creation/closing belongs to application lifespan; use injected driver/providers in tests. Document startup from the reviewed root layout. Test Neo4j health, provider selection, processed/raw data paths and UI container reachability. Preserve union of ignore rules when integrating the branch; private outputs/caches/processed data are not code fixtures.

## Verify Correctness Independently

```bash
python -m pytest tests/backend
```

Use a FastAPI TestClient, fake evidence/model engine and temporary repositories; then run transaction/query tests in a dedicated Neo4j test namespace/database.

Must pass:
- AI approval, blank/whitespace rationale, invalid transitions and route/body mismatch are rejected.
- Negative/999/NaN/out-of-policy overrides fail; zero is a valid explicit override if the policy permits it.
- Stale revision fails; approved edit invalidates approval; apply does not approve; retries do not duplicate revisions.
- Inject audit failure: requirement/status/score/rank/turn state all roll back. Confirm with real Neo4j, not just mocks.
- Regeneration preserves previous approved/rejected content and versioned evidence.
- Golden scoring/ECI, unknown ranges, stable ties and scoped forecast deduplication.
- Altered/deleted/reordered events fail against the checkpoint; creation and evidence changes reconstruct.
- More than 200 records export completely with all required fields; freeze rejects stale/unapproved state and remains unchanged after edit.
- Missing OPENAI_API_KEY yields truthful unavailable/cache mode; OpenAI-only startup works without Anthropic credentials; driver closes cleanly.
- Canonical env keys are loaded only through root config, process overrides win, relative paths are cwd-independent, and native Streamlit settings apply before startup.
- Wrong route/body ID and unverified legacy evidence fail without writes; negative/out-of-range/non-finite model factors fail at the validated draft boundary.
- Legacy 0–10 arithmetic remains reproducible and labeled; v1 0–100 conversion is recomputed from source inputs rather than relabeling the old numbers.

The branch review's 16 passing diagnostics reproduced existing problems; these acceptance tests should assert the corrected behavior.

## Timeline and Handoff

First 45 minutes: shared schemas/fixtures/runtime boundary, API examples and ownership split of step 06. By hour 2.5–3: list/detail, valid draft persistence, scorer and one guarded transactional decision. By hour 6: evidence/edit/review adapters and versioning. By hour 8: complete history/export/freeze. Final hours: Neo4j transaction/restart tests and integration with Roles 2/4/5.

Delegate bounded backend subtasks to agents: scoring, transactional revision/audit, exports, or contract tests. Keep shared models/runtime coordinated. Handoff includes running API, OpenAPI/payloads, tests, migration/adaptation notes and documented gaps; frontend implementation stays with Role 4.
