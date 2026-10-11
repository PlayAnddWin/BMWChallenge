"""Adversarial governance regression cases independent of happy-path review."""
import copy
import pytest
from pydantic import ValidationError
from conftest import decision
from models import DecisionRequest, EditRequest, ApplyRequest, FreezeRequest, RequirementDetail
from services.decisions import DomainError


@pytest.mark.parametrize("bad", [None, [], True, " ", float("nan"), float("inf")])
def test_invalid_metric_target_rejected_before_writes(backend, draft, bad):
    _, repo, service, _ = backend
    draft["smart"]["metrics"][0]["target"] = bad
    before = repo.read()
    with pytest.raises(ValidationError):
        service.create_requirement(draft, "invalid")
    assert repo.read() == before


@pytest.mark.parametrize("change", [
    lambda d: d["smart"].update(metrics=["crash"]),
    lambda d: d["smart"].update(milestone=True),
    lambda d: d["smart"]["metrics"][0].update(provenance="SourceBacked"),
    lambda d: d["smart"]["metrics"][0].update(provenance="MadeUp"),
    lambda d: d["smart"]["milestone"].update(id="metric-1"),
    lambda d: d["target_scope"].update(regions="US"),
    lambda d: d["source_scope"].update(derivatives=[]),
    lambda d: d["assumptions"].append(copy.deepcopy(d["assumptions"][0])),
])
def test_nested_boundary_rejects_bad_shapes(backend, draft, change):
    _, repo, service, _ = backend
    change(draft)
    before = repo.read()
    with pytest.raises(ValidationError):
        service.create_requirement(draft, "invalid")
    assert repo.read() == before


def test_zero_target_is_measurable(backend, draft):
    _, _, service, record = backend
    draft["smart"]["metrics"][0]["target"] = 0
    request = EditRequest(expected_revision=1, actor_id="pm", actor_role="PM_USER", rationale="Zero errors required", patch={"smart": draft["smart"]})
    changed = service.edit_requirement(record["id"], request)
    approved = service.decide_requirement(record["id"], DecisionRequest(**decision(changed["revision"])))
    assert approved["status"] == "APPROVED"


def test_regeneration_order_is_identity_invariant_and_stale_turn_not_resurrected(backend, draft):
    _, repo, service, record = backend
    draft["target_scope"]["regions"] = ["US", "CN"]
    first = service.create_requirement(draft, "first")
    draft["target_scope"]["regions"] = ["CN", "US", "CN"]
    assert service.create_requirement(draft, "second")["id"] == first["id"]
    draft["title"] = "New title"
    pending = service.create_requirement(draft, "third")
    service.edit_requirement(first["id"], EditRequest(expected_revision=1, actor_id="pm", actor_role="PM_USER", rationale="Edit current", patch={"description": "Changed detail"}))
    newer = service.create_requirement(draft, "fourth")
    assert newer["pending_turn_id"] != pending["pending_turn_id"]
    assert repo.read()["turns"][newer["pending_turn_id"]]["expected_revision"] == 2


def test_current_source_reverification_blocks_approval_and_freeze(backend):
    _, repo, service, record = backend
    original = service.evidence_provider.resolve
    service.evidence_provider.resolve = lambda d: dict(original(d), verified=False)
    before = repo.read()
    with pytest.raises(DomainError, match="verified"):
        service.decide_requirement(record["id"], DecisionRequest(**decision()))
    assert repo.read() == before
    service.evidence_provider.resolve = original
    service.decide_requirement(record["id"], DecisionRequest(**decision()))
    request = FreezeRequest(selections={record["id"]: 2}, expected_ranking_id=repo.read()["rankings"][-1]["id"], actor_id="pm", actor_role="PM_USER", rationale="Freeze")
    service.evidence_provider.resolve = lambda d: dict(original(d), verified=False)
    before = repo.read()
    with pytest.raises(DomainError, match="Evidence changed"):
        service.freeze(request)
    assert repo.read() == before


def test_changed_config_rejected_and_granular_audit_rolls_back(backend, draft):
    _, repo, service, record = backend
    original = service.evidence_provider.resolve
    def changed(d):
        context = original(d)
        context["score_context"]["reference_volume"] = 999
        return context
    service.evidence_provider.resolve = changed
    draft["derivation_key"] = "other"
    before = repo.read()
    with pytest.raises(DomainError, match="configuration differs"):
        service.create_requirement(draft, "config-change")
    assert repo.read() == before
    service.evidence_provider.resolve = original
    req = EditRequest(expected_revision=1, actor_id="pm", actor_role="PM_USER", rationale="New factor", patch={"factor_proposals": {"severity_level": 3, "strategic_fit_level": 2, "complexity_level": 3}})
    service.audit_failure = lambda action: (_ for _ in ()).throw(RuntimeError("injected")) if action == "FACTORS_CHANGED" else None
    with pytest.raises(RuntimeError):
        service.edit_requirement(record["id"], req)
    assert repo.read() == before
    service.audit_failure = None
    service.edit_requirement(record["id"], req)
    assert {"FACTORS_CHANGED", "SCORE_RECALCULATED"} <= {e["action"] for e in repo.read()["events"]}


