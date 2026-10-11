"""Synthetic Role 1-shaped inputs exercise production governance boundaries."""
import copy

import pytest

from conftest import FakeEvidence, decision, fixture_draft
from models import ApplyRequest, DecisionRequest, EditRequest, FreezeRequest, RequirementDraft
from services.decisions import DomainError, GovernanceService, engine_review_patch
from services.repository import MemoryRepository
from test_neo4j_integration import graph_backend


def identity(revision=1):
    return dict(expected_revision=revision, actor_id="pm-input-review", actor_role="PM_USER",
                rationale="Review synthetic source inputs and proposal")


def reference(source, record, version, locator):
    return dict(source_id=source, record_id=record, source_version=version, locator=locator,
                claim_key="synthetic-input", relation="context", validation_state="verified")


class TransactionTrackingRepository(MemoryRepository):
    in_transaction = False

    def write_with_transaction(self, operation):
        def tracked(state, tx):
            self.in_transaction = True
            try:
                return operation(state, tx)
            finally:
                self.in_transaction = False
        return super().write_with_transaction(tracked)


class Role1Evidence(FakeEvidence):
    """Production closure policy over explicitly synthetic, deterministic sources."""
    def __init__(self, change=None):
        self.change = change
        self.drift = None
        self.guards = []
        self.reject_current = False
        self.repository = None

    def resolve(self, draft):
        context = super().resolve(draft)
        cohort = reference("cohort-source", "cohort-row", "cohort-hash", {"sheet": "Feedback", "row": 3})
        sales = reference("sales-source", "sales-row", "sales-hash", {"sheet": "Sales", "row": 2})
        strategy = reference("strategy-source", "strategy-page", "strategy-hash", {"page": 1})
        study = reference("study-source", "study-row", "study-hash", {"sheet": "Study", "row": 2})
        context["source_snapshots"][0]["raw_payload"]["native_value"] = 1
        for ref, raw, extra in (
                (cohort, {"text": "Synthetic denominator comment"}, {}),
                (sales, {"2030_volume": 100}, {"fact_id": "sales-fact"}),
                (strategy, {"text": "Synthetic strategy passage"}, {"chunk_id": "strategy-chunk"}),
                (study, {"native_rating": 3}, {"fact_id": "study-fact"})):
            snapshot = {key: copy.deepcopy(ref[key]) for key in ("source_id", "record_id", "source_version", "locator")}
            context["source_snapshots"].append(dict(snapshot, raw_payload=raw, **extra))
        context["evidence_closure_required"] = True
        context["source_references"] = copy.deepcopy(draft["evidence_links"])
        context["source_manifest"] = [{"id": snapshot["source_id"], "source_version": snapshot["source_version"]}
                                      for snapshot in context["source_snapshots"]]
        context["signal_snapshots"] = [{"id": draft["signal_id"], "revision": draft["signal_revision"],
            "summary": "Synthetic issue content", "evidence_links": copy.deepcopy(draft["evidence_links"])}]
        context["mapping_snapshots"] = [{"id": "study-mapping", "relation": "direct",
            "rationale": "Synthetic reviewed mapping", "source_references": [study]}]
        context["score_context"]["customer_references"] = ["feedback-1", "cohort-row"]
        context["score_context"]["strategy_references"] = ["strategy-chunk"]
        # Arithmetic inputs isolate governance faults from native scoring policy.
        # The sales fact alias is pinned in source_snapshots, not on this forecast.
        context["score_context"]["forecasts"][0].update(source_reference=sales)
        context["evidence_context"]["study_references"] = ["study-fact"]
        # Human acceptance belongs to the revision, not to the resolver input
        # basis. Keep assumption content pinned, including its ID/description.
        assumptions = [{key: copy.deepcopy(value) for key, value in assumption.items() if key != "review_state"}
                       for assumption in draft["assumptions"]]
        context["score_context"]["assumptions"] = assumptions
        context["evidence_context"]["assumptions"] = copy.deepcopy(assumptions)
        context["required_assumption_ids"] = ["assumption-1"]
        if self.drift == "score":
            context["score_context"]["region_counts"]["US"]["support"] = 1
        elif self.drift == "evidence":
            context["evidence_context"]["assignment_reviews"] = [1]
        elif self.drift == "source":
            context["source_snapshots"][0]["raw_payload"]["text"] = "Changed original content"
        elif self.drift == "mapping":
            context["mapping_snapshots"][0]["relation"] = "broad"
        elif self.drift == "signal":
            context["signal_snapshots"][0]["summary"] = "Changed content at the same signal revision"
        elif self.drift == "manifest":
            context["source_manifest"].append({"id": "additional-source", "source_version": "additional-hash"})
        elif self.drift == "json_type":
            context["source_snapshots"][0]["raw_payload"]["native_value"] = 1.0
        if self.change:
            self.change(context)
        return context

    def assert_current(self, context, tx):
        if isinstance(self.repository, TransactionTrackingRepository):
            assert self.repository.in_transaction
        self.guards.append((copy.deepcopy(context), tx))
        if self.reject_current:
            raise DomainError(409, "Source manifest or signal content changed during the transaction")


