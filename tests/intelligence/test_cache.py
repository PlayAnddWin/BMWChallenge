import json

import pytest

from services.analysis import run_analysis
from services.cache import FileDraftCache
from services.common import EngineError
from tests.intelligence.fakes import FakeWriter, SCOPE, ScriptedModel, draft_payload


def test_private_cache_reuses_a_validated_draft_without_model_call(tmp_path, signal, pack, evidence, audit):
    cache, events = FileDraftCache(tmp_path / "cache"), []
    args = dict(evidence_provider=evidence, audit=audit, progress=events.append,
                config_version="fixture-v1", draft_cache=cache)
    first_writer = FakeWriter()
    first = run_analysis([signal], SCOPE, "RUN-1", model=ScriptedModel({"requirements": [draft_payload(signal, pack)]}),
                         writer=first_writer, **args)
    assert first.state == "succeeded" and not first.cached_signal_ids
    # A restarted writer still receives a validated draft and the same stable derivation identity.
    model, restarted_writer = ScriptedModel(), FakeWriter()
    second = run_analysis([signal], SCOPE, "RUN-2", model=model, writer=restarted_writer, **args)
    assert second.cached_signal_ids == [signal.id] and not model.calls
    assert restarted_writer.submissions[0][3] == first_writer.submissions[0][3]
    key = first_writer.submissions[0][4]
    artifact = cache.get(key)
    assert artifact.generated_run_id == "RUN-1" and artifact.model == "fixture-model-v1"
    assert (cache.directory / f"{key}.json").stat().st_mode & 0o777 == 0o600


def test_corrupt_or_path_traversal_cache_is_rejected(tmp_path, signal, pack):
    cache = FileDraftCache(tmp_path)
    with pytest.raises(EngineError):
        cache.get("../../secrets")
    (tmp_path / ("a" * 64 + ".json")).write_text("{not-json}")
    with pytest.raises(EngineError):
        cache.get("a" * 64)


def test_schema_valid_but_unverified_cached_target_cannot_reach_writer(tmp_path, signal, pack, evidence, audit):
    writer, cache = FakeWriter(), FileDraftCache(tmp_path)
    args = dict(evidence_provider=evidence, writer=writer, audit=audit, progress=lambda event: None,
                config_version="fixture-v1", draft_cache=cache)
    run_analysis([signal], SCOPE, "RUN-1", model=ScriptedModel({"requirements": [draft_payload(signal, pack)]}), **args)
    key = writer.submissions[0][4]
    path = tmp_path / f"{key}.json"
    artifact = json.loads(path.read_text())
    artifact["draft"]["smart"]["metrics"][0]["review_state"] = "accepted"
    path.write_text(json.dumps(artifact))
    fresh_writer = FakeWriter()
    result = run_analysis([signal], SCOPE, "RUN-2", model=ScriptedModel(), **{**args, "writer": fresh_writer})
    assert result.state == "failed" and not fresh_writer.submissions
