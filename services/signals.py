"""Signal extraction agent: keyed interpretation over bounded, original evidence."""
from pydantic import ValidationError

from contracts.intelligence import (
    BatchResult, EvidenceSpan, ItemFailure, Scope, Signal, SignalBatchOutput,
    SignalGroupOutput, TopicBucket,
)
from services.common import EngineError, canonical, fingerprint, keyed_rows
from services.agents import SIGNAL_AGENT
from services.interfaces import AuditSink, EvidenceProvider, SignalRepository
from services.llm_client import ModelAdapter, keyed_output_schema, structured_reply

PROMPT_VERSION = SIGNAL_AGENT.prompt_version
SYSTEM_PROMPT = SIGNAL_AGENT.system_prompt


def _validate_bucket(bucket: TopicBucket, scope: Scope) -> None:
    for dimension in ("derivatives", "regions", "powertrains"):
        requested = getattr(scope, dimension)
        actual = getattr(bucket.scope, dimension)
        if requested and not set(actual) <= set(requested):
            raise EngineError(f"bucket outside requested {dimension}")
        for comment in bucket.examples:
            values = getattr(comment.scope, dimension)
            if actual and not set(values) <= set(actual):
                raise EngineError(f"comment {comment.id} outside bucket {dimension}")
    if any(comment.source.source_version not in bucket.source_versions for comment in bucket.examples):
        raise EngineError("discovery source version missing from bucket")


def _signals(group: SignalGroupOutput, bucket: TopicBucket) -> list[Signal]:
    comments = {comment.id: comment for comment in bucket.examples}
    issue_keys = [proposal.issue_key for proposal in group.signals]
    if len(set(issue_keys)) != len(issue_keys):
        raise EngineError("duplicate issue_key within group")
    result = []
    for proposal in group.signals:
        evidence = []
        seen = set()
        for span in proposal.evidence:
            comment = comments.get(span.feedback_id)
            if comment is None:
                raise EngineError(f"unknown feedback_id: {span.feedback_id}")
            start, end = span.start, span.end
            if start is None and end is None:
                start = comment.text.find(span.text)
                if start < 0:
                    raise EngineError(f"non-verbatim span for {span.feedback_id}")
                if start != comment.text.rfind(span.text):
                    raise EngineError(f"ambiguous repeated quote for {span.feedback_id}; explicit offsets required")
                end = start + len(span.text)
            if start is None or end is None or not start < end <= len(comment.text) or comment.text[start:end] != span.text:
                raise EngineError(f"non-verbatim span for {span.feedback_id}")
            key = (span.feedback_id, start, end)
            if key in seen:
                raise EngineError("duplicate evidence span")
            seen.add(key)
            evidence.append(EvidenceSpan(
                **{**span.model_dump(), "start": start, "end": end},
                id="EV-" + fingerprint([comment.id, comment.source.source_version, start, end, span.polarity]),
                source=comment.source, scope=comment.scope,
            ))
        supporting = {span.feedback_id for span in evidence if span.polarity == "supports"}
        opposing = {span.feedback_id for span in evidence if span.polarity == "opposes"}
        if not supporting:
            raise EngineError("issue requires traceable supporting feedback")
        assigned = {span.feedback_id for span in evidence}
        if any(gap.strip() == comment.text.strip() and comment.id not in assigned
               for gap in proposal.gaps for comment in bucket.examples):
            raise EngineError("supplied evidence quote is hidden in gaps instead of a cited assignment")
        gaps = list(dict.fromkeys(bucket.selection_limitations + proposal.gaps))
        if len(supporting) < 5:
            gaps.append(f"Only {len(supporting)} traceable model-assigned supporting comments; membership unreviewed; limited support.")
        gaps.append("Counts are distinct comments, not verified distinct customers or population prevalence.")
        if len(assigned) < bucket.topic_bucket_comment_count:
            gaps.append("Issue assignments cover a discovery subset; support is a lower bound.")
        identity = {"scope": bucket.scope.model_dump(), "category": bucket.category,
                    "issue_key": proposal.issue_key}
        result.append(Signal(
            id="SIG-" + fingerprint(identity), revision=1, input_group_id=bucket.input_group_id,
            issue_key=proposal.issue_key, title=proposal.title, problem_statement=proposal.problem_statement,
            category=bucket.category, source_scope=bucket.scope, sentiment=proposal.sentiment,
            intensity=proposal.intensity, evidence=evidence,
            intensity_reason=proposal.intensity_reason,
            topic_bucket_comment_count=bucket.topic_bucket_comment_count,
            issue_support_comment_count=len(supporting), opposing_comment_count=len(opposing),
            cohort_comment_count=bucket.cohort_comment_count,
            unassigned_candidate_count=bucket.topic_bucket_comment_count - len(assigned),
            source_versions=sorted(bucket.source_versions), gaps=gaps, consolidation_version=PROMPT_VERSION,
        ))
    return result


