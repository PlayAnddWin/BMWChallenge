"""Bounded generation stages; Role 3 owns durable jobs, scoring and atomic writes."""
from datetime import date

from contracts.intelligence import AnalysisResult, ItemFailure, ProgressEvent, Scope, Signal
from services.common import fingerprint
from services.cache import FileDraftCache
from services.drafting import PROMPT_VERSION, draft_requirement, validate_draft
from services.interfaces import AuditSink, EvidenceProvider, ProgressSink, RequirementWriter
from services.llm_client import ModelAdapter
from services.retrieval import build_evidence_pack

ENGINE_VERSION = "analysis-v1"


def derivation_key(signal: Signal, scope: Scope) -> str:
    # Stable across source/model/prompt revisions; versioned context belongs in the cache key.
    return fingerprint({"signal_id": signal.id, "target_scope": scope.model_dump(), "purpose": "customer-requirement"})


def run_analysis(signals: list[Signal], scope: Scope, run_id: str, *, evidence_provider: EvidenceProvider,
                 model: ModelAdapter, writer: RequirementWriter, audit: AuditSink, progress: ProgressSink,
                 config_version: str, max_drafts: int = 12,
                 draft_cache: FileDraftCache | None = None) -> AnalysisResult:
    if not 1 <= max_drafts <= 18:
        raise ValueError("max_drafts must be in [1,18]")
    if len({signal.id for signal in signals}) != len(signals):
        raise ValueError("duplicate analysis signal IDs")
    results, failures, cached = [], [], []

    def emit(stage: str, identifier: str | None = None, state: str = "running") -> None:
        progress(ProgressEvent(stage=stage, run_id=run_id, input_id=identifier, state=state))

    emit("import_check")
    emit("consolidate")
    if not signals:
        failures.append(ItemFailure(input_id="scope", code="no_signals", message="No signals selected"))
    for signal in signals[max_drafts:]:
        failures.append(ItemFailure(input_id=signal.id, code="draft_limit", message="Signal exceeds requested draft limit"))
    for signal in signals[:max_drafts]:
        try:
            emit("retrieve", signal.id)
            pack = build_evidence_pack(signal, scope, evidence_provider=evidence_provider)
            cache_key = fingerprint({"signal": signal.model_dump(), "pack": pack.model_dump(),
                                     "scope": scope.model_dump(), "model": model.model,
                                     "model_policy": getattr(model, "policy_version", "external-adapter-v1"),
                                     "prompt": PROMPT_VERSION, "engine": ENGINE_VERSION, "config": config_version,
                                     "planning_as_of": date.today().isoformat()})
            existing = writer.get_cached(cache_key, run_id)
            if existing is not None:
                results.append(existing)
                cached.append(signal.id)
                audit.record("GENERATION_CACHE_HIT", {"run_id": run_id, "cache_key": cache_key,
                                                       "requirement_id": existing.requirement_id})
                continue
            emit("draft", signal.id)
            artifact = draft_cache.get(cache_key) if draft_cache else None
            if artifact is not None:
                if artifact.model != model.model or artifact.prompt_version != PROMPT_VERSION:
                    raise ValueError("cached model/prompt metadata mismatch")
                emit("validate", signal.id)
                draft = validate_draft(artifact.draft, signal, pack, scope)
                cached.append(signal.id)
            else:
                draft = draft_requirement(signal, pack, scope, model=model,
                                          on_validate=lambda identifier: emit("validate", identifier), audit=audit, run_id=run_id)
                if draft_cache:
                    draft_cache.put(cache_key, draft, model=model.model, prompt_version=PROMPT_VERSION, run_id=run_id)
            audit.record("DRAFT_VALIDATED", {"run_id": run_id, "signal_id": signal.id,
                                              "signal_revision": signal.revision, "draft": draft.model_dump(),
                                              "evidence_pack": pack.model_dump(), "model": model.model,
                                              "prompt_version": PROMPT_VERSION, "config_version": config_version,
                                              "cached": artifact is not None})
            emit("score", signal.id)
            # Writer calculates authoritative scores and persists in its own managed transaction.
            # All retrieval/network/model work above is finished before this boundary.
            results.append(writer.submit(draft, signal, pack, derivation_key(signal, scope), cache_key, run_id))
        except Exception as error:
            # Per-item isolation is intentional; the durable job runner receives truthful partial results.
            failures.append(ItemFailure(input_id=signal.id, code=type(error).__name__, message=str(error)))
    state = "partial" if failures and results else "failed" if failures else "succeeded"
    emit("completed", state=state)
    return AnalysisResult(run_id=run_id, state=state, results=results, failures=failures, cached_signal_ids=cached)
