from services.analysis import derivation_key, run_analysis
from tests.intelligence.fakes import FakeWriter, SCOPE, ScriptedModel, draft_payload


def run(signals, evidence, model, writer, audit, events, **kwargs):
    return run_analysis(signals, SCOPE, "RUN-FIXTURE-001", evidence_provider=evidence, model=model,
                        writer=writer, audit=audit, progress=events.append, config_version="fixture-config-v1", **kwargs)


def test_fixture_draft_reaches_writer_with_authoritative_issue_context(signal, pack, evidence, audit):
    writer, events = FakeWriter(), []
    result = run([signal], evidence, ScriptedModel({"requirements": [draft_payload(signal, pack)]}), writer, audit, events)
    assert result.state == "succeeded" and result.results[0].outcome == "created"
    assert writer.submissions[0][1].issue_support_comment_count == 2
    assert writer.submissions[0][1].cohort_comment_count == 3
    assert [event.stage for event in events] == ["import_check", "consolidate", "retrieve", "draft", "validate", "score", "completed"]
    assert events[-1].state == "succeeded"


def test_cache_returns_identity_and_preserves_pm_changes(signal, pack, evidence, audit):
    writer, events = FakeWriter(), []
    first = run([signal], evidence, ScriptedModel({"requirements": [draft_payload(signal, pack)]}), writer, audit, events)
    record = writer.records[first.results[0].requirement_id]
    record.update(status="APPROVED", revision=3, description="PM-authored text")
    model = ScriptedModel()
    cached = run([signal], evidence, model, writer, audit, events)
    assert cached.cached_signal_ids == [signal.id]
    assert cached.results[0].requirement_id == first.results[0].requirement_id
    assert not model.calls and len(writer.submissions) == 1
    assert record["status"] == "APPROVED" and record["description"] == "PM-authored text"


def test_changed_context_regenerates_using_same_derivation_as_pending(signal, pack, evidence, audit):
    writer, events = FakeWriter(), []
    first = run([signal], evidence, ScriptedModel({"requirements": [draft_payload(signal, pack)]}), writer, audit, events)
    record = writer.records[first.results[0].requirement_id]
    record.update(status="REJECTED", revision=2)
    changed = signal.model_copy(update={"revision": 2, "intensity": 7.0})
    changed_pack = pack.model_copy(update={"signal_revision": 2})
    second = run([changed], evidence, ScriptedModel({"requirements": [draft_payload(changed, changed_pack)]}), writer, audit, events)
    assert second.results[0].outcome == "pending"
    assert first.results[0].requirement_id == second.results[0].requirement_id
    assert writer.submissions[0][3] == writer.submissions[1][3]
    assert record["status"] == "REJECTED" and record["revision"] == 2
    assert derivation_key(signal, SCOPE) == derivation_key(changed, SCOPE)


def test_partial_generation_keeps_valid_result(signal, pack, evidence, audit):
    other = signal.model_copy(update={"id": "SIG-OTHER"})
    writer, events = FakeWriter(), []
    model = ScriptedModel({"requirements": [draft_payload(signal, pack)]}, {"requirements": []})
    result = run([signal, other], evidence, model, writer, audit, events)
    assert result.state == "partial" and len(result.results) == 1 and len(result.failures) == 1
    assert result.failures[0].input_id == other.id
    assert events[-1].state == "partial"


def test_missing_credentials_is_explicit_failure(signal, evidence, audit):
    from services.llm_client import ModelUnavailable
    writer, events = FakeWriter(), []
    result = run([signal], evidence, ScriptedModel(ModelUnavailable("OPENAI_API_KEY is missing")), writer, audit, events)
    assert result.state == "failed" and not result.results and not writer.submissions
    assert "OPENAI_API_KEY" in result.failures[0].message


def test_no_signals_is_not_success(evidence, audit):
    result = run([], evidence, ScriptedModel(), FakeWriter(), audit, [])
    assert result.state == "failed"


def test_run_limit_is_reported_not_silently_omitted(signal, pack, evidence, audit):
    other = signal.model_copy(update={"id": "SIG-OTHER"})
    result = run([signal, other], evidence, ScriptedModel({"requirements": [draft_payload(signal, pack)]}),
                 FakeWriter(), audit, [], max_drafts=1)
    assert result.state == "partial" and result.failures[0].code == "draft_limit"
