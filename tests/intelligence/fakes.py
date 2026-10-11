"""Synthetic providers and fixed model responses. No external accounts or graph required."""
from copy import deepcopy
import json
from pathlib import Path

from contracts.intelligence import (
    DocumentPassage, FeedbackComment, Locator, NativeFact, ReviewContext, Scope,
    SourceReference, StudyContext, TopicBucket, WriteResult,
)
from services.common import EngineError, fingerprint, scope_matches
from services.llm_client import ModelReply

from tests.intelligence.fixture_case import FIXTURE
SCOPE = Scope.model_validate(FIXTURE["scope"])


def source(identifier: str, row: int = 2, *, page: int | None = None) -> SourceReference:
    return SourceReference(id=identifier, source_file_id="SOURCE-FIXTURE-001", source_version="synthetic-v1",
                           locator=Locator(page=page) if page else Locator(sheet="Fixture", row=row, column="E"))


class FixtureEvidence:
    def __init__(self):
        comments = {}
        for row in FIXTURE["rows"]:
            if row["canonical_id"] not in comments:
                comments[row["canonical_id"]] = FeedbackComment(
                    id=row["canonical_id"], original_id=row["original_id"], text=row["text"],
                    scope=Scope(derivatives=["G60"], regions=["US"], powertrains=[row["powertrain"]]),
                    source=source("RECORD-" + row["canonical_id"], row["row"]), source_label="A", annotations=[],
                )
            comments[row["canonical_id"]].annotations.append(row["annotation"])
        self.comments = list(comments.values())
        self.buckets = [TopicBucket(input_group_id="GROUP-FIXTURE-HVAC", category="HVAC", scope=SCOPE,
                                    topic_bucket_comment_count=3, cohort_comment_count=3, examples=self.comments,
                                    selection_limitations=["Synthetic bounded discovery, not a representative survey."],
                                    source_versions=["synthetic-v1"])]
        fact = NativeFact(id=FIXTURE["survey"]["id"], kind="survey", scope=SCOPE,
                          metric="HVAC access rating", value=FIXTURE["survey"]["distribution"], unit="proportion",
                          scale=FIXTURE["survey"]["scale"], respondent_count=None,
                          source=source("RECORD-SURVEY", 8))
        self.studies = [StudyContext(fact=fact, relation="direct", mapping_reviewed=True,
                                    mapping_version="fixture-map-v1", reason="Synthetic directly mapped access question.")]
        self.sales = [NativeFact(id=entry["id"], kind="sales", metric="sales forecast", value=float(entry["volume"]),
                                 unit="vehicles", year=2030, vehicle_line="G60_G68",
                                 scope=Scope(derivatives=["G60", "G68"], regions=[entry["region"]]),
                                 source=source("RECORD-" + entry["id"], 10)) for entry in FIXTURE["sales"]]
        self.strategy = [DocumentPassage(id=FIXTURE["strategy"]["id"], kind="strategy",
                                        text=FIXTURE["strategy"]["text"], scope=SCOPE, source=source("RECORD-STRATEGY", page=1))]
        self.equipment = [DocumentPassage(id=FIXTURE["option"]["id"], kind="option", text=FIXTURE["option"]["text"],
                                         scope=Scope(derivatives=["G60"], regions=["DE"]), source=source("RECORD-OPTION", page=1))]

    def get_topic_buckets(self, scope, limit=24):
        return [bucket for bucket in self.buckets if scope_matches(bucket.scope, scope)]

    def get_survey_facts(self, topic, scope):
        return self.studies

    def get_sales_facts(self, scope, year=2030):
        return self.sales

    def search_document_chunks(self, query, scope, document_kind, limit=3):
        return (self.equipment if document_kind == "option" else self.strategy)[:limit]


class ScriptedModel:
    model = "fixture-model-v1"

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, messages, schema, tools=None):
        self.calls.append({"messages": deepcopy(messages), "schema": schema, "tools": tools})
        if not self.responses:
            raise AssertionError("model exceeded scripted call budget")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if callable(response):
            response = response(messages)
        return response if isinstance(response, ModelReply) else ModelReply(content=deepcopy(response))


class MemorySignals:
    def __init__(self):
        self.versions = {}

    def save_version(self, signal):
        versions = self.versions.setdefault(signal.id, [])
        payload = signal.model_dump(exclude={"revision"})
        if versions and versions[-1].model_dump(exclude={"revision"}) == payload:
            return versions[-1]
        saved = signal.model_copy(update={"revision": len(versions) + 1}, deep=True)
        versions.append(saved)
        return saved


class RecordingAudit:
    def __init__(self):
        self.events = []

    def record(self, action, payload):
        self.events.append((action, deepcopy(payload)))


def signal_response(bucket):
    return {"input_group_id": bucket.input_group_id, "signals": [{
        "issue_key": "temperature-control-discoverability", "title": "Temperature controls are hard to find",
        "problem_statement": "Some fixture comments report difficulty finding temperature adjustment.",
        "sentiment": "negative", "intensity": 6.0,
        "intensity_reason": "Repeated fixture usability friction; no evidence of a core experience blocker.",
        "evidence": [{"feedback_id": comment.id, "start": 0, "end": len(comment.text), "text": comment.text,
                      "polarity": "opposes" if comment.id.endswith("003") else "supports"} for comment in bucket.examples],
        "gaps": [],
    }]}


