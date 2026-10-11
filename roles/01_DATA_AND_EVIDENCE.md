# Role 1 — Data Engineering & Evidence Provenance

## Exact Role

Own the transformation from BMW files to trustworthy, retrievable evidence. This is a data/backend specialty: **no frontend implementation and no model prompting**.

Success: import the real datasets, preserve every source/annotation, and return exact feedback spans, study cells, sales facts, and report pages through a stable Python provider.

Use this assignment with [the implementation plan](../IMPLEMENTATION_PLAN.md). The relevant branch findings and acceptance criteria are embedded below; [the original review](../AI_ENGINE_BRANCH_REVIEW.md) is supporting history. Baseline code is `origin/ai-requirements-engine` at reviewed snapshot `3bd0251`. This is the data-engineering part of the existing Neo4j/FastAPI/Streamlit application.

## Existing Code and File Ownership

| Own | Work |
| :--- | :--- |
| `pipeline/00_preprocess.py` | Replace the assumption of already-clean external JSON with documented raw-to-processed conversion; preserve originals |
| `pipeline/01_load_neo4j.py` | Source versions/locators, canonical comments, annotations, native facts, page-aware documents |
| `pipeline/02_build_kg.py`, `03_vfc_analysis.py` | Correct counts/relations; distinguish source-rate differences from actual conflicting preferences |
| New `services/evidence.py`, `services/ingestion.py` | Typed evidence-provider functions and conversion helpers |
| `tests/data/*` | Synthetic parser/provider tests and real-data count checks |

Supply source DTO/schema definitions to Role 3, who coordinates `models.py`, `config.py`, constraints and API entry points. Neo4j is the authoritative store. You define source nodes/relationships; Role 3 incorporates constraints and transaction infrastructure.

**Shared environment:** use root [.env.example](../.env.example) and [CONFIGURATION.md](../CONFIGURATION.md). Read `BMW_RAW_DATA_DIR`, `BMW_DATA_DIR` and graph settings through `config.py`; do not introduce `RAW_DATA_PATH`/`PROCESSED_DIR` aliases or your own dotenv/defaults. Relative paths resolve from the repository root.

### Branch Repairs Assigned to This Role

Task IDs match the repair backlog in the implementation plan. P0 repairs block the trustworthy judged demonstration. The hour-3 slice can start with real feedback while full heterogeneous conversion completes by hour 6; P1 repairs complete the sample/demo evidence.

| Task | Priority / ownership | Observed behavior at `3bd0251` | Implementation and acceptance proof |
| :--- | :--- | :--- | :--- |
| **R07** | P0, lead; Role 2 consumes membership | `01_load_neo4j.py:43–59` MERGEs a comment and SETs one type; mixed annotations are overwritten. Signals only reach broad VFCs. | Separate source rows/annotations/comments; expose original IDs/locators and distinct counts. Mixed fixture retains both annotations; Role 2's issue references exact spans rather than every topic member. |
| **R08** | P0, lead | Steps 00/01 expect external clean JSON; raw XLSX/PDF conversion and corporate-report ingestion are absent. | Produce reproducible conversion/manifest and page-aware strategy/equipment text. From supplied raw files, import counts match source and a report-page citation resolves; missing files return an explicit incomplete/failed result. |
| **R14** | P1, lead | `01_load_neo4j.py:97–146` skips valid G68 input and drops study cells/native distributions. | Preserve source G68/CN, all native study evidence, cells and unknown sample metadata. G68_CN test remains G68; a US distribution remains seven categories with N unknown. |
| **R11** | P1, support Role 3 | Scoring constants use max 2025 market volume; G60/G70 RoW is zeroed. | Supply actual region/year/line facts, including 2030/RoW and G60_G68 mapping. Role 3 reproduces scoped reach from your fact IDs with no double counting or invented powertrain split. |
| **R12** | P1, support Role 3 | Source A–D semantics and a complaint-channel discount are assumed; confidence ignores source concentration. | Preserve opaque labels, mapping evidence and concentration/coverage summaries. Unsupported channel meanings are unknown, not imported truth. |
| **R13** | P1, support Role 2 | Keyword study matching and presumed scales/pooled regions can produce unjustified tier upgrades. | Supply reviewed issue-to-metric relations and native scale/region/derivative metadata; ambiguous matches remain broad/unmapped. |

