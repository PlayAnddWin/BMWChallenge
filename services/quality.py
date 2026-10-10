"""Deterministic proposal-quality checks, distinct from semantic proof or PM approval."""
from datetime import date
import re

from contracts.intelligence import EvidencePack, RequirementDraft
from services.common import EngineError


class ProposalQualityError(EngineError):
    """Repairable proposal omissions. Invalid identity/evidence/review authority is not this error."""
    def __init__(self, issues: list[str]):
        self.issues = issues
        super().__init__("; ".join(issues))


def _numbers(text: str) -> set[float]:
    tokens = re.findall(r"(?<![\w])[-+]?\d+(?:\.\d+)?%?", text)
    return {float(token.rstrip("%")) / (100 if token.endswith("%") else 1) for token in tokens}


def _unit(unit: str) -> str:
    unit = unit.strip().lower()
    if unit.startswith("%") or unit.startswith("percentage") or unit.startswith("percent "):
        return "proportion"
    return {"%": "proportion", "ratio": "proportion", "fraction": "proportion", "percent": "proportion",
            "s": "seconds", "second": "seconds", "minute": "minutes", "participant": "participants",
            "user": "participants", "users": "participants", "action": "actions", "interaction": "actions",
            "interactions": "actions", "tap": "actions", "taps": "actions", "steps": "actions"}.get(unit, unit)


def _quantity(value: float, unit: str) -> tuple[float, str]:
    unit_lower = unit.strip().lower()
    percentage = unit_lower.startswith("%") or unit_lower.startswith("percent")
    return value / (100 if percentage else 1), _unit(unit)


def _quantities(text: str) -> set[tuple[float, str]]:
    matches = re.findall(r"(?<![\w])([-+]?\d+(?:\.\d+)?)\s*(%|seconds?\b|s\b|minutes?\b|participants?\b|users?\b|actions?\b|interactions?\b|taps?\b|steps\b)", text, re.I)
    return {_quantity(float(value), unit) for value, unit in matches}


def _asserts_safety(text: str, pattern: str) -> bool:
    """Conservative lexical flag, permitting explicit evidence disclaimers rather than treating them as claims."""
    disclaimers = r"(?:do(?:es)? not|don't|doesn't|cannot|can't) (?:establish|demonstrate|prove|confirm|support|show)|no (?:evidence|proof|basis)|not (?:established|demonstrated|proven|confirmed|supported)|\bno\s+(?:safety|distraction)\b"
    for clause in re.split(r"[.;\n]|\bbut\b|\bhowever\b", text, flags=re.I):
        if re.search(pattern, clause, re.I) and not re.search(disclaimers, clause, re.I):
            return True
    return False