def test_legacy_import_exact_original_and_no_false_approval(backend):
    _, repo, service, _ = backend
    original = {"req_id": "LEGACY-1", "status": "CONFLICTED", "title": "Original", "score": {"impact": 7, "market_reach_norm": 8, "effort": 4, "bmw_priority": 6.999}, "original_unknown": {"retain": True}}
    imported = service.import_legacy([original])[0]
    assert imported["legacy_original"] == original
    assert imported["status"] == "PM_REVIEW" and imported["has_conflict"]
    assert imported["score"]["policy_version"] == "legacy-v0"
    RequirementDetail.model_validate(imported)
    before = repo.read()
    assert service.import_legacy([original])[0] == imported
    assert repo.read() == before
    with pytest.raises(DomainError):
        service.decide_requirement(imported["id"], DecisionRequest(**decision()))


def test_sourcebacked_unlinked_target_blocks_approval(backend, draft):
    _, repo, service, record = backend
    smart = copy.deepcopy(record["smart"])
    smart["metrics"][0].update(provenance="SourceBacked", source_references=["invented-source"])
    changed = service.edit_requirement(record["id"], EditRequest(expected_revision=1, actor_id="pm", actor_role="PM_USER", rationale="Claim sourced target", patch={"smart": smart}))
    before = repo.read()
    with pytest.raises(DomainError, match="provenance"):
        service.decide_requirement(record["id"], DecisionRequest(**decision(changed["revision"])))
    assert repo.read() == before


def test_unchanged_draft_changed_evidence_proposes_refresh_and_blocks_stale_approval(backend, draft):
    _, repo, service, record = backend
    original = service.evidence_provider.resolve
    def updated(d):
        context = original(d)
        context["score_context"]["region_counts"]["US"]["support"] = 3
        context["evidence_context"]["distinct_support_comments"] = 3
        return context
    service.evidence_provider.resolve = updated
    before = repo.read()
    with pytest.raises(DomainError, match="inputs changed"):
        service.decide_requirement(record["id"], DecisionRequest(**decision()))
    assert repo.read() == before
    suggestion = service.create_requirement(draft, "new-inputs")
    turn = repo.read()["turns"][suggestion["pending_turn_id"]]
    assert turn["patch"] == {"evidence_links": draft["evidence_links"]}
    result = service.edit_requirement(record["id"], ApplyRequest(expected_revision=1, actor_id="pm", actor_role="PM_USER", rationale="Review updated cohort", turn_id=turn["id"], patch=turn["patch"]), apply=True)
    assert result["score_context"]["region_counts"]["US"]["support"] == 3
    assert result["score"]["calculated_score"] > record["score"]["calculated_score"]
    approved = service.decide_requirement(record["id"], DecisionRequest(**decision(2)))
    assert approved["smart"]["metrics"][0]["review_state"] == "reviewed"
    assert approved["assumptions"][0]["review_state"] == "accepted"


@pytest.mark.parametrize("links", [[{}], [{"signal_id": "different", "revision": 1}], [{"signal_id": "signal-us-temp", "revision": 0}], [{"signal_id": "signal-us-temp", "revision": True}], [{"signal_id": "signal-us-temp", "revision": 2}]])
def test_invalid_signal_revision_lineage_is_not_validated(backend, draft, links):
    _, repo, service, _ = backend
    before = repo.read()
    draft["finding_links"] = links
    with pytest.raises(ValidationError):
        service.create_requirement(draft, "unlinked")
    assert repo.read() == before


@pytest.mark.parametrize("override", [True, False, "0", "NaN"])
def test_override_does_not_coerce_non_numeric_values(override):
    with pytest.raises(ValidationError):
        DecisionRequest(**decision(score_override=override))


@pytest.mark.parametrize("revision", [True, False, 0, -1, "1", 1.5])
def test_freeze_requires_positive_integer_revision(revision):
    with pytest.raises(ValidationError):
        FreezeRequest(selections={"req": revision}, expected_ranking_id="rank", actor_id="pm", actor_role="PM_USER", rationale="Freeze")


