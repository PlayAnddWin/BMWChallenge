"""Native-input contracts: realistic metadata, deliberately synthetic volumes."""
from copy import deepcopy
from math import fsum
import json

import pytest

from services.scoring import calculate_evidence, calculate_score


SALES_VERSION = "a" * 64
STUDY_VERSION = "b" * 64
PROXY_ID = "ASSUMPTION-G60-BEV-WHOLE-LINE"


def sales_fact(line, region, volume, row):
    identity = f"FACT-SALES-{line}-{region}-2030"
    record_id = f"RECORD-SALES-{line}-{region}"
    locator = {"filename": "sales_forecasts.xlsx", "sheet": "Forecast",
               "row": row, "column": 6, "column_header": "volume_2030"}
    return {"id": identity, "kind": "sales", "unit": "vehicles", "vehicle_line": line,
            "region": region, "year": 2030, "volume": volume,
            "source_file_id": "SOURCE-SALES", "source_record_id": record_id,
            "source_version": SALES_VERSION, "source_locator": locator,
            "source_reference": {"source_id": "SOURCE-SALES", "record_id": record_id,
                                 "source_version": SALES_VERSION, "locator": deepcopy(locator)},
            "applicability": "whole_line", "powertrain_split": "unknown"}


def pin_baseline(context, facts, total=None):
    context["reference_forecasts"] = deepcopy(facts)
    context["reference_volume"] = fsum(fact["volume"] for fact in facts) if total is None else total
    context["reference_policy"] = {"year": 2030, "fact_ids": [fact["id"] for fact in facts],
                                   "source_versions": sorted({fact["source_version"] for fact in facts}),
                                   "total": context["reference_volume"]}


def role1_score_context():
    facts = [sales_fact(line, region, volume, row) for row, (line, region, volume) in enumerate([
        ("G60_G68", "EU", 90000), ("G60_G68", "US", 60000),
        ("G60_G68", "CN", 80000), ("G60_G68", "RoW", 20000),
        ("G70", "EU", 35000), ("G70", "US", 25000),
        ("G70", "CN", 45000), ("G70", "RoW", 10000),
        ("U10", "EU", 50000), ("U10", "US", 30000), ("U10", "RoW", 15000),
    ], 2)]
    context = {"input_kind": "role1", "severity_level": 2, "strategic_fit_level": 2,
               "complexity_level": 3, "target_regions": ["EU"],
               "target_vehicle_lines": ["G60"], "target_powertrains": ["BEV"],
               "region_counts": {"EU": {"support": 2, "cohort": 3}},
               "forecasts": deepcopy(facts),
               "reach_assessment": {"kind": "whole_line_proxy", "permitted": True,
                                    "assumption_ids": [PROXY_ID],
                                    "reason": "2030 G60/G68 sales cover the combined line, not G60 BEV."},
               "assumptions": [{"id": PROXY_ID, "review_state": "pending",
                                "description": "Use combined whole-line volume only as the G60 BEV upper-bound scenario."}],
               "strategy_references": [{"source_id": "SOURCE-REPORT", "locator": {"page": 4}}],
               "strategy_rationale": "Reviewed synthetic passage supports this policy level."}
    pin_baseline(context, facts)
    return context


def test_permitted_bev_proxy_preserves_headline_and_propagates_priority_bounds():
    context = role1_score_context()
    before = deepcopy(context)
    result = calculate_score(context)
    reach = result["factors"]["reach"]
    ratio = 90000 / context["reference_policy"]["total"]
    assert reach["value"] == reach["upper"] == pytest.approx(ratio)
    assert reach["lower"] == 0
    assert reach["provisional"] is True
    assert reach["availability"] == "provisional"
    assert reach["kind"] == "whole_line_proxy"
    assert reach["volume_interpretation"] == "sourced_whole_line_upper_bound"
    assert "upper-bound" in reach["headline_convention"]
    assert result["required_assumption_ids"] == reach["required_assumption_ids"] == [PROXY_ID]
    assert result["range"]["upper"] == result["calculated_score"]
    assert result["range"]["lower"] == pytest.approx(40 * (.3 + .4 * (2 / 3) ** .5) + 17.5)
    assert result["range"]["upper"] - result["range"]["lower"] == pytest.approx(25 * ratio)
    assert result["range"]["statistical_confidence_interval"] is False
    assert context == before
    overridden = calculate_score(context, override=0)
    assert overridden["effective_score"] == 0
    assert overridden["range"] == result["range"]
    result["factors"]["reach"]["reference_policy"]["fact_ids"].clear()
    result["factors"]["reach"]["assessment"]["assumption_ids"].clear()
    result["factors"]["reach"]["references"][0]["locator"]["row"] = 999
    assert context == before


