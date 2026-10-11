"""Authoritative requirement lifecycle; providers are injected, never model counts."""
import copy
import hashlib
import json
import uuid
from datetime import datetime, timezone

from models import RequirementDraft, EvidencePack, AnalysisEngineResult, SourceReference
from services.audit import append_event, canonical_json
from services.export import audit_bundle
from services.scoring import calculate_score, calculate_evidence, WEIGHTS, rank_requirements


class DomainError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def now():
    return datetime.now(timezone.utc).isoformat()


def pending_engine_review(draft):
    """Engine content cannot establish human review; compare content separately."""
    draft = copy.deepcopy(draft)
    # Preserved legacy originals may have incomplete or malformed SMART fields.
    smart = draft.get("smart")
    items = list(draft.get("assumptions", []))
    if isinstance(smart, dict):
        metrics = smart.get("metrics", [])
        if isinstance(metrics, list):
            items.extend(metrics)
        items.append(smart.get("milestone"))
    for item in items:
        if isinstance(item, dict):
            item["review_state"] = "pending"
    return draft


def engine_review_patch(current, patch):
    """Keep human states only when an engine-proposed item has identical content.

    IDs alone never carry a prior resolution onto changed text or target values.
    Direct PM edits intentionally bypass this engine-only normalization.
    """
    patch = copy.deepcopy(patch)
    def normalize(items, previous):
        by_id = {item.get("id"): item for item in previous if isinstance(item, dict)}
        for item in items:
            if not isinstance(item, dict):
                continue
            old = by_id.get(item.get("id"), {})
            content = {key: value for key, value in item.items() if key != "review_state"}
            old_content = {key: value for key, value in old.items() if key != "review_state"}
            item["review_state"] = old.get("review_state", "pending") if canonical_json(content) == canonical_json(old_content) else "pending"
    if "assumptions" in patch:
        normalize(patch["assumptions"], current.get("assumptions", []))
    if isinstance(patch.get("smart"), dict):
        smart = patch["smart"]
        old_smart = current.get("smart", {})
        if not isinstance(old_smart, dict):
            old_smart = {}
        normalize(smart.get("metrics", []), old_smart.get("metrics", []))
        normalize([smart.get("milestone")], [old_smart.get("milestone")])
    return patch


def evidence_basis(record):
    """Compare pinned evidence and inputs using their exact JSON types."""
    pack = record.get("evidence_pack", record)
    if pack.get("evidence_closure_required") is True:
        # Production inputs include signal content, mappings and source manifests
        # outside the score/ECI projections. Preserve and compare that whole basis.
        return canonical_json(pack)
    return canonical_json({"source_snapshots": record.get("source_snapshots", []),
        "required_assumption_ids": record.get("required_assumption_ids", []),
        "score_context": record.get("score_context"), "evidence_context": record.get("evidence_context")})