class NativeRole1Evidence(Role1Evidence):
    """Full native scoring metadata over the same explicitly synthetic sources."""
    def resolve(self, draft):
        context = super().resolve(draft)
        sales = reference("sales-source", "sales-row", "sales-hash", {"sheet": "Sales", "row": 2, "column": 3})
        baseline = reference("sales-source", "reference-sales-row", "sales-hash", {"sheet": "Sales", "row": 3, "column": 3})
        sales_snapshot = next(snapshot for snapshot in context["source_snapshots"] if snapshot["record_id"] == "sales-row")
        sales_snapshot["locator"] = copy.deepcopy(sales["locator"])
        context["source_snapshots"].append(dict(
            {key: copy.deepcopy(baseline[key]) for key in ("source_id", "record_id", "source_version", "locator")},
            fact_id="reference-sales-fact", raw_payload={"2030_volume": 100}))
        def fact(identity, region, ref):
            return dict(id=identity, kind="sales", unit="vehicles", vehicle_line="G60_G68",
                region=region, year=2030, volume=100, source_file_id=ref["source_id"],
                source_record_id=ref["record_id"], source_version=ref["source_version"],
                source_locator=copy.deepcopy(ref["locator"]), source_reference=copy.deepcopy(ref),
                applicability="whole_line", powertrain_split="unknown")
        facts = [fact("sales-fact", "US", sales), fact("reference-sales-fact", "EU", baseline)]
        context["score_context"].update(input_kind="role1", target_powertrains=draft["target_scope"]["powertrains"],
            forecasts=copy.deepcopy(facts[:1]), reference_forecasts=copy.deepcopy(facts), fact_snapshots=copy.deepcopy(facts),
            source_snapshots=copy.deepcopy(context["source_snapshots"]), required_assumption_ids=["assumption-1"],
            reference_policy={"year": 2030, "fact_ids": [item["id"] for item in facts], "source_versions": ["sales-hash"], "total": 200},
            reach_assessment={"kind": "whole_line_proxy", "permitted": True, "assumption_ids": ["assumption-1"],
                              "reason": "Synthetic combined whole-line sales are only a G60 BEV upper-bound proxy."})
        mapping = context["mapping_snapshots"][0]
        mapping.setdefault("review_state", "reviewed")
        mapping.setdefault("reason", mapping["rationale"])
        study = reference("study-source", "study-row", "study-hash", {"sheet": "Study", "row": 2})
        context["evidence_context"].update(input_kind="role1", source_count=2, support_source_counts={"source-1": 2},
            survey_facts=[{"id": "study-fact", "kind": "survey_cell", "unit": "native_rating", "value": 3,
                "derivative": "G60", "region": "US", "respondent_n": None, "scale": None,
                "source_file_id": "study-source", "source_record_id": "study-row", "source_version": "study-hash",
                "source_reference": study, "source_locator": copy.deepcopy(study["locator"]),
                "topic_mapping": copy.deepcopy(mapping)}])
        context["source_manifest"] = [{"id": snapshot["source_id"], "source_version": snapshot["source_version"]}
                                      for snapshot in context["source_snapshots"]]
        return context