def test_legacy_wrapped_flat_export_and_explicit_reconciliation(backend, draft):
    _, repo, service, _ = backend
    draft['derivation_key'] = 'reconciled-flat-legacy'
    wrapper = {"requirement": {"req_id": "LEGACY-FLAT", "title": "Historical", "status": "APPROVED", "impact": 7, "market_reach_norm": 8, "effort": 4, "bmw_priority": 6.75}, "source_signal": {"signal_id": "old"}, "history": [{"retain": "exactly"}]}
    imported = service.import_legacy([wrapper])[0]
    assert imported["legacy_original"] == wrapper
    assert imported["score"]["calculated_score"] == 7
    assert imported["score"]["effective_score"] == 6.75
    assert imported["approved_revision"] is None
    changed = service.edit_requirement(imported["id"], EditRequest(expected_revision=1, actor_id="pm", actor_role="PM_USER", rationale="Reconcile real source and propose v1", patch=draft))
    assert changed["id"] == "LEGACY-FLAT" and changed["revision"] == 2
    assert changed["score"]["policy_version"] == "v1" and changed["status"] == "PM_REVIEW"
    assert changed["legacy_original"] == wrapper and changed["provenance_state"] == "reconciled_legacy"
    assert repo.read()["revisions"]["LEGACY-FLAT"][0]["score"]["policy_version"] == "legacy-v0"


@pytest.mark.parametrize("bad", [-1, 11, float("nan"), float("inf"), True])
def test_legacy_stored_score_cannot_bypass_numeric_policy(backend, bad):
    _, repo, service, _ = backend
    before = repo.read()
    with pytest.raises(ValueError):
        service.import_legacy([{"id": "legacy", "impact": 7, "market_reach_norm": 8, "effort": 4, "bmw_priority": bad}])
    assert repo.read() == before


def test_dependency_failure_and_malformed_context_fail_truthfully_without_writes(backend, draft):
    _, repo, service, _ = backend
    before = repo.read()
    def failed(d):
        raise RuntimeError("source storage offline")
    service.evidence_provider.resolve = failed
    with pytest.raises(DomainError) as error:
        service.create_requirement(draft, "offline")
    assert error.value.status == 503 and repo.read() == before
    service.evidence_provider.resolve = lambda d: {"traceable": True, "verified": True}
    with pytest.raises(DomainError) as error:
        service.create_requirement(draft, "malformed")
    assert error.value.status == 422 and repo.read() == before


def test_scope_expansion_requires_an_explicit_reviewable_assumption(backend, draft):
    _, repo, service, _ = backend
    draft["target_scope"]["powertrains"] = ["BEV", "PHEV"]
    draft["assumptions"] = []
    before = repo.read()
    with pytest.raises(ValidationError, match="Scope extrapolation"):
        service.create_requirement(draft, "unsupported-scope")
    assert repo.read() == before


def test_snapshot_and_audit_shared_contracts_preserve_actual_payload(backend):
    from models import FreezeSnapshot, GovernanceAuditEvent, ScoreResult, EvidenceResult
    _, repo, service, record = backend
    service.decide_requirement(record["id"], DecisionRequest(**decision()))
    snapshot = service.freeze(FreezeRequest(selections={record["id"]: 2}, expected_ranking_id=repo.read()["rankings"][-1]["id"], actor_id="pm", actor_role="PM_USER", rationale="Freeze exact config"))
    assert FreezeSnapshot.model_validate(snapshot).model_dump() == snapshot
    for event in repo.read()["events"]:
        assert GovernanceAuditEvent.model_validate(event).model_dump() == event
    ScoreResult.model_validate(record["score"])
    EvidenceResult.model_validate(record["evidence"])
    assert snapshot["configurations"]["v1"]["fixed_reference_volume"] == 200


@pytest.mark.parametrize("status", ["DRAFT", "PM_REVIEW", "APPROVED", "REJECTED", "POSTPONED"])
@pytest.mark.parametrize("action", ["start_review", "approve", "reject", "defer", "reopen"])
def test_complete_state_action_transition_table(backend, status, action):
    _, repo, service, record = backend
    rid = record["id"]
    if status != "DRAFT":
        setup = {"PM_REVIEW": "start_review", "APPROVED": "approve", "REJECTED": "reject", "POSTPONED": "defer"}[status]
        service.decide_requirement(rid, DecisionRequest(**decision(1, setup)))
    before = repo.read()
    revision = before["requirements"][rid]["revision"]
    valid = (action == "reopen" and status not in ("DRAFT", "PM_REVIEW")) or (action != "reopen" and status in ("DRAFT", "PM_REVIEW") and (action != "start_review" or status == "DRAFT"))
    request = DecisionRequest(**decision(revision, action))
    if valid:
        result = service.decide_requirement(rid, request)
        assert result["status"] == {"start_review": "PM_REVIEW", "approve": "APPROVED", "reject": "REJECTED", "defer": "POSTPONED", "reopen": "PM_REVIEW"}[action]
        assert result["revision"] == revision + 1
    else:
        with pytest.raises(DomainError) as error:
            service.decide_requirement(rid, request)
        assert error.value.status == 422 and repo.read() == before