class GovernanceService:
    def __init__(self, repository, evidence_provider, audit_failure=None):
        self.repository = repository
        self.evidence_provider = evidence_provider
        self.audit_failure = audit_failure

    def event(self, state, action, entity_id, before, after, actor, rationale, entity_type=None):
        if self.audit_failure:
            self.audit_failure(action)
        return append_event(state, {"id": str(uuid.uuid4()), "entity_id": entity_id,
            "entity_type": entity_type or {"FREEZE_CREATED": "FreezeSnapshot", "RANKING_RECALCULATED": "RankingSnapshot", "SCORING_CONFIG_CREATED": "ScoringConfig", "SOURCE_IMPORTED": "SourceFile", "SIGNAL_CONSOLIDATED": "Signal", "CHALLENGE_REQUESTED": "ReviewConversation", "CHALLENGE_COMPLETED": "ReviewConversation"}.get(action, "Requirement"), "action": action, "timestamp": now(),
            "actor_id": actor[0], "actor_role": actor[1], "rationale": rationale,
            "before": copy.deepcopy(before), "after": copy.deepcopy(after)})

    def context(self, draft):
        # Resolve outside the write transaction; optionally recheck pins inside it.
        try:
            # Adapters own evidence resolution, never validated content or human
            # review. Isolate both directions so retained adapter references also
            # cannot change the context after it has been validated.
            context = copy.deepcopy(self.evidence_provider.resolve(copy.deepcopy(draft)))
        except DomainError:
            raise
        except (ValueError, TypeError) as exc:
            raise DomainError(422, "Evidence resolver rejected the draft") from exc
        except Exception as exc:
            raise DomainError(503, "Evidence resolver is unavailable") from exc
        if not isinstance(context, dict) or context.get("traceable") is not True:
            raise DomainError(422, "Evidence must resolve to original source versions")
        if not isinstance(context.get("score_context"), dict) or not isinstance(context.get("evidence_context"), dict) or not isinstance(context.get("verified"), bool):
            raise DomainError(422, "Evidence resolver returned an invalid authoritative context")
        required = context.get("required_assumption_ids", [])
        if not isinstance(required, list) or any(not isinstance(i, str) or not i.strip() for i in required):
            raise DomainError(422, "Evidence resolver returned invalid required assumption IDs")
        snapshots = context.get("source_snapshots", [])
        if not isinstance(snapshots, list) or any(not isinstance(item, dict) for item in snapshots):
            raise DomainError(422, "Evidence resolver returned invalid source snapshots")
        if "evidence_closure_required" in context and type(context["evidence_closure_required"]) is not bool:
            raise DomainError(422, "Evidence closure flag must be boolean")
        try:
            EvidencePack.model_validate(context)
            canonical_json(context)
            calculate_score(context["score_context"])
            calculate_evidence(context["evidence_context"])
        except (ValueError, TypeError, KeyError) as exc:
            raise DomainError(422, "Evidence resolver returned invalid authoritative scoring or snapshot data") from exc
        return context

    @staticmethod
    def context_fingerprint(context):
        """Pin the complete authoritative resolver context, including JSON types.

        Resolvers normalize draft-owned assumption review_state before returning
        context. Source/mapping review states remain authoritative pinned inputs;
        this helper deliberately does not strip fields from the resolved context.
        """
        return hashlib.sha256(canonical_json(context).encode("utf-8")).hexdigest()

    def assert_current(self, context, tx):
        """An optional resolver guard runs in the repository's own write transaction.

        Adapters without this capability retain their existing behavior. Isolate
        the context just as in resolve(), and let transaction/driver errors escape
        so managed transaction retries and rollback still work.
        """
        guard = getattr(self.evidence_provider, "assert_current", None)
        if callable(guard):
            try:
                current = guard(copy.deepcopy(context), tx)
            except (ValueError, TypeError) as exc:
                raise DomainError(409, "Evidence or source inputs changed; refresh the reviewed inputs") from exc
            if current is False:
                raise DomainError(409, "Evidence or source inputs changed; refresh the reviewed inputs")

    def guard_turn_inputs(self, turn, proposal_context, accepted_context, original):
        required = (accepted_context.get("evidence_closure_required") is True
                    or original.get("evidence_pack", {}).get("evidence_closure_required") is True
                    or proposal_context is not None and proposal_context.get("evidence_closure_required") is True)
        if "input_fingerprint" not in turn:
            if required:
                raise DomainError(409, "Production suggestions require pinned evidence inputs; regenerate the proposal")
            return
        if proposal_context is None or turn["input_fingerprint"] != self.context_fingerprint(proposal_context):
            raise DomainError(409, "Suggestion evidence or scoring inputs changed; regenerate the proposal")

    def score(self, record, context, override=None):
        record["source_snapshots"] = copy.deepcopy(context.get("source_snapshots", []))
        record["required_assumption_ids"] = copy.deepcopy(context.get("required_assumption_ids", []))
        record["score_context"] = copy.deepcopy(context["score_context"])
        record["evidence_context"] = copy.deepcopy(context["evidence_context"])
        if context.get("evidence_closure_required") is True:
            record["evidence_pack"] = copy.deepcopy(context)
        else:
            record.pop("evidence_pack", None)
        record["scoring_policy"] = {"id": context["score_context"].get("policy_version", "v1"), "policy_version": context["score_context"].get("policy_version", "v1"), "weights": copy.deepcopy(context["score_context"].get("weights", WEIGHTS)), "fixed_reference_volume": context["score_context"].get("reference_volume"), "evidence_policy_version": "eci-v1"}
        record["score"] = calculate_score(context["score_context"], override=override)
        if record["score"]["policy_version"] == "v1":
            record["scoring_policy"]["weights"] = {name: factor["weight"] for name, factor in record["score"]["factors"].items()}
        record["evidence"] = calculate_evidence(context["evidence_context"])
        record["prioritization_rationale"] = context.get("prioritization_rationale", "Versioned policy v1; contributions and source references are recorded in score.")
        record["validation_state"] = "verified" if context.get("verified") else "unverified"

    def install_config(self, state, record, actor, rationale):
        config = record["scoring_policy"]
        key = config["policy_version"]
        configs = state.setdefault("configs", {})
        if key in configs and canonical_json(configs[key]) != canonical_json(config):
            raise DomainError(409, "Scoring baseline configuration differs; explicit version migration required")
        if key not in configs:
            configs[key] = copy.deepcopy(config)
            self.event(state, "SCORING_CONFIG_CREATED", key, None, config, actor, rationale)

    def ranking(self, state, actor, rationale):
        eligible = [r for r in state["requirements"].values() if r["status"] not in ("REJECTED", "POSTPONED") and r["score"]["policy_version"] == "v1"]
        ranks = rank_requirements(eligible)
        eligible.sort(key=lambda r: ranks[r["id"]])
        ranking = {"id": str(uuid.uuid4()), "timestamp": now(), "config_id": eligible[0]["score"]["policy_version"] if eligible else "v1", "configurations": {r["id"]: copy.deepcopy(r.get("scoring_policy", {})) for r in eligible}, "context": "all current active requirements", "ranks": {r["id"]: i+1 for i, r in enumerate(eligible)}, "revisions": {r["id"]: r["revision"] for r in eligible}, "scores": {r["id"]: r["score"]["effective_score"] for r in eligible}}
        previous = state["rankings"][-1] if state["rankings"] else None
        state["rankings"].append(ranking)
        self.event(state, "RANKING_RECALCULATED", "baseline", previous, ranking, actor, rationale)

    def create_requirement(self, draft, run_id, generation_metadata=None):
        if not isinstance(run_id, str) or not run_id.strip():
            raise DomainError(422, "Derivation run ID is required")
        if generation_metadata is not None:
            if not isinstance(generation_metadata, dict) or set(generation_metadata) - {"model_metadata", "prompt_metadata"}:
                raise DomainError(422, "Generation metadata must be an object")
            validated = AnalysisEngineResult.model_validate(dict(generation_metadata, drafts=[])).model_dump()
            generation_metadata = {key: validated[key] for key in ("model_metadata", "prompt_metadata")}
        draft = pending_engine_review(RequirementDraft.model_validate(draft).model_dump())
        context = self.context(draft)
        # The engine supplies a stable semantic derivation key, never an ordinal.
        identity = {k: draft[k] for k in ("derivation_key", "signal_id", "target_scope")}
        rid = "REQ-" + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
        def create(state, tx):
            self.assert_current(context, tx)
            # Reconciliation preserves original legacy IDs. Resolve their reviewed
            # semantic identity before considering a new hashed record.
            matches = [record for record in state["requirements"].values()
                       if canonical_json({key: record.get(key) for key in identity}) == canonical_json(identity)]
            candidates = {record['id']: record for record in matches}
            if rid in state['requirements']:
                candidates[rid] = state['requirements'][rid]
            if len(candidates) > 1:
                raise DomainError(409, "Semantic requirement identity is ambiguous")
            current = next(iter(candidates.values()), None)
            entity_id = current["id"] if current else rid
            if current:
                old = pending_engine_review({key: current[key] for key in draft})
                evidence_changed = evidence_basis(current) != evidence_basis(context) or current.get("validation_state") != ("verified" if context.get("verified") else "unverified")
                if canonical_json(old) == canonical_json(draft) and not evidence_changed:
                    return current
                tid = "regen-" + hashlib.sha256(json.dumps({"entity_id": entity_id, "base_revision": current["revision"], "draft": draft, "evidence_context": context}, sort_keys=True).encode()).hexdigest()
                if tid not in state["turns"]:
                    state["turns"][tid] = {"id": tid, "entity_id": entity_id, "expected_revision": current["revision"], "patch": engine_review_patch(current, {key: value for key, value in draft.items() if canonical_json(old[key]) != canonical_json(value) or (evidence_changed and key == "evidence_links")}), "state": "pending", "run_id": run_id,
                        "input_fingerprint": self.context_fingerprint(context), "input_draft": copy.deepcopy(draft)}
                    if generation_metadata is not None:
                        state["turns"][tid]["generation_metadata"] = copy.deepcopy(generation_metadata)
                    self.event(state, "REGENERATION_PROPOSED", entity_id, current, state["turns"][tid], ("engine", "AI_SYSTEM"), "Regeneration requires PM application")
                return dict(id=entity_id, revision=current["revision"], **({"pending_turn_id": tid} if state["turns"][tid]["state"] == "pending" else {}))
            record = dict(draft, id=rid, revision=1, status="DRAFT", approved_revision=None, run_id=run_id, created_at=now(), updated_at=now())
            if generation_metadata is not None:
                record["generation_metadata"] = copy.deepcopy(generation_metadata)
            self.score(record, context)
            self.install_config(state, record, ("engine", "AI_SYSTEM"), "Install authoritative scoring baseline")
            state["requirements"][rid] = record
            state["revisions"][rid] = [copy.deepcopy(record)]
            self.event(state, "REQUIREMENT_DERIVED", rid, None, record, ("engine", "AI_SYSTEM"), "Validated draft persisted with original evidence and source versions")
            self.ranking(state, ("engine", "AI_SYSTEM"), "Requirement derived")
            return record
        return self.repository.write_with_transaction(create)

    def guard(self, record, rid, request):
        if request.id is not None and request.id != rid:
            raise DomainError(422, "Route and body requirement IDs differ")
        if request.actor_role != "PM_USER" or request.actor_id.upper() == "AI_SYSTEM":
            raise DomainError(422, "A named human PM must authorize this mutation")
        if record is None:
            raise DomainError(404, "Requirement not found")
        if record["revision"] != request.expected_revision:
            raise DomainError(409, "Stale requirement revision")

    def approve_guard(self, record, request):
        # Revalidate persisted content as well as engine input; imported/tampered data is never trusted.
        RequirementDraft.model_validate({k: record[k] for k in RequirementDraft.model_fields})
        smart = record["smart"]
        metrics = smart["metrics"]
        milestone = smart["milestone"]
        evidence_ids = {l["record_id"] for l in record["evidence_links"]} | {l["source_id"] for l in record["evidence_links"]}
        for item in metrics + [milestone]:
            if item["provenance"] == "SourceBacked" and not set(item["source_references"]) <= evidence_ids:
                raise DomainError(422, "Target provenance must reference resolved requirement evidence")
        target_ids = {m["id"] for m in metrics} | {milestone["id"]}
        if not target_ids <= set(request.reviewed_target_ids):
            raise DomainError(422, "PM must review all targets and milestone")
        links = [l for l in record["evidence_links"] if l["relation"] == "supports"]
        if record.get("validation_state") != "verified" or not links or any(l["validation_state"] != "verified" for l in record["evidence_links"]):
            raise DomainError(422, "Unverified or legacy evidence blocks approval")
        if not {l["record_id"] for l in record["evidence_links"]} <= set(request.reviewed_source_ids):
            raise DomainError(422, "PM must review underlying source assignments")
        strategy = record["score_context"]
        if strategy.get("strategic_fit_level") is not None and (not strategy.get("strategy_references") or not isinstance(strategy.get("strategy_rationale"), str) or not strategy["strategy_rationale"].strip()):
            raise DomainError(422, "Strategy assessment requires cited passages and rationale")
        assumptions = record["assumptions"]
        if not set(record.get("required_assumption_ids", [])) <= {a["id"] for a in assumptions}:
            raise DomainError(422, "Authoritative evidence proxy assumptions must be recorded explicitly")
        outstanding = {a["id"] for a in assumptions if a.get("review_state") != "resolved"}
        if not outstanding <= set(request.accepted_assumption_ids):
            raise DomainError(422, "PM must accept or resolve outstanding assumptions")

    def save(self, state, before, after, action, request, application_context=None):
        after["revision"] = before["revision"] + 1
        after["updated_at"] = now()
        after["revision_actor"] = request.actor_id
        after["revision_rationale"] = request.rationale
        after.pop("revision_application", None)
        if application_context is not None:
            # Pin proposal provenance to this immutable revision and its event.
            # The mutable turn alone is insufficient to reconstruct acceptance.
            after["revision_application"] = copy.deepcopy(application_context)
        state["requirements"][after["id"]] = after
        state["revisions"][after["id"]].append(copy.deepcopy(after))
        actor = (request.actor_id, request.actor_role)
        self.event(state, action, after["id"], before, after, actor, request.rationale)
        self.ranking(state, actor, request.rationale)
        for category, fields in (("EVIDENCE_CHANGED", ("evidence_links", "evidence_context", "validation_state", "required_assumption_ids", "source_snapshots")), ("FACTORS_CHANGED", ("factor_proposals", "score_context", "scoring_policy")), ("SCORE_RECALCULATED", ("score", "evidence"))):
            if category == "EVIDENCE_CHANGED" and ("evidence_pack" in before or "evidence_pack" in after):
                fields = (*fields, "evidence_pack")
            old = {k: before.get(k) for k in fields}
            new = {k: after.get(k) for k in fields}
            if canonical_json(old) != canonical_json(new):
                self.event(state, category, after["id"], old, new, actor, request.rationale)
        return after

    def decide_requirement(self, rid, request):
        context = None
        if request.action == "approve":
            original = self.repository.read()["requirements"].get(rid)
            self.guard(original, rid, request)
            if original["status"] not in ("DRAFT", "PM_REVIEW"):
                raise DomainError(422, "Invalid state transition")
            if original.get("validation_state") != "verified" or original.get("provenance_state") == "unresolved_legacy":
                raise DomainError(422, "Unverified or legacy evidence blocks approval")
            context = self.context({k: original[k] for k in RequirementDraft.model_fields})
            if not context.get("verified"):
                raise DomainError(422, "Current source evidence is not verified")
            if evidence_basis(context) != evidence_basis(original):
                raise DomainError(409, "Evidence or scoring inputs changed; refresh the reviewed revision")
        def decide(state, tx):
            before = state["requirements"].get(rid)
            self.guard(before, rid, request)
            after = copy.deepcopy(before)
            active = before["status"] in ("DRAFT", "PM_REVIEW")
            transitions = {"start_review": "PM_REVIEW", "approve": "APPROVED", "reject": "REJECTED", "defer": "POSTPONED", "reopen": "PM_REVIEW"}
            if (request.action == "reopen" and active) or (request.action != "reopen" and not active) or (request.action == "start_review" and before["status"] != "DRAFT"):
                raise DomainError(422, "Invalid state transition")
            if request.action == "approve":
                if evidence_basis(context) != evidence_basis(before):
                    raise DomainError(409, "Evidence or scoring inputs changed; refresh the reviewed revision")
                self.assert_current(context, tx)
                self.approve_guard(before, request)
                for target in after["smart"]["metrics"] + [after["smart"]["milestone"]]:
                    target["review_state"] = "reviewed"
                for assumption in after["assumptions"]:
                    if assumption["id"] in request.accepted_assumption_ids and assumption.get("review_state") != "resolved":
                        assumption["review_state"] = "accepted"
                after["review_confirmation"] = {"targets": request.reviewed_target_ids, "sources": request.reviewed_source_ids, "assumptions": request.accepted_assumption_ids, "actor_id": request.actor_id, "rationale": request.rationale}
            if request.action != "approve":
                after.pop("review_confirmation", None)
            after["status"] = transitions[request.action]
            after["approved_revision"] = before["revision"]+1 if request.action == "approve" else None
            if "score_override" in request.model_fields_set:
                after["score"] = calculate_score(after["score_context"], override=request.score_override)
                if canonical_json(before["score"]) != canonical_json(after["score"]):
                    self.event(state, "SCORE_OVERRIDDEN", rid, before["score"], after["score"], (request.actor_id, request.actor_role), request.rationale)
            return self.save(state, before, after, "STATUS_CHANGED", request)
        if context is None:
            return self.repository.write(lambda state: decide(state, None))
        return self.repository.write_with_transaction(decide)

    def edit_requirement(self, rid, request, apply=False):
        preflight = self.repository.read()
        if apply:
            prior = preflight["turns"].get(request.turn_id)
            if prior and prior["state"] == "applied":
                self.guard_identity(rid, request)
                fingerprint = hashlib.sha256(json.dumps(request.model_dump(), sort_keys=True).encode()).hexdigest()
                if prior.get("fingerprint") != fingerprint or prior["entity_id"] != rid:
                    raise DomainError(409, "Conflicting suggestion retry")
                return copy.deepcopy(prior["result"])
        allowed = set(RequirementDraft.model_fields) - {"derivation_key", "signal_id"}
        if preflight["requirements"].get(rid, {}).get("provenance_state") == "unresolved_legacy":
            allowed = set(RequirementDraft.model_fields)
        if not request.patch or not set(request.patch) <= allowed:
            raise DomainError(422, "Patch includes empty or server-owned fields")
        # Immutable read for source resolution, then check expected revision under lock.
        original = preflight["requirements"].get(rid)
        self.guard(original, rid, request)
        proposed = {k: original[k] for k in RequirementDraft.model_fields}
        proposed.update(request.patch)
        proposed = RequirementDraft.model_validate(proposed).model_dump()
        if apply:
            proposed = engine_review_patch(original, proposed)
        proposal_context = None
        if apply:
            prior = preflight["turns"].get(request.turn_id)
            self.guard_pending_turn(prior, rid, request)
            if "input_fingerprint" in prior:
                # Resolve the stored proposal, not the PM's accepted subset or
                # modifications. They may intentionally change factors or scope.
                # Regeneration pins its original normalized engine draft; chat
                # turns can reconstruct theirs from the immutable base + patch.
                input_draft = prior.get("input_draft")
                if input_draft is None:
                    input_draft = {k: copy.deepcopy(original[k]) for k in RequirementDraft.model_fields}
                    input_draft.update(copy.deepcopy(prior["patch"]))
                    input_draft = engine_review_patch(original, RequirementDraft.model_validate(input_draft).model_dump())
                else:
                    input_draft = RequirementDraft.model_validate(input_draft).model_dump()
                proposal_context = self.context(input_draft)
                if canonical_json(input_draft) == canonical_json(proposed):
                    context = proposal_context
                else:
                    context = self.context(proposed)
            else:
                context = self.context(proposed)
        else:
            context = self.context(proposed)
        fingerprint = hashlib.sha256(json.dumps(request.model_dump(), sort_keys=True).encode()).hexdigest()
        def edit(state, tx):
            turn = state["turns"].get(request.turn_id) if apply else None
            if apply:
                if not turn or turn["entity_id"] != rid:
                    raise DomainError(404, "Suggestion not found")
                if turn["state"] == "applied":
                    if turn["fingerprint"] != fingerprint:
                        raise DomainError(409, "Conflicting suggestion retry")
                    return turn["result"]
                self.guard_pending_turn(turn, rid, request)
            before = state["requirements"].get(rid)
            self.guard(before, rid, request)
            if apply:
                self.guard_turn_inputs(turn, proposal_context, context, before)
                if proposal_context is not None and self.context_fingerprint(proposal_context) != self.context_fingerprint(context):
                    self.assert_current(proposal_context, tx)
            self.assert_current(context, tx)
            after = copy.deepcopy(before)
            identity = {key: proposed[key] for key in ("derivation_key", "signal_id", "target_scope")}
            if any(other_id != rid and canonical_json({key: record.get(key) for key in identity}) == canonical_json(identity)
                   for other_id, record in state["requirements"].items()):
                raise DomainError(409, "Semantic requirement identity already exists")
            after.update(proposed)
            after.update(status="PM_REVIEW", approved_revision=None)
            if after.get("provenance_state") == "unresolved_legacy":
                after["provenance_state"] = "reconciled_legacy"
            after.pop("review_confirmation", None)
            self.score(after, context)
            self.install_config(state, after, (request.actor_id, request.actor_role), request.rationale)
            application_context = {"turn_id": turn["id"], "run_id": turn.get("run_id"),
                "base_revision": turn["expected_revision"], "proposed_patch": turn["patch"],
                "accepted_patch": request.patch} if apply else None
            if apply and "generation_metadata" in turn:
                application_context["generation_metadata"] = turn["generation_metadata"]
            result = self.save(state, before, after, "SUGGESTION_APPLIED" if apply else "CONTENT_CHANGED", request, application_context)
            if apply:
                prior_turn = copy.deepcopy(turn)
                turn.update(state="applied", fingerprint=fingerprint, result=copy.deepcopy(result))
                self.event(state, "SUGGESTION_APPLICATION_RECORDED", rid, prior_turn, turn,
                           (request.actor_id, request.actor_role), request.rationale, entity_type="ReviewTurn")
            return result
        # Idempotent retries must bypass stale preflight, resolved above separately.
        return self.repository.write_with_transaction(edit)

    def guard_pending_turn(self, turn, rid, request):
        if not turn or turn["entity_id"] != rid:
            raise DomainError(404, "Suggestion not found")
        if turn["state"] != "pending" or turn["expected_revision"] != request.expected_revision:
            raise DomainError(409, "Suggestion is stale or unavailable")
        if not set(request.patch) <= set(turn.get("patch") or {}):
            raise DomainError(422, "Accepted patch must edit fields in the stored proposal")

    def guard_identity(self, rid, request):
        if request.id is not None and request.id != rid:
            raise DomainError(422, "Route and body requirement IDs differ")
        if request.actor_role != "PM_USER" or request.actor_id.upper() == "AI_SYSTEM":
            raise DomainError(422, "A named human PM must authorize this mutation")

    def freeze_context_guard(self, record, context):
        """Production freezes close over all inputs, not just displayed citations.

        Source snapshots carry source_id/record_id/source_version/locator and a
        nonempty raw_payload. Reference IDs can also name their fact_id/chunk_id
        (or fact_ids/chunk_ids). Signal snapshots pin full content and revisions;
        both a singular signal_snapshot and a signal_snapshots list are supported.
        """
        snapshots = context.get("source_snapshots", [])
        fields = ("source_id", "record_id", "source_version", "locator")
        for link in record["evidence_links"]:
            if not any(canonical_json({k: snapshot.get(k) for k in fields}) == canonical_json({k: link[k] for k in fields}) and bool(snapshot.get("raw_payload")) for snapshot in snapshots):
                raise DomainError(422, "Freeze requires complete pinned source snapshots for every evidence link")
        if context.get("evidence_closure_required") is not True:
            return

        pins, identifiers = {}, {}
        for snapshot in snapshots:
            try:
                SourceReference.model_validate(dict({k: snapshot.get(k) for k in fields},
                    claim_key="freeze-input", relation="context", validation_state="verified"))
            except (ValueError, TypeError) as exc:
                raise DomainError(422, "Freeze requires complete pinned source snapshots for every authoritative input") from exc
            if not isinstance(snapshot.get("raw_payload"), dict) or not snapshot["raw_payload"]:
                raise DomainError(422, "Freeze requires complete pinned source snapshots for every authoritative input")
            key = canonical_json({k: snapshot[k] for k in fields})
            if key in pins and canonical_json(pins[key]["raw_payload"]) != canonical_json(snapshot["raw_payload"]):
                raise DomainError(422, "Freeze source snapshots have conflicting pinned payloads")
            pins[key] = snapshot
            ids = [snapshot.get(k) for k in ("record_id", "id", "fact_id", "chunk_id")]
            for name in ("fact_ids", "chunk_ids"):
                values = snapshot.get(name, [])
                if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values):
                    raise DomainError(422, "Freeze snapshot reference IDs must be nonblank identifiers")
                ids.extend(values)
            for identifier in ids:
                if isinstance(identifier, str) and identifier.strip():
                    identifiers.setdefault(identifier, set()).add(key)

        def pin_reference(reference):
            if isinstance(reference, list):
                for item in reference:
                    pin_reference(item)
                return
            if isinstance(reference, str):
                matches = identifiers.get(reference, set())
            elif isinstance(reference, dict):
                aliases = {"source_file_id": "source_id", "source_record_id": "record_id", "source_locator": "locator"}
                normalized = {aliases.get(k, k): value for k, value in reference.items()}
                tokens = [normalized[k] for k in ("id", "fact_id", "chunk_id") if k in normalized]
                coordinates = {k: normalized[k] for k in fields if k in normalized}
                if not tokens and not coordinates:
                    # A factor-to-reference collection is also a useful adapter shape.
                    for value in reference.values():
                        pin_reference(value)
                    return
                located = "record_id" in coordinates or "source_id" in coordinates and "locator" in coordinates
                matches = {key for key, snapshot in pins.items()
                           if (located or tokens)
                           and all(canonical_json(snapshot[k]) == canonical_json(value) for k, value in coordinates.items())
                           and all(isinstance(token, str) and key in identifiers.get(token, set()) for token in tokens)}
            else:
                matches = set()
            if len(matches) != 1:
                raise DomainError(422, "Freeze requires an unambiguous pinned source snapshot for every factor and forecast reference")

        signals = context.get("signal_snapshots", [])
        if not isinstance(signals, list):
            raise DomainError(422, "Freeze requires pinned signal snapshots")
        signals = list(signals)
        if context.get("signal_snapshot") is not None:
            signals.append(context["signal_snapshot"])
        signal_pins = {}
        identity_fields = {"id", "signal_id", "revision", "signal_revision"}
        for signal in signals:
            if not isinstance(signal, dict):
                raise DomainError(422, "Freeze requires pinned signal snapshots")
            signal_id = signal.get("signal_id", signal.get("id"))
            revision = signal.get("signal_revision", signal.get("revision"))
            payload = signal.get("raw_payload", signal.get("payload", signal))
            if (not isinstance(signal_id, str) or not signal_id.strip()
                    or type(revision) is not int or revision < 1
                    or not isinstance(payload, dict) or not set(payload) - identity_fields):
                raise DomainError(422, "Freeze requires full pinned signal content and revisions")
            key = (signal_id, revision)
            if key in signal_pins and canonical_json(signal_pins[key]) != canonical_json(signal):
                raise DomainError(422, "Freeze signal snapshots have conflicting pinned payloads")
            signal_pins[key] = signal

        def pin_signals(references):
            if not isinstance(references, list):
                raise DomainError(422, "Freeze requires pinned signal references")
            for reference in references:
                if not isinstance(reference, dict):
                    raise DomainError(422, "Freeze requires pinned signal references")
                signal_id = reference.get("signal_id", reference.get("id"))
                revision = reference.get("revision", reference.get("signal_revision"))
                if not isinstance(signal_id, str) or type(revision) is not int or (signal_id, revision) not in signal_pins:
                    raise DomainError(422, "Freeze requires pinned snapshots for every used signal revision")

        def check_references(value):
            if isinstance(value, dict):
                for name, item in value.items():
                    if name in {"source_snapshots", "raw_payload", "locator", "source_locator"}:
                        continue
                    if name in {"finding_links", "signal_references"}:
                        pin_signals(item)
                    elif name in {"source_reference", "evidence_links", "references"} or name.endswith("_references"):
                        pin_reference(item)
                    else:
                        check_references(item)
            elif isinstance(value, list):
                for item in value:
                    check_references(item)

        pin_signals(record["finding_links"])
        check_references(context)
        for signal in signals:
            check_references(signal.get("raw_payload", signal.get("payload", signal)))
        check_references(record["score"]["factors"])
        for forecast in context["score_context"].get("forecasts", []):
            if "source_reference" not in forecast:
                raise DomainError(422, "Freeze requires pinned sources for every forecast")
            pin_reference(forecast["source_reference"])

    def freeze(self, request):
        if request.actor_id.upper() == "AI_SYSTEM":
            raise DomainError(422, "A named human PM must authorize freeze")
        preflight = self.repository.read()
        contexts = {}
        for rid, revision in request.selections.items():
            record = preflight["requirements"].get(rid)
            if not record or record["revision"] != revision or record["status"] != "APPROVED":
                raise DomainError(409, "Freeze requires current approved revisions")
            context = self.context({k: record[k] for k in RequirementDraft.model_fields})
            if not context.get("verified") or evidence_basis(context) != evidence_basis(record):
                raise DomainError(409, "Evidence changed since approval; refresh and review")
            self.freeze_context_guard(record, context)
            contexts[rid] = context
        def freeze(state, tx):
            ranking = state["rankings"][-1] if state["rankings"] else None
            if not ranking or ranking["id"] != request.expected_ranking_id:
                raise DomainError(409, "Stale ranking baseline")
            records = []
            for rid, revision in request.selections.items():
                record = state["requirements"].get(rid)
                if not record or record["status"] != "APPROVED" or record["revision"] != revision or record["approved_revision"] != revision:
                    raise DomainError(409, "Freeze requires current approved revisions")
                if ranking["revisions"].get(rid) != revision:
                    raise DomainError(409, "Approved revision is outside the ranking baseline")
                if evidence_basis(contexts[rid]) != evidence_basis(record):
                    raise DomainError(409, "Evidence changed since approval; refresh and review")
                self.assert_current(contexts[rid], tx)
                records.append(copy.deepcopy(record))
            snapshot = {"id": str(uuid.uuid4()), "timestamp": now(), "actor_id": request.actor_id, "rationale": request.rationale, "requirements": records, "ranking": copy.deepcopy(ranking), "configurations": copy.deepcopy(state.get("configs", {})), "scoring_policies": {r["id"]: copy.deepcopy(r.get("scoring_policy", {})) for r in records}, "source_snapshots": [copy.deepcopy(source) for r in records for source in r.get("source_snapshots", [])]}
            self.event(state, "FREEZE_CREATED", snapshot["id"], None, snapshot, (request.actor_id, request.actor_role), request.rationale)
            snapshot["checkpoint"] = copy.deepcopy(state["checkpoint"])
            snapshot["audit"] = audit_bundle(state, snapshot["checkpoint"])
            state["freezes"][snapshot["id"]] = snapshot
            return snapshot
        return self.repository.write_with_transaction(freeze)

    def import_legacy(self, records, actor_id="legacy-import", rationale="Preserve unresolved historical proposals"):
        """Import without claiming validated evidence, conversion, or PM approval.

        Exact originals remain attached. Unverified legacy proposals cannot be frozen
        or enter the active v1 scoring baseline until source reconciliation.
        """
        if not isinstance(actor_id, str) or not actor_id.strip() or not isinstance(rationale, str) or not rationale.strip():
            raise DomainError(422, "Legacy import requires actor and rationale")
        originals = copy.deepcopy(list(records))
        prepared = []
        for original in originals:
            if not isinstance(original, dict):
                raise DomainError(422, "Legacy record must be an object")
            payload = original.get("requirement", original)
            if not isinstance(payload, dict):
                raise DomainError(422, "Legacy requirement payload must be an object")
            rid = payload.get("req_id") or payload.get("id")
            if not isinstance(rid, str) or not rid.strip():
                raise DomainError(422, "Legacy requirement ID is required")
            score = payload.get("score", payload)
            if not isinstance(score, dict):
                raise DomainError(422, "Legacy score must be an object")
            context = {"policy_version": "legacy-v0", "impact": score.get("impact"), "market_reach_norm": score.get("market_reach_norm"), "effort": score.get("effort")}
            calculated = calculate_score(context)
            stored = score.get("bmw_priority", payload.get("bmw_priority"))
            if stored is not None:
                validated = calculate_score(context, override=stored)
                calculated["effective_score"] = validated["effective_score"]
                calculated["legacy_stored_score"] = stored
            status = payload.get("status", "DRAFT")
            if status == "CONFLICTED":
                status = "PM_REVIEW"
            if status not in ("DRAFT", "PM_REVIEW", "APPROVED", "REJECTED", "POSTPONED"):
                raise DomainError(422, "Unsupported legacy status")
            record = {"id": rid, "revision": 1, "status": status, "approved_revision": None,
                "legacy_original": original, "validation_state": "unverified", "provenance_state": "unresolved_legacy",
                "title": payload.get("title", rid), "description": payload.get("description", payload.get("jtbd", "Unresolved legacy proposal")),
                "problem_statement": payload.get("jtbd", "Unresolved legacy evidence"), "smart": payload.get("smart", {}),
                "source_scope": {}, "target_scope": {}, "finding_links": [], "evidence_links": [], "assumptions": [], "uncertainties": [{"description": "Original source lineage requires reconciliation"}],
                "factor_proposals": {}, "has_conflict": payload.get("status") == "CONFLICTED", "derivation_key": "legacy:" + rid,
                "signal_id": "unresolved-legacy", "signal_revision": 1, "run_id": "legacy-import", "created_at": now(), "updated_at": now(),
                "score_context": context, "score": calculated, "evidence": {"policy_version": "unavailable-legacy", "value": None, "level": "Unavailable", "is_probability": False},
                "prioritization_rationale": "Legacy-v0 arithmetic preserved; no validated v1 conversion"}
            prepared.append(record)
        if len({r["id"] for r in prepared}) != len(prepared):
            raise DomainError(422, "Duplicate legacy requirement IDs")
        def import_records(state):
            results = []
            for record in prepared:
                current = state["requirements"].get(record["id"])
                if current:
                    if canonical_json(current.get("legacy_original")) != canonical_json(record["legacy_original"]):
                        raise DomainError(409, "Legacy import cannot overwrite existing authoritative content")
                    results.append(current)
                    continue
                state["requirements"][record["id"]] = record
                state["revisions"][record["id"]] = [copy.deepcopy(record)]
                self.event(state, "LEGACY_REQUIREMENT_IMPORTED", record["id"], None, record, (actor_id, "AI_SYSTEM"), rationale)
                results.append(record)
            return results
        return self.repository.write(import_records)
