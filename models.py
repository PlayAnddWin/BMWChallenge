"""Pydantic models shared across pipeline and API."""
from __future__ import annotations
import copy
from typing import Literal, Optional
from pydantic import BaseModel, Field


# Legacy engine DTOs below preserve historical payload compatibility only.
# New authoritative writes use the strict v1 contracts later in this module.
# ── Enums / Literals ───────────────────────────────────────────────────────
ModelCode       = Literal["F70", "G60", "G70"]
EvidenceTier    = Literal["STRONG", "MODERATE", "WEAK"]
GateColor       = Literal["GREEN", "AMBER", "RED"]
KanoType        = Literal["Must-Be", "One-Dimensional", "Attractive"]
MoSCoW          = Literal["Must-Have", "Should-Have", "Could-Have", "Won't-Have"]
RequirementStatus = Literal[
    "DRAFT", "PM_REVIEW", "APPROVED", "REJECTED", "POSTPONED", "CONFLICTED"
]
ActorRole       = Literal["AI_SYSTEM", "PM_USER", "ENG_LEAD"]


# ── Signal ─────────────────────────────────────────────────────────────────
class Signal(BaseModel):
    signal_id:         str
    model:             ModelCode
    vfc2:              str
    vfc3:              Optional[str] = None
    sentiment:         Literal["positive", "negative", "neutral"]
    intensity:         float = Field(..., ge=0, le=10)
    feedback_count:    int
    source_channels:   list[str]
    evidence_tier:     EvidenceTier
    summary:           str
    verbatim_samples:  list[str] = Field(default_factory=list, max_length=3)
    has_conflict:      bool = False
    conflict_detail:   Optional[str] = None
    data_gaps:         list[str] = Field(default_factory=list)


# ── Requirement ────────────────────────────────────────────────────────────
class MetricSpec(BaseModel):
    """BMW-style metric: 'Cabin temp from 35°C to 22°C in ≤8 min'"""
    current_value:  Optional[str] = None
    target_value:   str
    unit:           str
    timeframe:      Optional[str] = None


class RequirementScore(BaseModel):
    impact:             float = Field(..., ge=0, le=10)
    market_reach_norm:  float = Field(..., ge=0, le=10)
    effort:             float = Field(..., ge=1, le=10)
    confidence:         float = Field(..., ge=0, le=1)
    reach_count:        int
    rice_score:         float
    bmw_priority:       float
    bmw_priority_low:   float   # historical sensitivity bound; not a statistical CI
    bmw_priority_high:  float   # historical sensitivity bound; not a statistical CI


class Requirement(BaseModel):
    req_id:           str                        # e.g. REQ-F70-001
    model:            ModelCode
    title:            str
    jtbd:             str                        # "When I … I want … so that …"
    metric_spec:      MetricSpec
    smart_check:      dict[str, bool]            # S/M/A/R/T
    score:            RequirementScore
    kano:             KanoType
    moscow:           MoSCoW
    evidence_tier:    EvidenceTier
    gate_color:       GateColor
    status:           RequirementStatus = "DRAFT"
    source_signals:   list[str]                  # signal_ids
    vfc_tags:         list[str]
    data_gaps:        list[str] = Field(default_factory=list)
    conflict_detail:  Optional[str] = None
    notes:            Optional[str] = None


# ── Requirement status transition ──────────────────────────────────────────
class StatusTransition(BaseModel):
    req_id:        str
    to_status:     RequirementStatus
    actor:         str
    actor_role:    ActorRole
    rationale:     Optional[str] = None          # required for REJECTED/POSTPONED
    score_override: Optional[float] = None       # if PM overrides BMW Priority


# ── Audit event ────────────────────────────────────────────────────────────
class AuditEvent(BaseModel):
    entity_id:    str
    entity_type:  str
    action:       str
    actor:        str
    actor_role:   ActorRole
    timestamp:    str
    payload_hash: str
    detail:       Optional[str] = None


# v1 contracts are deliberately separate from legacy generation DTOs.
from pydantic import ConfigDict, field_validator, model_validator


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def recursively_finite(cls, values):
        """JSON dictionaries bypass scalar Pydantic finite-number constraints."""
        from math import isfinite
        active = set()
        def check(value):
            if isinstance(value, float) and not isfinite(value):
                raise ValueError("Nested numeric values must be finite")
            if isinstance(value, (dict, list, tuple)):
                identity = id(value)
                if identity in active:
                    raise ValueError("Contract data must not contain recursive containers")
                active.add(identity)
                try:
                    if isinstance(value, dict):
                        for key, item in value.items():
                            check(key)
                            check(item)
                    else:
                        for item in value:
                            check(item)
                finally:
                    active.remove(identity)
        check(values)
        return values



