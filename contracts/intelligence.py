"""Strict evidence/agent contracts; these are proposals, not decision models."""
from __future__ import annotations

from typing import Any, Generic, Literal, TypeVar
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class Scope(DTO):
    derivatives: list[str] = Field(min_length=1)
    regions: list[str] = Field(min_length=1)
    powertrains: list[str] = Field(default_factory=list)

    @field_validator("derivatives", "regions", "powertrains")
    @classmethod
    def normalize(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("scope entries cannot be empty")
        return sorted(set(values))


class Locator(DTO):
    sheet: str | None = None
    row: int | None = Field(default=None, ge=1)
    column: str | None = None
    page: int | None = Field(default=None, ge=1)
    printed_page: str | None = None

    @model_validator(mode="after")
    def resolvable(self) -> Locator:
        if self.page is None and not (self.sheet and self.row):
            raise ValueError("source requires a workbook row or physical PDF page")
        return self


class SourceReference(DTO):
    id: str = Field(min_length=1)
    source_file_id: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    locator: Locator


class FeedbackComment(DTO):
    id: str
    original_id: str
    text: str = Field(min_length=1)
    scope: Scope
    source: SourceReference
    source_label: str
    annotations: list[str] = Field(default_factory=list)


class TopicBucket(DTO):
    input_group_id: str
    category: str
    scope: Scope
    topic_bucket_comment_count: int = Field(ge=0)
    cohort_comment_count: int = Field(ge=0)
    examples: list[FeedbackComment] = Field(max_length=24)
    selection_limitations: list[str]
    source_versions: list[str]

    @model_validator(mode="after")
    def counts(self) -> TopicBucket:
        ids = [comment.id for comment in self.examples]
        if len(ids) != len(set(ids)):
            raise ValueError("discovery examples must be distinct canonical comments")
        if not len(ids) <= self.topic_bucket_comment_count <= self.cohort_comment_count:
            raise ValueError("examples <= bucket <= cohort counts required")
        return self


Polarity = Literal["supports", "opposes", "context"]


class SpanProposal(DTO):
    feedback_id: str
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, gt=0)
    text: str = Field(min_length=1)
    polarity: Polarity


class EvidenceSpan(SpanProposal):
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    id: str
    source: SourceReference
    scope: Scope
    assignment_method: Literal["model", "rule", "imported", "pm"] = "model"
    review_state: Literal["unreviewed", "confirmed"] = "unreviewed"


class SignalProposal(DTO):
    issue_key: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    title: str = Field(min_length=1)
    problem_statement: str = Field(min_length=1)
    sentiment: Literal["positive", "negative", "neutral"]
    intensity: float = Field(ge=0, le=10)
    intensity_reason: str = Field(min_length=1)
    evidence: list[SpanProposal] = Field(min_length=1, description="Both issue-supporting complaints and all supplied positive counterexamples; counterexamples use opposes.")
    gaps: list[str] = Field(default_factory=list, description="Missing data and discovery limitations only. Supplied counterexample quotes belong in evidence, never only here.")


class SignalGroupOutput(DTO):
    input_group_id: str
    signals: list[SignalProposal]


class SignalBatchOutput(DTO):
    results: list[SignalGroupOutput]


class Signal(DTO):
    id: str
    revision: int = Field(ge=1)
    input_group_id: str
    issue_key: str
    title: str
    problem_statement: str
    category: str
    source_scope: Scope
    sentiment: Literal["positive", "negative", "neutral"]
    intensity: float = Field(ge=0, le=10)
    intensity_reason: str = Field(min_length=1)
    evidence: list[EvidenceSpan]
    topic_bucket_comment_count: int = Field(ge=0)
    issue_support_comment_count: int = Field(ge=0)
    opposing_comment_count: int = Field(ge=0)
    cohort_comment_count: int = Field(ge=0)
    unassigned_candidate_count: int = Field(ge=0)
    source_versions: list[str]
    gaps: list[str]
    consolidation_version: str


class NativeFact(DTO):
    id: str
    kind: Literal["survey", "sales"]
    scope: Scope
    metric: str
    value: float | list[float]
    unit: str
    scale: str | None = None
    respondent_count: int | None = Field(default=None, ge=1)
    year: int | None = None
    vehicle_line: str | None = None
    source: SourceReference
    reviewed_usage: list[Literal["acceptance_target", "test_condition", "sample_size"]] = Field(default_factory=list)


class StudyContext(DTO):
    fact: NativeFact
    relation: Literal["direct", "broad", "contradictory", "unmapped"]
    mapping_reviewed: bool
    mapping_version: str
    reason: str


class DocumentPassage(DTO):
    id: str
    kind: Literal["option", "strategy"]
    text: str
    scope: Scope
    source: SourceReference
    reviewed_claim_types: list[Literal["feasibility", "architecture", "competitor", "milestone"]] = Field(default_factory=list)


class EvidencePack(DTO):
    signal_id: str
    signal_revision: int
    target_scope: Scope
    supporting: list[EvidenceSpan] = Field(max_length=8)
    opposing: list[EvidenceSpan] = Field(max_length=4)
    studies: list[StudyContext]
    sales: list[NativeFact]
    equipment: list[DocumentPassage] = Field(max_length=3)
    strategy: list[DocumentPassage] = Field(max_length=3)
    gaps: list[str]
    retrieval_version: str

    def citation_ids(self) -> set[str]:
        return {item.id for item in self.supporting + self.opposing + self.sales + self.equipment + self.strategy} | {
            study.fact.id for study in self.studies
        }


