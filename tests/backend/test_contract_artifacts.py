"""Published handoff examples must validate and preserve reconstructable audit."""
import json
from pathlib import Path

from models import DecisionRequest, EvidencePack, FreezeSnapshot, RequirementDetail, RequirementDraft
from services.api import create_app
from services.audit import verify_chain
from services.repository import MemoryRepository
from services.scoring import calculate_score


def test_published_fixture_matches_current_models_and_policy():
    from generate_contracts import build_examples
    fixture = build_examples()
    RequirementDraft.model_validate(fixture["draft"])
    EvidencePack.model_validate(fixture["resolved_evidence_context"])
    DecisionRequest.model_validate(fixture["decision_request_example"])
    for key in ("requirement_detail_example", "approved_example"):
        RequirementDetail.model_validate(fixture[key])
    frozen = fixture["freeze_example"]
    FreezeSnapshot.model_validate(frozen)
    assert verify_chain(frozen["audit"]["events"], frozen["checkpoint"])
    assert frozen["requirements"][0] == fixture["approved_example"]
    assert calculate_score(fixture["resolved_evidence_context"]["score_context"])["calculated_score"] == fixture["expected_priority"]["calculated_score"]
    assert fixture["review_run_example"]["state"] == "succeeded"
    assert fixture["review_thread_example"]["turns"][0]["state"] == "pending"
    proposal = fixture['proposal_example']
    assert fixture['analysis_run_example']['pending_turn_ids'] == [proposal['id']]
    assert proposal['entity_id'] == fixture['approved_example']['id']
    assert proposal['expected_revision'] == fixture['approved_example']['revision']
    assert proposal['state'] == 'pending' and proposal['stale'] is False
    assert any(item['id'] == proposal['id'] for item in fixture['proposal_list_example']['items'])
    assert fixture['proposal_discard_example']['state'] == 'discarded'


def test_published_openapi_matches_actual_app():
    published = json.loads((Path(__file__).resolve().parents[2] / "contracts/openapi.json").read_text())
    assert published == create_app(MemoryRepository()).openapi()
