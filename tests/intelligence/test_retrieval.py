import pytest

from contracts.intelligence import Scope
from services.retrieval import build_evidence_pack
from tests.intelligence.fakes import SCOPE


def test_native_distribution_and_unknown_sample_retained(pack):
    fact = pack.studies[0].fact
    assert len(fact.value) == 7 and sum(fact.value) == pytest.approx(1)
    assert fact.respondent_count is None
    assert fact.scale == "seven native response categories"
    assert any("unknown" in gap for gap in pack.gaps)


@pytest.mark.parametrize("context", ["opposes", "unreviewed", "wrong_region", "wrong_derivative"])
def test_weak_opposing_or_unmapped_study_cannot_upgrade_support(context, signal, evidence):
    study = evidence.studies[0]
    if context == "opposes":
        study = study.model_copy(update={"relation": "contradictory"})
    elif context == "unreviewed":
        study = study.model_copy(update={"mapping_reviewed": False})
    else:
        scope = Scope(derivatives=["G68"] if context == "wrong_derivative" else ["G60"],
                      regions=["CN"] if context == "wrong_region" else ["US"])
        study = study.model_copy(update={"fact": study.fact.model_copy(update={"scope": scope})})
    evidence.studies = [study]
    before = signal.model_dump()
    pack = build_evidence_pack(signal, SCOPE, evidence_provider=evidence)
    assert signal.model_dump() == before and signal.issue_support_comment_count == 2
    assert pack.studies[0].relation == ("contradictory" if context == "opposes" else "unmapped")
    assert "confidence" not in pack.model_dump() and "evidence_tier" not in signal.model_dump()
    if context == "wrong_derivative":
        assert pack.studies[0].fact.scope.derivatives == ["G68"]


def test_china_gap_is_not_zero_demand(signal, evidence):
    target = Scope(derivatives=["G60"], regions=["US", "CN"])
    pack = build_evidence_pack(signal, target, evidence_provider=evidence)
    assert any("CN" in gap and "not zero demand" in gap for gap in pack.gaps)
    assert not any("CN" in span.scope.regions for span in pack.supporting)


def test_german_option_not_asserted_for_us_and_powertrain_proxy_explicit(signal, evidence):
    target = Scope(derivatives=["G60"], regions=["US"], powertrains=["BEV"])
    pack = build_evidence_pack(signal, target, evidence_provider=evidence)
    assert not pack.equipment
    assert any("powertrain split" in gap for gap in pack.gaps)
    assert len(pack.sales) == 1 and pack.sales[0].vehicle_line == "G60_G68"
    assert all(span.scope.powertrains == ["BEV"] for span in pack.supporting)


def test_successor_mapping_is_an_assumption(signal, evidence):
    target = Scope(derivatives=["SUCCESSOR-G60"], regions=["US"])
    pack = build_evidence_pack(signal, target, evidence_provider=evidence)
    assert len(pack.supporting) == 2
    assert any("carryover" in gap for gap in pack.gaps)
