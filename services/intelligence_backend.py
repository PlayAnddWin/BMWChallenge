"""Wire Role 2 agents to native Role 1 evidence and Role 3 governance."""

from copy import deepcopy
from datetime import date
from pathlib import Path
from uuid import uuid4
import json

from openpyxl.utils import get_column_letter

from config import get_settings
from contracts.intelligence import (Scope, Locator, SourceReference, FeedbackComment, TopicBucket,
    NativeFact, StudyContext, DocumentPassage, Signal, ReviewContext, RequirementDraft as AIDraft,
    PatchOperation)
from models import RequirementDraft as BackendDraft
from services.analysis import derivation_key
from services.audit import canonical_json
from services.cache import FileDraftCache
from services.common import EngineError, fingerprint
from services.decisions import GovernanceService, DomainError
from services.drafting import draft_requirement, validate_draft, PROMPT_VERSION as DRAFT_PROMPT
from services.llm_client import OpenAIAdapter
from services.providers import GraphSourceProvider
from services.retrieval import build_evidence_pack
from services.signals import extract_signals, PROMPT_VERSION as SIGNAL_PROMPT
from services.review_assistant import answer_review_turn, PROMPT_VERSION as REVIEW_PROMPT
from services.tool_registry import ToolRegistry, ToolResult, validate_patch