def native_draft():
    draft = fixture_draft()
    draft["assumptions"][0]["description"] = "Use synthetic combined G60/G68 whole-line sales only as a G60 BEV upper-bound proxy."
    return draft


def setup_case(provider=None, draft=None):
    repository = TransactionTrackingRepository()
    provider = provider or Role1Evidence()
    provider.repository = repository
    service = GovernanceService(repository, provider)
    record = service.create_requirement(draft or fixture_draft(), "initial-synthetic-inputs")
    return repository, provider, service, record


def chat_proposal(service, record, patch, pin=True):
    proposed = {key: copy.deepcopy(record[key]) for key in RequirementDraft.model_fields}
    proposed.update(copy.deepcopy(patch))
    proposed = engine_review_patch(record, RequirementDraft.model_validate(proposed).model_dump())
    turn = dict(id="synthetic-chat-turn", entity_id=record["id"], expected_revision=record["revision"],
                patch=copy.deepcopy(patch), state="pending", run_id="synthetic-chat-run")
    if pin:
        turn["input_fingerprint"] = service.context_fingerprint(service.context(proposed))
    def persist(state):
        state["turns"][turn["id"]] = copy.deepcopy(turn)
        service.event(state, "CHALLENGE_COMPLETED", record["id"], None, turn,
                      ("pm-input-review", "PM_USER"), "Synthetic cited proposal")
    service.repository.write(persist)
    return turn["id"]


def regeneration(service, title="Engine proposal"):
    draft = fixture_draft()
    draft["title"] = title
    return service.create_requirement(draft, "synthetic-regeneration")["pending_turn_id"]


@pytest.mark.parametrize("kind", ["regeneration", "chat"])
@pytest.mark.parametrize("drift", ["score", "evidence", "source", "mapping", "signal", "manifest", "json_type"])
def test_same_revision_input_drift_rejects_apply_without_any_writes(kind, drift):
    repository, provider, service, record = setup_case()
    patch = {"title": "Engine proposal"}
    tid = regeneration(service) if kind == "regeneration" else chat_proposal(service, record, patch)
    before = repository.read()
    provider.drift = drift
    with pytest.raises(DomainError, match="inputs changed") as error:
        service.edit_requirement(record["id"], ApplyRequest(**identity(), turn_id=tid, patch=patch), apply=True)
    assert error.value.status == 409
    assert repository.read() == before
    assert before["requirements"][record["id"]]["revision"] == 1
    assert before["turns"][tid]["state"] == "pending"


@pytest.mark.parametrize("kind", ["regeneration", "chat"])
def test_pm_modifications_use_their_own_score_and_preserve_exact_retry(kind):
    repository, provider, service, record = setup_case()
    proposed = fixture_draft()
    proposed["title"] = "Engine proposal"
    proposed["factor_proposals"]["severity_level"] = 4
    patch = {key: proposed[key] for key in ("title", "factor_proposals")}
    tid = (service.create_requirement(proposed, "synthetic-regeneration")["pending_turn_id"]
           if kind == "regeneration" else chat_proposal(service, record, patch))
    accepted = copy.deepcopy(patch)
    accepted["title"] = "PM-adjusted proposal"
    accepted["factor_proposals"]["severity_level"] = 3
    provider.guards.clear()
    request = ApplyRequest(**identity(), turn_id=tid, patch=accepted)
    applied = service.edit_requirement(record["id"], request, apply=True)
    assert applied["score_context"]["severity_level"] == 3
    assert applied["status"] == "PM_REVIEW" and applied["approved_revision"] is None
    expected_proposal = dict(patch, evidence_links=proposed["evidence_links"]) if kind == "regeneration" else patch
    assert applied["revision_application"]["proposed_patch"] == expected_proposal
    assert applied["revision_application"]["accepted_patch"] == accepted
    assert [context["score_context"]["severity_level"] for context, tx in provider.guards] == [4, 3]
    assert all(tx is None for context, tx in provider.guards)
    service.edit_requirement(record["id"], EditRequest(**identity(2), patch={"title": "Later PM edit"}))
    before = repository.read()
    guard_count = len(provider.guards)
    provider.resolve = lambda draft: (_ for _ in ()).throw(AssertionError("Retry must not resolve evidence"))
    provider.reject_current = True
    assert service.edit_requirement(record["id"], request, apply=True) == applied
    assert len(provider.guards) == guard_count
    assert repository.read() == before
    with pytest.raises(DomainError, match="Conflicting suggestion retry") as error:
        service.edit_requirement(record["id"], request.model_copy(update={"rationale": "Changed retry"}), apply=True)
    assert error.value.status == 409 and repository.read() == before


