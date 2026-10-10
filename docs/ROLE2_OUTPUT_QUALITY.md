# Role 2 output-quality assessment

## Conclusion

The original structurally valid outputs were **not sufficient as PM-ready proposals**. Revised
outputs are suitable for the application's **evidence-backed, human-reviewed prototype workflow**
on the tested synthetic cases. They are proposals, not engineering-approved requirements, population
research findings or proof that all BMW topics/generalizations work.

This assessment includes direct reading of saved outputs, corrected deterministic regression tests
and real OpenAI calls. The stronger model helps, but neither a model nor JSON schema independently
proves semantic entailment or engineering feasibility.

## Before / after

| Dimension | Original mini-model output | Revised behavior |
| --- | --- | --- |
| Evidence lineage | Exact references after initial repairs | Retained; issue support/counterexamples remain explicit and source-resolvable within the fixture |
| Customer requirement | Generic 'easily locatable'; sometimes copied JTBD | Declarative task-specific requirement, separate originating problem and JTBD |
| Acceptance test | 80% plus time/sample thresholds buried in prose | Numeric comparator/value/unit, operational definition, starting/completion state, assistance policy and threshold rationale |
| Extra quantities | Hidden/unexplained numeric conditions | Separate typed parameters with purpose, value/unit, provenance and review state; unit mismatches rejected |
| Feasibility | Complaints cited as proof of current capability | Unassessed until explicitly reviewed capability evidence exists; Engineering-owned assumption |
| Milestone | Q2 2024 despite current planning date | Named G1 gate, date to be confirmed; arbitrary calendar commitments/past years rejected |
| Prioritization proposals | Safety escalation and uncited maximum strategy fit; invented effort | Cited severity/strategy proposals; unknown strategy/effort stay null and provisional |
| Counterexamples | Sometimes omitted, or misclassified as another problem/support | Exact opposing evidence and explicit claim relation; not asserted to be a regional preference conflict |
| Alternatives | Empty list | Multiple plausible proposed solution routes; no assertion that they already exist |
| Assumptions | Empty/generic statements | Typed target-validation, implementation and scope-carryover assumptions with UX/Engineering/PM owners |
| Review | Useful scope recommendation, but China absent from structured gaps | Cited read-only evidence inspection, structured gaps retained, pending previewed diff; no mutation/approval |
| Model authority | Invalid human-provenance choices possible in generic schema | Generated schemas exclude PMEntered/accepted; server checks remain independent |

## Model selection and observed trade-off

`gpt-5.4` was confirmed available using the account's model list and selected through canonical
`OPENAI_MODEL`. The default/template is now `gpt-5.4`.

- Medium reasoning draft calls exceeded the 45-second request budget. They are recorded as failures,
  not replaced with synthetic successes.
- Low reasoning produced more precise task definitions, source/scope distinctions, opposing findings,
  honest unknowns and owned review assumptions.
- GPT-5 reasoning with function tools requires the Responses API in the tested provider. The adapter
  uses that endpoint, `store=False`, and encrypted continuation replay only in memory. Legacy/classic
  OpenAI models retain Chat Completions; there is no other-provider fallback.
- This is an observed prototype comparison, not a controlled statistical model benchmark. Prompts,
  contracts and validators were also improved, so the gains cannot be attributed solely to the model.

## JSON templates and correctness layers

The templates are generated from Pydantic models in `contracts/intelligence.py` rather than manually
maintained JSON-shaped prompt examples:

1. **Closed schema:** required fields, types/enums, finite values/ranges, extra-field rejection.
2. **Provider strict output:** extraction/drafting schemas are converted to OpenAI's strict subset.
   Requested ID vocabularies and batch-size bounds are attached. Scope, allowed provenance/review state
   and assessed-factor evidence references are constrained for each request.
3. **Independent evidence validation:** keyed ID binding, exact original quote/offset membership,
   citation vocabulary/version/scope, native values and counts. Real fact metadata such as forecast
   year is included; cited comment counts are derived from distinct referenced assignments, not buckets.
4. **Proposal-quality validation:** operational metrics/reasons, typed numeric parameters and units,
   reviewed evidence for capability/targets/milestones, owned assumptions, distinct alternatives,
   honest unknown factors and safeguards against unsupported outcome/safety assertions.
