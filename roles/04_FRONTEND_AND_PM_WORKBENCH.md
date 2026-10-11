# Role 4 — Frontend & PM Workbench

## Exact Role

Own the complete interactive PM experience using the existing Streamlit UI. **No database access, model calls, score calculation, or backend decision rules.** Your app renders server state and sends explicit user actions to Role 3's API.

Success: independently demonstrate the complete workflow against fixture API responses, then substitute the live API and repeat it without changing business logic in the UI.

Use [the plan](../IMPLEMENTATION_PLAN.md) and [Role 3's API responsibilities](03_BACKEND_AND_GOVERNANCE.md). Relevant branch findings are embedded here; [the original review](../AI_ENGINE_BRANCH_REVIEW.md) is supporting history. Baseline `pipeline/08_streamlit_ui.py` at `3bd0251` already supplies dashboard, requirements/detail, conflict/gap explorer, and export pages; complete that interface.

## File Ownership

- `pipeline/08_streamlit_ui.py`: all PM-facing pages, interactions and integration.
- New `ui/client.py`, `ui/components.py`, `ui/fixtures.py`: HTTP adapter, rendering helpers and explicitly labeled synthetic previews.
- `tests/frontend/*`: mocked-API/client and Streamlit interaction checks.
- UI styling/local assets and presentation-friendly layout. Coordinate private screenshots with Role 5.

Role 3 owns config/compose/API and publishes example responses. Request API changes with a payload example; do not patch Neo4j or shared backend files to make the UI work.

**Shared environment:** use [.env.example](../.env.example) and [CONFIGURATION.md](../CONFIGURATION.md). Consume `API_BASE_URL` through the shared reader/client; do not declare another API URL or dotenv file. Native `STREAMLIT_SERVER_ADDRESS`/`STREAMLIT_SERVER_PORT` are forwarded before CLI startup by Role 3's launcher/Compose. Frontend does not require or receive the OpenAI key.

## Branch Repairs Assigned to This Role

Task IDs match the plan; backend validation remains Role 3's responsibility.

| Task | Priority / ownership | Observed behavior at `3bd0251` | Implementation and acceptance proof |
| :--- | :--- | :--- | :--- |
| **R09** | P0, lead with Role 3 | `08_streamlit_ui.py:26` uses localhost and ignores the supplied container host. | Configured API_BASE_URL, local/container client test and live Compose request; container UI reaches the api service on canonical API_PORT instead of itself. |
| **R01/R03** | P0, frontend integration | Decision UI permits unreviewed approval; override 0 means disabled; guard errors are not integrated. | Collect revision/rationale/target/assumption confirmations, show server-allowed actions, explicit override enable toggle and policy scale. Failed decision stays unapproved; explicit zero sends 0 rather than null. |
| **R16** | P1, label/rendering support | Explainability calls arbitrary impact perturbations a confidence interval. | Render backend range kind/assumptions as sensitivity/provisional bounds; never label it statistical confidence. |
| **R17** | P1, visibility support for Role 2 | Legacy export has zero WEAK/RED records despite an honesty-focused dashboard. | Show an actual engine-returned evidence-gap proposal and its source/target limitations; do not fabricate a weak case or hide it through default filters. |
| **R19** | P1, lead UI integration with Role 3 | Export calls summary list with limit 200 although 250 proposals exist. | Server full export independent of table pagination; mock 250 records and verify download contains all records and evidence/assumption fields. |
| **R07/R15/R18** | P0 source proof for R07; P1 target/history completion | Quotes lack source IDs, SMART flags are model assertions and all artifact histories are empty. | Render original clickable citations, proposed/unverified badges and actual history; empty legacy history must not be presented as complete traceability or approval proof. |

Additional interface checks: list returns `gate` while detail uses `gate_color`; normalize through one client adapter. API failure versus an empty successful result must remain distinct. No frontend callback may compensate for missing backend guards or run model/score/database logic.

### Baseline to Display Honestly

The reviewed output is 250 unverified proposals (49 F70, 102 G60, 99 G70), all with empty histories. Of 750 quote occurrences, 212 do not exactly match original model text; matching quotes still lack exported original IDs. Display backend validation state rather than treating every quote/SMART flag/GREEN gate as verified evidence or human approval.

Current 0–10 scores use a different policy from planned v1 0–100. Include policy/scale in responses and rendering, including override bounds. Live containers were not validated during review; the networking fix requires a real UI-to-API smoke check.

## Implementation Details

### HTTP client and fixture mode

- Centralize GET/mutation/download/polling calls in `ui/client.py` with base URL, timeout and typed normalization.
- Parse API error envelopes; show 409 stale-revision reload/compare, 422 invalid review inputs, and unavailable server/provider states.
- Keep credentials/provider keys on the backend; UI only uses API configuration.
- Inject a fixture client for independent work. Reuse `contracts/fixture_case.json` and label the preview synthetic. Production UI does not silently fall back to fixtures after API failure.
- Streamlit reruns are read-only unless the user explicitly submits a form/button; do not trigger generation, apply, approval or freeze on rendering.

### Requirements list/workbench

Table: rank/ID/title, scope, calculated/effective priority, evidence level, uncertainty/conflict badges and status. Filters include derivative, region, powertrain, status and evidence. Use pagination/load-more rather than hiding records beyond the initial limit.

Selected requirement panels:
- Requirement/problem and editable SMART fields, target provenance/review state, alternatives/assumptions.
- Score factors/contributions and source-linked rationale; display full provisional range, legacy versus v1 scale and overrides.
- Evidence: exact quotes/cells/pages, support/opposition/context labels, native survey values, bucket versus issue counts, coverage gaps.
- Review assistant: visible cited conversation, retrieval/run state, proposed before/after diff and server score preview.
- History: derivation and content/evidence/score/rank/status changes with actor/time/reason and version diff.

All backend-facing actions carry the current expected revision where applicable. Keep unsaved edits in session state keyed by requirement/revision; a stale response must not replace newer server data or discard the user's draft silently.

### Human-in-the-loop controls

1. Start review or edit explicitly; opening a panel is not a status change.
2. Ask a scoped chat question; display cited answer and gap notes.
3. Apply/Edit/Discard a pending suggestion. Applying sends the accepted diff and rationale; it does not approve.
4. After application, reload detail/history/ranking from the server. An approved edited item should show PM_REVIEW.
5. Approve separately: collect target/milestone review and accepted assumption IDs plus rationale, pinned to the current revision.
6. Reject/postpone/reopen through allowed server actions; show unavailable actions clearly.

Do not display approval optimistically before server success. Natural-language “approve this” can open the explicit decision form, not directly execute an assistant write.

### Sources, jobs and export

- Sources view shows real source versions, row/comment counts, parsing issues and coverage; open exact source record/page via API.
- Run analysis submits a bounded scope and polls its run ID without duplicate submissions. Show real successful/partial/failed/cached states and progress.
- Complete sample export and G1/audit downloads come from server endpoints, not reconstructed table data.
- Review package shows only selected approved revisions and accepted/open validations. Freeze errors remain visible.
- Clearly separate cross-source rate differences, actual supported conflicts and missing regional evidence.

### Presentation quality

Use consistent BMW-inspired navy/blue/neutral/amber styling, clear readable quotes and concise score explanations. Avoid spending the window rebuilding a second React UI or adding decorative graph/Kanban pages. The primary delivery is the existing Streamlit interface, completed and polished.

## Verify Correctness Independently

```bash
python -m pytest tests/frontend
```

Use mocked HTTP responses and Streamlit's AppTest where useful. Fixture client state simulates revisions/decisions but is not the production source of business rules.

Required cases:
- Fixture list/detail/evidence/score/history render without a real API, database or model.
- Docker/local API URLs are configuration-driven; gate/status/score-version field adapters are consistent.
- Empty versus failed/partial/cached results are distinct; no fabricated successful live state.
- Opening/refreshing a view sends no mutation.
- Edit/apply/approve sends the correct selected ID/revision/actor/rationale; apply and approve are different requests.
- HTTP 409 preserves draft and offers reload/compare; 422 shows specific review errors.
- Server approval failure leaves UI unapproved; confirmed success refreshes history/list/rank.
- Explicit zero override works; v1 0–100 and legacy 0–10 are never unlabeled/mixed.
- Export request is independent of the table's 200-row limit and uses full server downloads.
- No missing China feedback is rendered as a claimed touch/physical preference.
- A 250-record mock export retains all required nested evidence/assumption fields; the visible page limit does not truncate it.
- Empty legacy history, unverified quotes, model SMART flags, sensitivity bounds and an evidence-gap case have accurate labels; a GREEN gate never renders as PM approval.

Live check with Roles 2/3: generate/select a real requirement, open source evidence, inspect score, challenge/apply, approve, inspect history and download snapshot. In Docker, verify the UI reaches the API container. Inspect mutation requests and confirm refresh creates no duplicate decisions.

## Timeline and Handoff

By 45 minutes: agree API examples/error/version shapes and fixture client. By hour 3: fixture-backed complete list → detail → edit/decision flow; connect the first real API result. By hour 6: evidence/score/history/sources/jobs. By hour 8: chat/diff controls and package downloads. Final hours: live walkthrough, stale/error cases, layout polish and demo rehearsal.

Handoff includes frontend/client, fixture mode, passing checks, configuration/startup notes, live workflow recording with Role 5 and known UI gaps. All backend/AI repairs are requested from their owners rather than implemented in UI callbacks.
