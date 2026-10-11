import math

import pytest

from services.scoring import calculate_evidence, calculate_score, rank_requirements


def golden():
    return {"severity_level": 2, "strategic_fit_level": 2, "complexity_level": 3,
            "target_regions": ["EU"], "target_vehicle_lines": ["G60", "G68"],
            "region_counts": {"EU": {"support": 2, "cohort": 3}},
            "reference_volume": 200,
            "forecasts": [{"vehicle_line": line, "region": "EU", "year": 2030,
                           "volume": 100, "source_reference": "sales:cell:B2"}
                          for line in ["G60", "G68"]]}


def test_golden_score_unique_shared_sales_line():
    result = calculate_score(golden())
    assert result["factors"]["customer"]["value"] == pytest.approx(.6265986324)
    assert result["calculated_score"] == pytest.approx(55.0639452948)
    assert result["factors"]["reach"]["target_volume"] == 100
    assert result["range"]["lower"] == result["range"]["upper"]


def test_golden_evidence_independent_of_priority():
    result = calculate_evidence({"distinct_support_comments": 2, "target_regions": ["EU"],
                                 "covered_regions": ["EU"], "corroboration": "direct",
                                 "assignment_reviews": [.5]})
    assert result["value"] == pytest.approx(67.2795359447)
    assert result["level"] == "Moderate"
    assert result["is_probability"] is False


def test_unknown_factors_have_midpoints_and_nonstatistical_ranges():
    result = calculate_score({})
    assert result["calculated_score"] == 50
    assert result["range"]["lower"] == 0
    assert result["range"]["upper"] == 100
    assert result["range"]["statistical_confidence_interval"] is False
    assert all(factor["provisional"] for factor in result["factors"].values())


def test_partial_discovery_has_observed_lower_bound_and_candidate_upper():
    context = golden()
    context["region_counts"]["EU"]["unassigned_candidates"] = 1
    result = calculate_score(context)
    assert result["range"]["lower"] == pytest.approx(55.0639452948)
    assert result["range"]["upper"] == pytest.approx(58)
    assert result["factors"]["customer"]["provisional"]


@pytest.mark.parametrize("override", [-1, 999, math.nan, math.inf, True])
def test_invalid_override(override):
    with pytest.raises(ValueError):
        calculate_score(golden(), override)


def test_zero_override_separate_from_calculation():
    result = calculate_score(golden(), 0)
    assert result["effective_score"] == 0
    assert result["calculated_score"] > 0
    assert result["override"] == 0


@pytest.mark.parametrize("key,value", [("severity_level", 0), ("severity_level", math.nan),
                                      ("severity_level", 1.5),
                                      ("complexity_level", 6), ("strategic_fit_level", -1),
                                      ("reference_volume", 0)])
def test_invalid_input_factors(key, value):
    context = golden()
    context[key] = value
    with pytest.raises(ValueError):
        calculate_score(context)


def test_missing_regions_are_gaps_and_zero_cohort_is_unavailable():
    context = golden()
    context["target_regions"] += ["US"]
    context["region_counts"]["EU"] = {"support": 0, "cohort": 0}
    result = calculate_score(context)
    assert result["factors"]["customer"]["provisional"]
    assert result["factors"]["reach"]["provisional"]
    assert any("US" in warning for warning in result["warnings"])


def test_scoped_forecasts_ignore_wrong_year_and_region_but_reject_conflicts():
    context = golden()
    context["forecasts"] += [{"vehicle_line": "G60", "region": "US", "year": 2030,
                              "volume": 999, "source_reference": "out-of-scope"},
                             {"vehicle_line": "G60", "region": "EU", "year": 2025,
                              "volume": 999, "source_reference": "old"}]
    assert calculate_score(context)["factors"]["reach"]["value"] == .5
    context["forecasts"][1]["volume"] = 101
    with pytest.raises(ValueError, match="conflicting"):
        calculate_score(context)


def test_forecasts_require_real_source_reference():
    context = golden()
    context["forecasts"][0].pop("source_reference")
    with pytest.raises(ValueError, match="source_reference"):
        calculate_score(context)


@pytest.mark.parametrize("record", [{"support": 4, "cohort": 3},
                                    {"support": 1, "cohort": 3, "unassigned_candidates": 3},
                                    {"support": 1.5, "cohort": 3}, {"support": -1, "cohort": 3}])
def test_invalid_counts(record):
    context = golden()
    context["region_counts"]["EU"] = record
    with pytest.raises(ValueError):
        calculate_score(context)


def test_study_unknown_count_conservative_index_and_separate_contradiction():
    result = calculate_evidence({"study_only": True, "target_regions": ["EU", "US"],
                                 "covered_regions": ["EU"], "corroboration": "direct",
                                 "assignment_reviews": [1], "contradiction": True})
    assert result["components"]["support"] == 0
    assert result["range"]["upper"] - result["range"]["lower"] == 35
    assert "support" in result["unknown_components"]
    assert any("Contradictory" in warning for warning in result["warnings"])


def test_support_ids_are_distinct_and_untraceable_assignments_rejected():
    result = calculate_evidence({"support_ids": ["a", "a", "b"]})
    assert result["distinct_support_comments"] == 2
    with pytest.raises(ValueError):
        calculate_evidence({"assignment_reviews": [0]})


def test_legacy_arithmetic_and_override_remain_on_original_scale():
    context = {"policy_version": "legacy-v0", "impact": 8, "market_reach_norm": 6, "effort": 4}
    result = calculate_score(context)
    assert result["calculated_score"] == 6.8
    assert result["scale"] == [0, 10]
    assert calculate_score(context, 0)["effective_score"] == 0
    with pytest.raises(ValueError):
        calculate_score(context, 50)


def test_invalid_weights_fail():
    with pytest.raises(ValueError):
        calculate_score({"weights": {"customer": .4, "reach": .4, "strategy": .4, "ease": .4}})


def test_stable_ranks_and_inactive_decisions():
    rows = [{"id": "B", "status": "DRAFT", "effective_score": 50},
            {"id": "A", "status": "APPROVED", "effective_score": 50},
            {"id": "C", "status": "REJECTED", "effective_score": 99},
            {"id": "D", "status": "POSTPONED", "effective_score": 98}]
    assert rank_requirements(rows) == {"A": 1, "B": 2, "C": None, "D": None}
    assert rank_requirements(reversed(rows)) == rank_requirements(rows)


def test_ranks_cannot_mix_policy_scales():
    with pytest.raises(ValueError, match="mix"):
        rank_requirements([{"id": "A", "effective_score": 9, "policy_version": "legacy-v0"},
                           {"id": "B", "effective_score": 50, "policy_version": "v1"}])


def test_missing_support_is_unknown_instead_of_zero():
    context = golden()
    context["region_counts"] = {"EU": {"cohort": 3}}
    assert calculate_score(context)["factors"]["customer"]["provisional"]
