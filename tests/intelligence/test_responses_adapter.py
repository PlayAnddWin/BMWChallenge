from types import SimpleNamespace

from config import load_settings
from services.llm_client import OpenAIAdapter


def test_reasoning_tool_round_uses_responses_and_replays_encrypted_continuation_only_in_memory():
    settings = load_settings().model_copy(update={"openai_model": "gpt-5.4"})
    # Use an explicit synthetic secret; no network is made even if the developer has a real .env.
    from pydantic import SecretStr
    settings = settings.model_copy(update={"openai_api_key": SecretStr("synthetic-test-secret")})
    captured = []
    reasoning_data = {"type": "reasoning", "id": "rs-fixture", "summary": [], "encrypted_content": "opaque-fixture"}
    call_data = {"type": "function_call", "id": "fc-fixture", "call_id": "call-fixture", "name": "get_signal_evidence",
                 "arguments": '{"signal_id":"SIG-FIXTURE","revision":1}'}
    reasoning = SimpleNamespace(type="reasoning", model_dump=lambda **kwargs: reasoning_data)
    call = SimpleNamespace(**call_data, model_dump=lambda **kwargs: call_data)
    replies = [SimpleNamespace(id="resp-first", model="gpt-5.4", status="completed", output=[reasoning, call],
                               output_text="", usage=None),
               SimpleNamespace(id="resp-second", model="gpt-5.4", status="completed", output=[],
                               output_text='{"answer":"fixture"}', usage=None)]

    def create(**kwargs):
        captured.append(kwargs)
        return replies.pop(0)

    adapter = OpenAIAdapter(settings, client=SimpleNamespace(responses=SimpleNamespace(create=create)))
    messages = [{"role": "user", "content": "Return JSON after reading evidence"}]
    reply = adapter.complete(messages, {}, [{"type": "function", "function": {
        "name": "get_signal_evidence", "description": "Read fixture evidence", "parameters": {"type": "object"}}}])
    assert reply.tool_calls[0].id == "call-fixture"
    assert "opaque-fixture" not in repr(reply)
    messages += [{"role": "assistant", "content": None, "tool_calls": [{"id": "call-fixture", "type": "function",
                  "function": {"name": call_data["name"], "arguments": call_data["arguments"]}}]},
                 {"role": "tool", "tool_call_id": "call-fixture", "content": '{"fixture":true}'}]
    final = adapter.complete(messages, {})
    assert final.content == {"answer": "fixture"}
    assert captured[0]["store"] is False and captured[0]["reasoning"] == {"effort": "low"}
    assert captured[0]["tools"][0]["name"] == "get_signal_evidence"
    assert reasoning_data in captured[1]["input"]
    assert {"type": "function_call_output", "call_id": "call-fixture", "output": '{"fixture":true}'} in captured[1]["input"]
