from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import config
from services.common import EngineError
from services.llm_client import ModelUnavailable, OpenAIAdapter, keyed_output_schema, openai_strict_schema, structured_reply
from tests.intelligence.fakes import ScriptedModel


@pytest.fixture
def settings(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
    for name in ("OPENAI_API_KEY", "OPENAI_MODEL", "API_PORT", "API_BASE_URL", "BMW_CACHE_DIR"):
        monkeypatch.delenv(name, raising=False)
    return config.load_settings()


def test_missing_key_never_selects_another_provider(settings):
    adapter = OpenAIAdapter(settings)
    with pytest.raises(ModelUnavailable, match="OPENAI_API_KEY"):
        adapter.complete([], {})
    assert adapter._client is None


def test_config_paths_cwd_independence_and_process_precedence(monkeypatch, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / ".env").write_text("OPENAI_API_KEY=dotenv-secret\nAPI_PORT=8123\nAPI_BASE_URL=http://localhost:${API_PORT}\nBMW_CACHE_DIR=./private-cache\n")
    monkeypatch.setattr(config, "REPO_ROOT", root)
    monkeypatch.setenv("OPENAI_API_KEY", "process-secret")
    monkeypatch.setenv("API_PORT", "8124")
    monkeypatch.delenv("API_BASE_URL", raising=False)
    monkeypatch.delenv("BMW_CACHE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    loaded = config.load_settings()
    assert loaded.cache_dir == root / "private-cache"
    assert loaded.openai_api_key.get_secret_value() == "process-secret"
    assert loaded.api_base_url == "http://localhost:8124"
    assert "process-secret" not in repr(loaded)


def test_invalid_port_or_url_rejected(settings):
    with pytest.raises(ValidationError):
        config.Settings.model_validate({**settings.model_dump(), "api_port": 99999})
    with pytest.raises(ValidationError):
        config.Settings.model_validate({**settings.model_dump(), "api_base_url": "file:///etc/passwd"})


def test_adapter_sends_configured_model_and_parses_tool_call(settings):
    captured = []
    message = SimpleNamespace(content=None, refusal=None, tool_calls=[SimpleNamespace(
        id="call-1", function=SimpleNamespace(name="search_feedback", arguments='{"query":"temperature"}'))])
    response = SimpleNamespace(choices=[SimpleNamespace(message=message)])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kwargs: captured.append(kwargs) or response)))
    adapter = OpenAIAdapter(config.Settings.model_validate({**settings.model_dump(), "openai_api_key": "private-key",
                                                           "openai_model": "configured-model"}), client=client)
    reply = adapter.complete([{"role": "user", "content": "JSON"}], {}, [{"type": "function"}])
    assert captured[0]["model"] == "configured-model"
    assert reply.tool_calls[0].arguments == {"query": "temperature"}


def test_json_repairs_are_bounded():
    model = ScriptedModel(EngineError("bad JSON"), EngineError("still bad JSON"))
    with pytest.raises(EngineError):
        structured_reply(model, [], {})
    assert len(model.calls) == 2


def test_closed_draft_schema_enforces_all_nested_fields_with_provider():
    from contracts.intelligence import DraftBatchOutput
    original = DraftBatchOutput.model_json_schema()
    schema = openai_strict_schema(original)
    assert schema is not None
    target = schema["$defs"]["Target"]
    assert set(target["required"]) == set(target["properties"])
    assert "provenance" in target["required"] and target["additionalProperties"] is False
    assert "default" not in target["properties"]["review_state"]
    assert original == DraftBatchOutput.model_json_schema()


def test_dynamic_review_patch_schema_uses_json_mode_and_local_validation():
    from contracts.intelligence import ReviewOutput
    assert openai_strict_schema(ReviewOutput.model_json_schema()) is None


def test_keyed_provider_schema_bounds_cardinality_and_requested_identity():
    from contracts.intelligence import SignalBatchOutput
    schema = keyed_output_schema(SignalBatchOutput.model_json_schema(), "results", "SignalGroupOutput",
                                 "input_group_id", ["GROUP-1"])
    assert schema["properties"]["results"]["maxItems"] == 1
    assert schema["$defs"]["SignalGroupOutput"]["properties"]["input_group_id"]["enum"] == ["GROUP-1"]
    assert openai_strict_schema(schema) is not None


def test_multi_item_request_has_explicit_bounded_timeout():
    from services.llm_client import request_timeout
    assert request_timeout({}) == 45
    assert request_timeout({"properties": {"requirements": {"maxItems": 1}}}) == 45
    assert request_timeout({"properties": {"requirements": {"maxItems": 2}}}) == 90
    assert request_timeout({"properties": {"results": {"maxItems": 10}}}) == 90
