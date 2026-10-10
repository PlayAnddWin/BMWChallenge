"""Contextual review agent. All proposed edits stay pending until a separate PM request."""
import re
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from contracts.intelligence import ReviewContext, ReviewOutput, ReviewSuggestion
from services.common import EngineError, canonical
from services.agents import REVIEW_AGENT
from services.interfaces import ReviewBackend
from services.llm_client import ModelAdapter, ModelUnavailable
from services.tool_registry import ToolRegistry, validate_patch

PROMPT_VERSION = REVIEW_AGENT.prompt_version
MAX_TOOL_CALLS = REVIEW_AGENT.max_tool_calls
MAX_MODEL_ROUNDS = REVIEW_AGENT.max_model_rounds
SYSTEM_PROMPT = REVIEW_AGENT.system_prompt


def answer_review_turn(context: ReviewContext, question: str, tools: ToolRegistry, *, model: ModelAdapter,
                       backend: ReviewBackend, turn_id: str | None = None) -> ReviewSuggestion:
    question = question.strip()
    if not question or len(question) > 4000:
        raise EngineError("question must contain 1-4000 characters")
    current = backend.load_context(context.thread_id)
    if current.entity_id != context.entity_id or current.entity_kind != context.entity_kind:
        raise EngineError("review thread entity binding changed")
    # Always use the newly loaded revision and pinned evidence on follow-ups.
    context = current
    backend.mark_outdated_suggestions(context.thread_id, context.revision)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for entry in context.visible_history:
        if entry.get("role") not in {"user", "assistant"} or set(entry) != {"role", "content"}:
            raise EngineError("history must contain visible user/assistant text only")
        messages.append(entry)
    messages += [{"role": "user", "content": canonical({"pinned_context": context.model_dump(), "question": question})}]
    trace: dict[str, Any] = {"model": model.model, "prompt_version": PROMPT_VERSION,
                             "context": context.model_dump(), "question": question, "tools": [], "rounds": 0,
                             "validation_errors": []}
    allowed = set(context.evidence_ids)
    tool_count = 0
    limited = False
    output = None
    for round_index in range(MAX_MODEL_ROUNDS):
        trace["rounds"] = round_index + 1
        try:
            reply = model.complete(messages, ReviewOutput.model_json_schema(), tools.schemas())
            if reply.tool_calls:
                # Reject oversized tool batches before constructing an unbounded transcript.
                if len(reply.tool_calls) > MAX_TOOL_CALLS:
                    limited = True
                    trace["validation_errors"].append("oversized tool batch")
                    break
                if len({call.id for call in reply.tool_calls}) != len(reply.tool_calls):
                    raise EngineError("duplicate tool call IDs")
                messages.append({"role": "assistant", "content": None, "tool_calls": [
                    {"id": call.id, "type": "function", "function": {
                        "name": call.name, "arguments": canonical(call.arguments),
                    }} for call in reply.tool_calls
                ]})
                for call in reply.tool_calls:
                    if tool_count >= MAX_TOOL_CALLS:
                        limited = True
                        data = {"error": "tool budget exhausted"}
                    else:
                        tool_count += 1
                        try:
                            result = tools.execute(call.name, call.arguments, context)
                            allowed.update(result.evidence_ids)
                            data = result.model_dump()
                        except (EngineError, ValidationError) as error:
                            data = {"error": str(error)}
                    trace["tools"].append({"id": call.id, "name": call.name, "input": call.arguments, "result": data})
                    messages.append({"role": "tool", "tool_call_id": call.id, "content": canonical(data)})
                continue
            candidate = ReviewOutput.model_validate(reply.content)
            if not set(candidate.citation_ids) <= allowed:
                raise EngineError("answer cites evidence not supplied in this turn")
            inline = set(re.findall(r"\[([A-Za-z0-9][A-Za-z0-9_.:-]*)\]", candidate.answer))
            if not inline <= set(candidate.citation_ids):
                raise EngineError("inline citations must be supplied and declared in citation_ids")
            validate_patch(candidate.proposed_patch)
            if candidate.proposed_patch and context.entity_kind != "requirement":
                raise EngineError("signal-only review cannot patch a requirement")
            output = candidate
            break
        except ModelUnavailable:
            # Preserve an explicit unavailable failure; do not persist a fabricated successful answer.
            raise
        except (EngineError, ValidationError) as error:
            trace["validation_errors"].append(str(error))
            messages.append({"role": "user", "content": "Invalid output: " + str(error)
                             + ". Return valid JSON using only supplied evidence and allowed proposed fields."})
    if output is None:
        limited = True
        output = ReviewOutput(answer="Review could not be completed within the bounded model/tool budget.",
                              citation_ids=[], gaps=["No validated answer or change proposal produced."], proposed_patch=[])
    requirement = context.pinned.get("requirement", context.pinned)
    pack = context.pinned.get("evidence_pack", {})
    known_gaps = []
    for values in (requirement.get("uncertainties", []), pack.get("gaps", []), context.pinned.get("gaps", [])):
        if isinstance(values, list):
            known_gaps.extend(value for value in values if isinstance(value, str))
    output = output.model_copy(update={"gaps": list(dict.fromkeys(known_gaps + output.gaps))})
    preview = None
    state = "pending" if output.proposed_patch else "none"
    latest = backend.load_context(context.thread_id)
    if latest.entity_id != context.entity_id or latest.entity_kind != context.entity_kind:
        raise EngineError("review thread entity binding changed during turn")
    if latest.revision != context.revision and output.proposed_patch:
        state = "stale"
    elif output.proposed_patch:
        preview = backend.preview(context.entity_id, context.revision,
                                  [operation.model_dump(exclude_none=True) for operation in output.proposed_patch])
        if preview.get("base_revision") != context.revision:
            raise EngineError("backend preview does not match the suggestion base revision")
    suggestion = ReviewSuggestion(**output.model_dump(), turn_id=turn_id or str(uuid4()),
                                  thread_id=context.thread_id, entity_id=context.entity_id,
                                  base_revision=context.revision, state=state, score_preview=preview, limited=limited)
    trace["tool_calls"] = tool_count
    trace["supplied_evidence_ids"] = sorted(allowed)
    trace["visible_answer"] = suggestion.model_dump()
    backend.save_turn(suggestion, trace)
    return suggestion


def get_pending_suggestion(turn_id: str, transaction: Any, *, backend: ReviewBackend) -> ReviewSuggestion:
    suggestion = backend.get_pending_suggestion(turn_id, transaction)
    if suggestion.state != "pending":
        raise EngineError("suggestion is not pending")
    return suggestion


def mark_suggestion_applied(turn_id: str, revision: int, transaction: Any, *, backend: ReviewBackend) -> None:
    if revision < 1:
        raise EngineError("applied revision must be positive")
    # No transaction/session creation here. Role 3 validates stale/idempotent apply and calls this
    # from the same transaction as requirement revision, score, ranking and audit writes.
    backend.mark_suggestion_applied(turn_id, revision, transaction)