@pytest.mark.parametrize("production", [False, True])
def test_missing_fingerprint_preserves_older_synthetic_turns_only(production):
    repository, provider, service, record = setup_case(Role1Evidence() if production else FakeEvidence())
    patch = {"title": "Unpinned older proposal"}
    tid = chat_proposal(service, record, patch, pin=False)
    before = repository.read()
    request = ApplyRequest(**identity(), turn_id=tid, patch=patch)
    if production:
        with pytest.raises(DomainError, match="require pinned evidence inputs") as error:
            service.edit_requirement(record["id"], request, apply=True)
        assert error.value.status == 409 and repository.read() == before
    else:
        assert service.edit_requirement(record["id"], request, apply=True)["revision"] == 2


def test_exact_retry_wins_over_input_drift_if_another_application_commits_during_resolution():
    repository, provider, service, record = setup_case()
    tid = regeneration(service)
    request = ApplyRequest(**identity(), turn_id=tid, patch={"title": "Engine proposal"})
    resolve = provider.resolve
    committed = {}
    def concurrent_application(draft):
        provider.resolve = resolve
        committed["result"] = service.edit_requirement(record["id"], request, apply=True)
        committed["state"] = repository.read()
        provider.drift = "source"
        return resolve(draft)
    provider.resolve = concurrent_application
    assert service.edit_requirement(record["id"], request, apply=True) == committed["result"]
    assert repository.read() == committed["state"]


def test_regeneration_pins_canonical_context_and_normalized_original_draft():
    repository, provider, service, record = setup_case()
    reviewed = copy.deepcopy(record["smart"])
    reviewed["metrics"][0]["review_state"] = "reviewed"
    record = service.edit_requirement(record["id"], EditRequest(**identity(), patch={"smart": reviewed}))
    tid = regeneration(service)
    turn = repository.read()["turns"][tid]
    context = service.context(turn["input_draft"])
    assert turn["input_draft"]["smart"]["metrics"][0]["review_state"] == "pending"
    assert turn["input_fingerprint"] == service.context_fingerprint(context)
    assert service.context_fingerprint(dict(reversed(list(context.items())))) == turn["input_fingerprint"]
    assert len(turn["input_fingerprint"]) == 64
    before = repository.read()
    assert regeneration(service) == tid and repository.read() == before
    applied = service.edit_requirement(record["id"], ApplyRequest(**identity(2), turn_id=tid,
                                       patch={"title": "Human title"}), apply=True)
    assert applied["smart"]["metrics"][0]["review_state"] == "reviewed"


def freeze_request(repository, record):
    return FreezeRequest(selections={record["id"]: record["revision"]},
        expected_ranking_id=repository.read()["rankings"][-1]["id"], actor_id="pm-input-review",
        actor_role="PM_USER", rationale="Freeze synthetic input closure")