class SourceReference(StrictContract):
    source_id: str
    record_id: str
    locator: dict
    claim_key: str
    relation: Literal["supports", "contradicts", "context"]
    validation_state: Literal["verified", "unverified"]
    source_version: str


    @field_validator("source_id", "record_id", "claim_key", "source_version")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Source identity fields must not be blank")
        return value.strip()

    @field_validator("locator")
    @classmethod
    def located(cls, value):
        if not value:
            raise ValueError("Original evidence locator is required")
        def check(item, key=None):
            if item is None and key == "printed_page":
                return
            if isinstance(item, bool) or item is None:
                raise ValueError("Locator coordinates cannot be booleans or null")
            if isinstance(item, dict):
                if not item or any(not isinstance(key, str) or not key.strip() for key in item):
                    raise ValueError("Locator keys must be nonblank names")
                for name, coordinate in item.items():
                    check(coordinate, name)
            elif isinstance(item, list):
                if not item:
                    raise ValueError("Locator coordinate lists cannot be empty")
                for coordinate in item:
                    check(coordinate)
            elif isinstance(item, str):
                if not item.strip():
                    raise ValueError("Locator text must not be blank")
            elif not isinstance(item, (int, float)):
                raise ValueError("Locator coordinates must be JSON text or numbers")
        check(value)
        for key in ("page", "row"):
            if key in value and (type(value[key]) is not int or value[key] < 1):
                raise ValueError(f"Locator {key} must be a positive integer")
        return value