@pytest.mark.parametrize("conflict", [False, True])
def test_simultaneous_apply_retry_commits_one_revision(backend, draft, conflict):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    _, repo, service, record = backend
    draft["title"] = "Concurrent suggestion"
    turn = service.create_requirement(draft, "concurrent")["pending_turn_id"]
    request = ApplyRequest(expected_revision=1, actor_id="pm", actor_role="PM_USER", rationale="Apply safely", turn_id=turn, patch={"title": draft["title"]})
    barrier = Barrier(2)
    original = service.evidence_provider.resolve
    def synchronized(d):
        context = original(d)
        barrier.wait(timeout=5)
        return context
    service.evidence_provider.resolve = synchronized
    other = request.model_copy(update={"rationale": "Different retry"}) if conflict else request
    def apply(req):
        try:
            return service.edit_requirement(record["id"], req, apply=True)
        except DomainError as error:
            return error
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(apply, [request, other]))
    assert len(repo.read()["revisions"][record["id"]]) == 2
    if conflict:
        assert sum(isinstance(result, DomainError) and result.status == 409 for result in results) == 1
    else:
        assert results[0] == results[1]


@pytest.mark.parametrize("patch", [{}, {"status": "APPROVED"}, {"score": {}}])
def test_empty_and_server_owned_patch_never_writes(backend, patch):
    _, repo, service, record = backend
    before = repo.read()
    request = EditRequest(expected_revision=1, actor_id="pm", actor_role="PM_USER", rationale="Invalid patch", patch=patch)
    with pytest.raises(DomainError) as error:
        service.edit_requirement(record["id"], request)
    assert error.value.status == 422 and repo.read() == before


@pytest.mark.parametrize("turn_patch", [None, {"description": "Other field"}])
def test_missing_or_unrelated_turn_cannot_apply(backend, draft, turn_patch):
    _, repo, service, record = backend
    draft["title" if turn_patch is None else "description"] = "Suggested title" if turn_patch is None else "Other field"
    turn = service.create_requirement(draft, "turn")["pending_turn_id"]
    before = repo.read()
    request = ApplyRequest(expected_revision=1, actor_id="pm", actor_role="PM_USER", rationale="Apply title", turn_id="absent" if turn_patch is None else turn, patch={"title": "Suggested title"})
    with pytest.raises(DomainError) as error:
        service.edit_requirement(record["id"], request, apply=True)
    assert error.value.status == (404 if turn_patch is None else 422) and repo.read() == before


@pytest.mark.parametrize("record", [None, {"requirement": []}, {"score": []}, {"id": "legacy", "score": []}, {"id": "legacy", "impact": 1, "market_reach_norm": 1, "effort": 1, "status": "UNKNOWN"}])
def test_malformed_legacy_shapes_rejected_atomically(backend, record):
    _, repo, service, _ = backend
    before = repo.read()
    with pytest.raises(DomainError) as error:
        service.import_legacy([record])
    assert error.value.status == 422 and repo.read() == before


@pytest.mark.parametrize("key,value", [("regions", ["GLOBAL"]), ("regions", ["ROW"]), ("powertrains", ["BEVE"]), ("powertrains", ["magic"])])
def test_scope_vocabulary_requires_normalized_values(backend, draft, key, value):
    _, repo, service, _ = backend
    draft["target_scope"][key] = value
    before = repo.read()
    with pytest.raises(ValidationError):
        service.create_requirement(draft, "invalid-scope")
    assert repo.read() == before


def test_required_proxy_assumption_cannot_be_replaced_by_unrelated_assumption(backend, draft):
    _, repo, service, _ = backend
    original = service.evidence_provider.resolve
    service.evidence_provider.resolve = lambda d: dict(original(d), required_assumption_ids=["whole-line-bev-proxy"])
    draft["derivation_key"] = "required-proxy"
    record = service.create_requirement(draft, "proxy")
    before = repo.read()
    with pytest.raises(DomainError, match="proxy assumptions"):
        service.decide_requirement(record["id"], DecisionRequest(**decision()))
    assert repo.read() == before
    assumption = {"id": "whole-line-bev-proxy", "description": "Whole-line 2030 volume is an upper-bound proxy for BEV; no split available", "review_state": "pending"}
    changed = service.edit_requirement(record["id"], EditRequest(expected_revision=1, actor_id="pm", actor_role="PM_USER", rationale="Document proxy explicitly", patch={"assumptions": draft["assumptions"] + [assumption]}))
    approved = service.decide_requirement(record["id"], DecisionRequest(**decision(2, accepted_assumption_ids=["assumption-1", "whole-line-bev-proxy"])))
    assert approved["status"] == "APPROVED"


