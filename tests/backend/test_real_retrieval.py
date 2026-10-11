"""Role 03 HTTP contract over Role 01 conversion, loader and live provider.

All expected counts, native values and original text come from the supplied
corpus conversion. No fabricated evidence or fake provider is used.
"""
from collections import Counter, defaultdict
from itertools import product

import pytest
from fastapi.testclient import TestClient

from services.api import create_app
from services.providers import Role01SourceProvider
from services.repository import MemoryRepository
from test_real_bmw_data import real_data  # reuse the genuine Role 01 conversion/load fixture


@pytest.fixture(scope="module")
def real_api(real_data):
    driver, bundle, _, _ = real_data
    provider = Role01SourceProvider(driver)
    with TestClient(create_app(MemoryRepository(), source_provider=provider)) as client:
        yield client, bundle, provider


def assert_counts(result, bundle, comments):
    ids = {r["id"] for r in comments}
    annotations = [a for a in bundle.feedback_annotations if a["feedback_id"] in ids]
    assert result["cohort_distinct_comment_count"] == len(ids)
    assert result["cohort_annotation_count"] == len(annotations)
    assert result["source_versions"] == sorted({r["source_version"] for r in comments})
    assert result["source_labels_are_opaque"] is True
    assert result["source_label_counts"] == dict(Counter(r["source_label"] for r in comments if r.get("source_label")))
    topic_ids, topic_rows, issue_ids, issue_rows = defaultdict(set), Counter(), defaultdict(set), Counter()
    for annotation in annotations:
        topic, issue = annotation.get("vfc2_original"), annotation.get("vfc3_original")
        if topic is not None:
            topic_ids[topic].add(annotation["feedback_id"])
            topic_rows[topic] += 1
        if issue is not None:
            label = f'{topic or "(unclassified)"} :: {issue}'
            issue_ids[label].add(annotation["feedback_id"])
            issue_rows[label] += 1
    assert result["topic_bucket_distinct_comments"] == {k: len(v) for k, v in topic_ids.items()}
    assert result["topic_annotation_counts"] == dict(topic_rows)
    assert result["issue_label_distinct_comments"] == {k: len(v) for k, v in issue_ids.items()}
    assert result["issue_annotation_counts"] == dict(issue_rows)


@pytest.mark.parametrize("model,region,powertrain", list(product(
    ["F70", "G60", "G70", "G68"], ["US", "EU", "CN"], ["ICE", "BEV", "PHEV"])))
def test_every_real_cohort_and_annotation_aggregate(real_api, model, region, powertrain):
    client, bundle, _ = real_api
    scope = {"derivatives": [model], "regions": [region], "powertrains": [powertrain]}
    response = client.post("/api/evidence/cohort-counts", json={"scope": scope})
    assert response.status_code == 200, response.text
    comments = [r for r in bundle.feedback_comments if (r["model"], r["region"], r["powertrain"]) == (model, region, powertrain)]
    assert_counts(response.json(), bundle, comments)


@pytest.mark.parametrize("scope", [
    {}, {"regions": ["US", "USA"], "derivatives": ["G60", "G70", "G60"]},
    {"regions": ["EU", "US"], "derivatives": ["F70", "G60", "G70"], "powertrains": ["BEV", "BEVE", "ICE"]},
])
def test_union_cohorts_deduplicate_aliases_and_preserve_unknown_regions(real_api, scope):
    client, bundle, provider = real_api
    scopes = provider._scopes(scope)
    comments = [r for r in bundle.feedback_comments if any(
        (s.derivative is None or r["model"] == s.derivative) and
        (s.region is None or r["region"] == s.region) and
        (s.powertrain is None or r["powertrain"] == s.powertrain) for s in scopes)]
    response = client.post("/api/evidence/cohort-counts", json={"scope": scope})
    assert response.status_code == 200
    assert_counts(response.json(), bundle, comments)