def test_proxy_bounds_combine_with_customer_discovery_uncertainty():
    context = role1_score_context()
    original = calculate_score(context)
    context["region_counts"]["EU"]["unassigned_candidates"] = 1
    result = calculate_score(context)
    assert result["range"]["lower"] == original["range"]["lower"]
    assert result["range"]["upper"] - original["range"]["upper"] == pytest.approx(16 * (1 - (2 / 3) ** .5))
    assert result["range"]["lower"] <= result["calculated_score"] <= result["range"]["upper"]


def test_native_zero_forecast_is_known_but_missing_forecast_is_unavailable():
    context = role1_score_context()
    context.update(target_vehicle_lines=["G70"], target_powertrains=["ALL"])
    context["reach_assessment"].update(kind="direct", assumption_ids=[])
    for fact in context["forecasts"]:
        if fact["vehicle_line"] == "G70" and fact["region"] == "EU":
            fact["volume"] = 0
    pin_baseline(context, context["forecasts"])
    result = calculate_score(context)
    reach = result["factors"]["reach"]
    assert reach["availability"] == "available"
    assert reach["value"] == reach["lower"] == reach["upper"] == reach["target_volume"] == 0
    assert reach["provisional"] is False
    assert any("zero-volume" in warning for warning in result["warnings"])
    context["forecasts"] = [fact for fact in context["forecasts"]
                            if not (fact["vehicle_line"] == "G70" and fact["region"] == "EU")]
    missing = calculate_score(context)
    assert missing["factors"]["reach"]["availability"] == "unavailable"
    assert missing["factors"]["reach"]["value"] == .5
    assert "target_volume" not in missing["factors"]["reach"]
    assert any("G70/EU" in warning for warning in missing["warnings"])


def test_zero_proxy_remains_labeled_provisional_even_when_its_bounds_collapse():
    context = role1_score_context()
    context["forecasts"][0]["volume"] = 0
    pin_baseline(context, context["forecasts"])
    reach = calculate_score(context)["factors"]["reach"]
    assert reach["value"] == reach["lower"] == reach["upper"] == 0
    assert reach["availability"] == "provisional"
    assert reach["provisional"] is True


def test_known_zero_comment_support_does_not_become_missing_support():
    context = role1_score_context()
    context["region_counts"]["EU"]["support"] = 0
    known = calculate_score(context)["factors"]["customer"]
    assert known["value"] == known["lower"] == known["upper"] == .3
    context["region_counts"]["EU"].pop("support")
    missing = calculate_score(context)["factors"]["customer"]
    assert missing["value"] == .5
    assert missing["lower"] == .3
    assert missing["upper"] == .7


def test_native_g60_g68_dedup_keeps_both_provenances_and_fixed_reference():
    context = role1_score_context()
    total = context["reference_volume"]
    facts = context["forecasts"][1:] + [sales_fact("G60", "EU", 90000, 20),
                                          sales_fact("G68", "EU", 90000, 21)]
    context.update(forecasts=deepcopy(facts), target_vehicle_lines=["G60", "G68"], target_powertrains=["ALL"])
    context["reach_assessment"].update(kind="direct", assumption_ids=[])
    pin_baseline(context, facts, total)
    expected = calculate_score(context)
    reach = expected["factors"]["reach"]
    assert reach["target_volume"] == 90000
    assert reach["reference_volume"] == total
    assert reach["value"] == pytest.approx(90000 / total)
    assert len(reach["references"]) == len(reach["fact_ids"]) == 2
    assert reach["provisional"] is False
    context["forecasts"].reverse()
    context["reference_forecasts"].reverse()
    context["reference_policy"]["fact_ids"].reverse()
    context["target_vehicle_lines"].reverse()
    assert calculate_score(context) == expected
    context["forecasts"][0]["volume"] += 1
    with pytest.raises(ValueError, match="snapshot|conflicting"):
        calculate_score(context)


def test_reference_is_invariant_under_target_changes_and_can_use_separate_baseline():
    context = role1_score_context()
    context["forecasts"] = [fact for fact in context["forecasts"] if fact["region"] == "EU"]
    first = calculate_score(context)["factors"]["reach"]
    context.update(target_vehicle_lines=["G70"], target_powertrains=["ALL"])
    context["reach_assessment"].update(kind="direct", assumption_ids=[])
    second = calculate_score(context)["factors"]["reach"]
    assert first["reference_policy"] == second["reference_policy"]
    assert first["reference_volume"] == second["reference_volume"] == context["reference_policy"]["total"]
    assert first["value"] == pytest.approx(90000 / first["reference_volume"])
    assert second["value"] == pytest.approx(35000 / second["reference_volume"])
    context.pop("reference_volume")
    assert calculate_score(context)["factors"]["reach"] == second