class RequirementDraft(StrictContract):
    derivation_key: str = Field(min_length=1)
    signal_id: str = Field(min_length=1)
    signal_revision: int = Field(ge=1, strict=True)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    problem_statement: str = Field(min_length=1)
    source_scope: dict
    target_scope: dict
    smart: dict
    finding_links: list[dict] = Field(min_length=1)
    evidence_links: list[SourceReference] = Field(min_length=1)
    assumptions: list[dict] = Field(default_factory=list)
    uncertainties: list[dict] = Field(default_factory=list)
    factor_proposals: dict = Field(default_factory=dict)
    has_conflict: bool = Field(default=False, strict=True)
    jtbd: str = ""
    claims: list[dict] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)
    intelligence_draft: dict = Field(default_factory=dict)

    @field_validator("source_scope", "target_scope")
    @classmethod
    def valid_scope(cls, scope):
        scope = copy.deepcopy(scope)
        for key in ("derivatives", "regions", "powertrains"):
            values = scope.get(key)
            if not isinstance(values, list) or not values or any(not isinstance(v, str) or not v.strip() for v in values):
                raise ValueError(f"scope {key} must be a nonempty list of names")
            scope[key] = sorted(set(v.strip() for v in values))
            supported = {"regions": {"US", "EU", "CN", "RoW"}, "powertrains": {"BEV", "ICE", "PHEV", "HEV", "MHEV"}}
            if key in supported and not set(scope[key]) <= supported[key]:
                raise ValueError(f"Unsupported {key} scope values")
        return scope

    @field_validator("smart")
    @classmethod
    def valid_smart(cls, smart):
        from math import isfinite
        smart = copy.deepcopy(smart)
        for key in ("specific", "attainability", "relevance"):
            if not isinstance(smart.get(key), str) or not smart[key].strip():
                raise ValueError(f"SMART {key} requires nonblank text")
        metrics, milestone = smart.get("metrics"), smart.get("milestone")
        if not isinstance(metrics, list) or not metrics or not isinstance(milestone, dict):
            raise ValueError("SMART requires metric objects and a milestone object")
        ids = []
        for item, keys in [(m, ("id", "unit", "conditions", "verification_method")) for m in metrics] + [(milestone, ("id", "value"))]:
            if not isinstance(item, dict) or any(not isinstance(item.get(k), str) or not item[k].strip() for k in keys):
                raise ValueError("SMART entries require nonblank fields")
            if item.get("provenance") not in ("SourceBacked", "AIProposed", "PMEntered"):
                raise ValueError("Unsupported target provenance")
            if item.get("review_state", "pending") not in ("pending", "reviewed", "resolved", "accepted"):
                raise ValueError("Unsupported target review state")
            item.setdefault("review_state", "pending")
            if item["provenance"] == "SourceBacked":
                refs = item.get("source_references")
                if not isinstance(refs, list) or not refs or any(not isinstance(r, str) or not r.strip() for r in refs):
                    raise ValueError("SourceBacked target requires source reference IDs")
            ids.append(item["id"])
        if len(set(ids)) != len(ids):
            raise ValueError("Target and milestone IDs must be unique")
        for metric in metrics:
            operator = metric.get("operator")
            if operator not in ("<", "<=", "==", ">=", ">", "!="):
                raise ValueError("Metric requires an explicit supported comparison operator")
            target = metric.get("target")
            if isinstance(target, bool) or not isinstance(target, (str, int, float)) or (isinstance(target, str) and not target.strip()) or (isinstance(target, float) and not isfinite(target)):
                raise ValueError("Metric target must be nonblank text or a finite number")
            if operator in ("<", "<=", ">=", ">") and isinstance(target, str):
                from decimal import Decimal, InvalidOperation
                try:
                    measurable = Decimal(target).is_finite()
                except InvalidOperation:
                    measurable = False
                if not measurable:
                    raise ValueError("Ordered metric comparisons require a finite numeric target")
        return smart

    @field_validator("assumptions")
    @classmethod
    def valid_assumptions(cls, assumptions):
        ids = []
        for item in assumptions:
            if not isinstance(item.get("id"), str) or not item["id"].strip() or not isinstance(item.get("description"), str) or not item["description"].strip():
                raise ValueError("Assumptions require stable IDs and descriptions")
            if item.get("review_state", "pending") not in ("pending", "resolved", "accepted"):
                raise ValueError("Unsupported assumption review state")
            ids.append(item["id"])
        if len(set(ids)) != len(ids):
            raise ValueError("Assumption IDs must be unique")
        return assumptions

    @model_validator(mode="after")
    def successor_scope_mapping(self):
        supported = {"F70", "G60", "G68", "G70"}
        if not set(self.source_scope["derivatives"]) <= supported:
            raise ValueError("Source scope must use supplied derivatives")
        successors = set(self.target_scope["derivatives"]) - supported
        if successors:
            mappings = self.target_scope.get("successor_mappings")
            if not isinstance(mappings, list):
                raise ValueError("Future derivatives require explicit successor mappings")
            assumption_ids = {a["id"] for a in self.assumptions}
            for successor in successors:
                if not any(isinstance(mapping, dict) and mapping.get("source_derivative") in self.source_scope["derivatives"] and mapping.get("target_derivative") == successor and mapping.get("assumption_id") in assumption_ids for mapping in mappings):
                    raise ValueError("Successor mapping requires source, target and reviewable assumption ID")
        return self

    @model_validator(mode="after")
    def explicit_scope_assumptions(self):
        if any(set(self.target_scope[k]) - set(self.source_scope[k]) for k in ("derivatives", "regions", "powertrains")) and not self.assumptions:
            raise ValueError("Scope extrapolation requires explicit reviewable assumptions")
        return self

    @model_validator(mode="after")
    def linked_signal_revision(self):
        for link in self.finding_links:
            revision = link.get("revision")
            if not isinstance(link.get("signal_id"), str) or not link["signal_id"].strip() or isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
                raise ValueError("Finding links require a stable signal ID and positive revision")
        if not any(link["signal_id"] == self.signal_id and link["revision"] == self.signal_revision for link in self.finding_links):
            raise ValueError("Finding lineage must contain the originating signal revision")
        return self

    @field_validator("factor_proposals")
    @classmethod
    def valid_factors(cls, proposals):
        from math import isfinite
        bounds = {"severity_level": (1, 4), "strategic_fit_level": (0, 4), "complexity_level": (1, 5)}
        for key, (lower, upper) in bounds.items():
            value = proposals.get(key)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not lower <= value <= upper or (isinstance(value, float) and not isfinite(value)) or int(value) != value):
                raise ValueError(f"{key} must be a finite policy level {lower}..{upper}")
        return proposals

    @field_validator("title", "description", "problem_statement", "derivation_key", "signal_id")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()


class MutationRequest(StrictContract):
    id: Optional[str] = None
    expected_revision: int = Field(ge=1, strict=True)
    actor_id: str = Field(min_length=1)
    actor_role: Literal["PM_USER", "AI_SYSTEM", "ENG_LEAD"]
    rationale: str = Field(min_length=1)

    @field_validator("actor_id", "rationale")
    @classmethod
    def trimmed(cls, value):
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()


class DecisionRequest(MutationRequest):
    action: Literal["start_review", "approve", "reject", "defer", "reopen"]
    reviewed_target_ids: list[str] = Field(default_factory=list)
    accepted_assumption_ids: list[str] = Field(default_factory=list)
    reviewed_source_ids: list[str] = Field(default_factory=list)
    score_override: Optional[float] = Field(default=None, ge=0, le=100)

    @field_validator("score_override", mode="before")
    @classmethod
    def numeric_override(cls, value):
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise ValueError("Score override requires a finite numeric value")
        return value


class EditRequest(MutationRequest):
    patch: dict


class ApplyRequest(EditRequest):
    turn_id: str