@pytest.mark.parametrize("change", [
    lambda d: d["evidence_links"][0].update(source_id=" "),
    lambda d: d["evidence_links"][0].update(locator={}),
    lambda d: d["smart"].update(specific=" "),
    lambda d: d["smart"]["metrics"][0].update(review_state="auto-approved"),
    lambda d: d["assumptions"][0].update(description=" "),
    lambda d: d["assumptions"][0].update(review_state="auto-approved"),
    lambda d: d.update(title=" "),
])
def test_remaining_nested_validation_boundaries(backend, draft, change):
    _, repo, service, _ = backend
    change(draft)
    before = repo.read()
    with pytest.raises(ValidationError):
        service.create_requirement(draft, "bad-boundary")
    assert repo.read() == before


@pytest.mark.parametrize("failure,status", [(DomainError(503, "Explicit adapter unavailable"), 503), (ValueError("Bad draft"), 422), (TypeError("Bad scope"), 422)])
def test_resolver_known_error_taxonomy_is_preserved(backend, draft, failure, status):
    _, repo, service, _ = backend
    def fail(d):
        raise failure
    service.evidence_provider.resolve = fail
    before = repo.read()
    with pytest.raises(DomainError) as error:
        service.create_requirement(draft, "failure")
    assert error.value.status == status and repo.read() == before


def test_missing_requirement_mutation_is_404_without_writes(backend):
    _, repo, service, _ = backend
    before = repo.read()
    with pytest.raises(DomainError) as error:
        service.decide_requirement("missing", DecisionRequest(**decision(action="reject")))
    assert error.value.status == 404 and repo.read() == before


@pytest.mark.parametrize("identity", [{"id": "wrong"}, {"actor_id": "AI_SYSTEM"}, {"actor_role": "AI_SYSTEM"}])
def test_idempotent_application_retry_still_enforces_identity(backend, draft, identity):
    _, repo, service, record = backend
    draft["title"] = "Apply once"
    turn = service.create_requirement(draft, "retry")["pending_turn_id"]
    request = ApplyRequest(expected_revision=1, actor_id="pm", actor_role="PM_USER", rationale="Apply once", turn_id=turn, patch={"title": "Apply once"})
    service.edit_requirement(record["id"], request, apply=True)
    before = repo.read()
    changed = request.model_copy(update=identity)
    with pytest.raises(DomainError) as error:
        service.edit_requirement(record["id"], changed, apply=True)
    assert error.value.status == 422 and repo.read() == before


def test_freeze_rechecks_approval_after_provider_resolution(backend):
    _, repo, service, record = backend
    service.decide_requirement(record["id"], DecisionRequest(**decision()))
    request = FreezeRequest(selections={record["id"]: 2}, expected_ranking_id=repo.read()["rankings"][-1]["id"], actor_id="pm", actor_role="PM_USER", rationale="Freeze")
    original = service.evidence_provider.resolve
    def reopen_while_resolving(d):
        context = original(d)
        service.decide_requirement(record["id"], DecisionRequest(**decision(2, "reopen")))
        request.expected_ranking_id = repo.read()["rankings"][-1]["id"]
        return context
    service.evidence_provider.resolve = reopen_while_resolving
    with pytest.raises(DomainError, match="current approved"):
        service.freeze(request)
    assert not repo.read()["freezes"]


def test_legacy_duplicate_conflict_reimport_and_actor_guards(backend, draft):
    _, repo, service, _ = backend
    draft['derivation_key'] = 'reconciled-import-legacy'
    original = {"id": "legacy", "impact": 1, "market_reach_norm": 1, "effort": 1}
    before = repo.read()
    for records, options in [([original, original], {}), ([original], {"actor_id": " "}), ([original], {"rationale": " "})]:
        with pytest.raises(DomainError) as error:
            service.import_legacy(records, **options)
        assert error.value.status == 422 and repo.read() == before
    service.import_legacy([original])
    changed = service.edit_requirement("legacy", EditRequest(expected_revision=1, actor_id="pm", actor_role="PM_USER", rationale="Reconcile", patch=draft))
    before = repo.read()
    assert service.import_legacy([original])[0] == changed
    assert repo.read() == before
    with pytest.raises(DomainError) as error:
        service.import_legacy([dict(original, title="New original")])
    assert error.value.status == 409 and repo.read() == before


def test_malformed_required_assumption_ids_and_blank_freeze_rationale(backend, draft):
    _, repo, service, _ = backend
    original = service.evidence_provider.resolve
    service.evidence_provider.resolve = lambda d: dict(original(d), required_assumption_ids="proxy")
    before = repo.read()
    with pytest.raises(DomainError) as error:
        service.create_requirement(draft, "malformed-proxy")
    assert error.value.status == 422 and repo.read() == before
    with pytest.raises(ValidationError):
        FreezeRequest(selections={"req": 1}, expected_ranking_id="rank", actor_id="pm", actor_role="PM_USER", rationale=" ")


