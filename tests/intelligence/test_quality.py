from datetime import date

import pytest

from contracts.intelligence import RequirementDraft
from services.common import EngineError
from services.drafting import validate_draft
from tests.intelligence.fakes import SCOPE, draft_payload

AS_OF = date(2026, 10, 10)


def validate(payload, signal, pack):
    return validate_draft(RequirementDraft.model_validate(payload), signal, pack, SCOPE, as_of=AS_OF)


def test_customer_quotes_cannot_prove_engineering_feasibility(signal, pack):
    payload = draft_payload(signal, pack)
    payload["smart"]["feasibility"].update(provenance="SourceBacked", evidence_ids=[pack.supporting[0].id],
                                          text="The current cabin layout makes the change feasible.")
    with pytest.raises(EngineError, match="feasibility"):
        validate(payload, signal, pack)


def test_generic_strategy_is_not_reviewed_capability_evidence(signal, pack):
    payload = draft_payload(signal, pack)
    payload["smart"]["feasibility"].update(provenance="SourceBacked", evidence_ids=[pack.strategy[0].id])
    with pytest.raises(EngineError, match="feasibility"):
        validate(payload, signal, pack)


def test_explicitly_reviewed_capability_can_support_unreviewed_feasibility(signal, pack):
    passage = pack.strategy[0].model_copy(update={"reviewed_claim_types": ["feasibility"],
                                                "text": "Synthetic reviewed capability: the fixture control path can be configured."})
    pack = pack.model_copy(update={"strategy": [passage]})
    payload = draft_payload(signal, pack)
    payload["smart"]["feasibility"].update(provenance="SourceBacked", evidence_ids=[passage.id])
    draft = validate(payload, signal, pack)
    assert draft.smart.feasibility.review_state == "unreviewed"


@pytest.mark.parametrize("milestone", ["Review by Q2 2024", "SOP 2028", "Launch in 2030"])
def test_past_or_arbitrary_calendar_milestones_rejected(milestone, signal, pack):
    payload = draft_payload(signal, pack)
    payload["smart"]["milestone"]["text"] = milestone
    with pytest.raises(EngineError, match="milestone|planning date"):
        validate(payload, signal, pack)


def test_named_gate_without_invented_date_remains_a_reviewable_proposal(signal, pack):
    payload = draft_payload(signal, pack)
    payload["smart"]["milestone"]["text"] = "G1 concept freeze; date to be confirmed by PM"
    draft = validate(payload, signal, pack)
    assert draft.smart.milestone.provenance == "AIProposed"


@pytest.mark.parametrize("text", ["Complete within 5 seconds", "Conduct the study with 30 participants"])
def test_hidden_numeric_test_thresholds_or_sample_size_rejected(text, signal, pack):
    payload = draft_payload(signal, pack)
    payload["smart"]["metrics"][0]["test_method"] = text
    with pytest.raises(EngineError, match="parameters"):
        validate(payload, signal, pack)


def test_extra_numeric_condition_has_own_provenance_and_review_state(signal, pack):
    payload = draft_payload(signal, pack)
    metric = payload["smart"]["metrics"][0]
    metric.update(value=0.8, unit="proportion", operator=">=", conditions="At least 80% task completion within 5 seconds",
                  test_method="Observe completion without assistance", parameters=[{
                      "id": "parameter-time-window", "purpose": "test_condition", "value": 5.0, "unit": "seconds",
                      "provenance": "AIProposed", "review_state": "unreviewed", "evidence_ids": [],
                  }])
    draft = validate(payload, signal, pack)
    assert draft.smart.metrics[0].parameters[0].review_state == "unreviewed"
    metric["parameters"][0]["review_state"] = "accepted"
    with pytest.raises(EngineError, match="human review"):
        validate(payload, signal, pack)


def test_time_limit_cannot_be_mistagged_as_same_number_of_participants(signal, pack):
    payload = draft_payload(signal, pack)
    metric = payload["smart"]["metrics"][0]
    metric.update(conditions="Complete within 30 seconds", parameters=[{
        "id": "sample-size", "purpose": "sample_size", "value": 30.0, "unit": "participants",
        "provenance": "AIProposed", "evidence_ids": [],
    }])
    with pytest.raises(EngineError, match="units"):
        validate(payload, signal, pack)


def test_numeric_strategy_assessment_needs_actual_strategy_citation(signal, pack):
    payload = draft_payload(signal, pack)
    payload["factor_proposals"].append({"factor": "strategic_fit", "value": 4.0,
                                        "reason": "BMW innovation", "evidence_ids": []})
    with pytest.raises(EngineError, match="strategy citation"):
        validate(payload, signal, pack)


