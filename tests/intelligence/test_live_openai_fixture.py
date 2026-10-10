"""Real OpenAI + synthetic evidence. Independent of BMW imports, HTTP and Neo4j."""
from datetime import datetime, timezone
import json
from time import perf_counter
from uuid import uuid4

import pytest

from config import get_settings
from contracts.intelligence import Scope
from services.drafting import draft_requirement
from services.llm_client import OpenAIAdapter
from services.retrieval import build_evidence_pack
from services.review_assistant import answer_review_turn
from services.signals import extract_signals
from services.tool_registry import ToolRegistry, ToolResult
from tests.intelligence.fakes import FakeReviewBackend, FixtureEvidence, MemorySignals, RecordingAudit, SCOPE

pytestmark = pytest.mark.live


class ObservedOpenAI(OpenAIAdapter):
    """Record provider response IDs/usage and model output, never credentials."""
    def __init__(self, settings):
        super().__init__(settings)
        self.responses = []
        self.failures = []
        self.checkpoint = None

    def complete(self, messages, schema, tools=None):
        started = perf_counter()
        try:
            reply = super().complete(messages, schema, tools)
            self.responses.append({**reply.model_dump(), "elapsed_seconds": round(perf_counter() - started, 3)})
            return reply
        except Exception as error:
            message = str(error).replace(self._key.get_secret_value(), "[REDACTED]")
            self.failures.append({"kind": type(error).__name__, "message": message,
                                  "elapsed_seconds": round(perf_counter() - started, 3)})
            raise
        finally:
            if self.checkpoint:
                self.checkpoint()


@pytest.fixture(scope="module")
def live_fixture_case(request):
    if not request.config.getoption("--live-openai-fixture"):
        pytest.skip("requires explicit --live-openai-fixture; default tests never call OpenAI")
    settings = get_settings()
    if not settings.openai_api_key.get_secret_value().strip():
        pytest.fail("Set OPENAI_API_KEY in the private root .env before running live fixture checks", pytrace=False)
    model = ObservedOpenAI(settings)
    if request.config.getoption("--live-no-retries"):
        from openai import OpenAI
        model._client = OpenAI(api_key=settings.openai_api_key.get_secret_value(), timeout=45, max_retries=0)
    provider, repository, audit = FixtureEvidence(), MemorySignals(), RecordingAudit()
    case = {"model": model, "provider": provider, "repository": repository, "audit": audit,
            "mode": "REAL OPENAI / SYNTHETIC EVIDENCE / IN-MEMORY TEST BOUNDARIES",
            "started_at": datetime.now(timezone.utc).isoformat()}
    report_dir = settings.cache_dir / "live-fixture-checks"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_dir.chmod(0o700)
    case["report_path"] = report_dir / f"{uuid4()}.json"

    def checkpoint():
        # Keep responses/errors inspectable even when an external timeout interrupts the suite.
        report = {"mode": case["mode"], "started_at": case["started_at"], "model": model.model,
                  "responses": model.responses, "provider_failures": model.failures,
                  "signal": case["signal"].model_dump() if "signal" in case else None,
                  "evidence_pack": case["pack"].model_dump() if "pack" in case else None,
                  "draft": case["draft"].model_dump() if "draft" in case else None,
                  "review": case["review"].model_dump() if "review" in case else None,
                  "review_trace": case.get("review_trace"), "audit_events": case["audit"].events,
                  "additional_drafts": case.get("additional_drafts", []),
                  "checked_stages": case.get("checked_stages", [])}
        path = case["report_path"]
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            temporary.chmod(0o600)
            stream.write(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False))
        temporary.replace(path)

    model.checkpoint = checkpoint
    checkpoint()
    print(f"Live fixture report: {case['report_path']}")
    yield case
    checkpoint()


@pytest.fixture(scope="module")
def live_fixture_signal(live_fixture_case):
    case = live_fixture_case
    result = extract_signals(SCOPE, case["provider"], model=case["model"],
                             repository=case["repository"], audit=case["audit"])
    assert result.state == "succeeded", result.failures
    # The model chooses wording/issue_key; acceptance is based on the known underlying evidence.
    matching = [signal for signal in result.items if signal.issue_support_comment_count == 2
                and signal.opposing_comment_count == 1]
    assert matching, "Expected the two-comment discoverability issue with its positive counterexample"
    case["signal"] = matching[0]
    return matching[0]