def test_reference_policy_cannot_be_rebased_to_only_the_current_target():
    context = role1_score_context()
    context["reference_policy"].update(fact_ids=[context["forecasts"][0]["id"]], total=90000)
    context["reference_volume"] = 90000
    with pytest.raises(ValueError, match="complete fixed"):
        calculate_score(context)


def test_full_forecasts_can_supply_the_baseline_and_native_reference_aliases():
    context = role1_score_context()
    expected = calculate_score(context)
    context.pop("reference_forecasts")
    assert calculate_score(context) == expected
    for fact in context["forecasts"]:
        reference = fact["source_reference"]
        fact["source_reference"] = {"source_file_id": reference["source_id"],
                                    "source_record_id": reference["record_id"],
                                    "source_version": reference["source_version"],
                                    "source_locator": reference["locator"]}
    assert calculate_score(context)["calculated_score"] == expected["calculated_score"]


def test_explicit_reference_volume_cannot_disagree_with_the_pinned_policy():
    context = role1_score_context()
    context["reference_volume"] += 1
    with pytest.raises(ValueError, match="reference_volume disagrees"):
        calculate_score(context)


@pytest.mark.parametrize("field,value", [("year", 2025), ("fact_ids", ["MISSING-FACT"]),
                                         ("source_versions", ["different-snapshot"]),
                                         ("total", 0), ("total", 1), ("total", float("nan"))])
def test_reference_policy_must_match_the_native_fixed_snapshot(field, value):
    context = role1_score_context()
    context["reference_policy"][field] = value
    with pytest.raises(ValueError, match="reference_policy"):
        calculate_score(context)


def test_native_context_has_no_production_reference_default():
    context = role1_score_context()
    context.pop("reference_policy")
    context.pop("reference_volume")
    with pytest.raises(ValueError, match="reference_policy"):
        calculate_score(context)


@pytest.mark.parametrize("assumptions", [[], ["An uncatalogued whole-line proxy"],
                                        [{"id": PROXY_ID, "description": " "}]])
def test_proxy_is_unavailable_without_its_explicit_described_assumption(assumptions):
    context = role1_score_context()
    context["assumptions"] = assumptions
    result = calculate_score(context)
    assert result["factors"]["reach"]["availability"] == "unavailable"
    assert "target_volume" not in result["factors"]["reach"]
    assert PROXY_ID in result["required_assumption_ids"]
    assert any(PROXY_ID in warning for warning in result["warnings"])


def test_proxy_with_no_assumption_ids_or_no_permission_cannot_establish_reach():
    context = role1_score_context()
    context["reach_assessment"]["assumption_ids"] = []
    assert calculate_score(context)["factors"]["reach"]["availability"] == "unavailable"
    context = role1_score_context()
    context["reach_assessment"]["permitted"] = False
    assert calculate_score(context)["factors"]["reach"]["availability"] == "unavailable"
    context.pop("reach_assessment")
    assert calculate_score(context)["factors"]["reach"]["availability"] == "unavailable"


@pytest.mark.parametrize("powertrains", [["BEV"], ["ALL"]])
def test_direct_label_cannot_turn_g60_whole_line_sales_into_derivative_certainty(powertrains):
    context = role1_score_context()
    context["target_powertrains"] = powertrains
    context["reach_assessment"].update(kind="direct", assumption_ids=[])
    reach = calculate_score(context)["factors"]["reach"]
    assert reach["availability"] == "unavailable"
    assert "not direct" in reach["reason"]


def test_successor_bev_proxy_requires_an_explicit_sales_line_map_and_all_assumptions():
    context = role1_score_context()
    successor_id = "ASSUMPTION-F70-U10-SUCCESSOR"
    context["target_vehicle_lines"] = ["F70"]
    context["reach_assessment"].update(kind="successor_proxy", vehicle_line_map={"F70": "U10"},
                                        assumption_ids=[PROXY_ID, successor_id],
                                        reason="U10 is a reviewed successor proxy, still using unsplit whole-line sales.")
    context["assumptions"].append({"id": successor_id, "description": "U10 is an explicit planning proxy for the F70 target."})
    context["required_assumption_ids"] = ["ASSUMPTION-FEASIBILITY"]
    result = calculate_score(context)
    reach = result["factors"]["reach"]
    assert reach["target_volume"] == 50000
    assert reach["upper"] == pytest.approx(50000 / context["reference_volume"])
    assert reach["lower"] == 0
    assert result["required_assumption_ids"] == sorted([PROXY_ID, successor_id, "ASSUMPTION-FEASIBILITY"])
    context["assumptions"].pop()
    assert calculate_score(context)["factors"]["reach"]["availability"] == "unavailable"
    context["reach_assessment"].pop("vehicle_line_map")
    assert calculate_score(context)["factors"]["reach"]["availability"] == "unavailable"


