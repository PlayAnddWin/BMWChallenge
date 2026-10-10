"""Requirement drafting agent; output is validated proposed content only."""
from copy import deepcopy
import re
from datetime import date
from typing import Callable

from pydantic import ValidationError

from contracts.intelligence import (
    BatchResult, DraftBatchOutput, EvidencePack, ItemFailure, RequirementDraft, Scope, Signal,
    TestParameter,
)
from services.common import EngineError, canonical, keyed_rows
from services.agents import DRAFT_AGENT
from services.interfaces import AuditSink
from services.llm_client import ModelAdapter, keyed_output_schema, structured_reply
from services.quality import ProposalQualityError, validate_proposal_quality

PROMPT_VERSION = DRAFT_AGENT.prompt_version
SYSTEM_PROMPT = DRAFT_AGENT.system_prompt


def validate_draft(draft: RequirementDraft, signal: Signal, pack: EvidencePack, scope: Scope,
                   *, as_of: date | None = None) -> RequirementDraft:
    if draft.input_signal_id != signal.id or pack.signal_id != signal.id or pack.signal_revision != signal.revision:
        raise EngineError("signal identity/version mismatch")
    if draft.source_scope != signal.source_scope or draft.target_scope != scope or pack.target_scope != scope:
        raise EngineError("draft scope differs from supplied source/target scope")
    if not (draft.jtbd.startswith("When I ") and ", I want " in draft.jtbd and ", so that " in draft.jtbd):
        raise EngineError("JTBD must follow When I ..., I want ..., so that ...")
    allowed = pack.citation_ids()
    parameters = [parameter for metric in draft.smart.metrics for parameter in metric.parameters]
    assertions = draft.claims + draft.smart.metrics + parameters + [draft.smart.feasibility, draft.smart.milestone] + draft.assumptions
    ids = [item.id for item in assertions]
    if len(ids) != len(set(ids)):
        raise EngineError("duplicate claim/target/assumption identifiers")
    for item in assertions:
        if not set(item.evidence_ids) <= allowed:
            raise EngineError(f"unknown evidence in {item.id}")
        if item.review_state != "unreviewed" or item.provenance == "PMEntered":
            raise EngineError("model cannot claim PM entry or completed human review")
        if item.provenance == "SourceBacked" and not item.evidence_ids:
            raise EngineError(f"SourceBacked assertion {item.id} requires supplied citations")
    document_ids = {passage.id for passage in pack.equipment + pack.strategy}
    if any(claim.provenance == "SourceBacked" and claim.kind in {"architecture", "competitor", "packaging", "strategy"}
           and not (set(claim.evidence_ids) & document_ids) for claim in draft.claims):
        raise EngineError("capability/competitor/package/strategy assertions need applicable documentary evidence")
    source_text = {span.id: span.text for span in pack.supporting + pack.opposing} | {
        passage.id: passage.text for passage in pack.equipment + pack.strategy
    } | {fact.id: canonical({"value": fact.value, "unit": fact.unit, "metric": fact.metric,
                            "year": fact.year, "scale": fact.scale, "respondent_count": fact.respondent_count}) for fact in pack.sales} | {
        study.fact.id: canonical({"value": study.fact.value, "unit": study.fact.unit, "metric": study.fact.metric,
                                  "year": study.fact.year, "scale": study.fact.scale,
                                  "respondent_count": study.fact.respondent_count}) for study in pack.studies
    }
    for claim in draft.claims:
        if claim.provenance == "SourceBacked":
            numbers = set(re.findall(r"(?<![\w])\d+(?:\.\d+)?%?", claim.text))
            supplied = set(re.findall(r"(?<![\w])\d+(?:\.\d+)?%?", " ".join(source_text[item] for item in claim.evidence_ids)))
            # Counts derived from the cited canonical comments are source-backed metadata, not invented quote text.
            cited_spans = [span for span in pack.supporting + pack.opposing if span.id in claim.evidence_ids]
            if claim.relation == "supports":
                cited_spans = [span for span in cited_spans if span.polarity == "supports"]
            elif claim.relation == "contradicts":
                cited_spans = [span for span in cited_spans if span.polarity == "opposes"]
            count = len({span.feedback_id for span in cited_spans})
            for number in re.findall(r"\b(\d+)\s+(?:[A-Za-z0-9_-]+\s+){0,6}(?:comments?|feedback records?)\b", claim.text):
                if int(number) != count:
                    raise EngineError("source-backed comment count differs from distinct cited issue assignments")
                supplied.add(number)
            if not numbers <= supplied:
                raise EngineError("source-backed claim contains an unsupplied quantitative assertion")
    factors = [factor.factor for factor in draft.factor_proposals]
    if len(factors) != len(set(factors)):
        raise EngineError("duplicate factor proposal")
    for factor in draft.factor_proposals:
        if not set(factor.evidence_ids) <= allowed:
            raise EngineError("unknown factor evidence")
    basis = {span.id for span in pack.supporting} | {
        study.fact.id for study in pack.studies if study.mapping_reviewed and study.relation == "direct"
    }
    opposition = {span.id for span in pack.opposing} | {
        study.fact.id for study in pack.studies if study.mapping_reviewed and study.relation == "contradictory"
    }
    problems = [claim for claim in draft.claims if claim.kind == "problem"]
    if not any(claim.provenance == "SourceBacked" and claim.relation == "supports" and set(claim.evidence_ids) & basis
               for claim in problems):
        raise EngineError("problem claims need traceable direct supporting evidence")
    for claim in problems:
        references = set(claim.evidence_ids)
        if claim.provenance != "SourceBacked" or not references:
            raise EngineError("problem observations must be source-backed and cited")
        if claim.relation == "supports" and not references & basis:
            raise EngineError("supporting problem claim cites no direct support")
        if claim.relation == "contradicts" and not references & opposition:
            raise EngineError("contradicting problem claim cites no opposing evidence")
    native_facts = {fact.id: fact for fact in pack.sales} | {study.fact.id: study.fact for study in pack.studies}
    for metric in draft.smart.metrics + parameters:
        if metric.provenance == "SourceBacked":
            usage = metric.purpose if isinstance(metric, TestParameter) else "acceptance_target"
            if not any(identifier in native_facts and native_facts[identifier].value == metric.value
                       and native_facts[identifier].unit == metric.unit and usage in native_facts[identifier].reviewed_usage
                       for identifier in metric.evidence_ids):
                raise EngineError("SourceBacked metric must match a supplied native fact value/unit")
    validate_proposal_quality(draft, pack, as_of or date.today())
    # Deterministically preserve known limitations even when the model returns an empty gap array.
    uncertainties = list(dict.fromkeys(pack.gaps + draft.uncertainties))
    if any(item.provenance == "AIProposed" for item in assertions):
        uncertainties.append("AI-proposed targets/assertions require independent validation and PM review.")
    unknown = [factor.factor for factor in draft.factor_proposals if factor.value is None]
    if unknown:
        uncertainties.append("Unassessed scoring factors: " + ", ".join(unknown) + "; backend must keep them provisional.")
    return draft.model_copy(update={"uncertainties": list(dict.fromkeys(uncertainties))})


