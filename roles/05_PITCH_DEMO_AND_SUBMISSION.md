# Role 5 — Pitch, Demo, Acceptance & Submission

## Exact Role

Own the judge-facing story and delivery: pitch, demo script, manual acceptance, verified claims, rehearsal/Q&A, backup recording and final artifact checklist. Work alongside the four builders from the start. No implementation ownership is assigned to this role.

Success: deliver a rehearsed 3–5 minute presentation supported by a real working decision loop and all four BMW deliverable bundles.

Use [BMW's exact deliverables](../CHALLENGE_SPECIFICATION.md#7-expected-deliverables) and [the implementation plan](../IMPLEMENTATION_PLAN.md). This guide embeds the review facts and demonstration consequences; [the original review](../AI_ENGINE_BRANCH_REVIEW.md) is supporting history.

## Starting Evidence and What the Pitch May Claim

Reviewed baseline: `origin/ai-requirements-engine` at `3bd0251`, an existing Neo4j/FastAPI/Streamlit pipeline with signal/draft generation, PM screens and exported artifacts. The presentation should show the team's actual validated improvements to this baseline.

| Observed artifact fact | Implication for the demonstration |
| :--- | :--- |
| 250 proposals: 49 F70 / 102 G60 / 99 G70 | A substantial candidate list exists; count is not validated requirement quality. |
| All 250 requirement histories are empty | Capture an actual derivation/edit/decision history after backend repairs; the existing export cannot prove human review. |
| 750 quote occurrences: 538 exact model-text matches, 212 non-matches; zero exported feedback-ID lineage | Every showcased quote needs original ID/location and issue assignment verified with Roles 1/2. Non-matches may reflect preprocessing; do not call them fabricated or verified without investigation. |
| 218 self-report all SMART flags true; 225 declare no gaps | SMART flags/absence of declared gaps are model outputs, not feasibility or evidence certification. Show proposed targets and accepted assumptions. |
| Zero WEAK/RED requirements in the artifact | Select a genuinely weak/evidence-gap case from the corrected engine; do not claim existing filtering demonstrates honesty. |
| Legacy priority arithmetic matches for all 250 records | Arithmetic consistency is verified, while policy/scope/input validity still needs repair. State whether the demo uses legacy 0–10 or v1 0–100. |

Sixteen offline diagnostics characterized code and reproduced defects. Live Cypher, containers, raw-to-processed conversion and model generation were not validated by that review. Claim successful end-to-end execution only after recording the team's real run and acceptance evidence.

### Repair Verification You Coordinate

Task IDs match the implementation plan. Builders own fixes; you track user-visible proof in `docs/QA_LOG.md` and `docs/DELIVERABLES_CHECKLIST.md`.

| Task group | What to verify before presenting | Owner to obtain proof from |
| :--- | :--- | :--- |
| **R07/R08/R14** | Real heterogeneous inputs, preserved annotations, native G68/study evidence and exact original locators | 1, with 2 for issue assignment |
| **R05/R06/R13/R15/R17** | Keyed/cited generation, labeled proposed targets, scoped corroboration and visible genuine evidence gap | 2 |
| **R01/R02/R03/R04/R18** | Explicit PM approval, rationales, stable revisions, failed-write rollback and reconstructable history; regeneration preserves decisions | 3 |
| **R10/R11/R12/R16** | Tested startup/provider, source-based scoped scores, honest ECI and sensitivity labels | 3, using 1/2 inputs |
| **R09/R19** | UI connects to API in the actual deployment; full export is not capped at 200 and contains all BMW fields | 4 with 3 |

Ask for a run/requirement/revision ID, test result or exported artifact for each claim. Use unresolved items to choose a truthful demo scope and explicit limitation, not a stronger unsupported story.

## Owned Artifacts

| Artifact | Purpose |
| :--- | :--- |
| `docs/PITCH.md` | Narrative, slide outline and speaker notes |
| `docs/DEMO_SCRIPT.md` | Timed clicks, expected states and fallback route |
| `docs/DELIVERABLES_CHECKLIST.md` | Each BMW deliverable → actual artifact/location/owner/verification |
| `docs/WORKFLOW.md` | Judge-friendly source-to-decision diagram and AI/PM authority |
| `docs/QA_LOG.md` | Reproducible acceptance findings and resolution status |
| `var/presentation/` | Private deck, screenshots and recording containing BMW evidence |

These are artifacts to create during implementation, not files already delivered by this role guide. Keep internal quotes/data/screenshots in the private bundle; public narrative docs can use synthetic examples or source identifiers.

## Responsibilities and Coordination

- Role 1 validates data/coverage/citation claims.
- Role 2 validates what AI actually does and provides a real generation/challenge example.
- Role 3 validates scores, human gates, revisions, audit and exported package.
- Role 4 supplies the live UI route and helps capture the real walkthrough.
- You track promised functionality against actual behavior and report blockers early.

Builders own code/tests/runtime repair. Your QA does not replace their tests, and you do not invent stronger claims to hide unfinished work.

**Shared environment:** startup and delivery use [.env.example](../.env.example) and [CONFIGURATION.md](../CONFIGURATION.md). Obtain export/presentation locations and tested startup instructions from Role 3; do not create a presentation-specific env file or show credentials in screenshots. The model provider for the final demo is OpenAI.

## Four BMW Deliverable Bundles

1. **Concept + prototype:** running app, explanation of customer/product-manager value, startup instructions, real heterogeneous inputs → generated requirement → PM decision.
2. **Structured sample list:** actual CSV/JSON plus UI containing unique ID, actionable description, linked findings/sources, priority/rationale, evidence level, future assumptions/uncertainty and status.
3. **Workflow:** diagram and responsibility table showing autonomous AI analysis and mandatory human review/apply/approval gates.
4. **Audit trail:** one demonstrable chain from sources/consolidation through scores/ranks, content/evidence edits and human decisions; actor/time/rationale and before/after revisions.

The frozen G1 review package and audit manifest support these bundles. Three thoroughly verified cases are more persuasive than an unsupported claim that all 250 legacy drafts are validated. The team sample target is 12–18 verified/proposed requirements as evidence allows, not a BMW minimum.

## Pitch Implementation

Prepare a roughly four-minute story:

| Time | Content |
| :--- | :--- |
| 0:00–0:35 | PM problem: fragmented evidence, inconsistent prioritization, hard-to-reconstruct decisions |
| 0:35–1:05 | Product/workflow: evidence-backed requirement workbench, AI proposes, PM decides |
| 1:05–2:45 | Live loop: cited requirement, score explanation, challenge/change, explicit approval, history/export |
| 2:45–3:25 | Trust: one genuine uncertainty/coverage gap, how validation and human authority handle it |
| 3:25–4:00 | Measured impact, scalability/carryover limits, deliverables and next step |

Use the rubric: decision/scoring 25%, workflow 20%, traceability 20%, usability/feasibility 20%, innovation/impact 15%. Spend most demonstration time on a decision and its evidence, not architecture terminology.

### Claims and Impact

Measure actual elapsed time to a cited draft and to review one requirement. Record run ID, sample count, live/cache mode, start/end and limitations. Traceability coverage has a defined numerator/denominator; do not infer it from self-reported SMART flags.

Explain potential acceleration of planning activities, not elimination of the entire two-year development process. Do not claim calibrated confidence, guaranteed feasibility, competitor proof, tamper-proof infrastructure, or validated regional preferences when only proposals/heuristics exist.

Branch review facts suitable for truthful setup: pipeline/API/UI already existed; the team strengthened evidence validation and governance. Claims of implemented fixes must be demonstrated after they actually pass the acceptance checks.

## Demo Script and Three Cases

Select after inspecting real evidence:

- Strong support with relevant corroboration.
- A real trade-off or alternative solution supported by evidence.
- A valuable proposal with an explicit future/regional/feasibility gap.

For the main case record exact requirement/run/revision IDs and expected UI states:
1. Open the requirement and originating need.
2. Follow its source citation to an original quote/cell/page.
3. Explain the largest score factors and unknowns.
4. Ask a scoped challenge and inspect the cited proposed diff/score preview.
5. Apply the change with rationale; verify the new revision remains under review.
6. Approve explicitly after reviewing targets/assumptions.
7. Show content/evidence/score/rank/status history and export the approved snapshot.

Choose cached real model results as a labeled backup. A recording is a recording; fixture output is synthetic; do not present either as an unobserved live generation.

## Manual Acceptance and Correctness Verification

Start when the first hour-3 loop is available. Use a QA record with severity, steps, expected/actual outcome, owner, entity/run/revision IDs and resolution evidence.

| Check | Expected outcome / accountable builder |
| :--- | :--- |
| Heterogeneous inputs | Original quote, survey, sales and document records resolve / 1 |
| AI source honesty | Every demonstrated citation resolves; proposed metrics labeled / 2 with 1 |
| Score explanation | Factors reproduce score; correct scope/policy version shown / 3 |
| Apply versus approve | Application creates a new review revision, not approval / 3 and 4 |
| Stale/error behavior | Conflicting edit is surfaced; no false success / 3 and 4 |
| Complete audit | Initiator/time/rationale and before/after changes visible / 3 |
| Export completeness | All BMW fields present; not truncated at table limit / 3 and 4 |
| Freeze stability | Later live change does not alter exported approved snapshot / 3 |
| Coverage honesty | Missing China data/derivative confounding shown correctly / 1, 2 and 4 |
| Runtime and rehearsal | App starts, reaches backend, real sources display, walkthrough fits limit / 3 and 4 |

For repaired behavior, record both the original counterexample and the passing result: rejected AI approval; unchanged state after injected audit failure; preserved approved revision after regeneration; exact source citation; full 250-record fixture export. Distinguish a synthetic acceptance test from the actual BMW showcase case and a mocked check from a live graph/container check.

P0: cannot start/demo, invented/unresolvable evidence, automatic AI approval, lost/unlogged decisions or missing required deliverable. P1: missing important workflow/field or misleading score/coverage. P2: presentation/layout polish. Report with reproducible steps, not a vague “broken” message.

Independently verify deck completeness against the four exact BMW deliverables before the live app is ready. Later capture one actual artifact for each promise and rehearse with a person unfamiliar with implementation.

## Timeline and Final Handoff

- First 45 minutes: narrative, slide skeleton, deliverable checklist, case-selection questions.
- Hours 1–3: workflow diagram/Q&A draft; start manual checks as the first real loop appears.
- Hours 3–6: source-check cases with builders, time runs/review and refine story.
- Hours 6–8: lock demonstration route and collect real exports/screenshots.
- Hours 8–10: rehearse, record backup, resolve major demo blockers, verify bundle.
- Hours 10–12: final rehearsal and submission/presentation readiness.

Done when the deck fits 3–5 minutes, the demo and backup are truthful, all four bundles exist in the correct private/public locations, open limitations are clear, and the team can answer “where did this requirement/score come from, who approved it, and what changed?”
