"""FastAPI composition. Application lifespan owns the Neo4j driver.

create_app(repository, evidence_provider, engine, source_provider) mounts a fully
independent test instance; production never silently falls back to memory.
"""
import copy
import importlib
import json
import os
import uuid
import threading
import inspect
import math
from urllib.parse import quote
from itertools import islice
from contextlib import asynccontextmanager

from fastapi import FastAPI, APIRouter, BackgroundTasks, Query
from fastapi.responses import JSONResponse, Response, FileResponse
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from models import DecisionRequest, EditRequest, ApplyRequest, FreezeRequest, MutationRequest, RequirementDetail, RequirementDraft, ReviewEngineResult, AnalysisEngineResult
from services.decisions import GovernanceService, DomainError, now, engine_review_patch
from services.repository import Neo4jRepository, same_snapshot
from services.audit import canonical_json
from services.providers import UnavailableEvidenceProvider, GraphSourceProvider
from services.role1_resolver import Role1EvidenceResolver
from services.role1_import import Role1ImportAdapter
from services.export import export_requirements, export_freeze, audit_bundle
from services.scoring import calculate_score, rank_requirements


def create_app(repository=None, evidence_provider=None, engine=None, source_provider=None, driver=None, namespace="backend-v1", audit_failure=None, import_service=None, api_prefix="/api", max_analysis_drafts=1000, adapter_factory=None):
    if type(max_analysis_drafts) is not int or max_analysis_drafts < 1:
        raise ValueError("max_analysis_drafts must be a positive integer")
    if not isinstance(api_prefix, str) or (api_prefix and (not api_prefix.startswith("/") or api_prefix.endswith("/"))):
        raise ValueError("api_prefix must be empty or an absolute path without a trailing slash")
    explicit_components = {"evidence_provider": evidence_provider, "source_provider": source_provider, "engine": engine, "import_service": import_service}

    @asynccontextmanager
    async def lifespan(app):
        nonlocal engine, import_service, evidence_provider, source_provider
        owned = None
        try:
            if app.state.repository is None:
                import config
                from neo4j import GraphDatabase
                owned = driver or GraphDatabase.driver(config.NEO4J_URI, auth=(config.NEO4J_USER, config.NEO4J_PASSWORD))
                owned.verify_connectivity()
                repo = Neo4jRepository(owned, database=getattr(config, "NEO4J_DATABASE", None), namespace=namespace)
                repo.initialize()
                app.state.repository = repo
                app.state.source_provider = source_provider or GraphSourceProvider(owned, getattr(config, "NEO4J_DATABASE", None))
                app.state.service = GovernanceService(repo, evidence_provider or UnavailableEvidenceProvider(), audit_failure)
            factory = adapter_factory
            from config import load_settings
            configured_factory = load_settings().backend_adapter_factory
            if factory is None and configured_factory:
                module_name, separator, attribute = configured_factory.partition(":")
                if not separator or not module_name or not attribute or ":" in attribute:
                    raise ValueError("BACKEND_ADAPTER_FACTORY must be module:function")
                factory = getattr(importlib.import_module(module_name), attribute)
            if factory is not None:
                if not callable(factory):
                    raise ValueError("Backend adapter factory must be callable")
                components = factory(driver=owned if owned is not None else driver or getattr(app.state.repository, "driver", None), repository=app.state.repository)
                if inspect.iscoroutine(components):
                    components.close()
                    raise ValueError("Backend adapter factory must be synchronous")
                known = {"evidence_provider", "source_provider", "engine", "import_service"}
                if not isinstance(components, dict) or not set(components) <= known:
                    raise ValueError("Backend adapter factory must return a mapping of known components")
                explicit = explicit_components
                methods = {"evidence_provider": ("resolve",), "source_provider": ("get", "list_sources"), "engine": ("analyze", "review"), "import_service": ()}
                for name, component in components.items():
                    if explicit[name] is not None and explicit[name] is not component:
                        raise ValueError(f"Adapter factory conflicts with explicit {name}")
                    if name == "import_service" and not (callable(getattr(component, "import_data", None)) or all(callable(getattr(component, method, None)) for method in ("prepare_import", "commit_import"))):
                        raise ValueError("Adapter factory returned invalid import_service")
                    if component is None or any(not callable(getattr(component, method, None)) for method in methods[name]):
                        raise ValueError(f"Adapter factory returned invalid {name}")
                evidence_provider = components.get("evidence_provider", evidence_provider)
                source_provider = components.get("source_provider", source_provider)
                engine = components.get("engine", engine)
                import_service = components.get("import_service", import_service)
                if source_provider is not None:
                    app.state.source_provider = source_provider
                app.state.service = GovernanceService(app.state.repository, evidence_provider if evidence_provider is not None else UnavailableEvidenceProvider(), audit_failure)
            if isinstance(app.state.repository, Neo4jRepository):
                repo = app.state.repository
                if app.state.source_provider is None:
                    app.state.source_provider = GraphSourceProvider(repo.driver, repo.database)
                if evidence_provider is None:
                    evidence_provider = Role1EvidenceResolver(app.state.source_provider)
                if import_service is None:
                    import_service = Role1ImportAdapter(repo.driver, database=repo.database)
                app.state.service = GovernanceService(repo, evidence_provider, audit_failure)
            # Persist restart recovery rather than advertise interrupted work as running.
            def recover(state):
                for run in state["runs"].values():
                    if run["state"] in ("queued", "running"):
                        run.update(state="failed", stage="interrupted", failures=["Application restarted during run"])
            app.state.repository.write(recover)
            yield
        finally:
            if owned is not None and driver is None:
                owned.close()

    app = FastAPI(title="BMW requirements governance", version="2.0.0", lifespan=lifespan)
    app.state.repository = repository
    app.state.source_provider = source_provider
    app.state.service = GovernanceService(repository, evidence_provider or UnavailableEvidenceProvider(), audit_failure) if repository else None
    run_lock = threading.Lock()

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse(status_code=exc.status, content={"detail": exc.message})

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(request, exc):
        return JSONResponse(status_code=422, content={"detail": [{key: error[key] for key in ("loc", "type", "msg") if key in error} for error in exc.errors()]})

    @app.exception_handler(ValidationError)
    async def validation_error(request, exc):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    def state():
        return app.state.repository.read()

    def get(rid, data=None):
        record = (state() if data is None else data)["requirements"].get(rid)
        if not record:
            raise DomainError(404, "Requirement not found")
        return record

    def require_source_provider():
        if app.state.source_provider is None:
            raise DomainError(503, "Source provider is not integrated")
        return app.state.source_provider

    def source_call(method, *args):
        provider = require_source_provider()
        callback = getattr(provider, method, None)
        if not callable(callback):
            raise DomainError(503, "Source provider capability is not integrated")
        try:
            result = callback(*args)
            if method == "get":
                if not isinstance(result, dict):
                    raise DomainError(503, "Source provider returned invalid entity metadata")
            elif not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
                raise DomainError(503, "Source provider returned invalid entity list")
            json.dumps(result, allow_nan=False)
            return result
        except DomainError:
            raise
        except Exception as exc:
            raise DomainError(503, "Source provider is unavailable") from exc

    router = APIRouter()

    @router.get("/health")
    def health():
        try:
            state()
        except Exception as exc:
            raise DomainError(503, "Persistence is unavailable") from exc
        return {"status": "ok", "evidence_provider": type(app.state.service.evidence_provider).__name__, "engine_available": engine is not None}

    @router.get("/requirements/export")
    def requirements_export(format: str = Query("json", pattern="^(json|csv)$")):
        return Response(export_requirements(state()["requirements"].values(), format), media_type="application/json" if format == "json" else "text/csv", headers={"Content-Disposition": f'attachment; filename="requirements.{format}"'})

    @router.get("/requirements")
    def list_requirements(status: str | None = None, derivative: str | None = None, model: str | None = None, region: str | None = None, powertrain: str | None = None, evidence_level: str | None = None, search: str | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
        data = state()
        records = list(data["requirements"].values())
        records = [r for r in records if (not status or r["status"] == status) and (not (derivative or model) or (derivative or model) in r["target_scope"].get("derivatives", [])) and (not region or region in r["target_scope"].get("regions", [])) and (not powertrain or powertrain in r["target_scope"].get("powertrains", [])) and (not evidence_level or r["evidence"]["level"] == evidence_level) and (not search or search.lower() in (r["title"] + r["description"]).lower())]
        ranks = data["rankings"][-1]["ranks"] if data["rankings"] else {}
        records = [dict(r, rank=ranks.get(r["id"])) for r in records]
        # Active v1 ranks precede unranked v1 and separately labeled legacy rows.
        records.sort(key=lambda r: (r["score"]["policy_version"] != "v1", r["score"]["policy_version"], r["rank"] is None, r["rank"] or 0, -r["score"]["effective_score"], r["id"]))
        return {"items": records[offset:offset+limit], "total": len(records), "offset": offset, "ranking_id": data["rankings"][-1]["id"] if data["rankings"] else None}

    def gaps_from(data):
        return [{"req_id": r["id"], "model": ",".join(r["target_scope"].get("derivatives", [])), "title": r["title"], "gate": r["evidence"].get("level"), "gaps": r["uncertainties"]} for r in data["requirements"].values() if r["uncertainties"]]

    @router.get("/data-gaps")
    def data_gaps():
        return gaps_from(state())

    @router.get("/conflicts")
    def conflicts(model: str | None = None):
        provider = require_source_provider()
        if not hasattr(provider, "list_conflicts"):
            raise DomainError(503, "Conflict provider is not integrated")
        return source_call("list_conflicts", model)

    @router.get("/dashboard/summary")
    def dashboard_summary():
        data = state()
        counts = {}
        for r in data["requirements"].values():
            key = (",".join(r["target_scope"].get("derivatives", [])), r["evidence"].get("level"), r["status"])
            counts[key] = counts.get(key, 0) + 1
        return {"requirement_counts": [{"model": k[0], "gate": k[1], "status": k[2], "cnt": v} for k, v in sorted(counts.items())], "top_conflicts": conflicts()[:20], "data_gap_requirements": gaps_from(data)[:20]}

    @router.get("/requirements/{rid}", response_model=RequirementDetail)
    def detail(rid: str):
        data = state()
        record = get(rid, data)
        rankings = data["rankings"]
        record["rank"] = rankings[-1]["ranks"].get(rid) if rankings else None
        return record

    @router.post("/requirements", response_model=RequirementDetail)
    def submit_requirement(body: dict):
        """Admit an external draft; all counts, facts and citations resolve server-side."""
        if set(body) - {"draft", "run_id", "generation_metadata"}:
            raise DomainError(422, "Unexpected requirement submission fields")
        if not isinstance(body.get("draft"), dict) or not isinstance(body.get("run_id"), str) or not body["run_id"].strip():
            raise DomainError(422, "A validated draft and nonempty run_id are required")
        result = app.state.service.create_requirement(body["draft"], body["run_id"], generation_metadata=body.get("generation_metadata"))
        # Regeneration returns a pending proposal instead of replacing human work.
        return get(result["id"])

    @router.get("/requirements/{rid}/evidence")
    def evidence(rid: str):
        record = get(rid)
        return {"links": record["evidence_links"], "evidence": record["evidence"], "assumptions": record["assumptions"], "uncertainties": record["uncertainties"]}

    @router.patch("/requirements/{rid}", response_model=RequirementDetail)
    def edit(rid: str, request: EditRequest):
        return app.state.service.edit_requirement(rid, request)

    @router.post("/requirements/{rid}/decisions", response_model=RequirementDetail)
    def decide(rid: str, request: DecisionRequest):
        return app.state.service.decide_requirement(rid, request)

    @router.post("/requirements/{rid}/transition")
    def legacy_transition(rid: str, body: dict):
        if any(body.get(field) is not None and body[field] != rid for field in ("id", "req_id")):
            raise DomainError(422, "Route and body requirement IDs differ")
        actions = {"PM_REVIEW": "start_review", "APPROVED": "approve", "REJECTED": "reject", "POSTPONED": "defer"}
        if not isinstance(body.get("to_status"), str) or body["to_status"] not in actions:
            raise DomainError(422, "Invalid legacy transition")
        adapted = dict(body)
        adapted["id"] = adapted.pop("req_id", None) or adapted.get("id")
        adapted["actor_id"] = adapted.pop("actor", "")
        adapted["action"] = actions[adapted.pop("to_status")]
        return decide(rid, DecisionRequest.model_validate(adapted))

    @router.get("/requirements/{rid}/history")
    def history(rid: str):
        data = state()
        record = get(rid, data)
        related = {rid, "baseline", record.get("run_id"), record.get("signal_id")}
        for revision in data["revisions"][rid]:
            payload = revision.get("payload", revision)
            related.update(link.get("signal_id") for link in payload.get("finding_links", []))
            for link in payload.get("evidence_links", []):
                related.update((link.get("source_id"), link.get("record_id")))
            related.update((payload.get("run_id"), payload.get("signal_id")))
        related.discard(None)
        # Full chain prefix accompanies filtered view for independent verification.
        return {"revisions": data["revisions"][rid], "events": [e for e in data["events"] if e["entity_id"] in related or any(e.get("after", {}).get(k) in related for k in ("run_id", "signal_id") if isinstance(e.get("after"), dict))], "audit": audit_bundle(data)}

    @router.get("/requirements/{rid}/history/export")
    def history_export(rid: str, format: str = Query("json", pattern="^json$")):
        filename = quote(f"requirement-{rid}-history.json", safe="")
        return JSONResponse(history(rid), headers={"Content-Disposition": f"attachment; filename=\"requirement-history.json\"; filename*=UTF-8''{filename}"})

    @router.post("/requirements/{rid}/challenge-applications", response_model=RequirementDetail)
    def apply(rid: str, request: ApplyRequest):
        return app.state.service.edit_requirement(rid, request, apply=True)

    @router.post("/freezes")
    def freeze(request: FreezeRequest):
        return app.state.service.freeze(request)

    def frozen(fid, data=None):
        snapshot = (state() if data is None else data)["freezes"].get(fid)
        if not snapshot:
            raise DomainError(404, "Freeze not found")
        return snapshot

    @router.get("/freezes/{fid}")
    def freeze_detail(fid: str):
        return frozen(fid)

    @router.get("/freezes/{fid}/export")
    def freeze_export(fid: str, format: str = Query("json", pattern="^(json|markdown)$")):
        return Response(export_freeze(frozen(fid), format), media_type="application/json" if format == "json" else "text/markdown")

    @router.get("/freezes/{fid}/audit")
    def freeze_audit(fid: str):
        data = state()
        return audit_bundle(data, frozen(fid, data)["checkpoint"])

    @router.get("/sources")
    def sources():
        return source_call("list_sources")

    @router.get("/sources/{sid}")
    def source(sid: str):
        return source_call("get", "source", sid)

    @router.get("/sources/{sid}/file")
    def source_file(sid: str):
        import config
        metadata = source_call("get", "source", sid)
        root = config.RAW_DATA_DIR.resolve()
        filename = metadata.get("filename")
        if not isinstance(filename, str) or not filename.strip():
            raise DomainError(404, "Source filename is unavailable")
        path = (root / filename).resolve()
        if not path.is_relative_to(root) or path.suffix.lower() != ".pdf" or not path.is_file():
            raise DomainError(404, "Source PDF is not in the imported allowlist")
        return FileResponse(path, media_type="application/pdf")

    @router.get("/source-records/{sid}")
    def source_record(sid: str):
        return source_call("get", "record", sid)

    @router.get("/document-chunks/{sid}")
    def chunk(sid: str):
        return source_call("get", "chunk", sid)

    @router.get("/facts/{sid}")
    def fact(sid: str):
        return source_call("get", "fact", sid)

    @router.get("/signals")
    def signals():
        provider = require_source_provider()
        if not hasattr(provider, "list_signals"):
            raise DomainError(503, "Signal provider is not integrated")
        return source_call("list_signals")

    @router.post("/evidence/{operation}")
    def retrieve_evidence(operation: str, body: dict):
        capabilities = {
            "feedback-search": ("search_feedback", ("query", "scope", "limit")),
            "survey-facts": ("get_survey_facts", ("topic", "scope")),
            "sales-facts": ("get_sales_facts", ("scope", "year")),
            "document-search": ("search_document_chunks", ("query", "scope", "document_kind", "limit")),
            "cohort-counts": ("get_comment_cohort_counts", ("scope",)),
        }
        if operation not in capabilities:
            raise DomainError(404, "Unknown evidence retrieval operation")
        method, fields = capabilities[operation]
        if set(body) - set(fields):
            raise DomainError(422, "Unexpected evidence retrieval fields")
        scope = body.get("scope", {})
        if not isinstance(scope, dict) or set(scope) - {"regions", "derivatives", "powertrains", "source_versions", "signal_ids", "topics"}:
            raise DomainError(422, "scope must contain only supported retrieval filters")
        validate_scope(scope)
        arguments = {"scope": scope}
        for field in ("query", "topic"):
            if field in fields:
                value = body.get(field)
                if not isinstance(value, str) or not value.strip():
                    raise DomainError(422, f"A nonempty {field} is required")
                arguments[field] = value.strip()
        if "limit" in fields:
            limit = body.get("limit", 6 if operation == "document-search" else 20)
            if type(limit) is not int or not 1 <= limit <= 200:
                raise DomainError(422, "limit must be an integer between 1 and 200")
            arguments["limit"] = limit
        if "year" in fields:
            year = body.get("year", 2030)
            if type(year) is not int or not 1900 <= year <= 2200:
                raise DomainError(422, "year must be an integer between 1900 and 2200")
            arguments["year"] = year
        if "document_kind" in fields:
            kind = body.get("document_kind")
            if kind is not None and (not isinstance(kind, str) or not kind.strip()):
                raise DomainError(422, "document_kind must be a nonempty string")
            arguments["document_kind"] = kind
        callback = getattr(require_source_provider(), method, None)
        if not callable(callback):
            raise DomainError(503, "Evidence retrieval capability is not integrated")
        try:
            result = callback(**arguments)
            if not isinstance(result, (dict, list)) or (isinstance(result, list) and any(not isinstance(item, dict) for item in result)):
                raise DomainError(503, "Evidence provider returned an invalid retrieval result")
            json.dumps(result, allow_nan=False)
            return result
        except DomainError:
            raise
        except Exception as exc:
            raise DomainError(503, "Evidence retrieval provider is unavailable") from exc

    def review_entity(kind, entity_id, data=None):
        if not isinstance(entity_id, str) or not entity_id.strip():
            raise DomainError(422, "A nonempty entity_id is required")
        if kind == "requirement":
            record = (data if data is not None else state())["requirements"].get(entity_id)
            if record is None:
                raise DomainError(404, "Requirement not found")
            return record
        entity = source_call("get", "signal", entity_id)
        if type(entity.get("revision")) is not int or entity["revision"] < 1:
            raise DomainError(503, "Signal provider must expose a positive revision")
        return entity

    @router.post("/review-threads")
    def create_thread(body: dict):
        if body.get("entity_kind") not in ("requirement", "signal"):
            raise DomainError(422, "entity_kind must be requirement or signal")
        entity = review_entity(body["entity_kind"], body.get("entity_id"))
        identity = MutationRequest.model_validate({k: body[k] for k in MutationRequest.model_fields if k in body})
        app.state.service.guard(entity, body["entity_id"], identity)
        thread = {"id": str(uuid.uuid4()), "entity_kind": body["entity_kind"], "entity_id": body["entity_id"], "actor_id": identity.actor_id, "timestamp": now()}
        def save_thread(data):
            app.state.service.guard(review_entity(thread["entity_kind"], thread["entity_id"], data), thread["entity_id"], identity)
            data["threads"][thread["id"]] = thread
            app.state.service.event(data, "REVIEW_THREAD_CREATED", thread["entity_id"], None, thread, (identity.actor_id, identity.actor_role), identity.rationale, entity_type="ReviewThread")
        app.state.repository.write(save_thread)
        return thread

    @router.get("/review-threads/{thread_id}")
    def thread_detail(thread_id: str):
        data = state()
        thread = data["threads"].get(thread_id)
        if not thread:
            raise DomainError(404, "Thread not found")
        current_revision = review_entity(thread["entity_kind"], thread["entity_id"], data)["revision"]
        turns = [dict(t, stale=t.get("expected_revision") != current_revision) if t["state"] in ("pending", "read_only") else t for t in data["turns"].values() if t.get("thread_id") == thread_id]
        return dict(thread, turns=turns, current_revision=current_revision)

    def change_preview(data, record, patch, expected_revision):
        if type(expected_revision) is not int or expected_revision < 1:
            raise DomainError(422, "Preview requires a positive expected_revision")
        if expected_revision != record["revision"]:
            raise DomainError(409, "Stale preview base")
        allowed = set(RequirementDraft.model_fields) - {"derivation_key", "signal_id"}
        if record.get("provenance_state") == "unresolved_legacy":
            allowed = set(RequirementDraft.model_fields)
        if not isinstance(patch, dict) or not patch or not set(patch) <= allowed:
            raise DomainError(422, "Preview patch contains empty or server-owned fields")
        proposed = {key: record[key] for key in RequirementDraft.model_fields}
        proposed.update(patch)
        proposed = RequirementDraft.model_validate(proposed).model_dump()
        candidate = dict(copy.deepcopy(record), **proposed, status="PM_REVIEW", approved_revision=None)
        candidate.pop("review_confirmation", None)
        resolved_context = app.state.service.context(proposed)
        app.state.service.score(candidate, resolved_context)
        policy = candidate["score"]["policy_version"]
        baseline = data["configs"].get(policy)
        if baseline is not None and canonical_json(baseline) != canonical_json(candidate["scoring_policy"]):
            raise DomainError(409, "Preview differs from scoring baseline configuration")
        old_ranks = rank_requirements(r for r in data["requirements"].values() if r["score"]["policy_version"] == policy)
        updated = [candidate if r["id"] == record["id"] else r for r in data["requirements"].values()]
        new_ranks = rank_requirements(r for r in updated if r["score"]["policy_version"] == policy)
        evidence_ids = {item["record_id"] for item in proposed["evidence_links"]} | {item["source_id"] for item in proposed["evidence_links"]}
        checks = {
            "unresolved_target_provenance_ids": [item["id"] for item in proposed["smart"]["metrics"] + [proposed["smart"]["milestone"]] if item["provenance"] == "SourceBacked" and not set(item["source_references"]) <= evidence_ids],
            "reviewed_target_ids": [item["id"] for item in proposed["smart"]["metrics"] + [proposed["smart"]["milestone"]]],
            "reviewed_source_ids": sorted({item["record_id"] for item in proposed["evidence_links"]}),
            "accepted_assumption_ids": [item["id"] for item in proposed["assumptions"] if item.get("review_state") != "resolved"],
            "missing_required_assumption_ids": sorted(set(candidate["required_assumption_ids"]) - {item["id"] for item in proposed["assumptions"]}),
            "verified_evidence": candidate["validation_state"] == "verified" and bool([l for l in proposed["evidence_links"] if l["relation"] == "supports"]) and all(l["validation_state"] == "verified" for l in proposed["evidence_links"]),
            "strategy_citation_required": candidate["score_context"].get("strategic_fit_level") is not None and (not candidate["score_context"].get("strategy_references") or not candidate["score_context"].get("strategy_rationale", "").strip()),
        }
        return {"mode": "preview", "base_revision": record["revision"], "input_fingerprint": app.state.service.context_fingerprint(resolved_context), "ranking_id": data["rankings"][-1]["id"] if data["rankings"] else None,
                "proposed_content": proposed, "old_score": record["score"], "new_score": candidate["score"], "old_evidence": record["evidence"], "new_evidence": candidate["evidence"],
                "old_ranks": old_ranks, "new_ranks": new_ranks,
                "rank_movement": {rid: {"before": old_ranks.get(rid), "after": new_ranks.get(rid)} for rid in old_ranks.keys() | new_ranks.keys() if old_ranks.get(rid) != new_ranks.get(rid)},
                "unresolved_checks": checks, "human_review_required": True}

    def execute_review(run, thread, body):
        try:
            if not engine or not hasattr(engine, "review"):
                raise DomainError(503, "Role 2 review engine is not integrated")
            base_snapshot = state()
            current = review_entity(thread["entity_kind"], thread["entity_id"], base_snapshot)
            if current["revision"] != body["expected_revision"]:
                raise DomainError(409, "Stale review base")
            # Calls are outside write transactions; apply separately checks the base.
            run.update(state="running", stage="retrieve")
            app.state.repository.write(lambda data: data["runs"].update({run["id"]: copy.deepcopy(run)}))
            arguments = (copy.deepcopy(current), body["question"], thread_detail(thread["id"])["turns"])
            if "progress_callback" in inspect.signature(engine.review).parameters:
                result = engine.review(*arguments, progress_callback=make_progress_callback(run))
            else:
                result = engine.review(*arguments)
            result = ReviewEngineResult.model_validate(result).model_dump()
            json.dumps(result, allow_nan=False)
            # ReviewEngineResult already validates citation and tool-record shapes.
            pinned_links = {canonical_json(link) for link in current.get("evidence_links", [])}
            for citation in result["citations"]:
                if canonical_json(citation) not in pinned_links:
                    provider = app.state.source_provider
                    if provider is None or not hasattr(provider, "verify_reference") or provider.verify_reference(copy.deepcopy(citation)) is not True:
                        raise DomainError(422, "Review citation does not resolve to an authoritative source version")
            preview = None
            patch = result.get("patch")
            if patch is not None:
                if thread["entity_kind"] != "requirement":
                    raise DomainError(422, "Signal review is read-only")
                patch = engine_review_patch(current, patch)
                result["patch"] = patch
                preview = change_preview(base_snapshot, current, patch, body["expected_revision"])
            turn = {"id": str(uuid.uuid4()), "thread_id": thread["id"], "entity_id": thread["entity_id"], "expected_revision": body["expected_revision"], "run_id": run["id"], "question": body["question"], "answer": result["answer"], "citations": result.get("citations", []), "tool_records": result.get("tool_records", []), "patch": result.get("patch"), "state": "pending" if result.get("patch") else "read_only"}
            turn.update(coverage_gaps=result["coverage_gaps"], model_metadata=result["model_metadata"], prompt_metadata=result["prompt_metadata"])
            turn["score_preview"] = preview["new_score"] if preview else None
            turn["change_preview"] = preview
            if preview:
                turn["input_fingerprint"] = preview["input_fingerprint"]
            def save_review(data):
                latest = review_entity(thread["entity_kind"], thread["entity_id"], data)
                turn["stale"] = latest["revision"] != turn["expected_revision"]
                data["turns"][turn["id"]] = turn
                app.state.service.event(data, "CHALLENGE_COMPLETED", thread["entity_id"], None, turn, (body["actor_id"], body["actor_role"]), body["rationale"])
            app.state.repository.write(save_review)
            run.update(state="succeeded", stage="completed", progress=1, result_ids=[turn["id"]], model_metadata=result["model_metadata"], prompt_metadata=result["prompt_metadata"])
        except Exception as exc:
            run.update(state="failed", stage="failed", failures=[str(exc)])
        app.state.repository.write(lambda data: data["runs"].update({run["id"]: copy.deepcopy(run)}))

    def admit_review(thread, body, tasks, new_thread=False, request=None):
        thread_id = thread["id"]
        request = request or MutationRequest.model_validate({k: body[k] for k in MutationRequest.model_fields if k in body})
        if not isinstance(body.get("question"), str) or not body["question"].strip():
            raise DomainError(422, "A nonempty question is required")
        body.update(request.model_dump())
        run = {"id": str(uuid.uuid4()), "kind": "challenge", "thread_id": thread_id, "entity_kind": thread["entity_kind"], "entity_id": thread["entity_id"], "state": "queued", "stage": "retrieve", "progress": 0, "failures": [], "result_ids": [], "mode": "live" if engine else "unavailable"}
        def start(data):
            if any(r.get("thread_id") == thread_id and r["state"] in ("queued", "running") for r in data["runs"].values()):
                raise DomainError(409, "Another review turn is active in this thread")
            app.state.service.guard(review_entity(thread["entity_kind"], thread["entity_id"], data), thread["entity_id"], request)
            if new_thread:
                data["threads"][thread_id] = thread
                app.state.service.event(data, "REVIEW_THREAD_CREATED", thread["entity_id"], None, thread, (request.actor_id, request.actor_role), request.rationale, entity_type="ReviewThread")
            data["runs"][run["id"]] = run
            app.state.service.event(data, "CHALLENGE_REQUESTED", thread["entity_id"], None, dict(body, run_id=run["id"]), (request.actor_id, request.actor_role), request.rationale)
        app.state.repository.write(start)
        tasks.add_task(execute_review, copy.deepcopy(run), thread, body)
        return run

    @router.post("/review-threads/{thread_id}/turns")
    def review_turn(thread_id: str, body: dict, tasks: BackgroundTasks):
        return admit_review(thread_detail(thread_id), body, tasks)

    @router.post("/requirements/{rid}/challenges")
    def challenge(rid: str, body: dict, tasks: BackgroundTasks):
        if not isinstance(body.get("question"), str) or not body["question"].strip():
            raise DomainError(422, "A nonempty question is required")
        request = MutationRequest.model_validate({k: body[k] for k in MutationRequest.model_fields if k in body})
        thread = {"id": str(uuid.uuid4()), "entity_kind": "requirement", "entity_id": rid, "actor_id": request.actor_id, "timestamp": now()}
        return admit_review(thread, body, tasks, new_thread=True, request=request)

    def proposal_for_requirement(data, rid, turn_id):
        get(rid, data)
        turn = data["turns"].get(turn_id)
        thread = data["threads"].get(turn.get("thread_id")) if turn else None
        if not turn or turn.get("entity_id") != rid or turn.get("patch") is None or (thread and thread["entity_kind"] != "requirement"):
            raise DomainError(404, "Requirement proposal not found")
        return turn

    def proposal_view(data, rid, turn):
        result = copy.deepcopy(turn)
        result["stale"] = result.get("expected_revision") != get(rid, data)["revision"]
        return result

    @router.get("/requirements/{rid}/proposals")
    def requirement_proposals(rid: str, proposal_state: str | None = Query(None, alias="state", pattern="^(pending|discarded|applied)$"), limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
        data = state()
        get(rid, data)
        proposals = []
        for turn in data["turns"].values():
            if turn.get("entity_id") != rid or turn.get("patch") is None or (proposal_state and turn["state"] != proposal_state):
                continue
            try:
                proposal_for_requirement(data, rid, turn["id"])
            except DomainError as exc:
                if exc.status == 404:
                    continue
                raise
            proposals.append(proposal_view(data, rid, turn))
        return {"items": proposals[offset:offset + limit], "total": len(proposals), "offset": offset}

    @router.get("/requirements/{rid}/proposals/{turn_id}")
    def requirement_proposal(rid: str, turn_id: str):
        data = state()
        return proposal_view(data, rid, proposal_for_requirement(data, rid, turn_id))

    def discard_proposal(entity_kind, entity_id, turn_id, request, thread_id=None):
        app.state.service.guard_identity(entity_id, request)
        payload = request.model_dump()
        def discard_turn(data):
            if thread_id is None:
                turn = proposal_for_requirement(data, entity_id, turn_id)
            else:
                turn = data["turns"].get(turn_id)
                if not turn or turn.get("thread_id") != thread_id:
                    raise DomainError(404, "Turn not found")
            if turn["state"] == "discarded":
                if not same_snapshot(turn.get("discard_request"), payload):
                    raise DomainError(409, "Conflicting discard retry")
                return turn
            if turn["state"] != "pending":
                raise DomainError(409, "Suggestion is not pending")
            app.state.service.guard(review_entity(entity_kind, entity_id, data), entity_id, request)
            before = copy.deepcopy(turn)
            turn["state"] = "discarded"
            turn["discard_request"] = copy.deepcopy(payload)
            app.state.service.event(data, "SUGGESTION_DISCARDED", entity_id, before, turn, (request.actor_id, request.actor_role), request.rationale)
            return turn
        return app.state.repository.write(discard_turn)

    @router.post("/requirements/{rid}/proposals/{turn_id}/discard")
    def discard_requirement_proposal(rid: str, turn_id: str, request: MutationRequest):
        return discard_proposal("requirement", rid, turn_id, request)

    @router.post("/review-threads/{thread_id}/turns/{turn_id}/discard")
    def discard(thread_id: str, turn_id: str, request: MutationRequest):
        thread = thread_detail(thread_id)
        return discard_proposal(thread["entity_kind"], thread["entity_id"], turn_id, request, thread_id)

    @router.post("/scoring/preview")
    def preview(body: dict):
        try:
            if "requirement_id" in body:
                data = state()
                record = get(body["requirement_id"], data)
                if "patch" in body:
                    return change_preview(data, record, body["patch"], body.get("expected_revision"))
                context = copy.deepcopy(record["score_context"])
                context["weights"] = body.get("weights", context.get("weights", {"customer": .4, "reach": .25, "strategy": .2, "ease": .15}))
                result = calculate_score(context)
                updated = [copy.deepcopy(r) for r in data["requirements"].values()]
                for row in updated:
                    if row["id"] == record["id"]:
                        row["score"] = result
                policy = record["score"]["policy_version"]
                old_ranks = rank_requirements(r for r in data["requirements"].values() if r["score"]["policy_version"] == policy)
                new_ranks = rank_requirements(r for r in updated if r["score"]["policy_version"] == policy)
                return {"mode": "preview", "old_score": record["score"], "new_score": result, "old_ranks": old_ranks, "new_ranks": new_ranks, "rank_movement": {rid: {"before": old_ranks[rid], "after": new_ranks[rid]} for rid in old_ranks if old_ranks[rid] != new_ranks[rid]}}
            result = calculate_score(body)
        except (ValueError, TypeError, AttributeError, KeyError) as exc:
            raise DomainError(422, "Invalid scoring context: " + str(exc)) from exc
        return {"mode": "preview", "score": result}

    def execute_analysis(run, body):
        try:
            if not engine:
                raise DomainError(503, "Role 1/2 engine adapter is not integrated")
            drafts = invoke_adapter(engine.analyze, body, run)
            metadata = {"model_metadata": {}, "prompt_metadata": {}}
            if isinstance(drafts, dict):
                # Validate envelope metadata independently; malformed individual
                # drafts must not discard valid siblings already supplied.
                envelope_drafts = drafts.get("drafts")
                if not isinstance(envelope_drafts, list):
                    raise DomainError(422, "Analysis envelope drafts must be a list")
                result = AnalysisEngineResult.model_validate(dict(drafts, drafts=[])).model_dump()
                json.dumps(result, allow_nan=False)
                drafts = envelope_drafts
                metadata = {key: result[key] for key in metadata}
            run.update(copy.deepcopy(metadata))
            limit = body.get("max_drafts", max_analysis_drafts)
            for index, draft in enumerate(islice(drafts, limit + 1)):
                if index == limit:
                    run["failures"].append("Engine output exceeds requested draft limit")
                    break
                try:
                    result = app.state.service.create_requirement(draft, run["id"], generation_metadata=metadata)
                    if result["id"] not in run["result_ids"]:
                        run["result_ids"].append(result["id"])
                    turn_id = result.get("pending_turn_id")
                    if turn_id and turn_id not in run["pending_turn_ids"]:
                        run["pending_turn_ids"].append(turn_id)
                except (ValueError, DomainError) as exc:
                    run["failures"].append(str(exc))
            run.update(state="partial" if run["failures"] and run["result_ids"] else "failed" if run["failures"] else "succeeded", stage="completed", progress=1)
        except Exception as exc:
            run["failures"].append(str(exc))
            run.update(state="partial" if run["result_ids"] else "failed", stage="failed")
        finally:
            try:
                app.state.repository.write(lambda data: data["runs"].update({run["id"]: copy.deepcopy(run)}))
            finally:
                run_lock.release()

    @router.post("/analysis-runs")
    def start_run(body: dict, tasks: BackgroundTasks):
        validate_scope(body)
        if engine and not run_lock.acquire(blocking=False):
            raise DomainError(409, "Another analysis or import run is active")
        # Admission happens before returning a run, never inside the worker.
        run = {"id": str(uuid.uuid4()), "kind": "analysis", "state": "running" if engine else "failed", "stage": "import_check" if engine else "configuration", "mode": "live" if engine else "unavailable", "progress": 0, "failures": [] if engine else ["Role 1/2 engine adapter is not integrated"], "result_ids": [], "pending_turn_ids": [], "scope": body, "timestamp": now()}
        try:
            app.state.repository.write(lambda data: data["runs"].update({run["id"]: copy.deepcopy(run)}))
        except Exception as exc:
            if engine:
                run_lock.release()
            raise DomainError(503, "Run admission persistence is unavailable") from exc
        if engine:
            tasks.add_task(execute_analysis, copy.deepcopy(run), body)
        return run

    def validate_scope(body):
        try:
            json.dumps(body, allow_nan=False)
        except (ValueError, TypeError) as exc:
            raise DomainError(422, "Run scope must contain finite JSON metadata") from exc
        for key in ("regions", "derivatives", "powertrains", "source_versions", "signal_ids", "topics"):
            if key in body and (not isinstance(body[key], list) or any(not isinstance(v, str) or not v.strip() for v in body[key])):
                raise DomainError(422, f"{key} must be a list of nonempty strings")
        if "max_drafts" in body and (type(body["max_drafts"]) is not int or not 1 <= body["max_drafts"] <= max_analysis_drafts):
            raise DomainError(422, f"max_drafts must be an integer in 1..{max_analysis_drafts}")
        for key in ("refresh", "use_cache"):
            if key in body and type(body[key]) is not bool:
                raise DomainError(422, f"{key} must be boolean")

    def make_progress_callback(run):
        def progress_callback(stage, progress, mode=None):
            if not isinstance(stage, str) or not stage.strip() or isinstance(progress, bool) or not isinstance(progress, (int, float)) or not math.isfinite(progress) or not 0 <= progress <= 1:
                raise DomainError(422, "Invalid adapter progress")
            if mode is not None and mode not in ("live", "cached", "partial", "unavailable"):
                raise DomainError(422, "Invalid adapter mode")
            run.update(stage=stage, progress=progress)
            if mode is not None:
                run["mode"] = mode
            app.state.repository.write(lambda data: data["runs"].update({run["id"]: copy.deepcopy(run)}))
        return progress_callback

    def invoke_adapter(method, body, run):
        progress_callback = make_progress_callback(run)
        # Scope and bounds are admitted by the API. Adapters may inspect them,
        # but cannot rewrite the request used for later governance checks.
        body = copy.deepcopy(body)
        if "progress_callback" in inspect.signature(method).parameters:
            return method(body, progress_callback=progress_callback)
        return method(body)

    def execute_import(run, body):
        try:
            transactional = all(callable(getattr(import_service, name, None)) for name in ("prepare_import", "commit_import"))
            if transactional:
                prepared = invoke_adapter(import_service.prepare_import, body, run)
            elif isinstance(app.state.repository, Neo4jRepository):
                raise DomainError(503, "Neo4j imports require prepare_import and commit_import; independently committed imports are unsafe")
            else:
                prepared = invoke_adapter(import_service.import_data, body, run)
            def persist(data, tx):
                audited_snapshots = {}
                emitted_events = set()
                def audit_sink(action, entity_id, before, after, rationale):
                    if action not in {"SOURCE_IMPORTED", "SIGNAL_CONSOLIDATED"}:
                        raise DomainError(422, "Unsupported source import audit action")
                    if not isinstance(entity_id, str) or not entity_id.strip() or not isinstance(rationale, str) or not rationale.strip():
                        raise DomainError(422, "Source audit requires a nonempty identity and rationale")
                    if not isinstance(after, dict) or after.get("id") != entity_id or (before is not None and (not isinstance(before, dict) or before.get("id") != entity_id)):
                        raise DomainError(422, "Source audit requires matching provenance snapshots")
                    if action == "SOURCE_IMPORTED" and (not isinstance(after.get("source_version"), str) or not after["source_version"].strip()):
                        raise DomainError(422, "Source audit requires an immutable source version")
                    if action == "SIGNAL_CONSOLIDATED" and (type(after.get("revision")) is not int or after["revision"] < 1):
                        raise DomainError(422, "Signal audit requires a positive integer revision")
                    payload = {"action": action, "entity_id": entity_id, "before": before, "after": after, "rationale": rationale}
                    signature = canonical_json(payload)
                    if signature in emitted_events:
                        return
                    app.state.service.event(data, action, entity_id, before, after, ("import-service", "AI_SYSTEM"), rationale)
                    emitted_events.add(signature)
                    audited_snapshots.setdefault((action, entity_id), []).append(copy.deepcopy(after))
                result = import_service.commit_import(prepared, tx=tx, audit_sink=audit_sink) if transactional else prepared
                # Validate before attaching adapter-owned metadata to the durable run.
                # Failed-run persistence must remain possible even for corrupt outputs.
                try:
                    json.dumps(result, sort_keys=True, allow_nan=False)
                except (ValueError, TypeError) as exc:
                    raise DomainError(422, "Import adapter result must be finite JSON metadata") from exc
                if not isinstance(result, dict) or not isinstance(result.get("result_ids", []), list) or not isinstance(result.get("failures", []), list):
                    raise DomainError(422, "Import adapter returned an invalid result")
                if transactional and not result.get("result_ids") and not result.get("failures"):
                    raise DomainError(422, "Import adapter must report imported results or explicit failures")
                for key in ("result_ids", "failures"):
                    if any(not isinstance(value, str) or not value.strip() for value in result.get(key, [])):
                        raise DomainError(422, f"Import adapter {key} must contain nonempty strings")
                for key in ("sources", "signals"):
                    snapshots = result.get(key, [])
                    if not isinstance(snapshots, list) or any(not isinstance(v, dict) or not isinstance(v.get("id"), str) or not v["id"].strip() for v in snapshots):
                        raise DomainError(422, "Import adapter snapshots require IDs")
                    if len({snapshot["id"] for snapshot in snapshots}) != len(snapshots):
                        raise DomainError(422, "Import adapter snapshots contain duplicate IDs")
                    if key == "sources" and any(not isinstance(snapshot.get("source_version"), str) or not snapshot["source_version"].strip() for snapshot in snapshots):
                        raise DomainError(422, "Imported sources require immutable versions")
                    if key == "signals" and any(type(snapshot.get("revision")) is not int or snapshot["revision"] < 1 for snapshot in snapshots):
                        raise DomainError(422, "Imported signals require positive integer revisions")
                    run[key] = copy.deepcopy(snapshots)
                run.update(result_ids=result.get("result_ids", []), failures=result.get("failures", []))
                for key in ("counts", "import_run_id", "evidence_manifest"):
                    if key in result:
                        run[key] = copy.deepcopy(result[key])
                run.update(state="partial" if run["failures"] and run["result_ids"] else "failed" if run["failures"] else "succeeded", stage="completed", progress=1)
                data["runs"][run["id"]] = copy.deepcopy(run)
                app.state.service.event(data, "SOURCE_IMPORTED", run["id"], None, run, ("import-service", "AI_SYSTEM"), "Configured source import completed", entity_type="ImportRun")
                for key, action in (("sources", "SOURCE_IMPORTED"), ("signals", "SIGNAL_CONSOLIDATED")):
                    for snapshot in run[key]:
                        matching = any(all(key in emitted and same_snapshot(value, emitted[key]) for key, value in snapshot.items())
                                       for emitted in audited_snapshots.get((action, snapshot["id"]), []))
                        if not matching:
                            audit_sink(action, snapshot["id"], None, dict(snapshot, run_id=run["id"]), "Versioned import metadata recorded")
            app.state.repository.write_with_transaction(persist)
        except Exception as exc:
            run.update(state="failed", stage="failed", failures=[str(exc)], result_ids=[])
            run.pop("sources", None)
            run.pop("signals", None)
            app.state.repository.write(lambda data: data["runs"].update({run["id"]: copy.deepcopy(run)}))
        finally:
            run_lock.release()

    @router.post("/import-runs")
    def start_import(body: dict, tasks: BackgroundTasks):
        validate_scope(body)
        if import_service and not run_lock.acquire(blocking=False):
            raise DomainError(409, "Another analysis or import run is active")
        run = {"id": str(uuid.uuid4()), "kind": "import", "state": "running" if import_service else "failed", "stage": "import" if import_service else "configuration", "mode": "live" if import_service else "unavailable", "progress": 0, "failures": [] if import_service else ["Role 1 import adapter is not integrated"], "result_ids": [], "scope": body, "timestamp": now()}
        try:
            app.state.repository.write(lambda data: data["runs"].update({run["id"]: copy.deepcopy(run)}))
        except Exception as exc:
            if import_service:
                run_lock.release()
            raise DomainError(503, "Run admission persistence is unavailable") from exc
        if import_service:
            tasks.add_task(execute_import, copy.deepcopy(run), body)
        return run

    @router.get("/analysis-runs/{run_id}")
    def run_status(run_id: str):
        run = state()["runs"].get(run_id)
        if not run:
            raise DomainError(404, "Run not found")
        return run

    app.include_router(router, prefix=api_prefix)
    if api_prefix:
        app.include_router(router, include_in_schema=False)  # legacy URL aliases
    return app


from config import API_PREFIX

app = create_app(api_prefix=API_PREFIX)