### Evidence Baseline to Repair

The reviewed export has 250 proposals and 750 quote occurrences. **538** are exact substrings in the original feedback for the stated model; **212** are not. None of the source-signal exports contains original feedback IDs. Matching text alone does not prove the correct issue/category/source assignment; preprocessing may explain non-matches.

Work with Role 2 to produce a private lineage-validation result per occurrence: requirement/signal ID, quoted text, candidate original feedback IDs and source locators, transformation if documented, assignment review, and validation state. Multiple matches remain ambiguous until disambiguated. Never attach the first search hit just to make a citation pass. Preserve legacy artifacts; rebuild verified evidence from the raw source versions.

The review used mocked services; live Neo4j query/import behavior was not validated. Your handoff requires a real test-graph import/query check as well as fixture tests.

## Responsibilities

1. Convert supplied XLSX/PDF inputs reproducibly into a documented intermediate format/manifest.
2. Preserve raw rows, canonical comments and per-topic/type annotations separately.
3. Preserve native survey distributions, scale/sample metadata, and actual source derivative/region.
4. Expose real forecast facts instead of scoring constants.
5. Ingest equipment and strategy passages with original text and page anchors.
6. Supply distinct counts, coverage and search results; never model-generated counts.
7. Record source/consolidation-statistic versions and report missing/unreadable data.

## Interfaces and Handoffs

```python
search_feedback(query, scope, limit=20)
get_source_record(record_id)
get_survey_facts(topic, scope)
get_sales_facts(scope, year=2030)
search_document_chunks(query, scope, document_kind=None, limit=6)
get_comment_cohort_counts(scope)
```

Each result includes stable ID, source-file hash/version, original text/value, locator, source/target applicability metadata, and explicit unknowns. Workbook locators contain sheet/row/column; PDFs use 1-based physical page and optional printed page label. Preserve original and normalized powertrain/category values.

Role 2 calls these functions for extraction/retrieval. Role 3 wraps them in HTTP endpoints and uses them for scoring/validation. Role 4 consumes those endpoints, not your database code.

Use injected driver/transaction and an audit sink. Evidence imports record events within their transaction; the sink never commits independently. Independent tests can use fake repositories/sinks before a live Neo4j integration.

## Implementation Details

### Feedback

- Hash source files; import the same version idempotently.
- Read columns by header name. Retain every original row and its row number.
- Canonicalize by source version plus original ID; verify repeated IDs have consistent text/metadata.
- Model `CustomerFeedback` plus separate `FeedbackAnnotation` and `SourceRecord` nodes. A comment may have Likes and DTU annotations for different topics.
- Normalize BEVE to BEV without removing the source value. Document commercial-region mapping for GB/Norway/Europe.
- Flag low-information text; preserve meaningful short text. Missing labels remain unknown or separately inferred, not overwritten.
- Add explicit unique constraints through Role 3. Skip missing VFC3 nodes rather than MERGE on null properties.
- Count DISTINCT canonical IDs per cohort/topic/issue, never annotation edges or spans.

Expected source rows / canonical IDs: F70 **4,076 / 2,128**, G60 **5,005 / 4,000**, G70 **4,690 / 4,000**; total **13,771 / 10,128**. Distinct comments are not proven distinct customers.

### Studies and forecasts

- CN/EU category/Mean rows retain original cells, region and derivative. Preserve G68_CN as source G68; map G60/G68 only where the shared sales line warrants it.
- US seven-category distributions remain native. `Sample total = 1` means 100%, not N=1. Unknown N/scale stays null.
- Supply a reviewed topic/metric map with direct/broad/unmapped/contradictory relation and reason. A satisfaction difference does not establish an interface preference.
- Sales facts use unique vehicle-line/region/year. G60_G68 is one line. Preserve zero versus absent values and all supplied RoW volumes.
- No supplied powertrain split: expose whole-line forecast as a possible upper-bound proxy, not BEV volume.

### Documents and statistics