class FreezeRequest(StrictContract):
    selections: dict[str, int] = Field(min_length=1)
    expected_ranking_id: str
    actor_id: str = Field(min_length=1)
    actor_role: Literal["PM_USER"]
    rationale: str = Field(min_length=1)

    @field_validator("selections", mode="before")
    @classmethod
    def valid_selection_revisions(cls, selections):
        if not isinstance(selections, dict) or any(not isinstance(k, str) or not k.strip() or isinstance(v, bool) or not isinstance(v, int) or v < 1 for k, v in selections.items()):
            raise ValueError("Freeze selections require stable IDs and positive integer revisions")
        return selections

    @field_validator("actor_id", "rationale")
    @classmethod
    def trimmed(cls, value):
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()


class RequirementDetail(StrictContract):
    # Read DTO also represents unresolved legacy imports. Strict draft validation
    # applies only when persisting a new authoritative draft/reconciled revision.
    derivation_key: str
    signal_id: str
    signal_revision: int
    title: str
    description: str
    problem_statement: str
    source_scope: dict
    target_scope: dict
    smart: dict
    finding_links: list[dict]
    evidence_links: list[SourceReference]
    assumptions: list[dict]
    uncertainties: list[dict]
    factor_proposals: dict
    has_conflict: bool
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    id: str
    revision: int = Field(ge=1)
    status: Literal["DRAFT", "PM_REVIEW", "APPROVED", "REJECTED", "POSTPONED"]
    approved_revision: Optional[int]
    run_id: str
    created_at: str
    updated_at: str
    score: dict
    evidence: dict
    prioritization_rationale: str
    validation_state: Literal["verified", "unverified"]


class EvidencePack(StrictContract):
    """Pinned Role 1/2 evidence context consumed before transactional mutation."""
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    traceable: bool
    verified: bool
    score_context: dict
    evidence_context: dict
    source_references: list[SourceReference] = Field(default_factory=list)


class ReviewEngineResult(StrictContract):
    """Optional metadata defaults keep existing Role 2 adapters compatible."""
    answer: str = Field(min_length=1, strict=True)
    citations: list[SourceReference] = Field(default_factory=list)
    tool_records: list[dict] = Field(default_factory=list)
    coverage_gaps: list[str] = Field(default_factory=list)
    model_metadata: dict = Field(default_factory=dict)
    prompt_metadata: dict = Field(default_factory=dict)
    patch: Optional[dict] = None

    @field_validator("answer", "coverage_gaps")
    @classmethod
    def nonblank_review_text(cls, value):
        items = value if isinstance(value, list) else [value]
        if any(not item.strip() for item in items):
            raise ValueError("Review text must be nonblank")
        return value


class AnalysisEngineResult(StrictContract):
    """Optional generation metadata envelope; drafts validate independently."""
    drafts: list[dict]
    failures: list[str] = Field(default_factory=list)
    model_metadata: dict = Field(default_factory=dict)
    prompt_metadata: dict = Field(default_factory=dict)


class ReviewSuggestion(StrictContract):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    id: str
    entity_id: str
    expected_revision: int = Field(ge=1, strict=True)
    patch: Optional[dict] = None
    coverage_gaps: list[str] = Field(default_factory=list)
    model_metadata: dict = Field(default_factory=dict)
    prompt_metadata: dict = Field(default_factory=dict)
    score_preview: Optional[dict] = None
    change_preview: Optional[dict] = None
    state: Literal["pending", "applied", "discarded", "read_only"]


class ScoreContext(StrictContract):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    policy_version: Literal["v1", "legacy-v0"] = "v1"
    reference_volume: Optional[float] = Field(default=None, gt=0)
    target_regions: list[str] = Field(default_factory=list)
    region_counts: dict = Field(default_factory=dict)
    forecasts: list[dict] = Field(default_factory=list)


class ScoreResult(StrictContract):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    policy_version: Literal["v1", "legacy-v0"]
    scale: list[float]
    calculated_score: float
    effective_score: float
    override: Optional[float]
    factors: dict
    range: dict


class EvidenceResult(StrictContract):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    policy_version: str
    value: Optional[float] = Field(ge=0, le=100)
    level: str
    is_probability: Literal[False]


class RequirementRevision(RequirementDetail):
    """Immutable complete requirement payload at one authoritative revision."""
    pass


class GovernanceAuditEvent(StrictContract):
    id: str
    entity_id: str
    entity_type: str
    action: str
    timestamp: str
    actor_id: str
    actor_role: ActorRole
    rationale: str
    before: object
    after: object
    sequence: int = Field(ge=1)
    previous_hash: str
    hash: str


class FreezeSnapshot(StrictContract):
    id: str
    timestamp: str
    actor_id: str
    rationale: str
    requirements: list[RequirementRevision]
    ranking: dict
    configurations: dict
    scoring_policies: dict
    source_snapshots: list[dict]
    checkpoint: dict
    audit: dict