@pytest.mark.parametrize("field", ["target_regions", "target_vehicle_lines", "target_powertrains"])
def test_production_target_scope_cannot_be_empty_or_inferred_from_available_facts(field):
    context = role1_score_context()
    context[field] = []
    with pytest.raises(ValueError, match="meaningful target"):
        calculate_score(context)


@pytest.mark.parametrize("field,value", [("kind", "survey_distribution"), ("unit", "percent"),
                                         ("source_file_id", ""), ("source_record_id", None),
                                         ("source_version", ""), ("volume", None),
                                         ("volume", True), ("volume", -1),
                                         ("applicability", "BEV"), ("powertrain_split", "known")])
def test_native_sales_validation_is_not_bypassed_by_arithmetic_values(field, value):
    context = role1_score_context()
    context["forecasts"][0][field] = value
    with pytest.raises(ValueError):
        calculate_score(context)


@pytest.mark.parametrize("field,value", [("source_id", "OTHER-SOURCE"), ("record_id", "OTHER-RECORD"),
                                         ("source_version", "OTHER-VERSION"),
                                         ("locator", {"sheet": "Forecast", "row": 999, "column": 6})])
def test_native_forecast_reference_must_agree_with_provenance(field, value):
    context = role1_score_context()
    context["forecasts"][0]["source_reference"][field] = value
    with pytest.raises(ValueError, match="source_reference"):
        calculate_score(context)


def test_native_metadata_is_detected_without_opt_in_and_cannot_be_downgraded_to_synthetic():
    context = role1_score_context()
    context.pop("input_kind")
    assert calculate_score(context)["input_basis"] == "role1_metadata"
    context["input_kind"] = "synthetic"
    with pytest.raises(ValueError, match="native metadata"):
        calculate_score(context)


def test_fact_snapshot_and_source_record_snapshot_mismatches_are_rejected():
    context = role1_score_context()
    context["fact_snapshots"] = deepcopy(context["reference_forecasts"])
    context["source_snapshots"] = [dict(deepcopy(fact["source_reference"]),
                                        locator={key: value for key, value in fact["source_locator"].items()
                                                 if key not in ("column", "column_header")})
                                    for fact in context["reference_forecasts"]]
    assert calculate_score(context)["factors"]["reach"]["availability"] == "provisional"
    context["fact_snapshots"][0]["volume"] += 1
    with pytest.raises(ValueError, match="native fact snapshot"):
        calculate_score(context)
    context["fact_snapshots"] = deepcopy(context["reference_forecasts"])
    context["source_snapshots"][0]["locator"]["row"] = 999
    with pytest.raises(ValueError, match="source record snapshot"):
        calculate_score(context)


def test_a_sales_baseline_cannot_mix_versions_of_the_same_source_file():
    context = role1_score_context()
    context["forecasts"][-1]["source_version"] = "c" * 64
    context["forecasts"][-1]["source_reference"]["source_version"] = "c" * 64
    pin_baseline(context, context["forecasts"])
    with pytest.raises(ValueError, match="mixed source versions"):
        calculate_score(context)


def survey_fact(identity="FACT-STUDY-TEMPERATURE", relation="direct"):
    return {"id": identity, "kind": "survey_distribution", "unit": "fraction",
            "metric": "Temperature control access", "derivative": "G60", "region": "EU",
            "distribution": {str(index): value for index, value in enumerate([.05, .1, .15, .2, .2, .2, .1], 1)},
            "respondent_n": None, "scale": None, "sample_total": 1,
            "source_file_id": "SOURCE-STUDY", "source_record_id": f"RECORD-{identity}",
            "source_version": STUDY_VERSION,
            "source_locator": {"filename": "customer_study.xlsx", "sheet": "EU", "row": 9, "column": 3},
            "mapping_strength": relation,
            "topic_mapping": {"id": f"MAPPING-{identity}", "relation": relation,
                              "review_state": "reviewed", "reason": "Human-reviewed mapping to this exact usability issue.",
                              "reviewed_by": "pm-fixture", "reviewed_at": "2026-10-01T12:00:00Z"}}


def role1_evidence_context():
    return {"input_kind": "role1", "support_ids": ["COMMENT-TEMP-1", "COMMENT-TEMP-2"],
            "distinct_support_comments": 999, "target_regions": ["EU"], "covered_regions": ["EU"],
            "assignment_reviews": [.5], "survey_facts": [survey_fact()], "source_count": 2,
            "support_source_counts": {"SOURCE-FEEDBACK": 2}}


