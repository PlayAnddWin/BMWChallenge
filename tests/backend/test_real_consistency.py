"""Role 01 reimports cannot race authoritative Role 03 source-dependent commits."""
from uuid import uuid4

import pytest

from conftest import fixture_draft, decision
from test_real_bmw_data import real_data, real_loader, reference
from models import DecisionRequest, EditRequest, FreezeRequest
from services.decisions import DomainError, GovernanceService
from services.real_evidence import RealEvidenceResolver
from services.repository import Neo4jRepository


@pytest.fixture
def consistent_backend(real_data):
    driver, bundle, sources, evidence = real_data
    comment = next(c for c in bundle.feedback_comments if c.get("model") == "G70" and c.get("region") == "US" and c.get("powertrain"))
    row = next(r for r in bundle.source_records if r.get("feedback_comment_id") == comment["id"])
    draft = fixture_draft()
    scope = {"derivatives": ["G70"], "regions": ["US"], "powertrains": [comment["powertrain"]]}
    draft.update(derivation_key="real-race-" + uuid4().hex, signal_id="manual-real-review-" + comment["id"],
        description=comment["original_text"], problem_statement=comment["original_text"],
        source_scope=scope, target_scope=scope, factor_proposals={}, evidence_links=[reference(row, {})])
    draft["finding_links"] = [{"signal_id": draft["signal_id"], "revision": 1}]
    namespace = "real-consistency-" + uuid4().hex
    repo = Neo4jRepository(driver, "neo4j", namespace)
    repo.initialize()
    service = GovernanceService(repo, RealEvidenceResolver(sources, evidence))
    record = service.create_requirement(draft, "manual-original-source-race-acceptance")
    try:
        yield driver, repo, service, record, draft, row
    finally:
        with driver.session() as session:
            session.run("MATCH (n) WHERE n.backend_key=$namespace OR n.backend_key STARTS WITH $prefix DETACH DELETE n",
                        namespace=namespace, prefix=namespace + ":").consume()


def reviewed(record):
    return DecisionRequest(**decision(revision=record["revision"], reviewed_source_ids=[record["evidence_links"][0]["record_id"]]))


@pytest.mark.parametrize("action", ["create", "edit", "approve", "freeze"])
def test_real_role01_reimport_between_preflight_and_commit_rolls_back_governance(consistent_backend, monkeypatch, action):
    driver, repo, service, record, draft, _ = consistent_backend
    if action == "freeze":
        record = service.decide_requirement(record["id"], reviewed(record))
    before = repo.read()
    write = repo.write_with_transaction
    def imported_before_commit(operation):
        entry = real_loader[driver]
        entry["loader"].load_processed_bundle(driver, entry["processed"])
        return write(operation)
    monkeypatch.setattr(repo, "write_with_transaction", imported_before_commit)
    with pytest.raises(DomainError, match="corpus changed") as error:
        if action == "create":
            service.create_requirement({**draft, "derivation_key": draft["derivation_key"] + "-new"}, "manual")
        elif action == "edit":
            service.edit_requirement(record["id"], EditRequest(expected_revision=record["revision"], actor_id="pm-test",
                actor_role="PM_USER", rationale="Authored real-source follow-up", patch={"title": "Authored follow-up"}))
        elif action == "approve":
            service.decide_requirement(record["id"], reviewed(record))
        else:
            service.freeze(FreezeRequest(selections={record["id"]: record["revision"]},
                expected_ranking_id=before["rankings"][-1]["id"], actor_id="pm-test", actor_role="PM_USER", rationale="Real source freeze"))
    assert error.value.status == 409
    assert repo.read() == before