def draft_payload(signal, pack):
    support = [span.id for span in pack.supporting]
    return {
        "input_signal_id": signal.id, "title": "Make temperature adjustment discoverable",
        "description": "Provide a discoverable path to adjust cabin temperature.",
        "problem_statement": signal.problem_statement,
        "jtbd": "When I adjust cabin temperature, I want accessible controls, so that I can complete the task easily.",
        "source_scope": signal.source_scope.model_dump(), "target_scope": pack.target_scope.model_dump(),
        "smart": {"specific": "Cabin temperature adjustment interaction", "metrics": [{
            "id": "metric-fixture-1", "value": 2.0, "unit": "actions", "operator": "<=",
            "conditions": "Synthetic usability task from the fixture home screen",
            "test_method": "Count deliberate actions during a fixture usability task",
            "measurement_definition": "An action is a deliberate tap or button press; finish when the temperature adjustment is reached.",
            "rationale": "A proposed short interaction path to test discoverability, not a BMW benchmark.",
            "provenance": "AIProposed", "review_state": "unreviewed", "evidence_ids": [],
        }], "feasibility": {"id": "feasibility-fixture-1", "text": "Requires engineering validation.",
                              "provenance": "AIProposed", "evidence_ids": []},
            "relevance": "Addresses the fixture discoverability issue.",
            "milestone": {"id": "milestone-fixture-1", "text": "Fixture concept freeze",
                          "provenance": "AIProposed", "evidence_ids": []}},
        "claims": [{"id": "claim-problem", "kind": "problem", "text": signal.problem_statement,
                    "provenance": "SourceBacked", "evidence_ids": support}],
        "factor_proposals": [{"factor": "severity", "value": 2.0, "reason": "Synthetic repeated usability friction",
                              "evidence_ids": support}],
        "alternatives": ["Improve navigation labels", "Provide a persistent shortcut"],
        "assumptions": [{"id": "assumption-target-review", "text": "PM/UX must validate the proposed threshold and test setup.",
                         "kind": "target_validation", "review_owner": "UX", "provenance": "AIProposed", "evidence_ids": []},
                        {"id": "assumption-implementation-review", "text": "Engineering must assess the implementation route.",
                         "kind": "implementation", "review_owner": "Engineering", "provenance": "AIProposed", "evidence_ids": []}],
        "uncertainties": [],
    }


class FakeWriter:
    """Records engine handoffs; does not claim proof of real backend transactions/scoring."""
    def __init__(self):
        self.submissions = []
        self.cache = {}
        self.records = {}

    def get_cached(self, cache_key, run_id):
        return self.cache.get(cache_key)

    def submit(self, draft, signal, pack, derivation_key, cache_key, run_id):
        self.submissions.append((draft, signal, pack, derivation_key, cache_key, run_id))
        identifier = "REQ-" + derivation_key
        if identifier in self.records:
            result = WriteResult(requirement_id=identifier, outcome="pending", suggestion_id="PENDING-" + cache_key)
        else:
            self.records[identifier] = {"draft": draft.model_dump(), "revision": 1, "status": "DRAFT"}
            result = WriteResult(requirement_id=identifier, outcome="created")
        self.cache[cache_key] = result.model_copy(update={"outcome": "existing"})
        return result


class FakeReviewBackend:
    def __init__(self, evidence_ids):
        self.context = ReviewContext(thread_id="THREAD-FIXTURE-001", entity_kind="requirement",
                                     entity_id="REQ-FIXTURE-001", revision=1,
                                     pinned={"description": "Fixture requirement", "status": "DRAFT"},
                                     evidence_ids=evidence_ids)
        self.turns = {}
        self.traces = {}
        self.previews = []
        self.transactions = []

    def load_context(self, thread_id):
        assert thread_id == self.context.thread_id
        return self.context.model_copy(deep=True)

    def mark_outdated_suggestions(self, thread_id, current_revision):
        for identifier, suggestion in list(self.turns.items()):
            if suggestion.thread_id == thread_id and suggestion.state == "pending" and suggestion.base_revision != current_revision:
                self.turns[identifier] = suggestion.model_copy(update={"state": "stale"})

    def preview(self, entity_id, expected_revision, patch):
        assert entity_id == self.context.entity_id and expected_revision == self.context.revision
        self.previews.append(deepcopy(patch))
        return {"base_revision": expected_revision, "fixture_only": True, "priority_delta": -1.0}

    def save_turn(self, suggestion, trace):
        if suggestion.turn_id in self.turns:
            raise EngineError("duplicate turn_id")
        self.turns[suggestion.turn_id] = suggestion
        self.traces[suggestion.turn_id] = deepcopy(trace)

    def get_pending_suggestion(self, turn_id, transaction):
        self.transactions.append(transaction)
        return self.turns[turn_id]

    def mark_suggestion_applied(self, turn_id, revision, transaction):
        self.transactions.append(transaction)
        suggestion = self.turns[turn_id]
        if suggestion.state != "pending" or suggestion.base_revision != self.context.revision:
            raise EngineError("suggestion is stale or no longer pending")
        self.turns[turn_id] = suggestion.model_copy(update={"state": "applied", "applied_revision": revision})