def seven_category_survey_fact():
    fact = survey_fact()
    categories = ["I Hate It", "A Failure", "Unsatisfactory", "Satisfactory",
                  "Excellent", "Delightful", "I Love It"]
    cells = [{"filename": "customer_study.xlsx", "sheet": "EU", "row": row, "column": 3,
              "source_record_id": f"RECORD-STUDY-CATEGORY-{row}", "column_header": "G60_EU",
              "response_category": category, "value_provenance": "source_numeric_cell"}
             for row, category in enumerate(categories, 9)]
    locator = {"filename": "customer_study.xlsx", "sheet": "EU", "row": cells[0]["row"],
               "question": "Temperature control access", "cells": deepcopy(cells)}
    fact.update(distribution=dict(zip(categories, [.05, .1, .15, .2, .2, .2, .1])),
                category_labels=categories, cell_locators=cells, source_locator=locator,
                source_record_id=cells[0]["source_record_id"])
    fact["source_reference"] = {"source_id": fact["source_file_id"], "record_id": fact["source_record_id"],
                                "source_version": fact["source_version"], "locator": deepcopy(locator),
                                "claim_key": "study-corroboration", "relation": "context", "validation_state": "verified"}
    return fact


def corporate_report_reference():
    quote = "Synthetic corporate-report passage for scoring contract verification."
    return {"source_id": "SOURCE-CORPORATE-REPORT", "record_id": "CHUNK-CORPORATE-REPORT-004",
            "source_version": "c" * 64, "claim_key": "strategy", "relation": "context", "validation_state": "verified",
            "locator": {"filename": "BMW-Group-Report-2025-en.pdf", "page": 4, "printed_page": None,
                        "span": {"start_offset": 0, "end_offset": len(quote), "quote": quote}}}


def proposed_factor_assessments():
    return {name: {"kind": "proposed_assessment", "reason": f"Unreviewed proposed {name} policy level.",
                   "assumption_ids": [f"ASSUMPTION-{name.upper()}-ASSESSMENT", "ASSUMPTION-SHARED-REVIEW"],
                   "provenance": "AIProposed"}
            for name in ("customer", "strategy", "ease")}


def test_native_seven_category_locator_and_null_report_page_roundtrip():
    from models import EvidenceResult, SourceReference
    from services.providers import GraphSourceProvider

    fact = seven_category_survey_fact()
    context = role1_evidence_context()
    context["survey_facts"] = [fact]
    before = deepcopy(context)
    for reference in (fact["source_reference"], corporate_report_reference()):
        validated = SourceReference.model_validate(reference).model_dump(mode="json")
        assert validated == reference
        assert GraphSourceProvider._valid_locator(validated["locator"]) is True
    result = calculate_evidence(context)
    assert result["value"] == pytest.approx(67.2795359447)
    assert result["distinct_support_comments"] == 2
    assert result["survey_unknowns"][fact["id"]] == ["respondent_n", "scale"]
    stored = EvidenceResult.model_validate(result).model_dump(mode="json")
    reference = stored["corroboration_details"][0]["references"][0]
    assert reference == fact["source_reference"]
    assert len(reference["locator"]["cells"]) == 7
    assert [cell["response_category"] for cell in reference["locator"]["cells"]] == fact["category_labels"]
    assert json.loads(json.dumps(stored, allow_nan=False)) == stored
    reference["locator"]["cells"][-1]["source_record_id"] = "changed"
    assert context == before


def test_score_preview_roundtrips_nested_study_and_report_references_with_assessments(backend):
    client, repository, _, _ = backend
    before = repository.read()
    context = role1_score_context()
    context.update(customer_references=[seven_category_survey_fact()["source_reference"]],
                   strategy_references=[corporate_report_reference()], factor_assessments=proposed_factor_assessments())
    response = client.post("/api/scoring/preview", json=context)
    assert response.status_code == 200, response.text
    score = response.json()["score"]
    assert score == calculate_score(context)
    assert score["factors"]["customer"]["references"] == context["customer_references"]
    assert score["factors"]["strategy"]["references"][0]["locator"]["printed_page"] is None
    assert score["factors"]["strategy"]["references"][0]["locator"]["span"]["start_offset"] == 0
    assert all(score["factors"][name]["provisional"] for name in ("customer", "strategy", "ease"))
    assert repository.read() == before