@pytest.mark.parametrize("operation", ["create", "regenerate", "edit", "apply", "approve", "freeze"])
def test_transaction_guard_rejection_rolls_back_the_complete_governance_state(operation):
    repository, provider, service, record = setup_case()
    tid = regeneration(service) if operation == "apply" else None
    if operation == "freeze":
        record = service.decide_requirement(record["id"], DecisionRequest(**decision()))
    before = repository.read()
    provider.guards.clear()
    provider.reject_current = True
    with pytest.raises(DomainError, match="changed during the transaction") as error:
        if operation == "create":
            draft = fixture_draft()
            draft["derivation_key"] = "another-semantic-requirement"
            service.create_requirement(draft, "another-synthetic-run")
        elif operation == "regenerate":
            regeneration(service)
        elif operation == "edit":
            service.edit_requirement(record["id"], EditRequest(**identity(), patch={"title": "PM edit"}))
        elif operation == "apply":
            service.edit_requirement(record["id"], ApplyRequest(**identity(), turn_id=tid,
                                     patch={"title": "Engine proposal"}), apply=True)
        elif operation == "approve":
            service.decide_requirement(record["id"], DecisionRequest(**decision()))
        else:
            service.freeze(freeze_request(repository, record))
    assert error.value.status == 409
    assert repository.read() == before
    assert len(provider.guards) == 1 and provider.guards[0][1] is None


def test_non_evidence_decisions_still_work_with_an_unavailable_resolver():
    repository, provider, service, record = setup_case()
    provider.resolve = lambda draft: (_ for _ in ()).throw(AssertionError("No evidence needed"))
    provider.reject_current = True
    before_calls = len(provider.guards)
    reviewed = service.decide_requirement(record["id"], DecisionRequest(**decision(action="start_review")))
    assert reviewed["status"] == "PM_REVIEW"
    assert service.decide_requirement(record["id"], DecisionRequest(**decision(2, "reject")))["status"] == "REJECTED"
    assert len(provider.guards) == before_calls


@pytest.mark.parametrize("missing", ["extra_raw", "extra_raw_type", "customer", "strategy", "forecast", "source",
                                     "mapping", "signal", "signal_revision", "signal_content"])
def test_production_freeze_requires_complete_inputs_beyond_displayed_evidence(missing):
    def incomplete(context):
        if missing in {"extra_raw", "extra_raw_type"}:
            context["source_snapshots"][1]["raw_payload"] = {} if missing == "extra_raw" else ["not a native payload"]
        elif missing == "customer":
            context["score_context"]["customer_references"].append("unresolved-comment")
        elif missing == "strategy":
            context["score_context"]["strategy_references"] = ["unresolved-passage"]
        elif missing == "forecast":
            context["score_context"]["forecasts"][0]["source_reference"] = "unresolved-sales"
        elif missing in {"source", "mapping"}:
            refs = context["source_references"] if missing == "source" else context["mapping_snapshots"][0]["source_references"]
            refs[0]["record_id"] = "unresolved-source-row"
        elif missing == "signal":
            context.pop("signal_snapshots")
        elif missing == "signal_revision":
            context["signal_snapshots"][0]["revision"] = 2
        else:
            context["signal_snapshots"][0] = {"id": "signal-us-temp", "revision": 1}
    repository, provider, service, record = setup_case(Role1Evidence(incomplete))
    record = service.decide_requirement(record["id"], DecisionRequest(**decision()))
    before = repository.read()
    with pytest.raises(DomainError, match="Freeze requires") as error:
        service.freeze(freeze_request(repository, record))
    assert error.value.status == 422 and repository.read() == before


def test_complete_freeze_pins_hidden_sources_signals_mappings_and_forecasts():
    repository, provider, service, record = setup_case()
    record = service.decide_requirement(record["id"], DecisionRequest(**decision()))
    frozen = service.freeze(freeze_request(repository, record))
    assert len(record["evidence_links"]) == 1
    assert {item["record_id"] for item in frozen["source_snapshots"]} == {
        "feedback-1", "cohort-row", "sales-row", "strategy-page", "study-row"}
    assert frozen["requirements"][0]["evidence_pack"] == record["evidence_pack"]
    assert frozen["requirements"][0]["evidence_pack"]["signal_snapshots"][0]["revision"] == 1
    provider.drift = "mapping"
    service.edit_requirement(record["id"], EditRequest(**identity(2), patch={"title": "Later revised proposal"}))
    assert repository.read()["freezes"][frozen["id"]] == frozen