Provenance = Literal["SourceBacked", "AIProposed", "PMEntered"]
ReviewState = Literal["unreviewed", "accepted", "rejected"]


class Claim(DTO):
    id: str
    kind: Literal["problem", "rationale", "strategy", "packaging", "competitor", "architecture", "commercial"]
    text: str = Field(min_length=1)
    relation: Literal["supports", "contradicts", "context"] = Field(default="supports", description="Relation to the requirement's reported problem; positive counterexamples contradict it, not support it.")
    provenance: Provenance
    review_state: ReviewState = "unreviewed"
    evidence_ids: list[str] = Field(default_factory=list)


class TestParameter(DTO):
    id: str
    purpose: Literal["test_condition", "sample_size"]
    value: float
    unit: str = Field(min_length=1)
    provenance: Provenance
    review_state: ReviewState = "unreviewed"
    evidence_ids: list[str] = Field(default_factory=list)


class Target(DTO):
    id: str
    value: float
    unit: str = Field(min_length=1)
    operator: Literal["<=", ">=", "="]
    conditions: str = Field(min_length=1)
    test_method: str = Field(min_length=1)
    measurement_definition: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    parameters: list[TestParameter] = Field(default_factory=list)
    provenance: Provenance
    review_state: ReviewState = "unreviewed"
    evidence_ids: list[str] = Field(default_factory=list)


class Assertion(DTO):
    id: str
    text: str = Field(min_length=1)
    provenance: Provenance
    review_state: ReviewState = "unreviewed"
    evidence_ids: list[str] = Field(default_factory=list)


class Assumption(Assertion):
    kind: Literal["target_validation", "scope_carryover", "implementation", "other"]
    review_owner: Literal["PM", "UX", "Engineering"]


class SmartFields(DTO):
    specific: str = Field(min_length=1)
    metrics: list[Target] = Field(min_length=1)
    feasibility: Assertion
    relevance: str = Field(min_length=1)
    milestone: Assertion


class FactorProposal(DTO):
    factor: Literal["severity", "strategic_fit", "complexity"]
    value: float | None
    reason: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    provisional: Literal[True] = True

    @model_validator(mode="after")
    def bounds(self) -> FactorProposal:
        if self.value is None:
            return self
        low, high = {"severity": (1, 4), "strategic_fit": (0, 4), "complexity": (1, 5)}[self.factor]
        if not low <= self.value <= high:
            raise ValueError(f"{self.factor} must be in [{low}, {high}]")
        return self


class RequirementDraft(DTO):
    input_signal_id: str
    title: str = Field(min_length=1)
    description: str = Field(min_length=1, description="Actionable declarative customer-facing requirement, not a copy of JTBD or a report of complaints.")
    problem_statement: str = Field(min_length=1)
    jtbd: str = Field(min_length=1)
    source_scope: Scope
    target_scope: Scope
    smart: SmartFields
    claims: list[Claim] = Field(min_length=1)
    factor_proposals: list[FactorProposal]
    alternatives: list[str] = Field(min_length=1)
    assumptions: list[Assumption]
    uncertainties: list[str]


class DraftBatchOutput(DTO):
    requirements: list[RequirementDraft]


class ItemFailure(DTO):
    input_id: str
    code: str
    message: str


T = TypeVar("T")


class BatchResult(DTO, Generic[T]):
    items: list[T]
    failures: list[ItemFailure]
    state: Literal["succeeded", "partial", "failed"]


class PatchOperation(DTO):
    op: Literal["add", "replace", "remove"]
    path: str
    value: Any = None


class ReviewOutput(DTO):
    answer: str = Field(min_length=1)
    citation_ids: list[str]
    gaps: list[str]
    proposed_patch: list[PatchOperation] = Field(default_factory=list, max_length=20)


class ReviewContext(DTO):
    thread_id: str
    entity_kind: Literal["signal", "requirement"]
    entity_id: str
    revision: int = Field(ge=1)
    pinned: dict[str, Any]
    evidence_ids: list[str]
    visible_history: list[dict[str, str]] = Field(default_factory=list, max_length=12)


class ReviewSuggestion(ReviewOutput):
    turn_id: str
    thread_id: str
    entity_id: str
    base_revision: int
    state: Literal["none", "pending", "applied", "discarded", "stale"]
    score_preview: dict[str, Any] | None = None
    applied_revision: int | None = None
    limited: bool = False


class ProgressEvent(DTO):
    stage: Literal["import_check", "consolidate", "retrieve", "draft", "validate", "score", "completed"]
    run_id: str
    input_id: str | None = None
    state: Literal["queued", "running", "succeeded", "partial", "failed"] = "running"


class WriteResult(DTO):
    requirement_id: str
    outcome: Literal["created", "existing", "pending"]
    suggestion_id: str | None = None


class AnalysisResult(DTO):
    run_id: str
    state: Literal["succeeded", "partial", "failed"]
    results: list[WriteResult]
    failures: list[ItemFailure]
    cached_signal_ids: list[str]