@pytest.mark.parametrize("locator", [None, [], {}, {"page": True}, {"page": 0}, {"page": "4"},
                                     {"row": 1.0}, {"column": 0}, {"column": True}, {"chunk_number": -1},
                                     {"row": {"value": 9}}, {"printed_page": False}, {"filename": " "},
                                     {"unreported_coordinate": None}, {"cells": []}, {"cells": [None]},
                                     {"cells": [True]}, {"cells": ["invented-cell"]}, {"cells": [{}]},
                                     {"cells": [{"row": 0}]}, {"cells": [{"column": "3"}]},
                                     {"cells": [{"source_record_id": " "}]}, {"cells": [{"value_provenance": None}]},
                                     {"cells": [{" ": "unreported"}]}, {"cells": [{1: "unreported"}]},
                                     {"cells": [{"metadata": ()}]}, {"span": {"start_offset": -1, "end_offset": 4}},
                                     {"span": {"start_offset": True}}, {"span": {"end_offset": 4.0}},
                                     {"position": float("nan")}, {"position": float("inf")},
                                     {"span": {"quote": "\t"}}])
def test_reference_locator_rejects_invented_or_invalid_nested_coordinates(locator):
    context = role1_score_context()
    reference = corporate_report_reference()
    reference["locator"] = locator
    context["strategy_references"] = [reference]
    with pytest.raises(ValueError, match="strategy_references"):
        calculate_score(context)


@pytest.mark.parametrize("field,value", [("source_record_id", "INVENTED-ROW"),
                                         ("response_category", "Invented category"),
                                         ("value_provenance", "invented"), ("row", 999), ("column", 99)])
def test_nested_distribution_reference_cannot_change_pinned_cell_identity(field, value):
    context = role1_evidence_context()
    fact = seven_category_survey_fact()
    fact["source_reference"]["locator"]["cells"][-1][field] = value
    context["survey_facts"] = [fact]
    with pytest.raises(ValueError, match="source_reference disagrees with native source_locator"):
        calculate_evidence(context)


def test_nested_distribution_cell_order_and_metadata_types_are_exact_snapshot_identity():
    context = role1_evidence_context()
    fact = seven_category_survey_fact()
    context["survey_facts"] = [fact]
    fact["source_reference"]["locator"]["cells"].reverse()
    with pytest.raises(ValueError, match="source_reference disagrees"):
        calculate_evidence(context)
    fact["source_reference"]["locator"] = deepcopy(fact["source_locator"])
    for locator in (fact["source_reference"]["locator"], fact["source_locator"]):
        locator["cells"][0]["native_metadata"] = {"position": 1}
    fact["source_reference"]["locator"]["cells"][0]["native_metadata"]["position"] = 1.0
    with pytest.raises(ValueError, match="source_reference disagrees"):
        calculate_evidence(context)


def test_distribution_cell_locators_cannot_disagree_with_the_reference_cells():
    context = role1_evidence_context()
    fact = seven_category_survey_fact()
    context["survey_facts"] = [fact]
    fact["cell_locators"][-1]["response_category"] = "Invented category"
    with pytest.raises(ValueError, match="native distribution cell_locators"):
        calculate_evidence(context)


def test_sales_still_requires_top_level_sheet_row_column_with_nested_locator_support():
    context = role1_score_context()
    fact = context["forecasts"][0]
    fact["source_locator"] = {"cells": [deepcopy(fact["source_locator"])]}
    fact["source_reference"]["locator"] = deepcopy(fact["source_locator"])
    with pytest.raises(ValueError, match="native sales cell"):
        calculate_score(context)


def test_proposed_assessments_are_provisional_without_changing_arithmetic_or_bounds():
    context = role1_score_context()
    baseline = calculate_score(context)
    context["factor_assessments"] = proposed_factor_assessments()
    context["required_assumption_ids"] = ["ASSUMPTION-FEASIBILITY"]
    before = deepcopy(context)
    result = calculate_score(context)
    assert result["calculated_score"] == baseline["calculated_score"]
    assert result["range"] == baseline["range"]
    for name in ("customer", "strategy", "ease"):
        factor = result["factors"][name]
        assessment = context["factor_assessments"][name]
        assert factor["assessment"] == dict(assessment, assumption_ids=sorted(assessment["assumption_ids"]))
        assert factor["reason"] == assessment["reason"]
        assert factor["assessment"]["provenance"] == "AIProposed"
        assert factor["availability"] == "provisional"
        assert factor["provisional"] is True
        for key in ("value", "lower", "upper", "weight", "contribution", "references"):
            assert factor[key] == baseline["factors"][name][key]
    expected_ids = {PROXY_ID, "ASSUMPTION-FEASIBILITY"} | {
        identity for assessment in context["factor_assessments"].values() for identity in assessment["assumption_ids"]}
    assert result["required_assumption_ids"] == sorted(expected_ids)
    result["factors"]["customer"]["assessment"]["assumption_ids"].append("changed")
    assert context == before
    overridden = calculate_score(context, override=0)
    assert overridden["effective_score"] == 0
    assert overridden["calculated_score"] == baseline["calculated_score"]
    assert overridden["range"] == baseline["range"]