@pytest.mark.parametrize("native", [False, True])
def test_approval_acceptance_keeps_normalized_production_input_fingerprint_and_freeze(native):
    draft = native_draft() if native else fixture_draft()
    repository, provider, service, record = setup_case(NativeRole1Evidence() if native else Role1Evidence(), draft)
    before_context = service.context({key: record[key] for key in RequirementDraft.model_fields})
    before_fingerprint = service.context_fingerprint(before_context)
    approved = service.decide_requirement(record["id"], DecisionRequest(**decision()))
    assert record["assumptions"][0]["review_state"] == "pending"
    assert approved["assumptions"][0]["review_state"] == "accepted"
    after_context = service.context({key: approved[key] for key in RequirementDraft.model_fields})
    assert after_context == before_context == approved["evidence_pack"]
    assert service.context_fingerprint(after_context) == before_fingerprint
    assert all("review_state" not in item for item in after_context["score_context"]["assumptions"])
    assert all("review_state" not in item for item in after_context["evidence_context"]["assumptions"])
    if native:
        assert approved["score"]["input_basis"] == approved["evidence"]["input_basis"] == "role1_metadata"
        reach = approved["score"]["factors"]["reach"]
        assert reach["kind"] == "whole_line_proxy" and reach["lower"] == 0 and reach["upper"] == .5
        assert approved["evidence"]["corroboration_kind"] == "direct"
        assert after_context["evidence_context"]["survey_facts"][0]["topic_mapping"]["review_state"] == "reviewed"
    before = repository.read()
    assert service.create_requirement(draft, "unchanged-after-approval") == approved
    assert repository.read() == before and before["turns"] == {}
    frozen = service.freeze(freeze_request(repository, approved))
    assert frozen["requirements"][0]["evidence_pack"] == before_context
    if native:
        assert any(snapshot.get("fact_id") == "reference-sales-fact" for snapshot in frozen["source_snapshots"])


def test_production_assumption_content_still_changes_the_basis_and_requires_human_review():
    draft = native_draft()
    repository, provider, service, record = setup_case(NativeRole1Evidence(), draft)
    approved = service.decide_requirement(record["id"], DecisionRequest(**decision()))
    changed = copy.deepcopy(draft)
    changed["assumptions"][0].update(description="A changed synthetic proxy assumption needs separate review.", review_state="resolved")
    result = service.create_requirement(changed, "changed-assumption-content")
    turn = repository.read()["turns"][result["pending_turn_id"]]
    assert turn["input_fingerprint"] != service.context_fingerprint(approved["evidence_pack"])
    assert turn["patch"]["assumptions"][0]["review_state"] == "pending"
    assert repository.read()["requirements"][record["id"]] == approved
    applied = service.edit_requirement(record["id"], ApplyRequest(**identity(2), turn_id=turn["id"], patch=turn["patch"]), apply=True)
    assert applied["assumptions"][0]["review_state"] == "pending"
    assert applied["score_context"]["assumptions"][0]["description"] == changed["assumptions"][0]["description"]
    before = repository.read()
    with pytest.raises(DomainError, match="outstanding assumptions") as error:
        service.decide_requirement(record["id"], DecisionRequest(**decision(3, accepted_assumption_ids=[])))
    assert error.value.status == 422 and repository.read() == before


def test_assumption_normalization_keeps_reviewed_mapping_state_in_the_input_fingerprint():
    repository, provider, service, record = setup_case(NativeRole1Evidence(), native_draft())
    patch = {"title": "Native scoring proposal"}
    tid = chat_proposal(service, record, patch)
    before = repository.read()
    provider.change = lambda context: context["mapping_snapshots"][0].update(review_state="pending")
    with pytest.raises(DomainError, match="inputs changed") as error:
        service.edit_requirement(record["id"], ApplyRequest(**identity(), turn_id=tid, patch=patch), apply=True)
    assert error.value.status == 409 and repository.read() == before