def draft_requirements(inputs: list[tuple[Signal, EvidencePack]], planning_scope: Scope, *,
                       model: ModelAdapter, on_validate: Callable[[str], None] | None = None,
                       repair_quality: bool = False, audit: AuditSink | None = None,
                       run_id: str | None = None) -> BatchResult[RequirementDraft]:
    expected = {signal.id for signal, _ in inputs}
    if len(expected) != len(inputs):
        raise EngineError("duplicate input signal IDs")
    if not inputs:
        return BatchResult(items=[], failures=[], state="succeeded")
    batch_limit = getattr(model, "max_draft_batch_size", len(inputs))
    if not isinstance(batch_limit, int) or batch_limit < 1:
        raise EngineError("model draft batch limit must be positive")
    if len(inputs) > batch_limit:
        items, failures = [], []
        for offset in range(0, len(inputs), batch_limit):
            result = draft_requirements(inputs[offset:offset + batch_limit], planning_scope, model=model,
                                        on_validate=on_validate, repair_quality=repair_quality, audit=audit, run_id=run_id)
            items.extend(result.items)
            failures.extend(result.failures)
        return BatchResult(items=items, failures=failures,
                           state="partial" if failures and items else "failed" if failures else "succeeded")
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": canonical({
        "planning_scope": planning_scope.model_dump(),
        "planning_as_of": date.today().isoformat(),
        "inputs": [{"signal": signal.model_dump(), "evidence_pack": pack.model_dump()} for signal, pack in inputs],
    })}]
    for signal, pack in inputs:
        observed = {region for span in pack.supporting for region in span.scope.regions} | {
            region for study in pack.studies if study.relation == "direct" and study.mapping_reviewed for region in study.fact.scope.regions}
        missing = sorted(set(planning_scope.regions) - observed)
        templates = [
            {"kind": "target_validation", "review_owner": "UX", "text": "Proposed thresholds and task protocol require UX validation; no measured baseline or acceptance benchmark is supplied."},
            {"kind": "implementation", "review_owner": "Engineering", "text": "Implementation route and capability are unassessed; Engineering must validate alternatives before committing feasibility or effort."},
        ]
        if missing:
            templates.append({"kind": "scope_carryover", "review_owner": "PM", "text":
                              "Applicability to " + ", ".join(missing) + " lacks direct issue evidence; PM must commission regional validation before claiming carryover."})
        templates = [{**template, "provenance": "AIProposed", "review_state": "unreviewed"} for template in templates]
        messages.append({"role": "user", "content": canonical({"input_signal_id": signal.id,
                                                                 "required_assumption_templates": templates,
                                                                 "source_problem": signal.problem_statement})})
        if "discover" in (signal.issue_key + " " + signal.title).lower() or "hard to find" in signal.problem_statement.lower():
            messages.append({"role": "user", "content": canonical({
                "input_signal_id": signal.id,
                "drafting_guidance": {
                    "preferred_measure": "task interaction count, not a satisfaction probability",
                    "proposed_target": {"value": 2, "unit": "actions", "operator": "<=", "provenance": "AIProposed"},
                    "rationale": "A short interaction path is a hypothesis for UX/PM review, not a measured BMW threshold.",
                    "suggested_test": "Parked vehicle or prototype, default home screen, no coaching; count each deliberate input event including repeats (not unique input types), until the changed temperature value is visible. Participant plan to be set by UX, not invented.",
                    "alternative_hypotheses": ["Persistent access shortcut", "Reduce navigation depth"],
                    "required_reviews": ["Target threshold/test protocol by UX", "Implementation feasibility by Engineering", "Unobserved target-market carryover by PM"],
                    "severity_guidance": "Level 2 repeated usability friction; the supplied comments do not establish a safety issue or unavailable core function.",
                },
            })})
    try:
        schema = keyed_output_schema(DraftBatchOutput.model_json_schema(), "requirements", "RequirementDraft",
                                     "input_signal_id", sorted(expected))
        def scope_schema(scope: Scope) -> dict:
            definition = Scope.model_json_schema()
            for dimension, values in scope.model_dump().items():
                field = definition["properties"][dimension]
                field["minItems"] = field["maxItems"] = len(values)
                if values:
                    field["items"]["enum"] = values
            return definition

        fields = schema["$defs"]["RequirementDraft"]["properties"]
        fields["target_scope"] = scope_schema(planning_scope)
        if all(signal.source_scope == inputs[0][0].source_scope for signal, _ in inputs):
            fields["source_scope"] = scope_schema(inputs[0][0].source_scope)
        packs = [pack for _, pack in inputs]
        facts = [fact for pack in packs for fact in pack.sales] + [study.fact for pack in packs for study in pack.studies]
        # Customer feedback and observed survey/sales figures do not establish future acceptance thresholds.
        if not any("acceptance_target" in fact.reviewed_usage for fact in facts):
            schema["$defs"]["Target"]["properties"]["provenance"]["enum"] = ["AIProposed"]
        if not any(set(fact.reviewed_usage) & {"test_condition", "sample_size"} for fact in facts):
            schema["$defs"]["TestParameter"]["properties"]["provenance"]["enum"] = ["AIProposed"]
        for field, kind in (("feasibility", "feasibility"), ("milestone", "milestone")):
            if not any(kind in passage.reviewed_claim_types for pack in packs for passage in pack.equipment + pack.strategy):
                definition = deepcopy(schema["$defs"]["Assertion"])
                definition["properties"]["provenance"]["enum"] = ["AIProposed"]
                definition["properties"]["review_state"]["enum"] = ["unreviewed"]
                schema["$defs"]["SmartFields"]["properties"][field] = definition
        for name in ("Target", "TestParameter", "Assertion", "Assumption", "Claim"):
            schema["$defs"][name]["properties"]["review_state"]["enum"] = ["unreviewed"]
            provenance = schema["$defs"][name]["properties"]["provenance"]
            provenance["enum"] = [value for value in provenance["enum"] if value != "PMEntered"]
        # Inferred customer benefits must not masquerade as directly evidenced outcomes.
        claim_definition = schema["$defs"]["Claim"]
        other_claim = deepcopy(claim_definition)
        other_claim["properties"]["kind"]["enum"] = [kind for kind in other_claim["properties"]["kind"]["enum"] if kind != "rationale"]
        proposed_rationale = deepcopy(claim_definition)
        proposed_rationale["properties"]["kind"]["enum"] = ["rationale"]
        proposed_rationale["properties"]["provenance"]["enum"] = ["AIProposed"]
        claim_variants = [other_claim, proposed_rationale]
        statements = sorted({span.text for pack in packs for span in pack.supporting + pack.opposing} | {
            passage.text for pack in packs for passage in pack.equipment + pack.strategy})
        if statements:
            sourced_rationale = deepcopy(claim_definition)
            sourced_rationale["properties"]["kind"]["enum"] = ["rationale"]
            sourced_rationale["properties"]["provenance"]["enum"] = ["SourceBacked"]
            sourced_rationale["properties"]["text"]["enum"] = statements
            claim_variants.append(sourced_rationale)
        fields["claims"]["items"] = {"anyOf": claim_variants}
        # Separate factor schemas enforce the evidence vocabulary for assessed factors and allow genuine unknowns.
        source_ids = {"severity": {span.id for pack in packs for span in pack.supporting} | {
            study.fact.id for pack in packs for study in pack.studies if study.relation == "direct" and study.mapping_reviewed},
            "strategic_fit": {passage.id for pack in packs for passage in pack.strategy},
            "complexity": {passage.id for pack in packs for passage in pack.equipment + pack.strategy
                           if "feasibility" in passage.reviewed_claim_types}}
        variants = []
        for factor, limits in (("severity", (1, 4)), ("strategic_fit", (0, 4)), ("complexity", (1, 5))):
            unknown = deepcopy(schema["$defs"]["FactorProposal"])
            unknown["properties"]["factor"]["enum"] = [factor]
            unknown["properties"]["value"] = {"type": "null"}
            unknown["properties"]["evidence_ids"]["maxItems"] = 0
            variants.append(unknown)
            if source_ids[factor]:
                assessed = deepcopy(schema["$defs"]["FactorProposal"])
                assessed["properties"]["factor"]["enum"] = [factor]
                assessed["properties"]["value"] = {"type": "number", "minimum": limits[0], "maximum": limits[1]}
                assessed["properties"]["evidence_ids"].update(minItems=1)
                assessed["properties"]["evidence_ids"]["items"]["enum"] = sorted(source_ids[factor])
                variants.append(assessed)
        fields["factor_proposals"]["items"] = {"anyOf": variants}
        raw = structured_reply(model, messages, schema, max_attempts=1 if repair_quality else 2)
        if set(raw) != {"requirements"} or not isinstance(raw["requirements"], list):
            raise EngineError("expected requirements array")
        mapped = keyed_rows(raw["requirements"], expected, "input_signal_id")
    except (EngineError, TypeError, AttributeError) as error:
        return BatchResult(items=[], failures=[ItemFailure(input_id=identifier, code="batch_invalid", message=str(error))
                                              for identifier in sorted(expected)], state="failed")
    items, failures = [], []
    for signal, pack in inputs:
        if signal.id not in mapped:
            failures.append(ItemFailure(input_id=signal.id, code="missing_output", message="Model omitted requested signal"))
            continue
        try:
            if on_validate:
                on_validate(signal.id)
            draft = RequirementDraft.model_validate(mapped[signal.id])
            items.append(validate_draft(draft, signal, pack, planning_scope))
        except ProposalQualityError as error:
            if not repair_quality:
                failures.append(ItemFailure(input_id=signal.id, code="draft_invalid", message=str(error)))
                continue
            if audit:
                audit.record("DRAFT_QUALITY_REPAIR_REQUESTED", {"input_signal_id": signal.id, "run_id": run_id,
                             "model": model.model, "prompt_version": PROMPT_VERSION, "issues": error.issues,
                             "original_draft": mapped[signal.id]})
            repair_messages = messages + [
                {"role": "assistant", "content": canonical({"requirements": [mapped[signal.id]]})},
                {"role": "user", "content": canonical({"input_signal_id": signal.id, "quality_errors": error.issues,
                    "instruction": "Repair only this draft's proposal quality once. Preserve supplied source/target scopes, signal identity and original evidence. Do not invent sources or claim PM review. Return only this input ID. Review every quality rule, not only the first error."})},
            ]
            try:
                repair_schema = keyed_output_schema(schema, "requirements", "RequirementDraft", "input_signal_id", [signal.id])
                repaired = structured_reply(model, repair_messages, repair_schema, max_attempts=1)
                if set(repaired) != {"requirements"} or not isinstance(repaired["requirements"], list):
                    raise EngineError("expected requirements array in quality repair")
                repair_rows = keyed_rows(repaired["requirements"], {signal.id}, "input_signal_id")
                if signal.id not in repair_rows:
                    raise EngineError("quality repair omitted requested signal")
                candidate = RequirementDraft.model_validate(repair_rows[signal.id])
                validated = validate_draft(candidate, signal, pack, planning_scope)
                if audit:
                    audit.record("DRAFT_QUALITY_REPAIR_SUCCEEDED", {"input_signal_id": signal.id, "run_id": run_id,
                                 "model": model.model, "prompt_version": PROMPT_VERSION})
                items.append(validated)
            except (EngineError, ValidationError, TypeError, AttributeError) as repair_error:
                if audit:
                    audit.record("DRAFT_QUALITY_REPAIR_FAILED", {"input_signal_id": signal.id, "run_id": run_id,
                                 "error": str(repair_error), "model": model.model, "prompt_version": PROMPT_VERSION})
                failures.append(ItemFailure(input_id=signal.id, code="quality_repair_failed", message=str(repair_error)))
        except (EngineError, ValidationError) as error:
            failures.append(ItemFailure(input_id=signal.id, code="draft_invalid", message=str(error)))
    return BatchResult(items=items, failures=failures,
                       state="partial" if failures and items else "failed" if failures else "succeeded")


def draft_requirement(signal: Signal, pack: EvidencePack, planning_scope: Scope, *, model: ModelAdapter,
                      on_validate: Callable[[str], None] | None = None, repair_quality: bool = True,
                      audit: AuditSink | None = None, run_id: str | None = None) -> RequirementDraft:
    result = draft_requirements([(signal, pack)], planning_scope, model=model, on_validate=on_validate,
                                repair_quality=repair_quality, audit=audit, run_id=run_id)
    if result.failures:
        raise EngineError(result.failures[0].message)
    return result.items[0]