@pytest.mark.parametrize("name,level", [("customer", "severity_level"), ("strategy", "strategic_fit_level"),
                                       ("ease", "complexity_level")])
def test_explicit_unknown_assessment_uses_midpoint_and_full_bounds(name, level):
    context = role1_score_context()
    context.pop(level)
    assessment = {"kind": "unknown", "reason": f"No reviewed {name} assessment available.",
                  "assumption_ids": ["ASSUMPTION-UNKNOWN-ASSESSMENT"], "provenance": "AIProposed"}
    context["factor_assessments"] = {name: assessment}
    score = calculate_score(context)
    factor = score["factors"][name]
    assert factor["assessment"] == assessment
    assert factor["reason"] == assessment["reason"]
    assert factor["value"] == .5
    assert factor["lower"] == 0
    assert factor["upper"] == 1
    assert factor["provisional"] is True
    assert factor["availability"] == "unknown"
    assert score["required_assumption_ids"] == sorted([PROXY_ID, "ASSUMPTION-UNKNOWN-ASSESSMENT"])
    assert score["range"]["lower"] <= score["calculated_score"] <= score["range"]["upper"]


@pytest.mark.parametrize("name", ["customer", "strategy", "ease"])
def test_unknown_assessment_cannot_silently_discard_a_supplied_level(name):
    context = role1_score_context()
    context["factor_assessments"] = {name: {"kind": "unknown", "reason": "No reviewed policy level.",
                                          "assumption_ids": [], "provenance": "AIProposed"}}
    with pytest.raises(ValueError, match="unknown assessment cannot supply"):
        calculate_score(context)


@pytest.mark.parametrize("field,value", [("kind", "known"), ("kind", True), ("reason", " "),
                                         ("provenance", "PMEntered"), ("assumption_ids", "unscoped-assumption"),
                                         ("assumption_ids", [""]), ("metadata", float("nan"))])
def test_factor_assessment_metadata_is_validated(field, value):
    context = role1_score_context()
    context["factor_assessments"] = proposed_factor_assessments()
    context["factor_assessments"]["strategy"][field] = value
    with pytest.raises(ValueError, match="factor_assessments"):
        calculate_score(context)


def test_unknown_survey_n_and_scale_remain_visible_with_known_comment_support():
    context = role1_evidence_context()
    before = deepcopy(context)
    result = calculate_evidence(context)
    assert result["distinct_support_comments"] == 2
    assert result["value"] == pytest.approx(67.2795359447)
    assert result["corroboration_kind"] == "direct"
    assert result["components"]["corroboration"] == 1
    assert result["unknown_components"] == []
    assert result["survey_unknowns"] == {"FACT-STUDY-TEMPERATURE": ["respondent_n", "scale"]}
    assert result["range"]["lower"] == result["range"]["upper"]
    assert any("Unknown survey respondent N" in warning for warning in result["warnings"])
    assert any("Unknown native survey scale" in warning for warning in result["warnings"])
    assert not any("Unknown distinct customer" in warning for warning in result["warnings"])
    assert context == before
    result["corroboration_details"][0]["mapping"]["reviewed_by"] = "changed"
    result["corroboration_details"][0]["references"][0]["locator"]["row"] = 999
    assert context == before
    context["survey_facts"][0].update(respondent_n=250, scale="1-7 explicitly labeled categories")
    known = calculate_evidence(context)
    assert known["value"] == result["value"]
    assert known["survey_unknowns"] == {}


@pytest.mark.parametrize("relation,value,contradiction", [("unmapped", 0, False), ("broad", .5, False),
                                                        ("direct", 1, False), ("contradictory", 0, True)])
def test_corroboration_and_opposition_come_from_reviewed_relations_only(relation, value, contradiction):
    context = role1_evidence_context()
    context["survey_facts"] = [survey_fact(relation=relation)]
    context.update(corroboration="direct", contradiction=not contradiction)
    result = calculate_evidence(context)
    assert result["corroboration_kind"] == relation
    assert result["components"]["corroboration"] == value
    assert result["contradiction"] is contradiction
    assert result["distinct_support_comments"] == 2
    assert any("Contradictory evidence" in warning for warning in result["warnings"]) is contradiction


def test_pending_mapping_and_mapping_strength_scalar_do_not_upgrade_evidence():
    context = role1_evidence_context()
    context["survey_facts"][0]["topic_mapping"]["review_state"] = "pending"
    context.update(corroboration=1, contradiction=True)
    result = calculate_evidence(context)
    assert result["components"]["corroboration"] == 0
    assert result["corroboration_kind"] == "unmapped"
    assert result["contradiction"] is False
    context["survey_facts"][0]["topic_mapping"] = None
    assert calculate_evidence(context)["components"]["corroboration"] == 0