@pytest.mark.parametrize("operation", ["approve", "freeze", "regenerate"])
def test_hidden_signal_and_mapping_basis_is_bound_to_the_reviewed_revision(operation):
    repository, provider, service, record = setup_case()
    if operation == "freeze":
        record = service.decide_requirement(record["id"], DecisionRequest(**decision()))
    before = repository.read()
    provider.drift = "mapping"
    if operation == "regenerate":
        assert service.create_requirement(fixture_draft(), "changed-inputs")["pending_turn_id"]
        assert repository.read()["requirements"] == before["requirements"]
    else:
        with pytest.raises(DomainError) as error:
            if operation == "approve":
                service.decide_requirement(record["id"], DecisionRequest(**decision()))
            else:
                service.freeze(freeze_request(repository, record))
        assert error.value.status == 409 and repository.read() == before


def test_transactional_guard_cannot_mutate_the_pinned_context():
    repository, provider, service, record = setup_case()
    def mutating_guard(context, tx):
        assert repository.in_transaction and tx is None
        context["source_snapshots"][0]["raw_payload"]["text"] = "Forged by guard"
        context["signal_snapshots"][0]["summary"] = "Forged signal"
    provider.assert_current = mutating_guard
    edited = service.edit_requirement(record["id"], EditRequest(**identity(), patch={"title": "PM edit"}))
    assert edited["evidence_pack"]["signal_snapshots"][0]["summary"] == "Synthetic issue content"
    assert edited["source_snapshots"][0]["raw_payload"]["text"].startswith("Synthetic comment")


def test_neo4j_callback_guard_and_lineage_use_the_supplied_transaction_only():
    from test_repository_review import recording_repository
    memory, provider, _, record = setup_case()
    repository, tx = recording_repository(memory.read())
    provider.repository = repository
    provider.guards.clear()
    service = GovernanceService(repository, provider)
    service.edit_requirement(record["id"], EditRequest(**identity(), patch={"title": "Graph PM edit"}))
    assert provider.guards[0][1] is tx
    lineage = [(query, params) for query, params in tx.queries if "MATCH (v:RequirementRevision" in query]
    assert len(lineage) == 5
    assert all(params["key"] == f"{repository.namespace}:{record['id']}:2" for query, params in lineage)
    assert all("MERGE (v)-[" in query and "MERGE (s:" not in query for query, params in lineage)
    facts = next(params["rows"] for query, params in lineage if "USES_FACT" in query)
    assert {"id": "sales-fact", "source_id": "sales-source", "source_version": "sales-hash"} in facts
    signals = next(params["rows"] for query, params in lineage if "DERIVED_FROM_SIGNAL" in query)
    assert signals == [{"id": "signal-us-temp", "revision": 1}]


def test_real_transaction_guard_failure_rolls_back_provider_and_governance_writes(graph_backend):
    from test_neo4j_integration import graph_snapshot
    repository, service, record = graph_backend
    provider = Role1Evidence()
    service.evidence_provider = provider
    tid = regeneration(service)
    before, graph_before = repository.read(), graph_snapshot(repository)
    def reject_after_transactional_write(context, tx):
        assert tx is not None
        tx.run("MERGE (p:EvidenceInputProbe {backend_key:$key}) SET p.touched=true",
               key=repository.namespace + ":input-probe").consume()
        raise DomainError(409, "Concurrent input drift")
    provider.assert_current = reject_after_transactional_write
    with pytest.raises(DomainError, match="Concurrent input drift"):
        service.edit_requirement(record["id"], ApplyRequest(**identity(), turn_id=tid,
                                 patch={"title": "Engine proposal"}), apply=True)
    assert repository.read() == before
    assert graph_snapshot(repository) == graph_before