@pytest.mark.parametrize("model", ["F70", "G60", "G70"])
def test_feedback_search_http_obeys_scope_and_preserves_all_row_annotations(real_api, model):
    client, bundle, _ = real_api
    response = client.post("/api/evidence/feedback-search", json={"query": "car seat", "scope": {"derivatives": [model], "regions": ["US"]}, "limit": 100})
    assert response.status_code == 200, response.text
    originals = {r["id"]: r for r in bundle.feedback_comments}
    expected = sorted(r["id"] for r in bundle.feedback_comments if r["model"] == model and r["region"] == "US" and any(t in r["original_text"].lower() for t in ["car", "seat"]))[:100]
    assert [r["id"] for r in response.json()] == expected
    annotations = defaultdict(set)
    for row in bundle.feedback_annotations:
        annotations[row["feedback_id"]].add(row["id"])
    for row in response.json():
        assert row["original_text"] == originals[row["id"]]["original_text"]
        assert {a["id"] for a in row["annotations"]} == annotations[row["id"]]
        assert row["source_label_semantics"] == "opaque"
        assert row["source_records"]
        for source in row["source_records"]:
            assert source["source_record_id"] == source["id"]
            assert source["source_version"] == row["source_version"] == source["source_sha256"]
            response = client.get('/api/source-records/' + source["id"])
            assert response.status_code == 200
            assert response.json()["feedback_comment_id"] == row["id"]


@pytest.mark.parametrize("region,year", list(product(["US", "EU", "CN", "RoW"], [2025, 2030, 2035])))
def test_every_native_sales_region_year_and_exact_source_cells(real_api, region, year):
    client, bundle, _ = real_api
    response = client.post("/api/evidence/sales-facts", json={"scope": {"regions": [region]}, "year": year})
    assert response.status_code == 200, response.text
    expected = {r["id"]: r for r in bundle.facts if r["kind"] == "sales" and r["region"] == region and r["year"] == year}
    assert {r["id"] for r in response.json()["facts"]} == set(expected)
    for row in response.json()["facts"]:
        for key in ["volume", "vehicle_line", "year", "region", "source_version", "source_file_id"]:
            assert row[key] == expected[row["id"]][key]
        assert row["source_locator"] == expected[row["id"]]["locator"]
        assert row["source_sha256"] == row["source_version"]
    assert response.json()["powertrain_split"] == "unknown"


@pytest.mark.parametrize("model", ["G60", "G68"])
def test_shared_sales_line_stays_proxy_and_never_derivative_volume(real_api, model):
    client, bundle, _ = real_api
    result = client.post("/api/evidence/sales-facts", json={"scope": {"derivatives": [model]}, "year": 2030}).json()
    assert result["facts"] == []
    assert {r["id"] for r in result["possible_whole_line_proxy_candidates"]} == {
        r["id"] for r in bundle.facts if r["kind"] == "sales" and r["year"] == 2030 and r["vehicle_line"] == "G60_G68"}
    assert result["proxy_candidates_are_not_direct_derivative_volumes"]


@pytest.mark.parametrize("model,region", [("G68", "CN"), ("G70", "CN"), ("F70", "EU"), ("G60", "EU"), ("G70", "EU")])
def test_native_survey_values_preserved_and_unmapped_topic_never_supports(real_api, model, region):
    _, bundle, provider = real_api
    result = provider.get_survey_facts(scope={"derivatives": [model], "regions": [region]})
    expected = {r["id"]: r for r in bundle.facts if r["kind"].startswith("survey") and r["derivative"] == model and r["region"] == region}
    assert {r["id"] for r in result["facts"]} == set(expected)
    for fact in result["facts"]:
        assert fact["value"] == expected[fact["id"]]["value"]
        assert fact["mapping_strength"] == "unmapped"
        assert fact["source_sha256"] == fact["source_version"]
    assert provider.get_survey_facts("not-a-reviewed-topic", {"derivatives": [model], "regions": [region]})["facts"] == []