def extract_signals(scope: Scope, evidence_provider: EvidenceProvider, *, model: ModelAdapter,
                    repository: SignalRepository, audit: AuditSink) -> BatchResult[Signal]:
    buckets = evidence_provider.get_topic_buckets(scope, limit=24)
    if not buckets:
        return BatchResult(items=[], failures=[ItemFailure(input_id="scope", code="no_evidence",
                                                          message="No topic buckets in requested scope")], state="failed")
    ids = [bucket.input_group_id for bucket in buckets]
    if len(set(ids)) != len(ids):
        raise EngineError("duplicate provider input_group_id")
    for bucket in buckets:
        _validate_bucket(bucket, scope)
    items, failures = [], []
    # Retain the useful legacy batching boundary, with keyed validation instead of positional binding.
    for offset in range(0, len(buckets), 10):
        batch = buckets[offset:offset + 10]
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": canonical([bucket.model_dump() for bucket in batch])}]
        try:
            schema = keyed_output_schema(SignalBatchOutput.model_json_schema(), "results", "SignalGroupOutput",
                                         "input_group_id", [bucket.input_group_id for bucket in batch])
            # Index arithmetic is deterministic service work, not a language-model task.
            schema["$defs"]["SpanProposal"]["properties"]["start"] = {"type": "null"}
            schema["$defs"]["SpanProposal"]["properties"]["end"] = {"type": "null"}
            raw = structured_reply(model, messages, schema)
            if set(raw) != {"results"} or not isinstance(raw["results"], list):
                raise EngineError("expected results array")
            mapped = keyed_rows(raw["results"], {bucket.input_group_id for bucket in batch}, "input_group_id")
        except (EngineError, TypeError, AttributeError) as error:
            failures.extend(ItemFailure(input_id=bucket.input_group_id, code="batch_invalid", message=str(error))
                            for bucket in batch)
            continue
        for bucket in batch:
            if bucket.input_group_id not in mapped:
                failures.append(ItemFailure(input_id=bucket.input_group_id, code="missing_output",
                                            message="Model omitted requested group"))
                continue
            try:
                group = SignalGroupOutput.model_validate(mapped[bucket.input_group_id])
                validated = _signals(group, bucket)
                for candidate in validated:
                    saved = repository.save_version(candidate)
                    audit.record("SIGNAL_CONSOLIDATED", {
                        "signal": saved.model_dump(), "model": model.model, "prompt_version": PROMPT_VERSION,
                        "input_ids": [comment.id for comment in bucket.examples],
                    })
                    items.append(saved)
            except (EngineError, ValidationError) as error:
                failures.append(ItemFailure(input_id=bucket.input_group_id, code="signal_invalid", message=str(error)))
    state = "partial" if failures and items else "failed" if failures else "succeeded"
    return BatchResult(items=items, failures=failures, state=state)