def test_unknown_strategy_and_effort_remain_null_not_false_precision(signal, pack):
    payload = draft_payload(signal, pack)
    payload["factor_proposals"] += [
        {"factor": "strategic_fit", "value": None, "reason": "No applicable strategy assessment", "evidence_ids": []},
        {"factor": "complexity", "value": None, "reason": "Implementation route awaits Engineering review", "evidence_ids": []},
    ]
    draft = validate(payload, signal, pack)
    assert draft.factor_proposals[-1].value is None
    assert any("Unassessed scoring factors" in gap for gap in draft.uncertainties)


def test_numeric_effort_without_capability_basis_is_rejected(signal, pack):
    payload = draft_payload(signal, pack)
    payload["factor_proposals"].append({"factor": "complexity", "value": 2.0,
                                        "reason": "Manageable within current production capabilities", "evidence_ids": []})
    with pytest.raises(EngineError, match="complexity must be null"):
        validate(payload, signal, pack)


def test_severity_cannot_be_inflated_by_unsupplied_safety_claim(signal, pack):
    payload = draft_payload(signal, pack)
    payload["factor_proposals"][0].update(value=3.0, reason="Driver distraction is a safety concern")
    with pytest.raises(EngineError, match="safety/distraction"):
        validate(payload, signal, pack)


def test_proposed_targets_need_assumptions_and_distinct_alternatives(signal, pack):
    payload = draft_payload(signal, pack)
    payload["assumptions"] = []
    with pytest.raises(EngineError, match="assumptions"):
        validate(payload, signal, pack)
    payload = draft_payload(signal, pack)
    payload["alternatives"] = ["Add a shortcut", "Add a shortcut"]
    with pytest.raises(EngineError, match="distinct"):
        validate(payload, signal, pack)


def test_requirement_description_cannot_just_duplicate_jtbd(signal, pack):
    payload = draft_payload(signal, pack)
    payload["description"] = payload["jtbd"]
    with pytest.raises(EngineError, match="duplicate JTBD"):
        validate(payload, signal, pack)


def test_market_extrapolation_needs_an_owned_reviewable_assumption(signal, pack):
    from contracts.intelligence import Scope
    target = Scope(derivatives=["G60"], regions=["US", "CN"])
    broader_pack = pack.model_copy(update={"target_scope": target})
    payload = draft_payload(signal, broader_pack)
    with pytest.raises(EngineError, match="scope_carryover"):
        validate_draft(RequirementDraft.model_validate(payload), signal, broader_pack, target, as_of=AS_OF)


def test_source_citation_does_not_prove_inferred_distraction(signal, pack):
    payload = draft_payload(signal, pack)
    payload["claims"][0]["text"] = "The controls cause driver distraction and a safety risk."
    with pytest.raises(EngineError, match="safety/distraction"):
        validate(payload, signal, pack)


def test_bounded_quality_repair_can_fix_omission_and_records_issues(signal, pack, audit):
    from services.drafting import draft_requirement
    from tests.intelligence.fakes import ScriptedModel
    invalid = draft_payload(signal, pack)
    invalid["assumptions"] = []
    model = ScriptedModel({"requirements": [invalid]}, {"requirements": [draft_payload(signal, pack)]})
    draft = draft_requirement(signal, pack, SCOPE, model=model, audit=audit, run_id="RUN-REPAIR")
    assert draft.assumptions and len(model.calls) == 2
    repairs = [(action, payload) for action, payload in audit.events if action.startswith("DRAFT_QUALITY_REPAIR")]
    assert [action for action, _ in repairs] == ["DRAFT_QUALITY_REPAIR_REQUESTED", "DRAFT_QUALITY_REPAIR_SUCCEEDED"]
    assert repairs[0][1]["issues"]


def test_quality_repair_cannot_loop_until_a_good_output(signal, pack):
    from services.drafting import draft_requirement
    from tests.intelligence.fakes import ScriptedModel
    invalid = draft_payload(signal, pack)
    invalid["assumptions"] = []
    model = ScriptedModel({"requirements": [invalid]}, {"requirements": [invalid]})
    with pytest.raises(EngineError, match="assumptions"):
        draft_requirement(signal, pack, SCOPE, model=model)
    assert len(model.calls) == 2


def test_invented_evidence_is_not_repaired_into_a_success(signal, pack):
    from services.drafting import draft_requirement
    from tests.intelligence.fakes import ScriptedModel
    invalid = draft_payload(signal, pack)
    invalid["claims"][0]["evidence_ids"] = ["INVENTED"]
    model = ScriptedModel({"requirements": [invalid]})
    with pytest.raises(EngineError, match="unknown evidence"):
        draft_requirement(signal, pack, SCOPE, model=model)
    assert len(model.calls) == 1