@pytest.fixture(scope="module")
def live_fixture_draft(live_fixture_case, live_fixture_signal):
    case = live_fixture_case
    # Deliberately propose broader planning scope so the live assistant must expose the CN gap.
    scope = Scope(derivatives=["G60"], regions=["US", "CN"])
    pack = build_evidence_pack(live_fixture_signal, scope, evidence_provider=case["provider"])
    case["pack"] = pack
    case["draft"] = draft_requirement(live_fixture_signal, pack, scope, model=case["model"], audit=case["audit"], run_id="LIVE-QUALITY-FIXTURE")
    return case["draft"]


def test_real_openai_extracts_source_resolved_fixture_signal(live_fixture_case, live_fixture_signal):
    comments = {comment.id: comment for comment in live_fixture_case["provider"].comments}
    assert live_fixture_signal.issue_support_comment_count == 2
    assert live_fixture_signal.opposing_comment_count == 1
    assert live_fixture_signal.cohort_comment_count == 3
    assert live_fixture_signal.sentiment == "negative"
    assert 4 <= live_fixture_signal.intensity <= 6 and live_fixture_signal.intensity_reason
    for span in live_fixture_signal.evidence:
        original = comments[span.feedback_id]
        assert original.text[span.start:span.end] == span.text
        assert original.source == span.source
    assert all(response["provider_metadata"]["response_id"] for response in live_fixture_case["model"].responses)
    live_fixture_case.setdefault("checked_stages", []).append("extraction")


def test_real_openai_draft_has_citations_and_unreviewed_proposed_targets(live_fixture_case, live_fixture_draft):
    draft = live_fixture_draft
    assert draft.input_signal_id == live_fixture_case["signal"].id
    assert draft.smart.metrics
    assert all(metric.provenance == "AIProposed" and metric.review_state == "unreviewed" for metric in draft.smart.metrics)
    assert draft.smart.milestone.provenance == "AIProposed" and draft.smart.milestone.review_state == "unreviewed"
    assert any("CN" in gap for gap in draft.uncertainties)
    assert any(claim.kind == "problem" and claim.evidence_ids for claim in draft.claims)
    assert draft.smart.feasibility.provenance == "AIProposed"
    assert not any(str(year) in draft.smart.milestone.text for year in range(2000, 2100))
    assert all(metric.measurement_definition and metric.rationale for metric in draft.smart.metrics)
    assert len(draft.alternatives) >= 2 and draft.assumptions
    factors = {factor.factor: factor for factor in draft.factor_proposals}
    assert factors["severity"].value == 2 and factors["severity"].evidence_ids
    assert factors["strategic_fit"].evidence_ids and factors["strategic_fit"].value <= 3
    assert factors["complexity"].value is None
    live_fixture_case.setdefault("checked_stages", []).append("drafting")


