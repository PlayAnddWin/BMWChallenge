# Role 2 real-data integration

The default API now constructs `services.intelligence_backend.Role2Engine` using
the shared Neo4j repository and `GraphSourceProvider`. It consumes the existing
imported corpus; no fixture JSON or external adapter factory is required.

## Start

Keep the existing root `.env` with `OPENAI_API_KEY`, `OPENAI_MODEL=gpt-5.4` and
the working graph/data settings. From the repository root:

```bash
docker compose up -d --build --no-deps api
```

OpenAPI is at <http://localhost:8000/docs>. With the current local defaults:

```bash
curl -X POST http://localhost:8000/api/analysis-runs \
  -H 'Content-Type: application/json' \
  -d '{"derivatives":["G60"],"regions":["US"],"powertrains":["BEV"],"topics":["Air conditioning, setting"],"query":"temperature","max_drafts":1}'
```

Poll `GET /api/analysis-runs/{id}`, then inspect the returned requirement IDs.
Generation is bounded (at most 18 drafts); use a narrow scope and a small
`max_drafts` for interactive work. `use_cache=true` is the default; `refresh=true`
requests fresh interpretation. Actual signal/draft caches live under the ignored
`BMW_CACHE_DIR`, with original source versions and model/prompt identity in keys.

## Wiring

- **Discovery:** distinct native comments and topic/cohort counts from Neo4j,
  with bounded original examples and exact workbook row/version references.
- **Extraction:** the existing strict signal agent, exact-quote validation and
  immutable `Signal`/`SignalRevision` persistence with transactional audit.
- **Drafts:** native sales/study retrieval, existing quality validation and a
  schema bridge to the backend writer. JTBD, claims, alternatives, test
  definitions and the original AI evidence pack are retained.
- **Scoring:** backend-owned, using the complete sourced 2030 baseline and
  actual scoped comment membership. Whole-line reach remains a reviewed
  upper-bound proxy; targets and model assessments remain proposed.
- **Review:** existing bounded read-only tools, actual source citations and
  current requirement revisions. JSON-pointer proposals become backend field
  diffs and receive a backend preview. Application and approval stay separate.

Unreviewed studies remain unmapped. Native documents with unknown market or
derivative applicability can be inspected by review tools but are not promoted
to scoped feasibility or strategy proof. Missing OpenAI credentials produce an
explicit unavailable model result, never mocked generation.

## Small live verification

On 2026-10-11, the integrated API and real OpenAI model:

1. Generated and persisted `REQ-89523a26cb9e08be18720396` from original G60/US/BEV
   feedback, with one distinct supporting comment, cohort **1,271** and derived
   2030 reference volume **486,000**.
2. Repeated the same request from cache, returning the same identity with
   **zero new model responses**.
3. Produced a read-only answer with **two verified citations**, leaving the
   requirement unchanged.
4. Produced a pending description proposal with an authoritative backend
   preview; the requirement remained **DRAFT, revision 1**.

These are bounded real-data integration checks. The full test suites were not
rerun. Earlier synthetic-only checks in the Role 2 handoff/coverage documents
remain historical; test-only fixture values now reside in Python under
`tests/intelligence`, outside the application.