def test_predicted_customer_benefit_is_not_a_sourced_fact(signal, pack):
    payload = draft_payload(signal, pack)
    payload["claims"].append({"id": "benefit", "kind": "rationale", "text": "The redesign will increase satisfaction.",
                              "provenance": "SourceBacked", "evidence_ids": [pack.supporting[0].id]})
    with pytest.raises(EngineError, match="inferred benefit"):
        validate(payload, signal, pack)


@pytest.mark.parametrize("reason", [
    "Usability friction; supplied comments do not establish a safety issue.",
    "The supplied evidence does not show the function is unavailable or establish safety harm.",
    "No safety or distraction impact is established by the source material.",
])
def test_explicit_safety_uncertainty_is_not_mistaken_for_a_safety_claim(reason, signal, pack):
    payload = draft_payload(signal, pack)
    payload["factor_proposals"][0]["reason"] = reason
    assert validate(payload, signal, pack).factor_proposals[0].value == 2
    payload["factor_proposals"][0]["reason"] += " But the controls create a safety risk."
    with pytest.raises(EngineError, match="safety/distraction"):
        validate(payload, signal, pack)


def test_positive_counterexample_is_a_cited_contradiction_not_support(signal, pack):
    payload = draft_payload(signal, pack)
    payload["claims"].append({"id": "counterexample", "kind": "problem", "relation": "contradicts",
                              "text": "Temperature adjustment is easy to find.", "provenance": "SourceBacked",
                              "evidence_ids": [pack.opposing[0].id]})
    assert validate(payload, signal, pack).claims[-1].relation == "contradicts"
    payload["claims"][-1]["relation"] = "supports"
    with pytest.raises(EngineError, match="no direct support"):
        validate(payload, signal, pack)


def test_accidental_mixed_language_in_authored_test_method_is_rejected(signal, pack):
    payload = draft_payload(signal, pack)
    payload["smart"]["metrics"][0]["test_method"] = "Record success if the phone remains داخل the pocket."
    with pytest.raises(EngineError, match="Arabic-script fragment"):
        validate(payload, signal, pack)


def test_source_backed_acceptance_threshold_needs_explicit_reviewed_usage(signal, pack):
    fact = pack.sales[0]
    payload = draft_payload(signal, pack)
    metric = payload["smart"]["metrics"][0]
    metric.update(value=fact.value, unit=fact.unit, provenance="SourceBacked", evidence_ids=[fact.id])
    with pytest.raises(EngineError, match="native fact"):
        validate(payload, signal, pack)
    reviewed_fact = fact.model_copy(update={"reviewed_usage": ["acceptance_target"]})
    reviewed_pack = pack.model_copy(update={"sales": [reviewed_fact]})
    assert validate(payload, signal, reviewed_pack).smart.metrics[0].provenance == "SourceBacked"


def test_forecast_year_is_supplied_fact_metadata_not_an_invented_assertion(signal, pack):
    payload = draft_payload(signal, pack)
    payload["claims"].append({"id": "forecast", "kind": "commercial", "relation": "context",
                              "text": "A 2030 forecast is supplied for US G60/G68.", "provenance": "SourceBacked",
                              "evidence_ids": [pack.sales[0].id]})
    assert validate(payload, signal, pack).claims[-1].text.startswith("A 2030")
    payload["claims"][-1]["text"] = "A 2040 forecast is supplied for US G60/G68."
    with pytest.raises(EngineError, match="unsupplied quantitative"):
        validate(payload, signal, pack)


def test_true_cited_comment_count_is_allowed_but_bucket_count_is_not(signal, pack):
    payload = draft_payload(signal, pack)
    payload["claims"][0]["text"] = "2 US G60 BEV feedback comments report discoverability friction."
    assert validate(payload, signal, pack).claims[0].text.startswith("2 ")
    payload["claims"][0]["text"] = "100 supporting comments report discoverability friction."
    with pytest.raises(EngineError, match="comment count"):
        validate(payload, signal, pack)


def test_percent_formula_and_procedure_numbers_are_not_hidden_thresholds(signal, pack):
    payload = draft_payload(signal, pack)
    metric = payload["smart"]["metrics"][0]
    metric.update(value=95.0, unit="% task success", operator=">=", conditions="Task success must be at least 95%",
                  test_method="Execute: 1) start at the home screen; 2) attempt the task; 3) record success.",
                  measurement_definition="Task success percentage = successful trials / total trials x 100.")
    assert validate(payload, signal, pack).smart.metrics[0].value == 95
    metric["test_method"] += " Complete within 100 seconds."
    with pytest.raises(EngineError, match="parameters"):
        validate(payload, signal, pack)