def test_real_openai_review_uses_read_only_tool_and_leaves_patch_pending(live_fixture_case, live_fixture_draft):
    case = live_fixture_case
    signal, pack = case["signal"], case["pack"]
    boundary = FakeReviewBackend(sorted(pack.citation_ids()))
    boundary.context = boundary.context.model_copy(update={"pinned": {
        "requirement": live_fixture_draft.model_dump(), "signal_id": signal.id,
        "signal_revision": signal.revision,
    }})
    before = boundary.context.model_dump()

    def get_evidence(args):
        assert args.signal_id == signal.id and args.revision == signal.revision
        return ToolResult(data={"signal": signal.model_dump()},
                          evidence_ids=[span.id for span in signal.evidence], source_versions=signal.source_versions)

    tools = ToolRegistry({"get_signal_evidence": get_evidence})
    suggestion = answer_review_turn(boundary.context,
        "Call get_signal_evidence to inspect the exact issue support and counterexample. Does this fixture "
        "justify including China in the target regions? Cite the supplied evidence, explain the China gap, "
        "and propose replacing /target_scope/regions with [\"US\"] for PM review. Do not apply or approve it.",
        tools, model=case["model"], backend=boundary)
    case["review"] = suggestion
    case["review_trace"] = boundary.traces[suggestion.turn_id]
    assert not suggestion.limited and suggestion.state == "pending"
    assert suggestion.score_preview["fixture_only"] is True
    assert suggestion.citation_ids and set(suggestion.citation_ids) <= pack.citation_ids()
    assert any("CN" in gap or "China" in gap for gap in suggestion.gaps)
    assert any(operation.path == "/target_scope/regions" and operation.value == ["US"] for operation in suggestion.proposed_patch)
    assert boundary.context.model_dump() == before
    trace = case["review_trace"]
    assert any(call["name"] == "get_signal_evidence" and "data" in call["result"] for call in trace["tools"])
    assert trace["tool_calls"] <= 6 and trace["rounds"] <= 4
    case.setdefault("checked_stages", []).append("review")


def test_real_openai_weak_storage_draft_keeps_unknown_strategy_and_effort(live_fixture_case):
    from contracts.intelligence import FeedbackComment, TopicBucket
    from tests.intelligence.fakes import ScriptedModel, signal_response, source
    case = live_fixture_case
    scope = Scope(derivatives=["F70"], regions=["EU"], powertrains=["ICE"])
    provider = FixtureEvidence()
    texts = ["My phone slips out of the storage pocket.", "The storage pocket does not hold my phone securely.",
             "My phone stays securely in the storage pocket."]
    provider.comments = [FeedbackComment(id=f"FB-STORAGE-{index:03d}", original_id=f"STORAGE-{index}",
                                        text=text, scope=scope, source=source(f"RECORD-STORAGE-{index}", index + 1),
                                        source_label="A") for index, text in enumerate(texts, 1)]
    bucket = TopicBucket(input_group_id="GROUP-FIXTURE-STORAGE", category="Storage", scope=scope,
                         topic_bucket_comment_count=100, cohort_comment_count=300, examples=provider.comments,
                         selection_limitations=["Synthetic partial discovery; no exhaustively assigned membership."],
                         source_versions=["synthetic-v1"])
    provider.buckets = [bucket]
    provider.studies = provider.sales = provider.strategy = provider.equipment = []
    # Known fixture memberships are supplied deterministically; this case evaluates live drafting on another topic/line.
    group = signal_response(bucket)
    group["signals"][0].update(issue_key="phone-storage-retention", title="Phone storage does not retain a phone",
                               problem_statement="Fixture comments report that phones slip out of the storage pocket.")
    result = extract_signals(scope, provider, model=ScriptedModel({"results": [group]}),
                             repository=MemorySignals(), audit=case["audit"])
    signal = result.items[0]
    pack = build_evidence_pack(signal, scope, evidence_provider=provider)
    draft = draft_requirement(signal, pack, scope, model=case["model"], audit=case["audit"], run_id="LIVE-STORAGE-FIXTURE")
    case.setdefault("additional_drafts", []).append({"mode": "Real OpenAI draft / fixed synthetic memberships",
                                                      "signal": signal.model_dump(), "pack": pack.model_dump(), "draft": draft.model_dump()})
    assert signal.issue_support_comment_count == 2 and signal.unassigned_candidate_count == 97
    assert draft.source_scope == scope and draft.target_scope == scope
    assert draft.smart.feasibility.provenance == "AIProposed"
    assert all(metric.provenance == "AIProposed" and metric.measurement_definition for metric in draft.smart.metrics)
    assert len(draft.alternatives) >= 2 and draft.assumptions
    factors = {factor.factor: factor for factor in draft.factor_proposals}
    assert factors["strategic_fit"].value is None and factors["complexity"].value is None
    assert any("lower bound" in gap for gap in draft.uncertainties)
    assert any("forecast" in gap for gap in draft.uncertainties)
    case.setdefault("checked_stages", []).append("weak_storage_drafting")