@pytest.mark.parametrize("missing", ["strategy_references", "strategy_rationale"])
def test_uncited_strategy_is_provisional_and_blocks_approval(backend, draft, missing):
    _, repo, service, _ = backend
    original = service.evidence_provider.resolve
    def uncited(d):
        context = original(d)
        context["score_context"].pop(missing)
        return context
    service.evidence_provider.resolve = uncited
    draft["derivation_key"] = "uncited-" + missing
    record = service.create_requirement(draft, "uncited")
    assert record["score"]["factors"]["strategy"]["provisional"]
    before = repo.read()
    with pytest.raises(DomainError, match="cited passages"):
        service.decide_requirement(record["id"], DecisionRequest(**decision()))
    assert repo.read() == before


@pytest.mark.parametrize("snapshots", [[], [{"source_id": "source-1", "record_id": "feedback-1", "source_version": "wrong", "locator": {"sheet": "Feedback", "row": 2}, "raw_payload": {"text": "wrong version"}}], [{"source_id": "source-1", "record_id": "feedback-1", "source_version": "hash-1", "locator": {"sheet": "Feedback", "row": 2}}]])
def test_approval_does_not_imply_complete_reconstructable_freeze(backend, draft, snapshots):
    _, repo, service, _ = backend
    original = service.evidence_provider.resolve
    service.evidence_provider.resolve = lambda d: dict(original(d), source_snapshots=copy.deepcopy(snapshots))
    draft["derivation_key"] = "incomplete-source-pack"
    record = service.create_requirement(draft, "source-pack")
    service.decide_requirement(record["id"], DecisionRequest(**decision()))
    before = repo.read()
    request = FreezeRequest(selections={record["id"]: 2}, expected_ranking_id=before["rankings"][-1]["id"], actor_id="pm", actor_role="PM_USER", rationale="Freeze source pack")
    with pytest.raises(DomainError, match="pinned source snapshots"):
        service.freeze(request)
    assert repo.read() == before


def test_pinned_source_payload_survives_provider_and_live_mutation(backend):
    _, repo, service, record = backend
    service.decide_requirement(record["id"], DecisionRequest(**decision()))
    before = repo.read()
    request = FreezeRequest(selections={record["id"]: 2}, expected_ranking_id=before["rankings"][-1]["id"], actor_id="pm", actor_role="PM_USER", rationale="Copy sources")
    snapshot = service.freeze(request)
    assert snapshot["source_snapshots"][0]["raw_payload"]["text"].startswith("Synthetic")
    service.edit_requirement(record["id"], EditRequest(expected_revision=2, actor_id="pm", actor_role="PM_USER", rationale="Later edit", patch={"title": "Later changed title"}))
    assert repo.read()["freezes"][snapshot["id"]] == snapshot


def test_missing_support_and_invalid_source_snapshot_context_rejected(backend, draft):
    _, repo, service, _ = backend
    draft["evidence_links"][0]["relation"] = "context"
    draft["derivation_key"] = "context-only"
    record = service.create_requirement(draft, "context-only")
    before = repo.read()
    with pytest.raises(DomainError, match="evidence blocks"):
        service.decide_requirement(record["id"], DecisionRequest(**decision()))
    assert repo.read() == before
    original = service.evidence_provider.resolve
    service.evidence_provider.resolve = lambda d: dict(original(d), source_snapshots=["malformed"])
    with pytest.raises(DomainError, match="source snapshots"):
        service.create_requirement(draft, "malformed-source")
    assert repo.read() == before


@pytest.mark.parametrize("mapping", [None, [], [{"source_derivative": "invented", "target_derivative": "G61", "assumption_id": "assumption-1"}], [{"source_derivative": "G60", "target_derivative": "G61", "assumption_id": "invented"}]])
def test_future_derivative_needs_explicit_valid_mapping(backend, draft, mapping):
    _, repo, service, _ = backend
    draft["target_scope"]["derivatives"] = ["G61"]
    if mapping is not None:
        draft["target_scope"]["successor_mappings"] = mapping
    before = repo.read()
    with pytest.raises(ValidationError):
        service.create_requirement(draft, "future-scope")
    assert repo.read() == before


def test_source_derivative_vocabulary_and_explicit_successor_proposal(backend, draft):
    _, repo, service, _ = backend
    bad = copy.deepcopy(draft)
    bad["source_scope"]["derivatives"] = ["invented"]
    with pytest.raises(ValidationError, match="supplied derivatives"):
        service.create_requirement(bad, "invalid-source")
    draft["target_scope"]["derivatives"] = ["G61"]
    draft["target_scope"]["successor_mappings"] = [{"source_derivative": "G60", "target_derivative": "G61", "assumption_id": "assumption-1"}]
    record = service.create_requirement(draft, "future-proposal")
    assert record["target_scope"]["successor_mappings"][0]["assumption_id"] == "assumption-1"