def test_every_original_source_record_and_document_span_survives_real_load(real_api, real_data):
    _, bundle, provider = real_api
    driver, _, _, _ = real_data
    with driver.session() as session:
        loaded = {row["r"]["id"]: provider._normalize(row["r"]) for row in session.run("MATCH (r:SourceRecord) RETURN properties(r) AS r")}
        chunks = {row["c"]["id"]: row["c"] for row in session.run("MATCH (c:DocumentChunk) RETURN properties(c) AS c")}
    for original in bundle.source_records:
        row = loaded[original["id"]]
        assert row["raw_payload"] == original["raw_payload"]
        assert row["locator"] == {key: value for key, value in original["locator"].items() if value is not None}
        assert row["source_version"] == original["source_version"]
    for original in bundle.document_chunks:
        row = chunks[original["id"]]
        page = loaded[row["source_record_id"]]["raw_payload"]["text"]
        assert row["original_text"] == original["original_text"] == page[row["start_offset"]:row["end_offset"]]
        assert row["applicability"] == "unknown"


@pytest.mark.parametrize("scope", [{"derivatives": ["G60"]}, {"regions": ["US"]}, {"regions": ["CN"]}])
def test_unknown_pdf_applicability_never_becomes_target_evidence(real_api, scope):
    client, _, _ = real_api
    response = client.post("/api/evidence/document-search", json={"query": "BMW", "scope": scope, "document_kind": "option_catalog"})
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize("scope", [{"topics": ["HVAC"]}, {"signal_ids": ["missing"]}])
def test_unsupported_filters_fail_without_silently_widening_retrieval(real_api, scope):
    client, _, _ = real_api
    assert client.post("/api/evidence/feedback-search", json={"query": "seat", "scope": scope}).status_code == 422


@pytest.mark.parametrize("model", ["G60", "G70"])
def test_all_native_us_distributions_and_unknown_samples_loaded_without_scale_invention(real_api, real_data, model):
    _, bundle, _ = real_api
    driver, _, _, _ = real_data
    expected = {r["id"]: r for r in bundle.facts if r["kind"] == "survey_distribution" and r["derivative"] == model and r["region"] == "US"}
    with driver.session() as session:
        rows = list(session.run("MATCH (f:Fact {kind:'survey_distribution', derivative:$model, region:'US'}) RETURN properties(f) AS fact", model=model))
    import json
    assert {r["fact"]["id"] for r in rows} == set(expected)
    for row in rows:
        fact = row["fact"]
        original = expected[fact["id"]]
        assert json.loads(fact["distribution_json"]) == original["distribution"]
        assert len(original["distribution"]) == 7
        assert fact.get("respondent_n") == original.get("respondent_n") is None
        assert fact.get("scale") == original.get("scale")
        assert fact["source_version"] == original["source_version"]
        assert json.loads(fact["cell_locators_json"]) == original["cell_locators"]


def test_feedback_lineage_public_role01_lookup_resolves_each_original_row(real_api):
    _, bundle, provider = real_api
    for model in ["F70", "G60", "G70"]:
        comments = {r["id"]: r for r in bundle.feedback_comments if r["model"] == model}
        row = next(r for r in bundle.source_records if r.get("feedback_comment_id") in comments)
        actual = provider.evidence.get_feedback_for_source_record(row["id"])
        assert len(actual) == 1
        original = comments[row["feedback_comment_id"]]
        for key in ["id", "original_text", "model", "region", "source_version"]:
            assert actual[0].get(key) == original.get(key)
    assert provider.evidence.get_feedback_for_source_record("not-imported") == []


