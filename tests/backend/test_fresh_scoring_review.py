import copy
from itertools import permutations

import pytest

from services.scoring import calculate_evidence, calculate_score
from test_scoring import golden


def test_shared_forecast_retains_all_provenance_independent_of_order():
    context = golden()
    context["forecasts"][0]["source_reference"] = "sales-first"
    context["forecasts"][1]["source_reference"] = {"source_id": "sales-second", "locator": {"sheet": "Forecast", "row": 3}}
    before = copy.deepcopy(context)
    expected = calculate_score(context)
    for order in permutations(context["forecasts"]):
        assert calculate_score(dict(context, forecasts=list(order))) == expected
    assert context == before
    reach = expected["factors"]["reach"]
    assert reach["target_volume"] == 100
    assert len(reach["references"]) == 2
    reach["references"][1]["locator"]["row"] = 99
    assert context == before


@pytest.mark.parametrize("field", ["customer_references", "strategy_references"])
@pytest.mark.parametrize("value", ["passage", True, 1, {}, [" "], [1], [{"source_id": "source"}], [{"source_id": "source", "locator": {"page": True}}]])
def test_factor_provenance_must_be_a_traceable_reference_collection(field, value):
    with pytest.raises(ValueError, match=field):
        calculate_score(dict(golden(), **{field: value}))


def test_factor_reference_collection_is_copied_and_deduplicated():
    reference = {"source_id": "source", "locator": {"page": 1}}
    context = dict(golden(), customer_references=[reference, reference], strategy_references=[reference])
    result = calculate_score(context)
    assert result["factors"]["customer"]["references"] == [reference]
    result["factors"]["strategy"]["references"][0]["locator"]["page"] = 2
    assert reference["locator"]["page"] == 1


@pytest.mark.parametrize("epsilon", [1e-10, -1e-10, 0])
def test_near_unity_weights_never_escape_policy_scale(epsilon):
    context = golden()
    context.update(severity_level=4, strategic_fit_level=4, complexity_level=1,
                   region_counts={"EU": {"support": 1, "cohort": 1}}, reference_volume=100,
                   weights={"customer": .4, "reach": .25, "strategy": .2, "ease": .15 + epsilon})
    result = calculate_score(context)
    assert result["calculated_score"] == pytest.approx(100)
    assert 0 <= result["range"]["lower"] <= result["calculated_score"] <= result["range"]["upper"] <= 100
    assert sum(factor["weight"] for factor in result["factors"].values()) == pytest.approx(1)


def test_fully_known_range_contains_exact_stored_calculation():
    score = calculate_score(golden())
    assert score["range"]["lower"] == score["calculated_score"] == score["range"]["upper"]


@pytest.mark.parametrize("calculate,flag", [(calculate_score, "severity_provisional"), (calculate_score, "study_only"), (calculate_evidence, "contradiction"), (calculate_evidence, "proposed_targets"), (calculate_evidence, "study_only")])
@pytest.mark.parametrize("value", ["false", 0, 1, None, [], {}])
def test_provisional_and_warning_flags_are_explicit_booleans(calculate, flag, value):
    with pytest.raises(ValueError, match=flag):
        calculate({flag: value})


@pytest.mark.parametrize("calculate", [calculate_score, calculate_evidence])
def test_blank_assumption_description_is_rejected(calculate):
    with pytest.raises(ValueError, match="assumptions"):
        calculate({"assumptions": [" \t "]})


@pytest.mark.parametrize("field", ["customer_references", "strategy_references"])
@pytest.mark.parametrize("metadata", [float("nan"), float("inf"), object()])
def test_factor_reference_metadata_is_finite_and_serializable(field, metadata):
    reference = {"source_id": "source", "locator": {"page": 1}, "metadata": metadata}
    with pytest.raises(ValueError, match="serializable source references"):
        calculate_score(dict(golden(), **{field: [reference]}))


@pytest.mark.parametrize("calculate", [calculate_score, calculate_evidence])
@pytest.mark.parametrize("metadata", [float("nan"), float("inf"), float("-inf"), object()])
def test_nested_annotation_metadata_must_be_finite_json(calculate, metadata):
    with pytest.raises(ValueError, match="finite JSON metadata"):
        calculate({"assumptions": [{"description": "Proxy", "metadata": {"values": [metadata]}}]})


@pytest.mark.parametrize("calculate", [calculate_score, calculate_evidence])
def test_valid_nested_annotation_metadata_is_preserved_and_copied(calculate):
    context = {"assumptions": [{"description": "Proxy", "metadata": {"values": [1.5, None, True, "text"]}}]}
    before = copy.deepcopy(context)
    result = calculate(context)
    assert result["assumptions"] == context["assumptions"]
    result["assumptions"][0]["metadata"]["values"].append("changed")
    assert context == before


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_raw_http_preview_rejects_nonfinite_nested_annotations(backend, token):
    client, repo, _, _ = backend
    before = repo.read()
    response = client.post("/api/scoring/preview", content='{"assumptions":[{"description":"Proxy","value":' + token + '}]}' ,
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 422, response.text
    assert "finite JSON metadata" in response.json()["detail"]
    assert repo.read() == before
