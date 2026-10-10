from copy import deepcopy

import pytest

from contracts.intelligence import RequirementDraft
from services.drafting import draft_requirements, validate_draft
from services.retrieval import build_evidence_pack
from tests.intelligence.fakes import SCOPE, ScriptedModel, draft_payload


def test_proposed_target_and_weak_support_remain_honest(signal, pack):
    payload = draft_payload(signal, pack)
    result = draft_requirements([(signal, pack)], SCOPE, model=ScriptedModel({"requirements": [payload]}))
    assert result.state == "succeeded"
    draft = result.items[0]
    assert draft.smart.metrics[0].provenance == "AIProposed"
    assert draft.smart.metrics[0].review_state == "unreviewed"
    assert draft.smart.milestone.text == "Fixture concept freeze"
    assert any("Only 2" in gap for gap in draft.uncertainties)
    assert not {"status", "priority", "confidence", "score"} & draft.model_dump().keys()


def test_reverse_drafts_bind_by_signal_id(signal, pack, evidence):
    other = signal.model_copy(update={"id": "SIG-OTHER", "title": "Other fixture issue"})
    other_pack = build_evidence_pack(other, SCOPE, evidence_provider=evidence)
    first, second = draft_payload(signal, pack), draft_payload(other, other_pack)
    first["title"], second["title"] = "First", "Second"
    result = draft_requirements([(signal, pack), (other, other_pack)], SCOPE,
                                model=ScriptedModel({"requirements": [second, first]}))
    assert [(draft.input_signal_id, draft.title) for draft in result.items] == [(signal.id, "First"), (other.id, "Second")]


@pytest.mark.parametrize("corrupt", ["unknown_id", "duplicate_id", "unknown_citation", "approved_target", "pm_target",
                                    "unsupported_number", "unsupported_architecture", "score", "legacy_smart", "factor"])
def test_bad_draft_rejected(corrupt, signal, pack):
    payload = draft_payload(signal, pack)
    rows = [payload]
    if corrupt == "unknown_id":
        payload["input_signal_id"] = "NOT-REQUESTED"
    elif corrupt == "duplicate_id":
        rows.append(deepcopy(payload))
    elif corrupt == "unknown_citation":
        payload["claims"][0]["evidence_ids"] = ["NOT-SUPPLIED"]
    elif corrupt == "approved_target":
        payload["smart"]["metrics"][0]["review_state"] = "accepted"
    elif corrupt == "pm_target":
        payload["smart"]["metrics"][0]["provenance"] = "PMEntered"
    elif corrupt == "unsupported_number":
        metric = payload["smart"]["metrics"][0]
        metric.update(provenance="SourceBacked", evidence_ids=[pack.studies[0].fact.id])
    elif corrupt == "unsupported_architecture":
        payload["claims"].append({"id": "architecture", "kind": "architecture", "text": "Existing roof camera is compatible",
                                  "provenance": "SourceBacked", "evidence_ids": []})
    elif corrupt == "score":
        payload["priority"] = 100
    elif corrupt == "legacy_smart":
        payload["smart_check"] = {"specific": True, "measurable": True, "attainable": True, "relevant": True, "timely": True}
    else:
        payload["factor_proposals"][0]["value"] = 999.0
    result = draft_requirements([(signal, pack)], SCOPE, model=ScriptedModel({"requirements": rows}))
    assert result.state == "failed" and not result.items


def test_one_invalid_draft_does_not_discard_other(signal, pack):
    other = signal.model_copy(update={"id": "SIG-OTHER"})
    other_pack = pack.model_copy(update={"signal_id": other.id})
    invalid = draft_payload(other, other_pack)
    invalid["smart"]["metrics"][0]["test_method"] = ""
    result = draft_requirements([(signal, pack), (other, other_pack)], SCOPE,
                                model=ScriptedModel({"requirements": [invalid, draft_payload(signal, pack)]}))
    assert result.state == "partial" and len(result.items) == 1
    assert result.items[0].input_signal_id == signal.id
    assert result.failures[0].input_id == other.id


def test_missing_draft_explicit(signal, pack):
    result = draft_requirements([(signal, pack)], SCOPE, model=ScriptedModel({"requirements": []}))
    assert result.failures[0].code == "missing_output"


def test_unsupported_architecture_can_only_be_unreviewed_proposal(signal, pack):
    payload = draft_payload(signal, pack)
    payload["claims"].append({"id": "architecture", "kind": "architecture", "text": "Explore a new camera solution",
                              "provenance": "AIProposed", "evidence_ids": []})
    draft = validate_draft(RequirementDraft.model_validate(payload), signal, pack, SCOPE)
    assert draft.claims[-1].review_state == "unreviewed"
    assert any("independent validation" in gap for gap in draft.uncertainties)


@pytest.mark.parametrize("kind,text", [("problem", "100 comments support this narrow issue."),
                                      ("commercial", "Package uptake will increase by 18%."),
                                      ("competitor", "A competitor provides a faster interface.")])
def test_citation_alone_cannot_support_unsupplied_numbers_or_capabilities(kind, text, signal, pack):
    payload = draft_payload(signal, pack)
    payload["claims"].append({"id": "unsupported-assertion", "kind": kind, "text": text,
                              "provenance": "SourceBacked", "evidence_ids": [pack.supporting[0].id]})
    result = draft_requirements([(signal, pack)], SCOPE, model=ScriptedModel({"requirements": [payload]}))
    assert result.state == "failed"


@pytest.mark.parametrize("field,scope", [("source_scope", {"derivatives": ["G60"], "regions": ["US"], "powertrains": ["BEV"]}),
                                        ("target_scope", {"derivatives": ["G60"], "regions": ["CN"], "powertrains": []})])
def test_model_cannot_silently_expand_or_narrow_supplied_scope(field, scope, signal, pack):
    payload = draft_payload(signal, pack)
    payload[field] = scope
    model = ScriptedModel({"requirements": [payload]})
    result = draft_requirements([(signal, pack)], SCOPE, model=model)
    assert result.state == "failed" and "scope differs" in result.failures[0].message
    fields = model.calls[0]["schema"]["$defs"]["RequirementDraft"]["properties"]
    assert fields["source_scope"]["properties"]["powertrains"]["maxItems"] == 0
    assert fields["target_scope"]["properties"]["regions"]["items"]["enum"] == ["US"]


def test_provider_batch_limit_isolates_signal_vocabularies_and_preserves_partial_results(signal, pack):
    other = signal.model_copy(update={"id": "SIG-OTHER"})
    other_pack = pack.model_copy(update={"signal_id": other.id})
    model = ScriptedModel({"requirements": []}, {"requirements": [draft_payload(other, other_pack)]})
    model.max_draft_batch_size = 1
    result = draft_requirements([(signal, pack), (other, other_pack)], SCOPE, model=model)
    assert result.state == "partial" and result.items[0].input_signal_id == other.id
    assert result.failures[0].input_id == signal.id
    assert len(model.calls) == 2
    for call, expected_id in zip(model.calls, [signal.id, other.id]):
        ids = call["schema"]["$defs"]["RequirementDraft"]["properties"]["input_signal_id"]["enum"]
        assert ids == [expected_id]
