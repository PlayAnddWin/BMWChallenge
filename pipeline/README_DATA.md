# Role 01: data conversion and evidence provenance

This layer converts Excel/PDF inputs into immutable, hash-versioned records, loads those records into Neo4j, and exposes bounded evidence queries. The converter never edits or deletes input files. It does not infer source-channel meaning, customer prevalence, survey sample size, a BEV share, or a study-to-topic relationship.

## Install and run

Run these commands from the repository root. On Linux/macOS, create or activate
the project's Python environment and install the data-layer dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r pipeline/requirements-data.txt
```

On Windows, activate with `.venv\Scripts\Activate.ps1`. Python 3.14.7 was used
for the local verification below. The dependency file includes `pypdf[crypto]`
for the AES-encrypted BMW PDFs and `fonttools` for their embedded fonts.

Create a private root `.env` from [`.env.example`](../.env.example) if one does
not already exist. All four pipeline commands use root `config.py`, which loads
this file with process-environment precedence. `BMW_RAW_DATA_DIR` defaults to
`./BMW-Data`; `BMW_DATA_DIR` is the **processed destination**, defaulting to
`./var/processed`. Relative environment and CLI paths resolve from the repository
root even when a command is called from another directory. OpenAI credentials
are not required for this data pipeline.

### Start local Neo4j

With `NEO4J_USER=neo4j`, a populated `NEO4J_PASSWORD`, and
`NEO4J_URI=bolt://localhost:7687` in the root `.env`, run:

```bash
docker compose --file compose.data.yaml up -d --wait
docker compose --file compose.data.yaml ps
```

The service exposes Bolt on `127.0.0.1:7687` and Neo4j Browser at
<http://localhost:7474/browser/>. Log in using the graph credentials from `.env`.
Imported data is stored in the named volume `bmwchallenge-data_neo4j_data` and
survives container restarts. Stop the service with
`docker compose --file compose.data.yaml stop`; start it again with the command
above. A password change in `.env` must also be applied to the existing Neo4j
user: container initialization only sets the password for a new data volume.

The Compose file caps the container at 3 GiB, with a 1536 MiB maximum heap,
256 MiB page cache, and 1 GiB transaction-memory budget. This was verified on
the complete corpus. A 512 MiB heap hit Neo4j's 358.4 MiB transaction limit and
rolled back the atomic import. Keep the larger budget when incorporating this
service into Role 3's application Compose configuration.

### Convert, import and build summaries

Put the nine supplied files, including `BMW-Group-Report-2025-en.pdf`, in the
configured raw directory. Then run:

```bash
python pipeline/00_preprocess.py --check-known-feedback-counts
python pipeline/01_load_neo4j.py
python pipeline/03_vfc_analysis.py
python pipeline/02_build_kg.py
```

The processed destination must be outside the raw directory. Paths can also be
overridden explicitly:

```bash
python pipeline/00_preprocess.py --raw-data-dir "<raw directory>" --output-dir "<processed directory>"
python pipeline/01_load_neo4j.py --processed-dir "<processed directory>"
```

The converter requires at least one feedback workbook, survey workbook, sales workbook, option-catalog PDF, and corporate-report PDF. It prints the manifest status and returns a nonzero exit code for failed or incomplete conversions. Use repeated `--required-document-kind` flags to specify a different reviewed document set. `--check-known-feedback-counts` validates the complete three-model export against the documented source-row and distinct-comment targets.

To add reviewed survey relations later, provide a populated copy of `pipeline/study_topic_mappings.example.json`:

```bash
python pipeline/01_load_neo4j.py --study-mappings "<reviewed mapping file>"
```

For an intentionally partial import, inspect `manifest.json` and then pass `--allow-partial`. The import is idempotent by source hash and stable record ID. All source, comment, annotation, fact, chunk, import-run, and injected audit-sink writes occur in the same Neo4j write transaction.

The graph scripts require a reachable Neo4j instance and report connection/query
failures with a nonzero exit code. Summary writes are batched and resolve exact
comment IDs through uniqueness indexes rather than rescanning the annotation
graph for every group.

## Input recognition and intermediate format