- Extract the option catalogs and `BMW-Group-Report-2025-en.pdf` page by page; chunk within pages and retain exact original text.
- Retain German equipment wording; translations are derived. German packages are not automatically valid in other markets.
- Use Neo4j full-text indexes or a deterministic bounded lexical provider behind your interface. Record extraction failures and verify showcase passages manually.
- Correct step 02/03 aggregation so annotation polarity is issue-specific. Keep opaque Source A–D labels unless their semantics are actually documented; do not assume Source C is a complaint channel.

## Shared Synthetic Fixture

Coordinate `contracts/fixture_case.json` with Role 3; all roles reuse these synthetic aliases.

| Row | Business ID / canonical alias | Scope | Annotation | Original text |
| ---: | :--- | :--- | :--- | :--- |
| 2 | FIXTURE-001 / FB-FIXTURE-001 | G60, US, BEVE | Likes / Interior | I like the cabin, but temperature adjustment is hard to find. |
| 3 | FIXTURE-001 / FB-FIXTURE-001 | G60, US, BEVE | DTU / HVAC | I like the cabin, but temperature adjustment is hard to find. |
| 4 | FIXTURE-002 / FB-FIXTURE-002 | G60, US, BEVE | DTU / HVAC | Temperature adjustment is hard to find. |
| 5 | FIXTURE-003 / FB-FIXTURE-003 | G60, US, ICE | Likes / HVAC | Temperature adjustment is easy to find. |

Expected: four rows, three comments, four annotations. Role 2's fixture issue links two supporting comments and one positive counterexample. All-powertrain G60/US denominator is three.

Additional fixture IDs:
- FACT-FIXTURE-SURVEY-001: directly mapped independent HVAC-access rating distribution, seven categories totaling 1; respondent N unknown.
- FACT-FIXTURE-SALES-US and FACT-FIXTURE-SALES-EU: G60_G68 whole-line 2030 volumes 100 each; full fixture reference 200.
- CHUNK-FIXTURE-STRATEGY-001 and CHUNK-FIXTURE-OPTION-001: synthetic page-1 passages; option applicability explicitly Germany.

Production IDs are collision-resistant/source-versioned; a fixture alias map resolves the test IDs. Synthetic data must not be presented as BMW findings.

## Verify Correctness Independently

Planned command after integration scaffold:

```bash
python -m pytest tests/data
```

Use generated synthetic XLSX/PDF and fake repositories, then a test-only Neo4j namespace/database for real query verification.

| Test | Expected result |
| :--- | :--- |
| Duplicate ID/mixed annotations | Four rows → three comments/four annotations; both polarities survive |
| Reimport / new version | Same hash creates no duplicates; new version does not overwrite old evidence |
| Source resolution | Quote/value equals exact row/cell/page contents |
| G68 / US studies | G68 retained; native distributions preserved; unknown N not invented |
| Missing / zero / RoW sales | Missing stays missing, zero stays zero, source volumes match |
| Group counts | DISTINCT comment counts, no annotation/relationship inflation |
| Malformed wrapper / missing labels | Typed import report; no silent skip presented as success |
| No China free-text input | Coverage gap, not zero demand or inferred preference |
| Legacy quote reconciliation | Each used demo quote has an exact original ID/span/locator; transformed or ambiguous matches remain labeled and cannot be counted as verified support |
| Original run/input missing | No successful empty import; required inputs and failures appear in the manifest/run state |

Real check: run conversion/import on all supplied files, compare the count table, inspect one source per input kind, and hand over three verified quote/cell/page references for the demo. Live Neo4j behavior remains a separate check from mocked tests.

## Timeline and Definition of Done

- First 45 minutes: source contracts/fixture/mappings, data-path manifest and schema handoff.
- By hour 2: real feedback and exact source provider available.
- By hour 6: native studies/sales/documents and corrected cohort statistics.
- Hours 6–10: provenance and real-query checks; help Role 5 validate claims.

Handoff includes provider, processed-format manifest, fixture responses, passing tests, actual counts, verified citations and gaps. Keep raw/processed evidence, quotes, caches and exports private/ignored. Do not implement frontend screens; send example payloads to Role 4.
