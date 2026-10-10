"""Run with python -m tests.intelligence.demo. Entirely synthetic; no external services."""
import json

from services.analysis import run_analysis
from services.retrieval import build_evidence_pack
from services.review_assistant import answer_review_turn
from services.signals import extract_signals
from services.tool_registry import ToolRegistry
from tests.intelligence.fakes import (
    FakeReviewBackend, FakeWriter, FixtureEvidence, MemorySignals, RecordingAudit,
    SCOPE, ScriptedModel, draft_payload, signal_response,
)


def run_demo() -> dict:
    provider, repository, audit = FixtureEvidence(), MemorySignals(), RecordingAudit()
    signal = extract_signals(SCOPE, provider, model=ScriptedModel({"results": [signal_response(provider.buckets[0])]}),
                             repository=repository, audit=audit).items[0]
    pack = build_evidence_pack(signal, SCOPE, evidence_provider=provider)
    events, writer = [], FakeWriter()
    generated = run_analysis([signal], SCOPE, "RUN-FIXTURE-DEMO", evidence_provider=provider,
                             model=ScriptedModel({"requirements": [draft_payload(signal, pack)]}),
                             writer=writer, audit=audit, progress=events.append, config_version="fixture-v1")
    backend = FakeReviewBackend(sorted(pack.citation_ids()))
    suggestion = answer_review_turn(backend.context, "Does this justify global scope?", ToolRegistry({}),
        model=ScriptedModel({"answer": "The synthetic fixture has US issue evidence, not direct China evidence.",
                             "citation_ids": [pack.supporting[0].id], "gaps": ["China evidence missing"],
                             "proposed_patch": [{"op": "replace", "path": "/target_scope/regions", "value": ["US"]}]}),
        backend=backend)
    return {"mode": "SYNTHETIC FIXTURE — not BMW findings or live AI", "signal_alias": "SIG-FIXTURE-001",
            "signal_id": signal.id, "support": signal.issue_support_comment_count,
            "counterexamples": signal.opposing_comment_count, "cohort": signal.cohort_comment_count,
            "generation_state": generated.state, "suggestion_state": suggestion.state,
            "preview_source": "fake backend", "target_review_state": writer.submissions[0][0].smart.metrics[0].review_state,
            "requirement_unchanged_by_review": backend.context.revision == 1,
            "stages": [event.stage for event in events]}


if __name__ == "__main__":
    print(json.dumps(run_demo(), indent=2))
