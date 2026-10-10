from copy import deepcopy

import pytest

from services.signals import extract_signals
from tests.intelligence.fakes import SCOPE, ScriptedModel, signal_response


def extract(evidence, repository, audit, response):
    return extract_signals(SCOPE, evidence, model=ScriptedModel(response), repository=repository, audit=audit)


def two_buckets(evidence):
    original = evidence.buckets[0]
    # Categories share their first twenty characters; full category identity must remain distinct.
    evidence.buckets = [original.model_copy(update={"category": "a" * 20 + suffix,
                                                    "input_group_id": "group-" + suffix}) for suffix in ("one", "two")]
    return evidence.buckets


def test_shared_fixture_counts_and_source_resolution(signal, evidence):
    assert sum(len(comment.annotations) for comment in evidence.comments) == 4
    assert len(evidence.comments) == 3
    assert signal.issue_support_comment_count == 2
    assert signal.opposing_comment_count == 1
    assert signal.cohort_comment_count == 3
    comments = {comment.id: comment for comment in evidence.comments}
    for span in signal.evidence:
        assert comments[span.feedback_id].text[span.start:span.end] == span.text
        assert span.source == comments[span.feedback_id].source
        assert span.source.locator.row in {2, 4, 5}


def test_reverse_order_and_same_prefix_categories(evidence, repository, audit):
    buckets = two_buckets(evidence)
    response = {"results": [signal_response(bucket) for bucket in reversed(buckets)]}
    result = extract(evidence, repository, audit, response)
    assert result.state == "succeeded"
    assert len({signal.id for signal in result.items}) == 2
    assert [(signal.input_group_id, signal.category) for signal in result.items] == [
        (bucket.input_group_id, bucket.category) for bucket in buckets
    ]


@pytest.mark.parametrize("corrupt", ["quote", "offset", "feedback_id", "intensity", "nan", "duplicate_span"])
def test_invalid_evidence_or_range_never_saved(corrupt, evidence, repository, audit):
    group = signal_response(evidence.buckets[0])
    proposal = group["signals"][0]
    span = proposal["evidence"][0]
    if corrupt == "quote":
        span["text"] = "An invented or translated quote"
    elif corrupt == "offset":
        span["end"] += 1
    elif corrupt == "feedback_id":
        span["feedback_id"] = "NOT-SUPPLIED"
    elif corrupt == "intensity":
        proposal["intensity"] = 999.0
    elif corrupt == "nan":
        proposal["intensity"] = float("nan")
    else:
        proposal["evidence"].append(deepcopy(span))
    result = extract(evidence, repository, audit, {"results": [group]})
    assert result.state == "failed"
    assert not repository.versions
    assert not audit.events


@pytest.mark.parametrize("corrupt", ["unknown", "duplicate"])
def test_bad_group_identity_fails_batch(corrupt, evidence, repository, audit):
    group = signal_response(evidence.buckets[0])
    rows = [group]
    if corrupt == "unknown":
        group["input_group_id"] = "not-requested"
    else:
        rows.append(deepcopy(group))
    result = extract(evidence, repository, audit, {"results": rows})
    assert result.state == "failed" and not result.items
    assert not repository.versions


def test_missing_group_preserves_valid_partial_result(evidence, repository, audit):
    buckets = two_buckets(evidence)
    result = extract(evidence, repository, audit, {"results": [signal_response(buckets[1])]})
    assert result.state == "partial"
    assert result.items[0].input_group_id == buckets[1].input_group_id
    assert result.failures[0].input_id == buckets[0].input_group_id
    assert result.failures[0].code == "missing_output"


def test_rerun_identity_and_immutable_versions(evidence, repository, audit):
    response = {"results": [signal_response(evidence.buckets[0])]}
    first = extract(evidence, repository, audit, response).items[0]
    identical = extract(evidence, repository, audit, response).items[0]
    response["results"][0]["signals"][0]["intensity"] = 8.0
    changed = extract(evidence, repository, audit, response).items[0]
    assert first.id == identical.id == changed.id
    assert first.revision == identical.revision == 1
    assert changed.revision == 2 and first.intensity == 6.0
    assert len(repository.versions[first.id]) == 2


def test_bucket_volume_is_not_issue_support(evidence, repository, audit):
    evidence.buckets[0] = evidence.buckets[0].model_copy(update={
        "topic_bucket_comment_count": 100, "cohort_comment_count": 200,
    })
    result = extract(evidence, repository, audit, {"results": [signal_response(evidence.buckets[0])]})
    signal = result.items[0]
    assert signal.topic_bucket_comment_count == 100
    assert signal.issue_support_comment_count == 2
    assert signal.unassigned_candidate_count == 97
    assert any("lower bound" in gap for gap in signal.gaps)


def test_unique_exact_quote_offsets_resolved_deterministically(evidence, repository, audit):
    group = signal_response(evidence.buckets[0])
    for span in group["signals"][0]["evidence"]:
        span.pop("start")
        span.pop("end")
    result = extract(evidence, repository, audit, {"results": [group]})
    assert result.state == "succeeded"
    originals = {comment.id: comment.text for comment in evidence.comments}
    for span in result.items[0].evidence:
        assert originals[span.feedback_id][span.start:span.end] == span.text


def test_repeated_quote_without_locator_is_ambiguous(evidence, repository, audit):
    bucket = evidence.buckets[0]
    comment = bucket.examples[0].model_copy(update={"text": "Hard to find. Hard to find."})
    evidence.buckets[0] = bucket.model_copy(update={"examples": [comment] + bucket.examples[1:]})
    group = signal_response(evidence.buckets[0])
    span = group["signals"][0]["evidence"][0]
    span.update(text="Hard to find.")
    span.pop("start")
    span.pop("end")
    result = extract(evidence, repository, audit, {"results": [group]})
    assert result.state == "failed" and "ambiguous" in result.failures[0].message
    assert not repository.versions


def test_supplied_counterexample_cannot_be_hidden_as_a_gap(evidence, repository, audit):
    group = signal_response(evidence.buckets[0])
    proposal = group["signals"][0]
    counterexample = proposal["evidence"].pop()
    proposal["gaps"] = [counterexample["text"]]
    result = extract(evidence, repository, audit, {"results": [group]})
    assert result.state == "failed" and "hidden in gaps" in result.failures[0].message


def test_provider_schema_leaves_offset_arithmetic_to_the_service(evidence, repository, audit):
    group = signal_response(evidence.buckets[0])
    for span in group["signals"][0]["evidence"]:
        span.update(start=None, end=None)
    model = ScriptedModel({"results": [group]})
    result = extract_signals(SCOPE, evidence, model=model, repository=repository, audit=audit)
    assert result.state == "succeeded"
    fields = model.calls[0]["schema"]["$defs"]["SpanProposal"]["properties"]
    assert fields["start"]["type"] == fields["end"]["type"] == "null"
    assert all(isinstance(span.start, int) and isinstance(span.end, int) for span in result.items[0].evidence)
