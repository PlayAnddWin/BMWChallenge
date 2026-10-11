import copy
import pytest
from fastapi.testclient import TestClient
from services.api import create_app
from services.repository import MemoryRepository


def fixture_draft():
    return {"derivation_key": "temperature-access", "signal_id": "signal-us-temp", "signal_revision": 1,
        "title": "Accessible temperature controls", "description": "Allow drivers to adjust cabin temperature in two actions", "problem_statement": "Drivers struggle to access temperature controls",
        "source_scope": {"derivatives": ["G60"], "regions": ["US"], "powertrains": ["BEV"]},
        "target_scope": {"derivatives": ["G60"], "regions": ["US"], "powertrains": ["BEV"]},
        "smart": {"specific": "Cabin temperature UI", "metrics": [{"id": "metric-1", "target": "2", "operator": "<=", "unit": "actions", "conditions": "stationary vehicle", "verification_method": "Usability test", "provenance": "AIProposed"}], "attainability": "Requires validation", "relevance": "Addresses cited usability friction", "milestone": {"id": "milestone-1", "value": "G1 planning", "provenance": "PMEntered"}},
        "finding_links": [{"signal_id": "signal-us-temp", "revision": 1}],
        "evidence_links": [{"source_id": "source-1", "record_id": "feedback-1", "locator": {"sheet": "Feedback", "row": 2}, "claim_key": "problem", "relation": "supports", "validation_state": "verified", "source_version": "hash-1"}],
        "assumptions": [{"id": "assumption-1", "description": "UI feasibility requires validation", "review_state": "pending"}], "uncertainties": [], "factor_proposals": {"severity_level": 2, "strategic_fit_level": 2, "complexity_level": 3}, "has_conflict": False}


class FakeEvidence:
    def resolve(self, draft):
        links = draft["evidence_links"]
        valid = all(l["source_id"] == "source-1" and l["record_id"] == "feedback-1" and l["source_version"] == "hash-1" for l in links)
        return {"traceable": valid, "verified": valid,
            "source_snapshots": [{"source_id": "source-1", "record_id": "feedback-1", "source_version": "hash-1", "locator": {"sheet": "Feedback", "row": 2}, "raw_payload": {"text": "Synthetic comment about temperature-control access"}}],
            "score_context": {"severity_level": draft["factor_proposals"].get("severity_level"), "strategic_fit_level": draft["factor_proposals"].get("strategic_fit_level"), "strategy_references": ["feedback-1"], "strategy_rationale": "Synthetic policy alignment used only by this fake resolver", "complexity_level": draft["factor_proposals"].get("complexity_level"), "target_regions": ["US"], "region_counts": {"US": {"support": 2, "cohort": 3}}, "forecasts": [{"vehicle_line": "G60_G68", "region": "US", "year": 2030, "volume": 100, "source_reference": "sales-1"}], "target_vehicle_lines": ["G60"], "reference_volume": 200},
            "evidence_context": {"distinct_support_comments": 2, "target_regions": ["US"], "covered_regions": ["US"], "corroboration": 1, "assignment_reviews": [.5]}}


@pytest.fixture
def draft():
    return fixture_draft()


@pytest.fixture
def backend(draft):
    repo = MemoryRepository()
    app = create_app(repo, FakeEvidence())
    with TestClient(app) as client:
        created = app.state.service.create_requirement(draft, "synthetic-run")
        yield client, repo, app.state.service, created


def decision(revision=1, action="approve", **kwargs):
    body = {"expected_revision": revision, "action": action, "actor_id": "pm-test", "actor_role": "PM_USER", "rationale": "Reviewed original synthetic evidence", "reviewed_target_ids": ["metric-1", "milestone-1"], "accepted_assumption_ids": ["assumption-1"], "reviewed_source_ids": ["feedback-1"]}
    body.update(kwargs)
    return body
