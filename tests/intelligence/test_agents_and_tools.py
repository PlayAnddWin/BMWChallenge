from services.agents import DRAFT_AGENT, REVIEW_AGENT, SIGNAL_AGENT
from services.tool_registry import ALLOWED_TOOLS, ARGUMENT_MODELS, TOOL_DESCRIPTIONS, ToolRegistry, ToolResult


def test_agents_have_distinct_roles_contracts_and_enforced_budgets():
    assert len({agent.name for agent in (SIGNAL_AGENT, DRAFT_AGENT, REVIEW_AGENT)}) == 3
    for agent in (SIGNAL_AGENT, DRAFT_AGENT, REVIEW_AGENT):
        assert agent.description and "INPUT:" in agent.instructions and "OUTPUT:" in agent.instructions
        assert "BOUNDARY:" in agent.instructions and agent.output_model.model_json_schema()
    assert SIGNAL_AGENT.max_tool_calls == DRAFT_AGENT.max_tool_calls == 0
    assert REVIEW_AGENT.max_tool_calls == 6 and REVIEW_AGENT.max_model_rounds == 4


def test_every_registered_tool_has_specific_use_and_interpretation_guidance():
    assert set(TOOL_DESCRIPTIONS) == set(ARGUMENT_MODELS) == set(ALLOWED_TOOLS)
    schemas = ToolRegistry({name: lambda args: ToolResult(data={}) for name in ALLOWED_TOOLS}).schemas()
    for schema in schemas:
        function = schema["function"]
        assert len(function["description"]) > 200
        assert "read-only" in function["description"].lower()
        assert function["parameters"]["additionalProperties"] is False
    evidence = next(schema["function"] for schema in schemas if schema["function"]["name"] == "get_signal_evidence")
    assert evidence["strict"] is True and "revision" in evidence["parameters"]["required"]
    preview = next(schema["function"] for schema in schemas if schema["function"]["name"] == "preview_requirement_change")
    assert preview["strict"] is False  # Dynamic JSON-pointer values remain locally/backend validated.