@pytest.mark.parametrize("kind,label,field", [
    ("record", "SourceRecord", "raw_payload_json"),
    ("chunk", "DocumentChunk", "original_text"),
    ("feedback", "CustomerFeedback", "model"),
    ("annotation", "FeedbackAnnotation", "polarity"),
    ("fact", "Fact", "volume"),
])
def test_same_hash_graph_corruption_cannot_become_original_evidence(real_api, real_data, kind, label, field):
    from services.decisions import DomainError
    import json
    _, bundle, provider = real_api
    driver, _, _, _ = real_data
    candidates = {"record": bundle.source_records, "chunk": bundle.document_chunks,
                  "feedback": bundle.feedback_comments, "annotation": bundle.feedback_annotations,
                  "fact": [r for r in bundle.facts if r["kind"] == "sales"]}
    original = candidates[kind][0]
    with driver.session() as session:
        before = session.run(f"MATCH (n:{label} {{id:$id}}) RETURN n[$field] AS value", id=original["id"], field=field).single()["value"]
    tampered = json.dumps({"fabricated_original": True}) if kind == "record" else 987654321 if kind == "fact" else "CORRUPTED-ORIGINAL"
    try:
        with driver.session() as session:
            session.run(f"MATCH (n:{label} {{id:$id}}) SET n[$field]=$value", id=original["id"], field=field, value=tampered).consume()
        with pytest.raises(DomainError) as error:
            if kind in {"record", "chunk"}:
                provider.get(kind, original["id"])
            else:
                provider.get_comment_cohort_counts({})
        assert error.value.status == 409
    finally:
        with driver.session() as session:
            session.run(f"MATCH (n:{label} {{id:$id}}) SET n[$field]=$value", id=original["id"], field=field, value=before).consume()
    provider.validate_graph_integrity()


def test_retained_versions_of_one_real_workbook_fail_before_cohort_query(real_api, real_data):
    from services.decisions import DomainError
    from uuid import uuid4
    _, bundle, provider = real_api
    driver, _, _, _ = real_data
    source = next(r for r in bundle.source_files if r["kind"] == "feedback")
    alternate = {**source, "id": "corruption-probe-" + uuid4().hex,
                 "sha256": "different-retained-version", "source_version": "different-retained-version"}
    alternate = {key: value for key, value in alternate.items() if value is not None}
    try:
        with driver.session() as session:
            session.run("CREATE (s:SourceFile) SET s=$source", source=alternate).consume()
        with pytest.raises(DomainError) as error:
            provider.evidence.get_comment_cohort_counts(None)
        assert error.value.status == 409
        assert "Multiple imported versions" in error.value.message
    finally:
        with driver.session() as session:
            session.run("MATCH (s:SourceFile {id:$id}) DETACH DELETE s", id=alternate["id"]).consume()


@pytest.mark.parametrize("relation", ["DESCRIBES", "HAS_ANNOTATION"])
def test_duplicate_original_membership_edges_fail_integrity(real_api, real_data, relation):
    from services.decisions import DomainError
    _, bundle, provider = real_api
    driver, _, _, _ = real_data
    if relation == "DESCRIBES":
        row = next(r for r in bundle.source_records if r.get("feedback_comment_id"))
        source, target = row["id"], row["feedback_comment_id"]
    else:
        row = bundle.feedback_annotations[0]
        source, target = row["feedback_id"], row["id"]
    edge_id = None
    try:
        with driver.session() as session:
            edge_id = session.run(f"MATCH (s {{id:$source}}), (t {{id:$target}}) CREATE (s)-[r:{relation}]->(t) RETURN elementId(r) AS edge", source=source, target=target).single()["edge"]
        with pytest.raises(DomainError) as error:
            provider.get_comment_cohort_counts({})
        assert error.value.status == 409
        assert "memberships" in error.value.message
    finally:
        if edge_id is not None:
            with driver.session() as session:
                session.run("MATCH ()-[r]->() WHERE elementId(r)=$id DELETE r", id=edge_id).consume()


def test_original_registry_uses_role01_discovery_for_office_temporary_files(real_data, tmp_path):
    import shutil
    from config import get_settings
    from services.providers import Role01SourceProvider
    from services.ingestion import discover_sources
    driver, _, _, _ = real_data
    raw = get_settings().raw_data_dir
    staged = tmp_path / "original-corpus"
    shutil.copytree(raw, staged)
    workbook = next(p for p in discover_sources(staged) if p.suffix.lower() == ".xlsx")
    temporary = staged / ("~$" + workbook.name)
    shutil.copyfile(workbook, temporary)
    assert temporary not in discover_sources(staged)
    sources = Role01SourceProvider(driver, raw_data_dir=staged)
    assert len(sources.list_sources()) == len(discover_sources(staged))