- `.xlsx` and `.xlsm` are supported through `openpyxl`; `.pdf` is supported through `pypdf`. Excel's transient `~$` lock files are excluded from source discovery.
- Feedback workbook names should include `feedback`, `explorer`, or `hackathon`. The parser finds a sheet/header containing both an original feedback ID and text. It resolves fields by header names, including BMW's `Derivate (e-code)`, and preserves every nonempty source row plus its sheet/row locator. An explicit derivative wins over filename inference and is retained in `model_original`.
- Survey workbook names should include `study`, `studies`, `survey`, or `customer`. All numeric cells are preserved as native facts with per-column model/market scope. Explicit `1..7` categories and seven-row percentage blocks are also represented as native distributions with exact cell locators; the sample base is not treated as respondent N, and topic mappings remain unreviewed until explicitly approved.
- Sales workbook names should include `sales`, `volume`, or `forecast`. A sales fact is emitted only when vehicle line, region, year, and source value are identifiable. Zero is retained; missing values are not converted to zero. `G60_G68` remains one sales line and no powertrain split is synthesized.
- Every PDF page is recorded, including empty/unextractable pages. Text chunks stay within their physical page, preserve exact extracted text spans, and cite 1-based physical pages. Printed page labels remain null unless later supplied from a reviewed source. Required document subtypes default to option catalogs and corporate reports so a catalog-only input set is reported as partial.
- Other workbook layouts remain available in `source_records.jsonl`; unmatched numeric cells are not silently promoted to scoped business facts.

The output has `source_files.jsonl`, `source_records.jsonl`, `feedback_comments.jsonl`, `feedback_annotations.jsonl`, `facts.jsonl`, `document_chunks.jsonl`, and `manifest.json`. IDs are derived from source-file SHA-256 and source locators/business IDs. A repeated feedback ID with conflicting text or scope is flagged and kept as separate row-backed comments. Multiple annotations on a consistent comment stay separate. `BEVE` is normalized to `BEV` while its original value remains stored. A missing label stays missing.

The manifest records converted source hashes, parser counts, per-file failures, missing required kinds, known feedback count checks, and overall `succeeded`/`partial`/`failed` state. A missing or unreadable required input cannot appear as an empty successful conversion.

## Validation

Run the synthetic parser/provider checks and, when the private `BMW-Data` folder
is present at the repository root, the real-corpus count and citation checks:

```powershell
python -m unittest discover -s tests/data -v
```

The real-corpus test checks the documented per-model feedback counts, source
discovery, sales facts, and that every PDF chunk is an exact substring of its
page record. It skips when the private source directory is absent. To produce
an inspectable bundle and manifest, run `00_preprocess.py` with explicit raw
and output directories as shown above.

To test a separately staged corpus without copying or changing the protected
source folder, set `BMW_RAW_DATA_DIR` to its path. `BMW_DATA_DIR` continues to mean
processed output. The corpus must include the BMW
Group Report PDF in addition to the feedback, study, sales, and option-catalog
files; the report is discovered by a filename containing `report`, `annual`,
or `strategy` and is stored as `corporate_report` evidence.

## Live Neo4j integration check

`tests/data/test_live_neo4j.py` is skipped unless
`BMW_NEO4J_TEST_URI` points to a **loopback** Neo4j instance. It imports the
complete real corpus twice, checks that counts do not grow on reimport, and
resolves both an exact feedback source row and a corporate-report page citation.
The checks are additive; they do not clear or delete graph data. Use a disposable
test instance because they write the imported evidence into its default database.
The other live checks verify exact sales/study cell citations, native seven-category
shares, summary membership/idempotence, and rollback of new evidence, taxonomy
and audit writes when the injected audit sink fails.

The local Compose service uses authentication. Tests default to the canonical
`NEO4J_USER` and `NEO4J_PASSWORD` from root configuration; dedicated test servers
can override these with `BMW_NEO4J_TEST_USER` and `BMW_NEO4J_TEST_PASSWORD`.
For an already auth-disabled loopback test instance, set
`BMW_NEO4J_TEST_AUTH_DISABLED=1`. Run the complete checks, including all four live
Neo4j tests, on Linux/macOS with:

```bash
BMW_NEO4J_TEST_URI=bolt://127.0.0.1:7687 python -m unittest discover -s tests/data -v
```

Or in PowerShell:

```powershell
$env:BMW_NEO4J_TEST_URI = "bolt://127.0.0.1:7687"
python -m unittest discover -s tests/data -v
```

The Neo4j driver must be installed from `pipeline/requirements-data.txt` in the
Python environment running this command. Neo4j itself must already be running;
the test never starts or configures a system service.

The full checks perform several real PDF conversions and take several minutes.

## Neo4j entities and evidence queries