def test_reviewed_contradiction_is_separate_from_direct_support():
    context = role1_evidence_context()
    baseline = calculate_evidence(context)
    context["survey_facts"].append(survey_fact("FACT-STUDY-OPPOSITION", "contradictory"))
    result = calculate_evidence(context)
    assert result["value"] == baseline["value"]
    assert result["contradiction"] is True
    assert {detail["kind"] for detail in result["corroboration_details"]} == {"direct", "contradictory"}


def test_scoped_resolver_assessment_can_limit_but_cannot_invent_corroboration():
    context = role1_evidence_context()
    baseline = calculate_evidence(context)
    context["corroboration_assessment"] = {"kind": "broad", "review_state": "reviewed",
                                          "fact_ids": [context["survey_facts"][0]["id"]],
                                          "reason": "Reviewed survey mapping is broad for this narrower requirement."}
    assert calculate_evidence(context)["value"] == pytest.approx(baseline["value"] - 12.5)
    context["corroboration_assessment"]["fact_ids"] = ["UNRESOLVED-FACT"]
    with pytest.raises(ValueError, match="available native survey facts"):
        calculate_evidence(context)


def test_scoped_positive_assessment_does_not_hide_reviewed_opposition():
    context = role1_evidence_context()
    context["survey_facts"].append(survey_fact("FACT-STUDY-OPPOSITION", "contradictory"))
    context["corroboration_assessment"] = {"kind": "broad", "review_state": "reviewed",
                                          "fact_ids": ["FACT-STUDY-TEMPERATURE"],
                                          "reason": "Supporting survey is broad for this scoped requirement."}
    result = calculate_evidence(context)
    assert result["components"]["corroboration"] == .5
    assert result["contradiction"] is True
    assert any("Contradictory evidence" in warning for warning in result["warnings"])


def test_source_concentration_and_regional_gaps_are_exposed_independently():
    context = role1_evidence_context()
    context["target_regions"].append("CN")
    context["coverage_gaps"] = ["No imported China free-text feedback"]
    result = calculate_evidence(context)
    assert result["components"]["coverage"] == .5
    assert result["source_count"] == 2
    assert result["source_concentration"]["support_source_counts"] == {"SOURCE-FEEDBACK": 2}
    assert result["source_concentration"]["largest_support_source_share"] == 1
    assert result["source_concentration"]["unattributed_support_comments"] == 0
    assert result["coverage_gaps"] == ["CN", "No imported China free-text feedback"]
    assert any("comment support concentrated in one source" in warning for warning in result["warnings"])
    assert any("CN" in warning for warning in result["warnings"])


def test_unmapped_real_context_cannot_use_legacy_scalar_corroboration():
    result = calculate_evidence({"input_kind": "role1", "target_regions": ["EU"],
                                 "distinct_support_comments": 2, "corroboration": "direct", "contradiction": True})
    assert result["components"]["corroboration"] == 0
    assert result["corroboration_kind"] == "unmapped"
    assert result["contradiction"] is False
    assert result["input_basis"] == "role1_metadata"


@pytest.mark.parametrize("value", [True, -1, 1.5, float("nan"), "250"])
def test_native_survey_n_is_validated_even_when_comment_support_is_known(value):
    context = role1_evidence_context()
    context["survey_facts"][0]["respondent_n"] = value
    with pytest.raises(ValueError):
        calculate_evidence(context)


def test_real_evidence_requires_target_regions():
    context = role1_evidence_context()
    context["target_regions"] = []
    with pytest.raises(ValueError, match="meaningful target_regions"):
        calculate_evidence(context)


def test_arithmetic_inputs_remain_compatible_and_are_labeled_synthetic():
    score = calculate_score({"severity_level": 2, "strategic_fit_level": 2, "complexity_level": 3,
                             "target_regions": ["EU"], "target_vehicle_lines": ["G60", "G68"],
                             "region_counts": {"EU": {"support": 2, "cohort": 3}}, "reference_volume": 200,
                             "forecasts": [{"vehicle_line": line, "region": "EU", "year": 2030,
                                            "volume": 100, "source_reference": "synthetic-cell"}
                                           for line in ("G60", "G68")]})
    assert score["calculated_score"] == pytest.approx(55.0639452948)
    assert score["input_basis"] == "synthetic_arithmetic"
    evidence = calculate_evidence({"distinct_support_comments": 2, "target_regions": ["EU"],
                                   "covered_regions": ["EU"], "corroboration": "direct", "assignment_reviews": [.5]})
    assert evidence["value"] == pytest.approx(67.2795359447)
    assert evidence["input_basis"] == "synthetic_arithmetic"