def validate_proposal_quality(draft: RequirementDraft, pack: EvidencePack, as_of: date) -> None:
    issues = []

    def require(condition: bool, message: str) -> None:
        if not condition:
            issues.append(message)

    require(draft.description.strip().casefold() != draft.jtbd.strip().casefold(),
            "description must be an actionable requirement, not a duplicate JTBD")
    authored = [draft.title, draft.description, draft.jtbd, draft.smart.specific, draft.smart.relevance] + draft.alternatives
    authored += [text for metric in draft.smart.metrics for text in
                 (metric.conditions, metric.test_method, metric.measurement_definition, metric.rationale)]
    # The current workbench authors English requirements. Original-language cited claims/spans are untouched.
    require(not any(re.search(r"[\u0600-\u06ff]", text) for text in authored),
            "English requirement prose contains an unexpected Arabic-script fragment; preserve original quotes separately")
    reviewed = {kind: {passage.id for passage in pack.equipment + pack.strategy if kind in passage.reviewed_claim_types}
                for kind in ("feasibility", "architecture", "competitor", "milestone")}
    if draft.smart.feasibility.provenance == "SourceBacked":
        require(bool(set(draft.smart.feasibility.evidence_ids) & reviewed["feasibility"]),
                "SourceBacked feasibility needs explicitly reviewed capability evidence; feedback is not feasibility proof")
    evidence_text = {span.id: span.text for span in pack.supporting + pack.opposing}
    evidence_text.update({study.fact.id: study.fact.metric for study in pack.studies})
    evidence_text.update({passage.id: passage.text for passage in pack.equipment + pack.strategy})
    safety = r"\b(?:safety|unsafe|danger|crash|distraction|distract\w*)\b|eyes.*road"
    for claim in draft.claims:
        if claim.provenance != "SourceBacked":
            continue
        if claim.kind in {"architecture", "competitor"}:
            require(bool(set(claim.evidence_ids) & reviewed[claim.kind]),
                    f"SourceBacked {claim.kind} needs explicitly reviewed documentary evidence")
        cited = " ".join(evidence_text.get(identifier, "") for identifier in claim.evidence_ids)
        require(not _asserts_safety(claim.text, safety) or bool(re.search(safety, cited, re.I)),
                "source-backed claim cannot add an unsupplied safety/distraction consequence")
        if claim.kind == "rationale":
            require(any(claim.text in evidence_text.get(identifier, "") for identifier in claim.evidence_ids),
                    "inferred benefit rationale must be AIProposed; source-backed rationale must be a direct supplied statement")
    milestone = draft.smart.milestone
    years = {int(year) for year in re.findall(r"\b(?:19|20)\d{2}\b", milestone.text)}
    require(not any(year < as_of.year for year in years), "new requirement milestone cannot be in the past")
    require(milestone.provenance != "AIProposed" or not years,
            "no supplied planning date: propose a named gate with date to be confirmed, not an invented calendar milestone")
    if milestone.provenance == "SourceBacked":
        require(bool(set(milestone.evidence_ids) & reviewed["milestone"]),
                "SourceBacked milestone needs reviewed planning evidence, not a sales forecast or feedback")
    for metric in draft.smart.metrics:
        declared_quantities = {_quantity(metric.value, metric.unit)} | {
            _quantity(parameter.value, parameter.unit) for parameter in metric.parameters}
        declared = {metric.value} | {parameter.value for parameter in metric.parameters} | {value for value, _ in declared_quantities}
        text = " ".join((metric.conditions, metric.test_method, metric.measurement_definition))
        # Procedure numbering and the mathematical percent-conversion factor are not proposed cutoffs.
        text = re.sub(r"(?<![\w])\d+\)\s+", "", text)
        if _unit(metric.unit) == "proportion":
            text = re.sub(r"(?:x|×|\*)\s*100\b", "", text, flags=re.I)
        require(_numbers(text) <= declared,
                "numeric test conditions/sample sizes must be explicit provenance-tagged metric parameters")
        require(_quantities(text) <= declared_quantities,
                "numeric test condition units do not match the declared target/parameter units")
    require(len({alternative.strip().casefold() for alternative in draft.alternatives}) == len(draft.alternatives),
            "alternatives must be distinct")
    proposed = draft.smart.metrics + [draft.smart.feasibility, milestone]
    require(not any(item.provenance == "AIProposed" for item in proposed) or bool(draft.assumptions),
            "proposed targets/feasibility/milestone need explicit reviewable assumptions")
    kinds = {assumption.kind for assumption in draft.assumptions}
    owners = {"target_validation": "UX", "implementation": "Engineering", "scope_carryover": "PM"}
    for assumption in draft.assumptions:
        require(assumption.kind not in owners or assumption.review_owner == owners[assumption.kind],
                "assumption review owner does not match the required PM/UX/Engineering responsibility")
    require(not any(metric.provenance == "AIProposed" for metric in draft.smart.metrics) or "target_validation" in kinds,
            "proposed acceptance target needs a target_validation assumption with review owner")
    require(draft.smart.feasibility.provenance != "AIProposed" or "implementation" in kinds,
            "unassessed feasibility needs an implementation assumption with review owner")
    observed_regions = {region for span in pack.supporting for region in span.scope.regions} | {
        region for study in pack.studies if study.relation == "direct" and study.mapping_reviewed for region in study.fact.scope.regions}
    require(not (set(draft.target_scope.regions) - observed_regions) or "scope_carryover" in kinds,
            "missing target-market evidence needs an explicit scope_carryover assumption with PM review")
    strategy_ids = {passage.id for passage in pack.strategy}
    supporting_ids = {span.id for span in pack.supporting} | {
        study.fact.id for study in pack.studies if study.mapping_reviewed and study.relation == "direct"}
    for factor in draft.factor_proposals:
        if factor.value is None:
            continue
        if factor.factor == "strategic_fit":
            require(bool(set(factor.evidence_ids) & strategy_ids),
                    "numeric strategic fit proposal requires a supplied strategy citation; otherwise use null")
        if factor.factor == "severity":
            require(bool(set(factor.evidence_ids) & supporting_ids), "numeric severity proposal requires direct issue-support citations")
            cited = " ".join(evidence_text.get(identifier, "") for identifier in factor.evidence_ids)
            require(not _asserts_safety(factor.reason, safety) or bool(re.search(safety, cited, re.I)),
                    "severity cannot be escalated using an unsupplied safety/distraction claim")
        if factor.factor == "complexity":
            require(bool(set(factor.evidence_ids) & reviewed["feasibility"]),
                    "implementation route is unverified: complexity must be null without reviewed capability evidence")
    if issues:
        raise ProposalQualityError(issues)