- `SourceFile` stores filename, content hash, kind, and conversion version.
- `SourceRecord` stores the exact workbook row or extracted PDF page payload plus a JSON locator. Facts, comments, annotations, and document chunks resolve back to these records.
- `CustomerFeedback` stores canonical comment text and original/normalized scope values. `FeedbackAnnotation` stores imported type and VFC2/VFC3 labels separately, so a comment can carry positive and negative annotations at distinct topics.
- `Fact` stores native survey cells/distributions and sourced sales values. Nested values and locators are JSON strings, while scope and measure fields remain scalar Neo4j properties.
- `DocumentChunk` stores exact page text and a physical page number.
- `StudyTopicMapping` is created only from a supplied, reviewed mapping file. Start from `pipeline/study_topic_mappings.example.json`, add exact `fact_ids`, relation (`direct`, `broad`, `contradictory`, or `unmapped`), reason, reviewer, and review time, then load with `--study-mappings <file>`. Mappings to a fact from another converted bundle are rejected.

`services.evidence` exports `EvidenceScope`, typed DTOs, and the `search_feedback`, `get_source_record`, `get_survey_facts`, `get_sales_facts`, `search_document_chunks`, and `get_comment_cohort_counts` providers. Construct `Neo4jEvidenceProvider(driver)` or configure the application-scoped provider. Search results include stable IDs, source hash/version, original text/value, exact row/cell/page locator, applicable scope, and unknowns. Provider/database failures raise `EvidenceProviderError`; they are not returned as empty evidence.

Fact `source_locator` values include the original column/cell metadata, not just
the parent row locator. Reviewed study mapping relations are exposed through
`mapping_strength`; an unmapped study stays explicitly unmapped.

Example service usage from the backend (the backend owns driver lifecycle):

```python
from services.evidence import EvidenceScope, Neo4jEvidenceProvider

provider = Neo4jEvidenceProvider(driver)
quotes = provider.search_feedback(
    "seat ventilation", EvidenceScope(model="G70", region="US"), limit=10
)
study = provider.get_survey_facts(
    "seat ventilation", EvidenceScope(model="G70", region="US")
)
```

Until the second call has a reviewed mapping, it returns an explicit `unmapped` state instead of treating a keyword match as corroboration. Use the quote's `source_records` locator or `get_source_record(id)` to resolve the original workbook row.

Counts distinguish unique canonical comments from annotations. Topic and issue counts are separate; broad bucket counts must not be reused as narrow issue support. Source A–D values stay opaque. `pipeline/02_build_kg.py` stores source-rate differences as descriptive records and same-VFC3 mixed polarity as a review candidate. It deliberately creates no formal preference-conflict edge: that requires a reviewed preference metric, not just a source-rate gap or opposing broad feedback labels.

## Verified local acceptance and current limits

Local verification on 2026-10-11 used Python 3.14.7, Neo4j Community 5.26.31,
Neo4j driver 6.4.0, pypdf 6.20.0 and conversion version **1.2.1**. The full command
above passed **25 tests with no skips**, including four live Neo4j tests.

| Model | Original feedback rows | Distinct canonical comments | Imported annotations |
| --- | ---: | ---: | ---: |
| F70 | 4,076 | 2,128 | 4,076 |
| G60 | 5,005 | 4,000 | 4,805 |
| G70 | 4,690 | 4,000 | 4,490 |
| **Total** | **13,771** | **10,128** | **13,371** |

Rows without labels remain source rows; annotations are not invented to make
their count equal the row count. The successful complete bundle has **9 source
files, 15,304 source records, 2,075 facts and 605 document chunks**. The facts
include 36 sales facts and 2,039 native study facts. Summary generation produced
1,430 topic/cohort summaries, 3,439 issue/cohort summaries and 577 descriptive
source-rate differences, with no formal preference-conflict edges.

Manual checks compared feedback, sales and study cells, all seven cells of a
native US distribution, a corporate-report passage and an option-catalog
passage directly with the original workbooks/PDFs. All nine raw-file SHA-256
values matched their conversion records. Reimport preserved every node and
relationship count, injected audit failure rolled back all new evidence, and
the imported corpus survived a Neo4j container restart. Private citation details
are available locally in `var/cache/role1-local-verification.json`; source files
and derived evidence remain ignored by Git.

The generic workbook adapters preserve uncertain layouts. Native survey cells
that lack recognizable seven-category headers remain raw cell facts; sales cells
lacking an explicit line/region/year remain source rows. Reviewed study-topic
mappings have not been populated for this local import, so topic requests return
an explicit `unmapped` state. The source has no China free-text cohort; that is
a coverage gap. G60/G68 sales remain one whole-line proxy with no inferred
derivative or powertrain split, and document market/derivative applicability
remains unknown until reviewed. These provider states are part of the handoff
to Roles 3 and 2.
