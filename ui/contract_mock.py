"""
Backend contract mock: serves Role 3's own contract examples (contracts/fixture_case.json)
through the same normalisers as the live client, with the same interface as ApiClient.

The fixture is synthetic ("fixture_kind": "synthetic_backend_contract"); the UI labels this
mode "Backend contract mock (synthetic)". It shows exactly what the real API sends, so a
mismatch between the UI and the backend contract shows up here before integration.

The fixture is a timeline: requirement_detail_example is revision 1 (DRAFT); the list, freeze
and export examples come from after approval (revision 2). The mock starts at the detail and
replays the history only up to that revision, so the PM can make the approval in memory.

Simulated server behaviour mirrors services/api.py and services/decisions.py on origin/backend:
revision +1 per decision, 409 on a stale expected_revision or ranking, 422 on a blank
rationale, an invalid transition or an incomplete approval review. Scores are never
calculated: a PM override replaces the effective score, as the backend reports it, and an
edit keeps the last server score (the real backend rescores). Challenge, apply, discard and
analysis answer 501: not simulated.
"""
from __future__ import annotations

import copy
import json
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ui.client import (ApiError, decision_payload, freeze_payload, mutation_payload, normalize_detail,
                       normalize_evidence_response, normalize_history, normalize_list, normalize_thread)

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "contracts" / "fixture_case.json"
TRANSITIONS = {"start_review": "PM_REVIEW", "approve": "APPROVED", "reject": "REJECTED", "defer": "POSTPONED",
               "reopen": "PM_REVIEW"}
