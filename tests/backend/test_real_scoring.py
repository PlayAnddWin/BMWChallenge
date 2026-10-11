"""Independent policy oracles over the actual Role 01 Neo4j provider.

Imported annotation buckets are coverage, not automatically verified issue
support. The policy exercise labels their hypothetical assignment explicitly;
it does not assert that a bucket proves a new BMW requirement.
"""
from collections import defaultdict
from math import log1p, sqrt

import pytest

from test_real_bmw_data import real_data  # shared conversion/production loader
from services.evidence import EvidenceScope
from services.scoring import calculate_evidence, calculate_score


@pytest.mark.parametrize("model", ["F70", "G60", "G70"])
@pytest.mark.parametrize("region", ["US", "EU", "CN"])
@pytest.mark.parametrize("powertrain", [None, "BEV", "ICE"])
def test_real_cohort_annotation_buckets_and_policy_arithmetic(real_data, model, region, powertrain):
    _, bundle, _, provider = real_data
    comments = {r["id"]: r for r in bundle.feedback_comments
                if r["model"] == model and r["region"] == region
                and (powertrain is None or r["powertrain"] == powertrain)}
    annotations = [a for a in bundle.feedback_annotations if a["feedback_id"] in comments]
    counts = provider.get_comment_cohort_counts(EvidenceScope(model=model, region=region, powertrain=powertrain))
    assert counts["cohort_distinct_comment_count"] == len(comments)
    assert counts["cohort_annotation_count"] == len(annotations)
    assert counts["source_labels_are_opaque"] is True
    groups, rows = defaultdict(set), defaultdict(int)
    for annotation in annotations:
        topic = annotation["vfc2_original"]
        if topic is not None:
            groups[topic].add(annotation["feedback_id"])
            rows[topic] += 1
    assert counts["topic_bucket_distinct_comments"] == {k: len(v) for k, v in groups.items()}
    assert counts["topic_annotation_counts"] == dict(rows)
    # Every actual bucket exercises the policy's denominator and ECI formula.
    for topic, support_ids in groups.items():
        score = calculate_score({"target_regions": [region],
            "region_counts": {region: {"support": len(support_ids), "cohort": len(comments)}},
            "customer_references": sorted(support_ids)})
        mention = sqrt(len(support_ids) / len(comments))
        assert score["factors"]["customer"]["value"] == pytest.approx(.3 + .4 * mention)
        assert score["factors"]["customer"]["lower"] == pytest.approx(.4 * mention)
        assert score["factors"]["customer"]["upper"] == pytest.approx(.6 + .4 * mention)
        assert score["range"]["statistical_confidence_interval"] is False
        eci = calculate_evidence({"support_ids": sorted(support_ids),
            "target_regions": [region], "covered_regions": [region],
            "corroboration": "none", "assignment_reviews": [.5] * len(support_ids),
            "source_count": len({comments[c]["source_file_id"] for c in support_ids})})
        expected = 100 * (.35 * min(1, log1p(len(support_ids)) / log1p(50)) + .25 + .15 * .5)
        assert eci["value"] == pytest.approx(expected)
        assert eci["is_probability"] is False
    if not comments:
        score = calculate_score({"target_regions": [region], "region_counts": {region: {"support": 0, "cohort": 0}}})
        assert score["factors"]["customer"]["provisional"]
        assert f"Unavailable customer cohort: {region}" in score["warnings"]


@pytest.mark.parametrize("region", ["US", "EU", "CN", "RoW"])
def test_real_sales_provider_reach_shared_lines_and_explicit_proxies(real_data, region):
    _, bundle, _, provider = real_data
    originals = [f for f in bundle.facts if f["kind"] == "sales" and f["year"] == 2030]
    # Fixed across every scope; full supplied 2030 market, independent of request.
    reference = sum(f["volume"] for f in originals)
    response = provider.get_sales_facts(EvidenceScope(region=region), 2030)
    facts = response["facts"]
    assert {f["id"] for f in facts} == {f["id"] for f in originals if f["region"] == region}
    assert response["powertrain_split"] == "unknown"
    assert facts
    forecasts = [{"vehicle_line": f["vehicle_line"], "region": f["region"], "year": f["year"],
                  "volume": f["volume"], "source_reference": {"source_id": f["source_file_id"],
                  "locator": f["source_locator"]}} for f in facts]
    lines = sorted({f["vehicle_line"] for f in facts})
    score = calculate_score({"target_regions": [region], "target_vehicle_lines": lines,
                             "forecasts": forecasts, "reference_volume": reference})
    assert score["factors"]["reach"]["value"] == pytest.approx(sum(f["volume"] for f in facts) / reference)
    for derivative in ("G60", "G68"):
        proxy = provider.get_sales_facts(EvidenceScope(model=derivative, region=region, powertrain="BEV"), 2030)
        assert proxy["facts"] == []
        assert proxy["proxy_candidates_are_not_direct_derivative_volumes"]
        assert {f["id"] for f in proxy["possible_whole_line_proxy_candidates"]} == {
            f["id"] for f in facts if f["vehicle_line"] == "G60_G68"}
    shared = [f for f in forecasts if f["vehicle_line"] == "G60_G68"]
    aliases = [{**f, "vehicle_line": alias} for f in shared for alias in ("G60", "G68")]
    shared_score = calculate_score({"target_regions": [region], "target_vehicle_lines": ["G60", "G68"],
                                   "forecasts": aliases, "reference_volume": reference})
    assert shared_score["factors"]["reach"]["target_volume"] == sum(f["volume"] for f in shared)


@pytest.mark.parametrize("model,region", [("F70", "EU"), ("G60", "EU"), ("G70", "EU"),
                                         ("G68", "CN"), ("G70", "CN"),
                                         ("G60", "US"), ("G70", "US"), ("F70", "US")])
def test_native_survey_cells_are_not_automatically_direct_issue_support(real_data, model, region):
    _, bundle, _, provider = real_data
    response = provider.get_survey_facts(None, EvidenceScope(model=model, region=region))
    original = {f["id"]: f for f in bundle.facts}
    expected_ids = sorted(f["id"] for f in bundle.facts if f["kind"].startswith("survey")
                          and f["derivative"] == model and f["region"] == region)[:200]
    assert [f["id"] for f in response["facts"]] == expected_ids
    for fact in response["facts"]:
        row = original[fact["id"]]
        assert fact.get("value") == row.get("value")
        assert fact["derivative"] == model
        assert fact["mapping_strength"] == "unmapped"
        assert fact["topic_mapping"] is None
        assert fact["respondent_n"] == row.get("respondent_n")
        if fact["kind"] == "survey_distribution":
            assert fact["distribution"] == row["distribution"]
            assert len(fact["distribution"]) == 7
    # A text search/topic guess cannot upgrade native context into evidence.
    guessed = provider.get_survey_facts("UNREVIEWED_REAL_TEST_ISSUE", EvidenceScope(model=model, region=region))
    assert guessed["facts"] == []
    assert guessed["mapping_status"] == "unmapped"