5. **Bounded repair:** one quality correction is allowed, logged and fully revalidated. It cannot
   turn invented evidence, unknown IDs or fabricated PM review into success.
6. **Review boundary:** dynamic JSON-pointer values use JSON mode plus typed local/allowed-path checks
   and mandatory backend validation/preview. Schema-valid edits are not applied or approved by AI.

Source-backed rationale is a direct supplied statement; inferred benefits must be AIProposed. Native
fact `reviewed_usage` and passage `reviewed_claim_types` are supplied review metadata, not model flags.
Source matching establishes traceability; membership/interpretation remains unreviewed until confirmed.

Example acceptance-target fragment (synthetic proposal, not a BMW benchmark):

```json
{
  "id": "TGT-001",
  "value": 2,
  "unit": "actions",
  "operator": "<=",
  "conditions": "Parked prototype, default home screen, no coaching",
  "test_method": "Count every deliberate input, including repeats, until the changed temperature value is visible",
  "measurement_definition": "Total input events required to complete a temperature adjustment",
  "rationale": "Short interaction path proposed for UX validation; no measured benchmark supplied",
  "parameters": [],
  "provenance": "AIProposed",
  "review_state": "unreviewed",
  "evidence_ids": []
}
```

## Agent and tool instructions

`services/agents.py` defines three distinct roles with purpose, inputs/outputs, instructions,
prompt version, output model and budgets. Their instructions explicitly distinguish original
evidence from hypotheses, forbid decision authority, and require schema-conformant output.

`services/tool_registry.py` defines all seven tool use-cases with parameter descriptions, source/scope
requirements, interpretation limitations, result caps and read-only boundaries. Closed read-tool
arguments use strict provider schemas; preview values remain dynamically validated. Tool handlers
must honor their supplied read-only contracts; the registry alone does not prove a handler's purity.

## Verified results

On Python 3.14.7:

```bash
python -m pytest tests/intelligence
# 129 passed, 15 opt-in live tests skipped

python -m pytest tests/intelligence/test_live_openai_fixture.py --live-openai-fixture --live-no-retries -v -s
# 4 passed in 104.05s, actual gpt-5.4-2026-03-05 / low reasoning
```

The final live run covered:

- G60/US feedback extraction: two supporting comments, one counterexample, exact source spans.
- G60/US+CN requirement: proposed interaction target, concrete test procedure, cited findings,
  China carryover review, unknown complexity and proposed G1 gate.
- Tool-assisted scope challenge: cited explanation and pending US-only patch; requirement unchanged.
- F70/EU/ICE storage requirement: two assigned supporting comments from a topic bucket of 100,
  97 unassigned candidates; missing strategy/forecast/capability stay visible. Fixed synthetic memberships
  were input; drafting was live. A missing dwell-time parameter was rejected and corrected by the one
  allowed quality repair, with all quantities still proposed/unreviewed.

Private final report: `var/cache/live-fixture-checks/ac0e79ae-872f-4cbd-adfd-057c00f44319.json`.
It contains actual provider response IDs, usage/latency, outputs, tool/context records and repair events.
Earlier failed/partial attempts are retained too. Checkpoints are written after each response/error.

After this quality assessment, nine more curated live evaluations were added and verified. They
revealed limits that the four core checks did not: rich multi-draft timeout/cross-citation, occasional
offset arithmetic and missing-preview-tool ambiguity. OpenAI drafting now isolates signals by request,
model offsets are constrained to null, and the assistant is explicitly told that the server previews
all returned allowed patches. See `ROLE2_LIVE_COVERAGE.md` for the scenario matrix and run evidence.

## Human review and further evidence

PM/UX must validate targets, participant/device plans, starting states and task success definitions.
Engineering must refine any physical motion/disturbance protocol and establish implementation
feasibility/effort. PM must accept or resolve market/powertrain/successor carryover assumptions.

Remaining prose can be polished, and synthetic cases do not establish the model's accuracy across all
BMW topics. Actual source ingestion/reconciliation, reviewed mappings, stable issue-key reconciliation
across differently named model outputs, authoritative scoring, transaction proofs and UI review are
separate integrations documented in `ROLE2_HANDOFF.md`. Accepting a proposal never substitutes for
the separate human approval gate.