@pytest.mark.parametrize("run_id", [None, "", " ", True, 1])
def test_derivation_requires_an_explicit_run_identity(backend, draft, run_id):
    _, repo, service, _ = backend
    before = repo.read()
    with pytest.raises(DomainError, match="run ID"):
        service.create_requirement(draft, run_id)
    assert repo.read() == before


@pytest.mark.parametrize("traceable", ["false", "true", 1, [], {}])
def test_traceable_flag_is_not_coerced_from_truthy_values(backend, draft, traceable):
    _, repo, service, _ = backend
    original = service.evidence_provider.resolve
    service.evidence_provider.resolve = lambda d: dict(original(d), traceable=traceable)
    before = repo.read()
    with pytest.raises(DomainError, match="source versions"):
        service.create_requirement(draft, "bad-context")
    assert repo.read() == before


@pytest.mark.parametrize("field,modify", [
    ("evidence_links", lambda d, value: d["evidence_links"][0]["locator"].update(row=value)),
    ("factor_proposals", lambda d, value: d["factor_proposals"].update(extra_factor={"value": value})),
    ("smart", lambda d, value: d["smart"]["metrics"][0].update(conditions_metadata={"nested": [value]})),
    ("assumptions", lambda d, value: d["assumptions"][0].update(extra={"nested": [value]})),
    ("uncertainties", lambda d, value: d["uncertainties"].append({"value": value})),
])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nested_nonfinite_draft_and_api_edit_fail_before_any_writes(backend, draft, field, modify, value):
    import json
    client, repo, service, record = backend
    modify(draft, value)
    before = repo.read()
    with pytest.raises(ValidationError, match="must be finite"):
        service.create_requirement(draft, "nested-nonfinite")
    assert repo.read() == before
    body = {"expected_revision": 1, "actor_id": "pm", "actor_role": "PM_USER", "rationale": "Reject nonfinite metadata", "patch": {field: draft[field]}}
    response = client.patch(f"/api/requirements/{record['id']}", content=json.dumps(body), headers={"Content-Type": "application/json"})
    assert response.status_code == 422, response.text
    assert repo.read() == before


def test_recursive_validation_preserves_models_and_shared_container_inputs(draft):
    from models import RequirementDraft, SourceReference
    source = SourceReference.model_validate(draft["evidence_links"][0])
    assert SourceReference.model_validate(source) == source
    valid = copy.deepcopy(draft)
    shared = {"finite": [1, 2.5]}
    valid["uncertainties"] = [shared, shared]
    RequirementDraft.model_validate(valid)
    cyclic = copy.deepcopy(draft)
    recursive = []
    recursive.append(recursive)
    cyclic["uncertainties"] = recursive
    with pytest.raises(ValidationError, match="recursive containers"):
        RequirementDraft.model_validate(cyclic)


def test_provider_source_snapshot_nonfinite_metadata_cannot_reach_repository(backend, draft):
    _, repo, service, _ = backend
    original = service.evidence_provider.resolve
    def malformed(d):
        context = original(d)
        context["source_snapshots"][0]["raw_payload"]["metadata"] = {"value": float("nan")}
        return context
    service.evidence_provider.resolve = malformed
    before = repo.read()
    with pytest.raises(DomainError) as error:
        service.create_requirement(draft, "bad-provider-metadata")
    assert error.value.status == 422
    assert repo.read() == before


@pytest.mark.parametrize("mutate", [
    lambda c: c["score_context"]["region_counts"]["US"].update(support=-1),
    lambda c: c["score_context"]["region_counts"]["US"].update(cohort=float("inf")),
    lambda c: c["score_context"].update(policy_version="unsupported"),
    lambda c: c["score_context"].update(warnings=[{"value": float("nan")}]),
    lambda c: c["evidence_context"].update(corroboration="unsupported"),
    lambda c: c["source_snapshots"][0]["raw_payload"].update(unserializable=object()),
])
def test_malformed_authoritative_provider_context_returns_http422_without_writes(backend, draft, mutate):
    client, repo, service, record = backend
    original = service.evidence_provider.resolve
    def invalid(d):
        context = original(d)
        mutate(context)
        return context
    service.evidence_provider.resolve = invalid
    before = repo.read()
    with pytest.raises(DomainError) as error:
        service.create_requirement(draft, "invalid-authoritative-context")
    assert error.value.status == 422 and repo.read() == before
    response = client.patch(f"/api/requirements/{record['id']}", json={"expected_revision": 1, "actor_id": "pm", "actor_role": "PM_USER", "rationale": "Validate provider context", "patch": {"title": "Edited title"}})
    assert response.status_code == 422, response.text
    assert repo.read() == before


