"""Ports implemented by Roles 1/3; fakes implement the identical boundaries."""
from typing import Any, Protocol

from contracts.intelligence import (
    DocumentPassage, EvidencePack, NativeFact, ProgressEvent, RequirementDraft,
    ReviewContext, ReviewSuggestion, Scope, Signal, StudyContext, TopicBucket, WriteResult,
)


class EvidenceProvider(Protocol):
    def get_topic_buckets(self, scope: Scope, limit: int = 24) -> list[TopicBucket]: ...
    def get_survey_facts(self, topic: str, scope: Scope) -> list[StudyContext]: ...
    def get_sales_facts(self, scope: Scope, year: int = 2030) -> list[NativeFact]: ...
    def search_document_chunks(self, query: str, scope: Scope, document_kind: str,
                               limit: int = 3) -> list[DocumentPassage]: ...


class SignalRepository(Protocol):
    def save_version(self, signal: Signal) -> Signal:
        """Reuse an identical version or append; never overwrite an earlier version."""
        ...


class RequirementWriter(Protocol):
    def submit(self, draft: RequirementDraft, signal: Signal, pack: EvidencePack,
               derivation_key: str, cache_key: str, run_id: str) -> WriteResult:
        """Calculate scores from authoritative inputs; create or propose, never overwrite."""
        ...
    def get_cached(self, cache_key: str, run_id: str) -> WriteResult | None:
        """Return current identity/result, not an old content snapshot."""
        ...


class AuditSink(Protocol):
    def record(self, action: str, payload: dict[str, Any]) -> None:
        """Record stage/tool provenance; backend supplies durable persistence."""
        ...


class ProgressSink(Protocol):
    def __call__(self, event: ProgressEvent) -> None: ...


class ReviewBackend(Protocol):
    def load_context(self, thread_id: str) -> ReviewContext: ...
    def mark_outdated_suggestions(self, thread_id: str, current_revision: int) -> None:
        """Mark older pending proposals stale; do not mutate requirements."""
        ...
    def preview(self, entity_id: str, expected_revision: int, patch: list[dict]) -> dict: ...
    def save_turn(self, suggestion: ReviewSuggestion, trace: dict) -> None:
        """Persist pending/read-only answer and trace without mutating the requirement."""
        ...
    def get_pending_suggestion(self, turn_id: str, transaction: Any) -> ReviewSuggestion: ...
    def mark_suggestion_applied(self, turn_id: str, revision: int, transaction: Any) -> None: ...
