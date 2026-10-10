"""Real-provider acceptance hooks. Default runs never make network calls."""
from importlib import import_module

import pytest

from config import get_settings
from contracts.intelligence import Scope
from services.analysis import derivation_key
from services.common import fingerprint
from services.drafting import draft_requirement
from services.llm_client import OpenAIAdapter
from services.review_assistant import answer_review_turn
from services.retrieval import build_evidence_pack
from services.signals import extract_signals

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def live_case(request):
    if not request.config.getoption("--live"):
        pytest.skip("real provider checks require --live and Role 1/3 adapters")
    factory_name = request.config.getoption("--live-factory")
    if not factory_name or ":" not in factory_name:
        pytest.fail("--live requires --live-factory module:function; no fake fallback")
    module_name, name = factory_name.split(":", 1)
    bundle = getattr(import_module(module_name), name)()
    provider = bundle["evidence_provider"]
    scope = Scope.model_validate(bundle["scope"])
    buckets = provider.get_topic_buckets(scope, limit=24)[:1]
    assert buckets, "live provider must return real topic evidence"
    assert all("fixture" not in comment.source.source_file_id.lower() and
               not comment.source.source_version.startswith("synthetic")
               for bucket in buckets for comment in bucket.examples), "synthetic evidence is not a live BMW check"

    class BoundedProvider:
        def get_topic_buckets(self, scope, limit=24):
            return buckets

    model = OpenAIAdapter(get_settings())
    extracted = extract_signals(scope, BoundedProvider(), model=model, repository=bundle["signal_repository"],
                                audit=bundle["audit"])
    assert extracted.state == "succeeded" and extracted.items, extracted.failures
    signal = extracted.items[0]
    pack = build_evidence_pack(signal, scope, evidence_provider=provider)
    draft = draft_requirement(signal, pack, scope, model=model)
    write = bundle["requirement_writer"].submit(draft, signal, pack, derivation_key(signal, scope),
                                                fingerprint({"live": draft.model_dump(), "model": model.model}), "LIVE-ROLE2")
    return {**bundle, "model": model, "signal": signal, "pack": pack, "draft": draft, "write": write}


def test_live_generation_resolves_original_spans_and_facts(live_case):
    for span in live_case["signal"].evidence:
        record = live_case["resolve_source"](span.source)
        assert record["source"] == span.source.model_dump()
        assert record["text"][span.start:span.end] == span.text
    pack = live_case["pack"]
    for fact in pack.sales + [study.fact for study in pack.studies]:
        record = live_case["resolve_source"](fact.source)
        assert record["source"] == fact.source.model_dump() and record["value"] == fact.value
    for passage in pack.equipment + pack.strategy:
        record = live_case["resolve_source"](passage.source)
        assert record["source"] == passage.source.model_dump() and passage.text in record["text"]
    assert live_case["draft"].smart.metrics[0].review_state == "unreviewed"


def test_live_scoped_challenge_is_previewed_and_source_resolved(live_case):
    backend, context, tools = live_case["review_for"](live_case["write"].requirement_id)
    suggestion = answer_review_turn(context,
        "Does the evidence justify the current target scope? Cite the sources, explain gaps, "
        "and propose a scoped assumption or description clarification for PM review.",
        tools, model=live_case["model"], backend=backend)
    assert not suggestion.limited and suggestion.state == "pending" and suggestion.score_preview is not None
    assert suggestion.citation_ids
    for identifier in suggestion.citation_ids:
        resolved = live_case["resolve_citation"](identifier)
        assert resolved["source_version"] and resolved["locator"]