def _private_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    temporary = path.parent / ("." + uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            temporary.chmod(0o600)
            stream.write(canonical_json(value))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class ObservedModel:
    max_draft_batch_size = 1
    policy_version = OpenAIAdapter.policy_version

    def __init__(self, settings):
        self.adapter = OpenAIAdapter(settings)
        self.model = self.adapter.model
        self.responses = []
        self.outputs = []

    def complete(self, messages, schema, tools=None):
        result = self.adapter.complete(messages, schema, tools)
        self.responses.append(deepcopy(result.provider_metadata))
        self.outputs.append(deepcopy(result.content))
        return result


class IntelligenceAudit:
    def __init__(self, repository):
        self.repository = repository

    def record(self, action, payload):
        entity_id = payload.get("signal_id") or payload.get("run_id") or payload.get("signal", {}).get("id") or "intelligence"
        def save(state):
            GovernanceService(self.repository, None).event(state, action, entity_id, None, payload,
                ("openai-engine", "AI_SYSTEM"), "Validated AI stage provenance", entity_type="IntelligenceRun")
        self.repository.write(save)


class IntelligenceEvidence:
    """Bounded, typed projections of actual imported records; no fixture fallback."""
    def __init__(self, sources, *, query="", topics=(), max_groups=4):
        self.sources = sources
        self.query, self.topics, self.max_groups = query, list(topics), max_groups
        self.references = {}

    def source(self, record_id, source_id, version, locator):
        column = locator.get("column")
        location = Locator(sheet=locator.get("sheet"), row=locator.get("row"),
                           column=get_column_letter(column) if isinstance(column, int) else column,
                           page=locator.get("page"), printed_page=locator.get("printed_page"))
        return SourceReference(id=record_id, source_file_id=source_id, source_version=version, locator=location)

    def remember(self, identifier, source, *, relation="context", locator=None, record_id=None):
        locator = locator or self.sources.get("record", source.id)["locator"]
        self.references[identifier] = {"source_id": source.source_file_id, "record_id": record_id or source.id,
            "source_version": source.source_version, "locator": deepcopy(locator), "claim_key": identifier,
            "relation": relation, "validation_state": "verified"}

    @staticmethod
    def filters(scope, *, powertrains=True):
        result = {"derivatives": scope.derivatives, "regions": scope.regions}
        if powertrains and scope.powertrains:
            result["powertrains"] = scope.powertrains
        return result

    def get_topic_buckets(self, scope, limit=24):
        rows = self.sources._read("""
            MATCH (fb:CustomerFeedback)-[:HAS_ANNOTATION]->(a:FeedbackAnnotation)
            WHERE fb.model IN $models AND fb.region IN $regions
              AND (size($powertrains)=0 OR fb.powertrain IN $powertrains)
              AND a.vfc2_original IS NOT NULL
              AND (size($topics)=0 OR a.vfc2_original IN $topics OR a.vfc3_original IN $topics)
            WITH a.vfc2_original AS category, collect(DISTINCT {id:fb.id,
                 matches:($search_text='' OR toLower(fb.original_text) CONTAINS toLower($search_text)),
                 positive:a.polarity='positive'}) AS comments
            WHERE any(comment IN comments WHERE comment.matches)
            RETURN category, comments ORDER BY category LIMIT $limit
        """, models=scope.derivatives, regions=scope.regions, powertrains=scope.powertrains,
            topics=self.topics, search_text=self.query, limit=min(limit, self.max_groups))
        cohort = self.sources.get_comment_cohort_counts(self.filters(scope))["cohort_distinct_comment_count"]
        buckets = []
        for row in rows:
            comments = {item["id"]: item for item in row["comments"]}
            matching = sorted(identity for identity, item in comments.items() if item["matches"])
            counterexamples = sorted(identity for identity, item in comments.items() if item["positive"] and identity not in matching)
            selected = list(dict.fromkeys(matching[:9] + counterexamples[:3]))[:12]
            examples = []
            for identity in selected:
                native = self.sources.get("feedback", identity)
                source_rows = self.sources._read("MATCH (r:SourceRecord {feedback_comment_id:$id}) RETURN properties(r) AS record ORDER BY r.id LIMIT 1", id=identity)
                record = GraphSourceProvider._normalize(source_rows[0]["record"])
                source = self.source(record["id"], native["source_file_id"], native["source_version"], record["locator"])
                examples.append(FeedbackComment(id=identity, original_id=str(native.get("original_id") or identity),
                    text=native["original_text"], source=source, source_label=native.get("source_label") or "unknown",
                    scope=Scope(derivatives=[native["model"]], regions=[native["region"]],
                                powertrains=[native["powertrain"]] if native.get("powertrain") else []), annotations=[]))
            buckets.append(TopicBucket(input_group_id="GROUP-" + fingerprint([row["category"], scope.model_dump()]),
                category=row["category"], scope=scope, topic_bucket_comment_count=len(comments), cohort_comment_count=cohort,
                examples=examples, source_versions=sorted({example.source.source_version for example in examples}),
                selection_limitations=["Bounded lexical discovery: at most twelve original comments per topic; not an exhaustive issue assignment or population sample."]))
        return buckets

    def get_survey_facts(self, topic, scope):
        result = self.sources.get_survey_facts(topic, self.filters(scope, powertrains=False))
        values = []
        for native in result["facts"][:24]:
            if not native.get("derivative") or not native.get("region"):
                continue
            source = self.source(native["source_record_id"], native["source_file_id"], native["source_version"], native["source_locator"])
            value = ([float(v) for v in native["distribution"].values()] if native.get("distribution") else float(native["value"]))
            fact = NativeFact(id=native["id"], kind="survey", scope=Scope(derivatives=[native["derivative"]], regions=[native["region"]]),
                metric=native.get("metric") or native.get("question") or "native study cell", value=value,
                unit=native.get("unit") or "native source value; unit unknown", scale=native.get("scale"),
                respondent_count=native.get("respondent_n"), source=source)
            mapping = native.get("topic_mapping") or {}
            values.append(StudyContext(fact=fact, relation=mapping.get("relation", "unmapped"),
                mapping_reviewed=mapping.get("review_state") == "reviewed", mapping_version=str(mapping.get("mapping_version") or "unmapped"),
                reason=mapping.get("reason") or "No reviewed issue-to-study relation"))
            self.remember(fact.id, source, locator=native["source_locator"], record_id=native["id"])
        return values

    def get_sales_facts(self, scope, year=2030):
        result = self.sources.get_sales_facts(self.filters(scope, powertrains=False), year)
        indexed = {fact["id"]: fact for fact in result["facts"] + result["possible_whole_line_proxy_candidates"]}
        facts = []
        for native in indexed.values():
            derivatives = ["G60", "G68"] if native["vehicle_line"] == "G60_G68" else [native["vehicle_line"]]
            source = self.source(native["source_record_id"], native["source_file_id"], native["source_version"], native["source_locator"])
            fact = NativeFact(id=native["id"], kind="sales", scope=Scope(derivatives=derivatives, regions=[native["region"]]),
                metric="Whole-line sales forecast; no derivative or powertrain split", value=float(native["volume"]),
                unit=native["unit"], year=year, vehicle_line=native["vehicle_line"], source=source)
            facts.append(fact)
            self.remember(fact.id, source, locator=native["source_locator"], record_id=native["id"])
        return facts

    def search_document_chunks(self, query, scope, document_kind, limit=3):
        # Native catalogs/reports have unknown market/derivative applicability.
        # They remain available to review tools, without being promoted to scoped
        # engineering/strategy proof by copying the requested scope onto them.
        native_kind = "option_catalog" if document_kind == "option" else "corporate_report"
        passages = []
        for native in self.sources.search_document_chunks(query, document_kind=native_kind, limit=limit):
            if not native.get("derivative") or not (native.get("market") or native.get("region")):
                continue
            source = self.source(native["source_record_id"], native["source_file_id"], native["source_version"], native["source_locator"])
            passage = DocumentPassage(id=native["id"], kind=document_kind, text=native["original_text"],
                scope=Scope(derivatives=[native["derivative"]], regions=[native.get("market") or native["region"]]), source=source)
            passages.append(passage)
            self.remember(passage.id, source, locator=native["source_locator"], record_id=native["id"])
        return passages

    def register_pack(self, pack):
        for span in pack.supporting + pack.opposing:
            self.remember(span.id, span.source, relation="supports" if span.polarity == "supports" else "contradicts" if span.polarity == "opposes" else "context")


class IntelligenceSignals:
    def __init__(self, repository, sources):
        self.repository, self.sources = repository, sources
        with repository.session() as session:
            for label in ("Signal", "SignalRevision"):
                session.run(f"CREATE CONSTRAINT intelligence_{label.lower()}_id IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE").consume()

    def save_version(self, signal):
        payload = signal.model_dump(exclude={"revision"})
        def save(state, tx):
            tx.run("MATCH (m:EvidenceManifest {id:'role1'}) SET m.lock_version=coalesce(m.lock_version,0) RETURN m.id").consume()
            for span in signal.evidence:
                row = tx.run("MATCH (fb:CustomerFeedback {id:$id}) MATCH (r:SourceRecord {id:$record}) RETURN fb.original_text AS text, fb.source_version AS version, r.feedback_comment_id AS owner", id=span.feedback_id, record=span.source.id).single()
                if not row or row["owner"] != span.feedback_id or row["version"] != span.source.source_version or row["text"][span.start:span.end] != span.text:
                    raise EngineError("Signal evidence changed or does not resolve to its original feedback span")
            existing = tx.run("MATCH (s:Signal {id:$id}) RETURN s.revision AS revision, s.payload_json AS payload", id=signal.id).single()
            before = json.loads(existing["payload"]) if existing else None
            if before and canonical_json({k: v for k, v in before.items() if k != "revision"}) == canonical_json(payload):
                return Signal.model_validate(before)
            revision = existing["revision"] + 1 if existing else 1
            saved = signal.model_copy(update={"revision": revision})
            value = saved.model_dump()
            tx.run("MERGE (s:Signal {id:$id}) SET s.revision=$revision, s.payload_json=$payload WITH s CREATE (v:SignalRevision {id:$version_id, signal_id:$id, revision:$revision, payload_json:$payload}) MERGE (s)-[:HAS_REVISION]->(v)",
                id=saved.id, revision=revision, version_id=f"{saved.id}@{revision}", payload=canonical_json(value)).consume()
            GovernanceService(self.repository, None).event(state, "SIGNAL_CONSOLIDATED", saved.id, before, value,
                ("openai-engine", "AI_SYSTEM"), "Exact source spans validated; issue assignments remain unreviewed", entity_type="Signal")
            return saved
        return self.repository.write_with_transaction(save)


def backend_draft(draft, signal, pack, evidence):
    evidence.register_pack(pack)
    links = {}
    for claim in draft.claims:
        for identifier in claim.evidence_ids:
            if identifier in evidence.references:
                ref = deepcopy(evidence.references[identifier])
                ref.update(claim_key=claim.id, relation=claim.relation)
                links[canonical_json(ref)] = ref
    for factor in draft.factor_proposals:
        for identifier in factor.evidence_ids:
            if identifier in evidence.references:
                ref = deepcopy(evidence.references[identifier])
                ref.update(claim_key="factor-" + factor.factor, relation="context")
                links[canonical_json(ref)] = ref
    metrics = []
    for metric in draft.smart.metrics:
        value = metric.model_dump()
        value.update(target=metric.value, operator="==" if metric.operator == "=" else metric.operator,
                     verification_method=metric.test_method, review_state="pending",
                     source_references=[evidence.references[i]["record_id"] for i in metric.evidence_ids if i in evidence.references])
        metrics.append(value)
    milestone = {**draft.smart.milestone.model_dump(), "value": draft.smart.milestone.text, "review_state": "pending",
        "source_references": [evidence.references[i]["record_id"] for i in draft.smart.milestone.evidence_ids if i in evidence.references]}
    assumptions = [{**item.model_dump(), "description": item.text, "review_state": "pending"} for item in draft.assumptions]
    proxy_id = "sales-proxy-" + fingerprint(draft.target_scope.model_dump())[:16]
    assumptions.append({"id": proxy_id, "kind": "powertrain_proxy", "review_owner": "PM", "provenance": "AIProposed", "review_state": "pending",
        "description": "Sourced 2030 whole-line sales are an upper-bound proxy only: neither the G60/G68 derivative split nor a powertrain split is supplied."})
    factors = {factor.factor + "_level": factor.value for factor in draft.factor_proposals}
    factors["role2_proposals"] = [factor.model_dump() for factor in draft.factor_proposals]
    strategy = next((factor for factor in draft.factor_proposals if factor.factor == "strategic_fit"), None)
    factors["strategy_rationale"] = strategy.reason if strategy else ""
    payload = {"derivation_key": derivation_key(signal, draft.target_scope), "signal_id": signal.id, "signal_revision": signal.revision,
        "title": draft.title, "description": draft.description, "problem_statement": draft.problem_statement,
        "source_scope": draft.source_scope.model_dump(), "target_scope": draft.target_scope.model_dump(),
        "smart": {"specific": draft.smart.specific, "metrics": metrics, "attainability": draft.smart.feasibility.text,
                  "feasibility": draft.smart.feasibility.model_dump(), "relevance": draft.smart.relevance, "milestone": milestone},
        "finding_links": [{"signal_id": signal.id, "revision": signal.revision}], "evidence_links": list(links.values()),
        "assumptions": assumptions, "uncertainties": [{"description": item} for item in draft.uncertainties],
        "factor_proposals": factors, "has_conflict": False,
        "jtbd": draft.jtbd, "claims": [claim.model_dump() for claim in draft.claims], "alternatives": draft.alternatives,
        "intelligence_draft": {"draft": draft.model_dump(), "pack": pack.model_dump(), "citation_map": deepcopy(evidence.references)}}
    return BackendDraft.model_validate(payload).model_dump()


class Role2Engine:
    def __init__(self, repository, sources, preview):
        self.repository, self.sources, self.preview = repository, sources, preview
        self.settings = get_settings()
        self.signals = IntelligenceSignals(repository, sources)
        self.audit = IntelligenceAudit(repository)
        self.cache = FileDraftCache.from_settings(self.settings)

    def analyze(self, body, progress_callback=None, run_id=None):
        progress = progress_callback or (lambda *args: None)
        run_id = run_id or str(uuid4())
        maximum = min(body.get("max_drafts", 4), 18)
        observed = self.sources._read("MATCH (fb:CustomerFeedback) WHERE fb.region IS NOT NULL RETURN collect(DISTINCT fb.model) AS models, collect(DISTINCT fb.region) AS regions, collect(DISTINCT fb.powertrain) AS powertrains")
        scope = Scope(derivatives=body.get("derivatives") or sorted(observed[0]["models"]),
            regions=body.get("regions") or sorted(observed[0]["regions"]),
            powertrains=body.get("powertrains") or sorted(observed[0]["powertrains"]))
        evidence = IntelligenceEvidence(self.sources, query=body.get("query", ""), topics=body.get("topics", []), max_groups=maximum)
        model = ObservedModel(self.settings)
        progress("consolidate", .1, "live")
        buckets = evidence.get_topic_buckets(scope)
        cache_key = fingerprint({"buckets": [b.model_dump() for b in buckets], "model": model.model, "prompt": SIGNAL_PROMPT, "adapter": model.policy_version})
        signal_path = self.settings.cache_dir / "signals" / (cache_key + ".json")
        use_cache = body.get("use_cache", True) and not body.get("refresh", False)
        cached_signals = use_cache and signal_path.is_file()
        if cached_signals:
            selected = [self.signals.save_version(Signal.model_validate(value)) for value in json.loads(signal_path.read_text())]
            failures = []
        else:
            result = extract_signals(scope, evidence, model=model, repository=self.signals, audit=self.audit)
            selected, failures = result.items, [item.message for item in result.failures]
            if selected and not failures:
                _private_json(signal_path, [signal.model_dump() for signal in selected])
        if body.get("signal_ids"):
            selected = [Signal.model_validate(self.sources.get("signal", identifier)["payload"]) for identifier in body["signal_ids"]]
        if not selected:
            raise EngineError("; ".join(failures) or "No source-grounded issue signals matched the requested scope")
        drafts = []
        for index, signal in enumerate(selected[:maximum]):
            progress("retrieve", .2 + .6 * index / maximum, "cached" if cached_signals else "live")
            pack = build_evidence_pack(signal, scope, evidence_provider=evidence)
            key = fingerprint({"signal": signal.model_dump(), "pack": pack.model_dump(), "model": model.model,
                               "prompt": DRAFT_PROMPT, "adapter": model.policy_version, "as_of": date.today().isoformat()})
            artifact = self.cache.get(key) if use_cache else None
            try:
                if artifact:
                    draft = validate_draft(artifact.draft, signal, pack, scope)
                else:
                    progress("draft", .3 + .6 * index / maximum, "live")
                    draft = draft_requirement(signal, pack, scope, model=model, audit=self.audit, run_id=run_id)
                    self.cache.put(key, draft, model=model.model, prompt_version=DRAFT_PROMPT, run_id=run_id)
                drafts.append(backend_draft(draft, signal, pack, evidence))
            except (EngineError, ValueError) as exc:
                failures.append(f"{signal.id}: {exc}")
                _private_json(self.settings.cache_dir / "generation-errors" / (key + ".json"),
                              {"signal_id": signal.id, "error": str(exc), "responses": model.responses, "outputs": model.outputs})
        if not drafts:
            raise EngineError("; ".join(failures))
        progress("validate", .95, "cached" if not model.responses else "live")
        return {"drafts": drafts, "failures": failures,
                "model_metadata": {"provider": "openai", "model": model.model, "responses": model.responses,
                                   "cached": not bool(model.responses)},
                "prompt_metadata": {"signal": SIGNAL_PROMPT, "draft": DRAFT_PROMPT}}

    def review(self, current, question, prior_turns, progress_callback=None):
        model = ObservedModel(self.settings)
        evidence = IntelligenceEvidence(self.sources)
        references = deepcopy(current.get("intelligence_draft", {}).get("citation_map", {}))
        for link in current.get("evidence_links", []):
            references.setdefault(link["record_id"], deepcopy(link))
        thread_id = "review-" + current["id"]
        history = []
        for turn in prior_turns[-6:]:
            if turn.get("question"):
                history.append({"role": "user", "content": turn["question"]})
            if turn.get("answer"):
                history.append({"role": "assistant", "content": turn["answer"]})

        def pinned(record):
            # Full snapshots remain in governance; model/tool context uses bounded
            # visible fields, exact citations and authoritative count summaries.
            fields = ("id", "revision", "signal_id", "signal_revision", "finding_links", "title", "description", "problem_statement", "jtbd", "source_scope", "target_scope",
                      "smart", "claims", "alternatives", "assumptions", "uncertainties", "factor_proposals", "evidence_links", "score", "evidence", "status")
            summary = {key: deepcopy(record[key]) for key in fields if key in record}
            summary["customer_counts"] = {region: {key: value for key, value in counts.items() if key not in {"support_ids", "cohort_ids"}}
                                          for region, counts in record.get("score_context", {}).get("region_counts", {}).items()}
            return summary

        engine = self

        class ReviewBridge:
            def load_context(self, _):
                latest = engine.repository.read()["requirements"].get(current["id"], current)
                return ReviewContext(thread_id=thread_id, entity_kind="requirement", entity_id=current["id"],
                    revision=latest["revision"], pinned={"requirement": pinned(latest)}, evidence_ids=sorted(references), visible_history=history)

            def mark_outdated_suggestions(self, *_):
                # Role 3 derives staleness from its authoritative revision/input
                # fingerprints and persists the resulting turn itself.
                pass

            def preview(self, entity_id, revision, operations):
                latest = engine.repository.read()["requirements"][entity_id]
                return engine.preview(entity_id, revision, translate_patch(latest, operations))

            def save_turn(self, suggestion, trace):
                self.suggestion, self.trace = suggestion, trace

        bridge = ReviewBridge()

        def feedback(args):
            hits = self.sources.search_feedback(args.query, IntelligenceEvidence.filters(args.scope), min(args.limit, 6))
            output = []
            for hit in hits:
                row = hit["source_records"][0]
                references[hit["id"]] = {"source_id": hit["source_file_id"], "record_id": row["source_record_id"],
                    "source_version": hit["source_version"], "locator": row["source_locator"], "claim_key": hit["id"],
                    "relation": "context", "validation_state": "verified"}
                output.append({key: hit[key] for key in ("id", "original_text", "model", "region", "powertrain", "source_version")})
            return ToolResult(data={"candidates": output, "assigned_support": False}, evidence_ids=[hit["id"] for hit in hits])

        def signal_evidence(args):
            native = self.sources.get("signal", args.signal_id)
            if native["revision"] != args.revision:
                raise EngineError("Stale signal revision")
            signal = Signal.model_validate(native["payload"])
            for span in signal.evidence:
                evidence.remember(span.id, span.source, relation="supports" if span.polarity == "supports" else "contradicts" if span.polarity == "opposes" else "context")
            references.update(evidence.references)
            return ToolResult(data={"signal": signal.model_dump()}, evidence_ids=[span.id for span in signal.evidence], source_versions=signal.source_versions)

        def survey(args):
            values = evidence.get_survey_facts(args.topic, args.scope)
            references.update(evidence.references)
            return ToolResult(data={"studies": [item.model_dump() for item in values], "mapping_status": "reviewed" if values else "unmapped"},
                evidence_ids=[item.fact.id for item in values])

        def sales(args):
            values = evidence.get_sales_facts(args.scope, args.year)
            references.update(evidence.references)
            return ToolResult(data={"facts": [item.model_dump() for item in values], "powertrain_split": "unknown"}, evidence_ids=[item.id for item in values])

        def documents(args):
            kind = "option_catalog" if args.document_kind == "option" else "corporate_report"
            values = self.sources.search_document_chunks(args.query, document_kind=kind, limit=min(args.limit, 2))
            output = []
            for item in values:
                references[item["id"]] = {"source_id": item["source_file_id"], "record_id": item["id"], "source_version": item["source_version"],
                    "locator": item["source_locator"], "claim_key": item["id"], "relation": "context", "validation_state": "verified"}
                output.append({"id": item["id"], "text": item["original_text"], "source_locator": item["source_locator"], "applicability": "unknown"})
            return ToolResult(data={"passages": output, "scope_applicability": "unknown; no automatic market or derivative carryover"}, evidence_ids=[item["id"] for item in values])

        handlers = {"search_feedback": feedback, "get_signal_evidence": signal_evidence, "get_survey_facts": survey,
                    "get_sales_volume": sales, "search_documents": documents,
                    "get_requirement_context": lambda args: ToolResult(data={"requirement": pinned(self.repository.read()["requirements"][args.requirement_id])}, evidence_ids=sorted(references)[:40]),
                    "preview_requirement_change": lambda args: ToolResult(data=bridge.preview(args.requirement_id, args.expected_revision,
                                                                                                   [op.model_dump() for op in args.patch]))}
        if progress_callback:
            progress_callback("review", .2, "live")
        suggestion = answer_review_turn(bridge.load_context(thread_id), question, ToolRegistry(handlers), model=model, backend=bridge)
        return {"answer": suggestion.answer, "citations": [references[identifier] for identifier in suggestion.citation_ids],
                "tool_records": bridge.trace["tools"], "coverage_gaps": suggestion.gaps,
                "patch": translate_patch(current, [op.model_dump() for op in suggestion.proposed_patch]) if suggestion.proposed_patch else None,
                "model_metadata": {"provider": "openai", "model": model.model, "responses": model.responses},
                "prompt_metadata": {"review": REVIEW_PROMPT}}


def translate_patch(current, operations):
    """Validate Role 2 pointers, then return Role 3's complete-field diff."""
    operations = [PatchOperation.model_validate(operation) for operation in operations]
    validate_patch(operations)
    proposed = {key: deepcopy(current.get(key, field.get_default(call_default_factory=True)))
                for key, field in BackendDraft.model_fields.items()}
    changed = set()
    for operation in operations:
        parts = operation.path.strip("/").split("/")
        changed.add(parts[0])
        target = proposed
        for part in parts[:-1]:
            target = target[int(part)] if isinstance(target, list) else target[part]
        key = int(parts[-1]) if isinstance(target, list) and parts[-1] != "-" else parts[-1]
        if operation.op == "remove":
            if isinstance(target, list):
                target.pop(key)
            else:
                target.pop(key)
        elif isinstance(target, list) and key == "-":
            target.append(deepcopy(operation.value))
        else:
            target[key] = deepcopy(operation.value)
    if "smart" in changed:
        smart = proposed["smart"]
        for metric in smart["metrics"]:
            if "value" in metric:
                metric["target"] = metric["value"]
            if "test_method" in metric:
                metric["verification_method"] = metric["test_method"]
            if metric.get("operator") == "=":
                metric["operator"] = "=="
            if metric.get("review_state") == "unreviewed":
                metric["review_state"] = "pending"
        if "feasibility" in smart:
            smart["attainability"] = smart["feasibility"]["text"]
        if "text" in smart["milestone"]:
            smart["milestone"]["value"] = smart["milestone"]["text"]
        if smart["milestone"].get("review_state") == "unreviewed":
            smart["milestone"]["review_state"] = "pending"
    if "assumptions" in changed:
        for assumption in proposed["assumptions"]:
            if "text" in assumption:
                assumption["description"] = assumption["text"]
            if assumption.get("review_state") == "unreviewed":
                assumption["review_state"] = "pending"
    validated = BackendDraft.model_validate(proposed).model_dump()
    return {key: validated[key] for key in changed}