@pytest.mark.parametrize("relation", ["supports", "contradicts", "context"])
@pytest.mark.parametrize("source_pack", ["missing", "wrong_version", "empty_raw", "valid"])
def test_freeze_requires_reconstructable_snapshots_for_every_evidence_relation(backend, draft, relation, source_pack):
    _, repo, service, _ = backend
    second = copy.deepcopy(draft["evidence_links"][0])
    second.update(record_id="feedback-2", locator={"sheet": "Feedback", "row": 3}, relation=relation)
    draft["evidence_links"].append(second)
    draft["derivation_key"] = "complete-source-pack-" + relation + "-" + source_pack
    original = service.evidence_provider.resolve
    def resolve(d):
        context = original(d)
        context.update(traceable=True, verified=True)
        if source_pack != "missing":
            snapshot = {k: copy.deepcopy(second[k]) for k in ("source_id", "record_id", "source_version", "locator")}
            snapshot["raw_payload"] = {} if source_pack == "empty_raw" else {"text": "Synthetic " + relation + " record preserved exactly"}
            if source_pack == "wrong_version":
                snapshot["source_version"] = "obsolete-version"
            context["source_snapshots"].append(snapshot)
        return context
    service.evidence_provider.resolve = resolve
    record = service.create_requirement(draft, "all-evidence-source-pack")
    approved = service.decide_requirement(record["id"], DecisionRequest(**decision(reviewed_source_ids=["feedback-1", "feedback-2"])))
    assert approved["status"] == "APPROVED"
    before = repo.read()
    request = FreezeRequest(selections={record["id"]: 2}, expected_ranking_id=before["rankings"][-1]["id"], actor_id="pm", actor_role="PM_USER", rationale="Freeze supporting and opposing context")
    if source_pack == "valid":
        frozen = service.freeze(request)
        raw = next(snapshot for snapshot in frozen["source_snapshots"] if snapshot["record_id"] == "feedback-2")
        assert raw["source_version"] == second["source_version"]
        assert raw["locator"] == second["locator"]
        assert raw["raw_payload"]["text"] == "Synthetic " + relation + " record preserved exactly"
        assert frozen["requirements"][0]["evidence_links"][1]["relation"] == relation
    else:
        with pytest.raises(DomainError) as error:
            service.freeze(request)
        assert error.value.status == 422
        assert repo.read() == before


def test_identical_pending_regeneration_is_idempotent_across_run_retries(backend, draft):
    _, repo, service, record = backend
    draft["title"] = "Repeated pending regeneration"
    first = service.create_requirement(draft, "regeneration-first")
    before = repo.read()
    repeated = service.create_requirement(copy.deepcopy(draft), "regeneration-retry")
    assert repeated == first
    assert repo.read() == before
    assert len(repo.read()["revisions"][record["id"]]) == 1
    assert len(repo.read()["turns"]) == 1


def test_resolved_assumption_is_preserved_and_needs_no_duplicate_acceptance(backend, draft):
    _, repo, service, record = backend
    resolved = copy.deepcopy(draft["assumptions"])
    resolved[0]["review_state"] = "resolved"
    reviewed = service.edit_requirement(record["id"], EditRequest(expected_revision=1, actor_id="pm", actor_role="PM_USER", rationale="Feasibility assumption independently resolved", patch={"assumptions": resolved}))
    assert reviewed["status"] == "PM_REVIEW"
    approved = service.decide_requirement(record["id"], DecisionRequest(**decision(2, accepted_assumption_ids=[])))
    assert approved["status"] == "APPROVED"
    assert approved["assumptions"][0]["review_state"] == "resolved"
    assert approved["review_confirmation"]["assumptions"] == []
    assert repo.read()["revisions"][record["id"]][1]["assumptions"] == resolved


@pytest.mark.parametrize("initial_action", ["approve", "reject", "defer"])
def test_invalid_terminal_approval_precedes_unavailable_evidence_dependency(backend, initial_action):
    _, repo, service, record = backend
    terminal = service.decide_requirement(record["id"], DecisionRequest(**decision(1, initial_action)))
    calls = []
    def offline(d):
        calls.append(d)
        raise RuntimeError("Evidence dependency offline")
    service.evidence_provider.resolve = offline
    before = repo.read()
    with pytest.raises(DomainError, match="Invalid state transition") as error:
        service.decide_requirement(record["id"], DecisionRequest(**decision(terminal["revision"])))
    assert error.value.status == 422
    assert calls == []
    assert repo.read() == before