# RequirementDraft fields a PM may patch (models.py; derivation_key and signal_id are server-owned)
EDITABLE = {"signal_revision", "title", "description", "problem_statement", "source_scope", "target_scope", "smart",
            "finding_links", "evidence_links", "assumptions", "uncertainties", "factor_proposals", "has_conflict"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_number(value) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


class ContractMockClient:
    synthetic = True
    legacy = False
    contract_mock = True
    source_label = "Backend contract mock (synthetic)"

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else FIXTURE_PATH
        if not self.path.exists():
            raise ApiError(404, f"No backend contract fixture at {self.path}. Check out "
                                "contracts/fixture_case.json from origin/backend.", "fixture_missing")
        self.fx = json.loads(self.path.read_text(encoding="utf-8"))
        self.base_url = f"contract://{self.path.parent.name}/{self.path.name}"
        detail = copy.deepcopy(self.fx["requirement_detail_example"])
        self.reqs = {detail["id"]: detail}
        listing = self.fx["list_example"]
        self.ranking_id = listing.get("ranking_id")
        self.ranks = {item["id"]: item.get("rank") for item in listing.get("items", [])}
        self.events = self._history_until(detail["revision"])
        self.thread = copy.deepcopy(self.fx["review_thread_example"])
        self.proposals = copy.deepcopy(self.fx["proposal_list_example"].get("items", []))
        self.freezes: dict[str, dict] = {}
        self.calls: list[tuple] = []          # (method, payload) log used by tests

    # ---------------------------------------------------------------- helpers
    def _history_until(self, revision: int) -> list[dict]:
        """History events in order, stopping before the first one that belongs to a later revision."""
        out = []
        for e in self.fx["history_example"].get("events", []):
            after = e.get("after") if isinstance(e.get("after"), dict) else {}
            if e.get("entity_type") == "Requirement" and (after.get("revision") or 0) > revision:
                break
            out.append(copy.deepcopy(e))
        return out

    def _get(self, rid: str) -> dict:
        if rid not in self.reqs:
            raise ApiError(404, "Requirement not found")
        return self.reqs[rid]

    def _event(self, action: str, rid: str, before, after, actor_id: str, rationale: str,
               entity_type: str = "Requirement"):
        self.events.append({"id": str(uuid.uuid4()), "entity_id": rid, "entity_type": entity_type,
                            "action": action, "timestamp": _now(), "actor_id": actor_id, "actor_role": "PM_USER",
                            "rationale": rationale, "before": before, "after": after,
                            "sequence": len(self.events) + 2})

    @staticmethod
    def _not_simulated(feature: str):
        raise ApiError(501, f"{feature} is not simulated by the backend contract mock. Use the live API.",
                       "unavailable")

    # ---------------------------------------------------------------- reads
    def health(self):
        return {"mode": "contract-mock", "base_url": self.base_url}

    def list_requirements(self, filters=None, offset=0, limit=50):
        f = filters or {}
        self.calls.append(("list_requirements", dict(f), offset, limit))
        rows = [dict(r, rank=self.ranks.get(r["id"])) for r in self.reqs.values()]
        scope = lambda r, k: r.get("target_scope", {}).get(k, [])  # noqa: E731
        rows = [r for r in rows
                if (not f.get("status") or r["status"] == f["status"])
                and (not f.get("derivative") or f["derivative"] in scope(r, "derivatives"))
                and (not f.get("region") or f["region"] in scope(r, "regions"))
                and (not f.get("powertrain") or f["powertrain"] in scope(r, "powertrains"))
                and (not f.get("evidence") or r["evidence"].get("level") == f["evidence"])
                and (not f.get("q") or f["q"].lower() in (r["title"] + r["description"]).lower())]
        counts = {}
        for r in self.reqs.values():
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        body = dict(self.fx["list_example"], items=copy.deepcopy(rows[offset:offset + limit]), total=len(rows),
                    offset=offset, ranking_id=self.ranking_id, counts=counts)
        return normalize_list(body, offset, limit)

    def get_requirement(self, rid):
        self.calls.append(("get_requirement", rid))
        return normalize_detail(dict(copy.deepcopy(self._get(rid)), rank=self.ranks.get(rid)))

    def list_all_requirements(self, filters=None):
        return self.list_requirements(filters, limit=max(1, len(self.reqs)))

    def get_evidence(self, rid):
        record = self._get(rid)
        body = copy.deepcopy(self.fx["evidence_example"])
        body["assumptions"], body["uncertainties"] = record["assumptions"], record["uncertainties"]
        return normalize_evidence_response(body)

    def _related_events(self, rid) -> list[dict]:
        self._get(rid)
        return [copy.deepcopy(e) for e in self.events if e.get("entity_id") in (rid, "baseline")]

    def get_history(self, rid):
        return normalize_history({"events": self._related_events(rid)}, legacy=False)

    def get_thread(self, rid):
        record = self._get(rid)
        thread = copy.deepcopy(self.thread) if self.thread.get("entity_id") == rid else {"turns": []}
        for t in thread.get("turns", []):          # the server's own stale rule (api.py thread_detail)
            if t.get("state") in ("pending", "read_only"):
                t["stale"] = t.get("expected_revision") != record["revision"]
        proposals = [dict(p, stale=p.get("expected_revision") != record["revision"])
                     for p in copy.deepcopy(self.proposals) if p.get("entity_id") == rid]
        return normalize_thread(thread, proposals, rid)

    def get_source_record(self, record_id):
        for r in self.reqs.values():
            for snap in r.get("source_snapshots", []):
                if snap.get("record_id") == record_id:
                    return {"id": record_id, "kind": "source_snapshot",
                            "file": f"{snap.get('source_id')} @ {snap.get('source_version')}",
                            "locator": snap.get("locator") or {}, "values": snap.get("raw_payload") or {}}
        raise ApiError(404, f"Source record {record_id} is not in the contract fixture.")

    def list_sources(self):
        raise ApiError(501, "The contract fixture has no sources listing.", "unavailable")

    def summary(self):
        return {}

    def get_run(self, run_id):
        raise ApiError(404, "Run not found")

    def get_freeze(self, freeze_id):
        if freeze_id not in self.freezes:
            raise ApiError(404, "Freeze not found")
        return copy.deepcopy(self.freezes[freeze_id])

    # ---------------------------------------------------------------- decisions (simulated)
    def decide(self, rid, action, expected_revision, actor_id, rationale, accepted_assumption_ids=None,
               reviewed_target_ids=None, score_override=None, override_enabled=False, reviewed_source_ids=None):
        body = decision_payload(rid, action, expected_revision, actor_id, rationale, accepted_assumption_ids,
                                reviewed_target_ids, reviewed_source_ids, score_override, override_enabled)
        self.calls.append(("decide", body))
        record = self._get(rid)
        if not str(body["rationale"] or "").strip():
            raise ApiError(422, "rationale: String should have at least 1 character")
        if body["expected_revision"] != record["revision"]:
            raise ApiError(409, "Stale requirement revision")
        active = record["status"] in ("DRAFT", "PM_REVIEW")
        if (action == "reopen" and active) or (action != "reopen" and not active) or \
                (action == "start_review" and record["status"] != "DRAFT"):
            raise ApiError(422, "Invalid state transition")
        before = copy.deepcopy(record)
        after = copy.deepcopy(record)
        if action == "approve":
            self._approve_guard(record, body)
            for t in after["smart"]["metrics"] + [after["smart"]["milestone"]]:
                t["review_state"] = "reviewed"
            for a in after["assumptions"]:
                if a["id"] in body["accepted_assumption_ids"] and a.get("review_state") != "resolved":
                    a["review_state"] = "accepted"
            after["review_confirmation"] = {"targets": body["reviewed_target_ids"],
                                            "sources": body["reviewed_source_ids"],
                                            "assumptions": body["accepted_assumption_ids"],
                                            "actor_id": actor_id, "rationale": rationale}
        else:
            after.pop("review_confirmation", None)
        after["status"] = TRANSITIONS[action]
        after["revision"] = record["revision"] + 1
        after["approved_revision"] = after["revision"] if action == "approve" else None
        after["updated_at"] = _now()
        if "score_override" in body:
            value = body["score_override"]
            if value is not None and not 0 <= value <= 100:
                raise ApiError(422, "score_override: must be between 0 and 100")
            after["score"] = dict(after["score"], override=value,
                                  effective_score=after["score"]["calculated_score"] if value is None else value)
            if after["score"] != before["score"]:
                self._event("SCORE_OVERRIDDEN", rid, before["score"], after["score"], actor_id, rationale)
        self.reqs[rid] = after
        self._event("STATUS_CHANGED", rid, before, after, actor_id, rationale)
        self.ranking_id = str(uuid.uuid4())       # the backend recalculates the ranking on every save
        self._event("RANKING_RECALCULATED", "baseline", None, {"id": self.ranking_id}, actor_id, rationale,
                    entity_type="RankingSnapshot")
        return copy.deepcopy(after)

    @staticmethod
    def _approve_guard(record: dict, body: dict):
        """The review-completeness checks of decisions.approve_guard, with the server's messages."""
        smart = record["smart"]
        targets = {m["id"] for m in smart["metrics"]} | {smart["milestone"]["id"]}
        if not targets <= set(body["reviewed_target_ids"]):
            raise ApiError(422, "PM must review all targets and milestone")
        links = record["evidence_links"]
        if record.get("validation_state") != "verified" or not [l for l in links if l["relation"] == "supports"] \
                or any(l["validation_state"] != "verified" for l in links):
            raise ApiError(422, "Unverified or legacy evidence blocks approval")
        if not {l["record_id"] for l in links} <= set(body["reviewed_source_ids"]):
            raise ApiError(422, "PM must review underlying source assignments")
        outstanding = {a["id"] for a in record["assumptions"] if a.get("review_state") != "resolved"}
        if not outstanding <= set(body["accepted_assumption_ids"]):
            raise ApiError(422, "PM must accept or resolve outstanding assumptions")

    def create_freeze(self, revisions, baseline_ranking_id, actor_id, rationale):
        body = freeze_payload(revisions, baseline_ranking_id, actor_id, rationale)
        self.calls.append(("create_freeze", body))
        if not str(body["rationale"] or "").strip():
            raise ApiError(422, "rationale: String should have at least 1 character")
        if not body["selections"]:
            raise ApiError(422, "selections: Dictionary should have at least 1 item")
        if body["expected_ranking_id"] != self.ranking_id:
            raise ApiError(409, "Ranking changed since the package was prepared; reload and review again")
        for rid, revision in body["selections"].items():
            record = self._get(rid)
            if record["status"] != "APPROVED" or record["approved_revision"] != revision:
                raise ApiError(409, f"{rid} revision {revision} is not the current approved revision")
        freeze = copy.deepcopy(self.fx["freeze_example"])
        freeze.update(id=str(uuid.uuid4()), timestamp=_now(), actor_id=actor_id, rationale=rationale,
                      requirements=[copy.deepcopy(self.reqs[rid]) for rid in body["selections"]])
        freeze["ranking"] = dict(freeze.get("ranking") or {}, id=self.ranking_id)
        self.freezes[freeze["id"]] = freeze
        self._event("REVIEW_PACKAGE_FROZEN", freeze["id"], None, {"selections": body["selections"]}, actor_id,
                    rationale, entity_type="Freeze")
        return dict(copy.deepcopy(freeze), freeze_id=freeze["id"])

    def edit(self, rid, expected_revision, patch, actor_id, rationale):
        """PATCH /requirements/{rid}: RequirementDraft fields only; returns to PM_REVIEW (decisions.edit_requirement).

        The real backend rescores the new content; the mock keeps the last server score unchanged."""
        body = mutation_payload(expected_revision, actor_id, rationale, id=rid, patch=patch)
        self.calls.append(("edit", body))
        record = self._get(rid)
        if not str(rationale or "").strip():
            raise ApiError(422, "rationale: String should have at least 1 character")
        if not patch or not set(patch) <= EDITABLE:
            raise ApiError(422, "Patch includes empty or server-owned fields")
        if expected_revision != record["revision"]:
            raise ApiError(409, "Stale requirement revision")
        for m in (patch.get("smart") or {}).get("metrics", []):
            if m.get("operator") in ("<", "<=", ">=", ">") and not _is_number(m.get("target")):
                raise ApiError(422, "Ordered metric comparisons require a finite numeric target")
        regions = (patch.get("target_scope") or {}).get("regions")
        if regions is not None and (not regions or not set(regions) <= {"US", "EU", "CN", "RoW"}):
            raise ApiError(422, "scope regions must be a nonempty list of names")
        before, after = copy.deepcopy(record), copy.deepcopy(record)
        after.update(copy.deepcopy(patch))
        after.update(status="PM_REVIEW", approved_revision=None, revision=record["revision"] + 1,
                     updated_at=_now())
        after.pop("review_confirmation", None)
        self.reqs[rid] = after
        self._event("CONTENT_CHANGED", rid, before, after, actor_id, rationale)
        self.ranking_id = str(uuid.uuid4())
        return copy.deepcopy(after)

    # ---------------------------------------------------------------- not simulated

    def challenge(self, *a, **k):
        self._not_simulated("The review assistant")

    def apply_suggestion(self, *a, **k):
        self._not_simulated("Applying suggestions")

    def discard_suggestion(self, *a, **k):
        self._not_simulated("Discarding suggestions")

    def start_analysis(self, *a, **k):
        self._not_simulated("Starting an analysis")

    # ---------------------------------------------------------------- downloads: the contract's own payloads
    def download(self, what, fmt, ref=None):
        self.calls.append(("download", what, fmt, ref))
        if what == "sample_list":
            if fmt != "json":
                raise ApiError(501, "The contract fixture only includes the JSON export.", "unavailable")
            data = self.fx["export_example"]
        elif what == "history":
            data = {"revisions": self.fx["history_example"].get("revisions", []),
                    "events": self._related_events(ref)}
        elif what in ("freeze", "audit"):
            freeze = self.get_freeze(ref)
            data = freeze if what == "freeze" else freeze.get("audit", {})
        else:
            raise ApiError(404, "Unknown export.")
        name = f"{what}{'-' + ref if ref else ''}_CONTRACT_MOCK.json"
        return json.dumps(data, ensure_ascii=False, indent=2).encode(), "application/json", name
