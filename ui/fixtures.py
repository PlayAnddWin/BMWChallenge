"""
SYNTHETIC preview data and an in-memory client with the same interface as ApiClient.

Purpose: let Role 4 build and demo the full PM workflow before Role 3's API is live,
and give tests a deterministic backend. Every value here is invented for UI
development (based on the shared Role 1 fixture: FB-FIXTURE-001..003, HVAC access).
Nothing in this file is a BMW finding, and the UI labels this mode as synthetic.

The simulated server behaviour (revisions, 409/422, apply != approve) mirrors the
plan's contract so the UI can be exercised; it is not the production source of rules.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone

from ui.client import ApiError, LEGACY_SCALE, V1_SCALE, normalize_detail, normalize_summary

T0 = datetime(2026, 10, 10, 9, 0, tzinfo=timezone.utc)


def _t(minutes: int) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat(timespec="seconds")


def _factors(customer, reach, strategy, ease, notes):
    w = {"customer_value": 0.40, "addressable_reach": 0.25, "strategic_fit": 0.20, "ease": 0.15}
    vals = {"customer_value": customer, "addressable_reach": reach, "strategic_fit": strategy, "ease": ease}
    labels = {"customer_value": "Customer value", "addressable_reach": "Addressable reach",
              "strategic_fit": "Strategic fit", "ease": "Ease of implementation"}
    return [{"key": k, "label": labels[k], "value": vals[k], "weight": w[k],
             "contribution": round(100 * w[k] * vals[k], 4), "reason": notes.get(k, ""),
             "provisional": k in notes.get("provisional", [])} for k in w]


QUOTES = {
    "FB-FIXTURE-001": "I like the cabin, but temperature adjustment is hard to find.",
    "FB-FIXTURE-002": "Temperature adjustment is hard to find.",
    "FB-FIXTURE-003": "Temperature adjustment is easy to find.",
}

SOURCE_RECORDS = {
    "SR-FIXTURE-ROW-3": {"id": "SR-FIXTURE-ROW-3", "kind": "feedback_row", "file": "G60_feedback_SYNTHETIC.xlsx",
                         "locator": {"sheet": "Feedback_Explorer", "row": 3},
                         "values": {"Feedback_Explorer ID": "FIXTURE-001", "Country": "US", "Engine Type": "BEVE",
                                    "Feedback Type": "Difficult to Use", "Vfc level2 Name": "HVAC",
                                    "Customer Feedback": QUOTES["FB-FIXTURE-001"]}},
    "SR-FIXTURE-ROW-4": {"id": "SR-FIXTURE-ROW-4", "kind": "feedback_row", "file": "G60_feedback_SYNTHETIC.xlsx",
                         "locator": {"sheet": "Feedback_Explorer", "row": 4},
                         "values": {"Feedback_Explorer ID": "FIXTURE-002", "Country": "US", "Engine Type": "BEVE",
                                    "Feedback Type": "Difficult to Use", "Vfc level2 Name": "HVAC",
                                    "Customer Feedback": QUOTES["FB-FIXTURE-002"]}},
    "SR-FIXTURE-ROW-5": {"id": "SR-FIXTURE-ROW-5", "kind": "feedback_row", "file": "G60_feedback_SYNTHETIC.xlsx",
                         "locator": {"sheet": "Feedback_Explorer", "row": 5},
                         "values": {"Feedback_Explorer ID": "FIXTURE-003", "Country": "US", "Engine Type": "ICE",
                                    "Feedback Type": "Likes", "Vfc level2 Name": "HVAC",
                                    "Customer Feedback": QUOTES["FB-FIXTURE-003"]}},
    "FACT-FIXTURE-SURVEY-001": {"id": "FACT-FIXTURE-SURVEY-001", "kind": "survey", "file": "studies_SYNTHETIC.xlsx",
                                "locator": {"sheet": "US_2025", "rows": "12-19", "column": "C"},
                                "values": {"question": "Operation of heater/ AC controls (synthetic)",
                                           "distribution": {"I Hate It": 0.02, "A Failure": 0.03,
                                                            "Unsatisfactory": 0.10, "Satisfactory": 0.30,
                                                            "Excellent": 0.25, "Delightful": 0.15, "I Love It": 0.15},
                                           "respondents": None, "scale": "7-point distribution, N unknown"}},
    "CHUNK-FIXTURE-STRATEGY-001": {"id": "CHUNK-FIXTURE-STRATEGY-001", "kind": "document_chunk",
                                   "file": "strategy_SYNTHETIC.pdf", "locator": {"page": 1},
                                   "values": {"text": "Synthetic strategy passage: intuitive operation of core "
                                                      "comfort functions is a stated product priority."}},
}


def _base_requirements() -> dict:
    reqs = {}

    reqs["REQ-FIX-001"] = {
        "id": "REQ-FIX-001", "revision": 2, "title": "Find temperature controls without searching",
        "description": "Drivers can adjust cabin temperature in at most two actions, without looking for the "
                       "control while driving.",
        "problem_statement": "Owners report that temperature adjustment is hard to find.",
        "topic": "HVAC operation",
        "source_scope": {"derivatives": ["G60"], "regions": ["US"], "powertrains": ["BEV"],
                         "signals": ["SIG-FIXTURE-001@1"]},
        "target_scope": {"derivatives": ["G60 successor"], "regions": ["US", "EU"], "powertrains": ["BEV", "ICE"],
                         "carryover_assumption": "G60 evidence informs the successor; architecture compatibility "
                                                 "is not claimed."},
        "smart": {
            "specific": "Temperature setpoint change from any screen state.",
            "measure": {"id": "T-1", "metric": "Interaction steps to change temperature", "operator": "<=",
                        "value": 2, "unit": "actions", "conditions": "Vehicle in motion, any infotainment screen",
                        "verification": "Usability test with 12 target customers",
                        "provenance": "AIProposed", "review_state": "unreviewed"},
            "milestone": {"id": "M-1", "value": "Concept freeze (G1)", "provenance": "AIProposed",
                          "review_state": "unreviewed"},
            "attainability": "Software/UI change on existing controls (proposal, not a feasibility certificate).",
            "relevance": "Core comfort function used on every drive.",
        },
        "score": {"calculated": 55.0639452948, "effective": 55.0639452948, "override": None,
                  "scale": V1_SCALE, "config_id": "score-v1-fixture", "provisional": False,
                  "range": {"low": 49.8, "high": 60.1, "kind": "provisional_bounds",
                            "label": "Provisional range from unassigned candidate comments"},
                  "factors": _factors(0.6265986324, 0.5, 0.5, 0.5, {
                      "customer_value": "Severity 2/4 (repeated usability friction); 2 of 3 US BEV comments support.",
                      "addressable_reach": "Whole-line 2030 volume 100 of reference 200 (synthetic).",
                      "strategic_fit": "Level 2/4, cites CHUNK-FIXTURE-STRATEGY-001.",
                      "ease": "Complexity 3/5 (existing hardware/packaging change)."}),
                  "rationale": "Customer value drives the score; reach is half of the reference volume."},
        "evidence": {
            "eci": 67.2795359447, "level": "Moderate",
            "components": {"support": 0.2796, "coverage": 1.0, "corroboration": 1.0, "assignment_validation": 0.5},
            "bucket_comment_count": 3, "issue_support_count": 2, "cohort_size": 3,
            "regional_coverage": [{"region": "US", "type": "feedback + study", "status": "covered"},
                                  {"region": "EU", "type": "none", "status": "gap"},
                                  {"region": "CN", "type": "none", "status": "gap"}],
            "quotes": [
                {"id": "FB-FIXTURE-001", "text": QUOTES["FB-FIXTURE-001"], "relation": "supports",
                 "validation": "verified", "locator": "G60 · Feedback_Explorer · row 3",
                 "source_record_id": "SR-FIXTURE-ROW-3", "scope": "G60 · US · BEV"},
                {"id": "FB-FIXTURE-002", "text": QUOTES["FB-FIXTURE-002"], "relation": "supports",
                 "validation": "verified", "locator": "G60 · Feedback_Explorer · row 4",
                 "source_record_id": "SR-FIXTURE-ROW-4", "scope": "G60 · US · BEV"},
                {"id": "FB-FIXTURE-003", "text": QUOTES["FB-FIXTURE-003"], "relation": "contradicts",
                 "validation": "verified", "locator": "G60 · Feedback_Explorer · row 5",
                 "source_record_id": "SR-FIXTURE-ROW-5", "scope": "G60 · US · ICE"},
            ],
            "facts": [{"id": "FACT-FIXTURE-SURVEY-001", "label": "US study: operation of heater/AC controls",
                       "value": "15% unsatisfied or worse (7-point distribution, N unknown)",
                       "relation": "direct", "source_record_id": "FACT-FIXTURE-SURVEY-001"}],
            "documents": [{"id": "CHUNK-FIXTURE-STRATEGY-001", "label": "Strategy passage, page 1",
                           "relation": "context", "source_record_id": "CHUNK-FIXTURE-STRATEGY-001"}],
            "gaps": ["No EU or China owner comments for this issue.", "Survey respondent count unknown."],
        },
        "assumptions": [{"id": "A-1", "text": "Successor keeps a touch-first centre display.", "state": "open"},
                        {"id": "A-2", "text": "US BEV friction generalises to EU owners.", "state": "open"}],
        "uncertainties": ["Respondent N unknown for the study corroboration."],
        "status": "PM_REVIEW", "approved_revision": None, "has_conflict": False,
        "flags": ["proposed_target"], "run_id": "RUN-FIXTURE-1",
        "created_at": _t(0), "updated_at": _t(20),
    }

    r2 = copy.deepcopy(reqs["REQ-FIX-001"])
    r2.update({
        "id": "REQ-FIX-002", "revision": 1, "title": "Climate comfort for rear passengers in China",
        "description": "Rear passengers can set their own climate zone without asking the driver.",
        "problem_statement": "No China owner comments exist; the proposal rests on study context only.",
        "topic": "Rear comfort",
        "source_scope": {"derivatives": ["G68"], "regions": ["CN"], "powertrains": [], "signals": []},
        "target_scope": {"derivatives": ["G60 successor (LWB)"], "regions": ["CN"], "powertrains": ["BEV", "ICE"]},
        "status": "DRAFT", "flags": ["evidence_gap", "proposed_target"],
        "created_at": _t(5), "updated_at": _t(5),
    })
    r2["score"] = {"calculated": 38.5, "effective": 38.5, "override": None, "scale": V1_SCALE,
                   "config_id": "score-v1-fixture", "provisional": True,
                   "range": {"low": 21.0, "high": 61.0, "kind": "unknown_factor_bounds",
                             "label": "Unknown factors evaluated at 0 and 1"},
                   "factors": _factors(0.5, 0.39, 0.5, 0.25, {
                       "customer_value": "Unknown: no owner comments. Provisional midpoint 0.5.",
                       "addressable_reach": "CN whole-line volume (upper-bound proxy, no powertrain split).",
                       "strategic_fit": "Not yet assessed (midpoint).", "ease": "Complexity 4/5 (new subsystem).",
                       "provisional": ["customer_value", "strategic_fit"]}),
                   "rationale": "Mostly provisional: customer value and strategic fit are unknown."}
    r2["evidence"] = {"eci": 18.75, "level": "Low",
                      "components": {"support": 0.0, "coverage": 0.5, "corroboration": 0.0,
                                     "assignment_validation": 0.5},
                      "bucket_comment_count": 0, "issue_support_count": 0, "cohort_size": None,
                      "regional_coverage": [{"region": "CN", "type": "study only", "status": "partial"},
                                            {"region": "US", "type": "none", "status": "not targeted"},
                                            {"region": "EU", "type": "none", "status": "not targeted"}],
                      "quotes": [], "facts": [], "documents": [],
                      "gaps": ["No China free-text feedback is supplied.",
                               "G68 (China) and G60 (EU) are different derivatives; differences may be confounded.",
                               "Study respondent count unknown."]}
    r2["assumptions"] = [{"id": "A-3", "text": "Chauffeur-driven use is common in this segment.", "state": "open"}]
    r2["uncertainties"] = ["No customer comments support this proposal yet."]
    reqs["REQ-FIX-002"] = r2

    r3 = copy.deepcopy(reqs["REQ-FIX-001"])
    r3.update({
        "id": "REQ-FIX-003", "revision": 1, "title": "Physical shortcut for climate and volume",
        "description": "Drivers can change volume and temperature with a physical control.",
        "topic": "Controls", "status": "DRAFT", "has_conflict": True,
        "flags": ["supported_conflict", "proposed_target"],
        "conflict_detail": "Synthetic conflict: some owners prefer touch-only surfaces, others physical controls.",
        "created_at": _t(6), "updated_at": _t(6),
    })
    r3["score"] = copy.deepcopy(r3["score"])
    r3["score"]["calculated"] = r3["score"]["effective"] = 47.2
    r3["evidence"] = copy.deepcopy(r3["evidence"])
    r3["evidence"]["eci"], r3["evidence"]["level"] = 52.0, "Moderate"
    reqs["REQ-FIX-003"] = r3

    r4 = copy.deepcopy(reqs["REQ-FIX-001"])
    r4.update({"id": "REQ-FIX-004", "revision": 3, "title": "Phone charging that does not overheat",
               "description": "Owners can charge their phone wirelessly on any drive without heat warnings.",
               "problem_statement": "Owners report heat warnings while charging on long drives (synthetic).",
               "topic": "Connectivity", "status": "APPROVED", "approved_revision": 3,
               "flags": [], "created_at": _t(1), "updated_at": _t(40)})
    r4["smart"] = copy.deepcopy(r4["smart"])
    r4["smart"]["measure"]["review_state"] = "reviewed"
    r4["smart"]["milestone"]["review_state"] = "reviewed"
    r4["score"] = copy.deepcopy(r4["score"])
    r4["score"]["calculated"] = r4["score"]["effective"] = 61.3
    r4["evidence"] = copy.deepcopy(r4["evidence"])
    r4["evidence"]["eci"], r4["evidence"]["level"] = 74.0, "High"
    r4["assumptions"] = [{"id": "A-4", "text": "Phone sizes stay within today's range.", "state": "accepted",
                          "accepted_rationale": "Reviewed with packaging team (synthetic)."}]
    reqs["REQ-FIX-004"] = r4

    r5 = {"id": "REQ-F70-001", "revision": None, "legacy": True, "title": "Physical owner's manual (legacy)",
          "description": "When I need help, I want a printed manual, so that I can look things up quickly.",
          "topic": "Owner's Manual", "target_scope": {"derivatives": ["F70"], "regions": [], "powertrains": []},
          "source_scope": {"derivatives": ["F70"]},
          "smart": {"measure": {"target": "≥85% satisfied", "provenance": "AIProposed", "review_state": "unreviewed"},
                    "model_flags": {k: True for k in "smart"}},
          "score": {"calculated": 7.32, "effective": 7.32, "override": None, "scale": LEGACY_SCALE,
                    "provisional": True, "factors": [],
                    "range": {"low": 6.9, "high": 7.7, "kind": "legacy_sensitivity",
                              "label": "Legacy ±15% impact sensitivity, not a statistical confidence interval"}},
          "evidence": {"level": "Strong", "legacy_tier": "STRONG", "eci": None,
                       "quotes": [{"id": None, "text": "there is no paper user manual (synthetic legacy example)",
                                   "relation": "supports", "validation": "unverified",
                                   "validation_note": "Legacy quote without an original feedback ID."}],
                       "regional_coverage": [], "gaps": []},
          "gate": "GREEN", "assumptions": [], "uncertainties": [], "status": "PM_REVIEW",
          "has_conflict": False, "flags": ["legacy_unverified"], "allowed_actions": ["reject", "defer"]}
    reqs["REQ-F70-001"] = r5
    return reqs


ALLOWED = {
    "DRAFT": ["start_review", "approve", "reject", "defer"],
    "PM_REVIEW": ["approve", "reject", "defer"],
    "APPROVED": ["reopen"],
    "REJECTED": ["reopen"],
    "POSTPONED": ["reopen"],
}
TRANSITION = {"start_review": "PM_REVIEW", "approve": "APPROVED", "reject": "REJECTED",
              "defer": "POSTPONED", "reopen": "PM_REVIEW"}


class FixtureClient:
    """In-memory stand-in for ApiClient. Same method names, same normalised shapes."""
    synthetic = True
    legacy = False
    base_url = "synthetic://fixtures"

    def __init__(self, extra_records: int = 0):
        self.reqs = _base_requirements()
        self.history: dict[str, list[dict]] = {k: [] for k in self.reqs}
        self.threads: dict[str, dict] = {k: {"id": f"TH-{k}", "turns": []} for k in self.reqs}
        self.runs: dict[str, dict] = {}
        self.freezes: dict[str, dict] = {}
        self.calls: list[tuple] = []          # (method, args) log used by tests
        self._seed_history()
        for i in range(extra_records):        # bulk records for export/pagination tests
            r = copy.deepcopy(self.reqs["REQ-FIX-003"])
            r.update({"id": f"REQ-BULK-{i:03d}", "title": f"Synthetic bulk requirement {i:03d}",
                      "has_conflict": False, "flags": []})
            r["score"] = dict(r["score"], calculated=30 + i % 40, effective=30 + i % 40)
            self.reqs[r["id"]] = r
            self.history[r["id"]] = []
            self.threads[r["id"]] = {"id": f"TH-{r['id']}", "turns": []}
        self.threads["REQ-FIX-001"]["turns"].append({
            "id": "TURN-1", "state": "pending", "base_revision": 2, "time": _t(25), "actor_id": "pm-demo-1",
            "question": "Does this evidence justify a global requirement?",
            "answer": "Only US BEV owners report this issue (FB-FIXTURE-001, FB-FIXTURE-002), and one ICE owner "
                      "finds the control easy (FB-FIXTURE-003). There are no EU or China comments, so a global "
                      "scope is not supported yet.",
            "citations": ["FB-FIXTURE-001", "FB-FIXTURE-002", "FB-FIXTURE-003"],
            "coverage_gaps": ["EU", "CN"],
            "proposed_patch": {"target_scope.regions": {"before": ["US", "EU"], "after": ["US"]},
                               "assumptions[+]": {"before": None,
                                                  "after": "Validate EU applicability in the next clinic."}},
            "score_preview": {"before": 55.06, "after": 52.4, "rank_before": 1, "rank_after": 2},
        })

    def _seed_history(self):
        self.history["REQ-FIX-001"] = [
            {"time": _t(0), "actor_kind": "AI", "actor_id": "RUN-FIXTURE-1 · gpt-4o-mini", "revision": 1,
             "action": "REQUIREMENT_DERIVED", "summary": "Drafted from SIG-FIXTURE-001@1 with 3 cited comments",
             "rationale": "Validated draft: all citations resolve.", "before": None,
             "after": {"status": "DRAFT", "score": 55.06}},
            {"time": _t(1), "actor_kind": "SYSTEM", "actor_id": "scoring v1", "revision": 1,
             "action": "SCORE_CALCULATED", "summary": "Priority 55.06 (v1), rank 1", "rationale": "Baseline config.",
             "before": None, "after": {"score": 55.06, "rank": 1}},
            {"time": _t(20), "actor_kind": "PM", "actor_id": "pm-demo-1", "revision": 2,
             "action": "STATUS_CHANGED", "summary": "Proposed → Under review",
             "rationale": "Starting review for the G1 list.", "before": {"status": "DRAFT"},
             "after": {"status": "PM_REVIEW"}},
        ]
        self.history["REQ-FIX-004"] = [
            {"time": _t(1), "actor_kind": "AI", "actor_id": "RUN-FIXTURE-1", "revision": 1,
             "action": "REQUIREMENT_DERIVED", "summary": "Drafted from SIG-FIXTURE-004@1", "rationale": "Validated.",
             "before": None, "after": {"status": "DRAFT"}},
            {"time": _t(40), "actor_kind": "PM", "actor_id": "pm-demo-1", "revision": 3,
             "action": "STATUS_CHANGED", "summary": "Under review → Approved",
             "rationale": "Targets and assumption A-4 reviewed.", "before": {"status": "PM_REVIEW"},
             "after": {"status": "APPROVED", "approved_revision": 3}},
        ]

    # ---------------------------------------------------------------- helpers
    def _get(self, rid):
        if rid not in self.reqs:
            raise ApiError(404, f"Requirement {rid} not found.", "not_found")
        return self.reqs[rid]

    def _check_revision(self, r, expected):
        if r.get("legacy"):
            return
        if expected != r["revision"]:
            raise ApiError(409, f"This requirement changed (now revision {r['revision']}). Reload to compare "
                                f"with your draft.", "stale_revision", current_revision=r["revision"])

    def _event(self, rid, actor, action, summary, rationale, before, after):
        r = self.reqs[rid]
        self.history[rid].append({"time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                  "actor_kind": "PM", "actor_id": actor, "revision": r.get("revision"),
                                  "action": action, "summary": summary, "rationale": rationale,
                                  "before": before, "after": after})

    def _ranked(self):
        live = [r for r in self.reqs.values() if r["status"] not in ("REJECTED", "POSTPONED")]
        return sorted(live, key=lambda r: (-(r["score"].get("effective") or 0) if not r.get("legacy") else 1, r["id"]))

    # ---------------------------------------------------------------- reads
    def health(self):
        return {"mode": "synthetic", "base_url": self.base_url}

    def list_requirements(self, filters=None, offset=0, limit=50):
        f = filters or {}
        self.calls.append(("list_requirements", f, offset, limit))
        rows = list(self.reqs.values())
        if f.get("derivative"):
            rows = [r for r in rows if any(f["derivative"] in d for d in r["target_scope"].get("derivatives", []))]
        if f.get("status"):
            rows = [r for r in rows if r["status"] == f["status"]]
        if f.get("evidence"):
            rows = [r for r in rows if (r["evidence"].get("level") or "") == f["evidence"]]
        if f.get("region"):
            rows = [r for r in rows if f["region"] in r["target_scope"].get("regions", [])]
        if f.get("q"):
            q = f["q"].lower()
            rows = [r for r in rows if q in (r["title"] + r["id"] + (r.get("description") or "")).lower()]
        rank = {r["id"]: i + 1 for i, r in enumerate(self._ranked())}
        rows.sort(key=lambda r: (rank.get(r["id"], 10_000), r["id"]))
        items = []
        for r in rows[offset:offset + limit]:
            s = normalize_summary({k: v for k, v in r.items()})
            s["rank"] = rank.get(r["id"])
            items.append(s)
        counts = {}
        for r in self.reqs.values():
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        return {"items": items, "total": len(rows), "offset": offset, "limit": limit, "legacy": False,
                "notice": None, "counts": counts}

    def get_requirement(self, rid):
        self.calls.append(("get_requirement", rid))
        r = copy.deepcopy(self._get(rid))
        if not r.get("legacy"):
            r["allowed_actions"] = ALLOWED[r["status"]]
        rank = {x["id"]: i + 1 for i, x in enumerate(self._ranked())}
        r["rank"] = rank.get(rid)
        return normalize_detail(r)

    def list_all_requirements(self, filters=None):
        return self.list_requirements(filters, limit=max(1, len(self.reqs)))

    def get_evidence(self, rid):
        return copy.deepcopy(self._get(rid)["evidence"])

    def get_history(self, rid):
        self._get(rid)
        return copy.deepcopy(self.history.get(rid, []))

    def get_thread(self, rid):
        self._get(rid)
        return copy.deepcopy(self.threads[rid])

    def get_source_record(self, record_id):
        if record_id not in SOURCE_RECORDS:
            raise ApiError(404, f"Source {record_id} not found.", "not_found")
        return copy.deepcopy(SOURCE_RECORDS[record_id])

    def list_sources(self):
        return {"synthetic": True, "files": [
            {"file": "G60_feedback_SYNTHETIC.xlsx", "kind": "feedback", "sha256": "synthetic-1f2e…",
             "rows": 4, "comments": 3, "annotations": 4, "issues": []},
            {"file": "studies_SYNTHETIC.xlsx", "kind": "survey", "sha256": "synthetic-9ab0…", "rows": 8,
             "facts": 1, "issues": ["Respondent counts not supplied (N unknown)."]},
            {"file": "sales_SYNTHETIC.xlsx", "kind": "sales", "sha256": "synthetic-77c1…", "rows": 2, "facts": 2,
             "issues": []},
            {"file": "strategy_SYNTHETIC.pdf", "kind": "document", "sha256": "synthetic-c3d4…", "pages": 1,
             "chunks": 1, "issues": []}],
            "coverage": [{"derivative": "G60", "EU": 0, "US": 3, "CN": 0, "RoW": 0}]}

    def summary(self):
        return {}

    def get_run(self, run_id):
        run = self.runs.get(run_id)
        if not run:
            raise ApiError(404, "Run not found.", "not_found")
        stages = ["import_check", "consolidate", "retrieve", "draft", "validate", "score", "completed"]
        run["polls"] += 1
        i = min(run["polls"], len(stages) - 1)
        run["stage"] = stages[i]
        run["progress"] = {"done": min(run["polls"], run["max_drafts"]), "total": run["max_drafts"]}
        if run["stage"] == "completed":
            run["state"] = "succeeded"
            run["result_ids"] = ["REQ-FIX-001"]
        return copy.deepcopy(run)

    def get_freeze(self, freeze_id):
        return copy.deepcopy(self.freezes[freeze_id])

    # ---------------------------------------------------------------- mutations
    def decide(self, rid, action, expected_revision, actor_id, rationale, accepted_assumption_ids=None,
               reviewed_target_ids=None, score_override=None, override_enabled=False, reviewed_source_ids=None):
        self.calls.append(("decide", rid, action, expected_revision, actor_id, rationale,
                           tuple(accepted_assumption_ids or []), tuple(reviewed_target_ids or []),
                           score_override, override_enabled))
        r = self._get(rid)
        self._check_revision(r, expected_revision)
        if not (rationale or "").strip():
            raise ApiError(422, "A rationale is required.", "validation", details={"rationale": "required"})
        allowed = r.get("allowed_actions") if r.get("legacy") else ALLOWED[r["status"]]
        if action not in allowed:
            raise ApiError(422, f"'{action}' is not allowed from {r['status']}.", "invalid_transition")
        if action == "approve":
            missing = []
            for key in ("measure", "milestone"):
                t = r.get("smart", {}).get(key) or {}
                if t.get("provenance") != "SourceBacked" and t.get("id") and t["id"] not in (reviewed_target_ids or []):
                    missing.append(f"Target {t['id']} ({key}) has not been reviewed.")
            for a in r.get("assumptions", []):
                if a.get("state") == "open" and a["id"] not in (accepted_assumption_ids or []):
                    missing.append(f"Assumption {a['id']} is still open.")
            if missing:
                raise ApiError(422, "Approval needs every proposed target and open assumption reviewed.",
                               "approval_incomplete", details=missing)
        if override_enabled:
            if score_override is None or not (0 <= float(score_override) <= r["score"]["scale"]["max"]):
                raise ApiError(422, f"Override must be between 0 and {r['score']['scale']['max']}.", "validation")
        before = {"status": r["status"], "revision": r.get("revision")}
        r["status"] = TRANSITION[action]
        if not r.get("legacy"):
            r["revision"] += 1
        if action == "approve":
            r["approved_revision"] = r.get("revision")
            for key in ("measure", "milestone"):
                if (r.get("smart", {}).get(key) or {}).get("id") in (reviewed_target_ids or []):
                    r["smart"][key]["review_state"] = "reviewed"
            for a in r.get("assumptions", []):
                if a["id"] in (accepted_assumption_ids or []):
                    a["state"] = "accepted"
        if action == "reopen":
            r["approved_revision"] = None
        if override_enabled:
            r["score"]["override"] = {"value": float(score_override), "rationale": rationale}
            r["score"]["effective"] = float(score_override)
        self._event(rid, actor_id, "STATUS_CHANGED", f"{before['status']} → {r['status']}", rationale, before,
                    {"status": r["status"], "revision": r.get("revision")})
        return {"id": rid, "status": r["status"], "revision": r.get("revision")}

    def edit(self, rid, expected_revision, patch, actor_id, rationale):
        self.calls.append(("edit", rid, expected_revision, json.dumps(patch, sort_keys=True), actor_id, rationale))
        r = self._get(rid)
        if r.get("legacy"):
            raise ApiError(501, "Editing needs the new /api contract.", "unavailable")
        self._check_revision(r, expected_revision)
        if not (rationale or "").strip():
            raise ApiError(422, "A rationale is required.", "validation", details={"rationale": "required"})
        before = {k: copy.deepcopy(r.get(k)) for k in patch if "." not in k}
        for key, value in patch.items():
            if key.startswith("smart."):
                part = key.split(".", 1)[1]
                if isinstance(r["smart"].get(part), dict) and isinstance(value, dict):
                    r["smart"][part].update(value)
                else:
                    r["smart"][part] = value
            else:
                r[key] = value
        r["revision"] += 1
        if r["status"] == "APPROVED":
            r["status"], r["approved_revision"] = "PM_REVIEW", None
        self._event(rid, actor_id, "CONTENT_CHANGED", f"Edited {', '.join(patch)}", rationale, before,
                    {k: patch[k] for k in patch})
        return {"id": rid, "revision": r["revision"], "status": r["status"]}

    def challenge(self, rid, question, expected_revision, actor_id):
        self.calls.append(("challenge", rid, question, expected_revision, actor_id))
        r = self._get(rid)
        self._check_revision(r, expected_revision)
        turn = {"id": f"TURN-{len(self.threads[rid]['turns']) + 1}", "state": "none", "base_revision": r["revision"],
                "time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "actor_id": actor_id,
                "question": question,
                "answer": "Synthetic answer: the cited comments are all from US owners, so the evidence does not "
                          "cover EU or China (FB-FIXTURE-001, FB-FIXTURE-002).",
                "citations": ["FB-FIXTURE-001", "FB-FIXTURE-002"], "coverage_gaps": ["EU", "CN"],
                "proposed_patch": None, "score_preview": None}
        self.threads[rid]["turns"].append(turn)
        self._event(rid, actor_id, "CHALLENGE_COMPLETED", "Asked the review assistant", question, None, None)
        return {"turn_id": turn["id"], "state": "succeeded"}

    def apply_suggestion(self, rid, turn_id, expected_revision, accepted_patch, actor_id, rationale):
        self.calls.append(("apply_suggestion", rid, turn_id, expected_revision, actor_id, rationale))
        r = self._get(rid)
        turn = next((t for t in self.threads[rid]["turns"] if t["id"] == turn_id), None)
        if not turn:
            raise ApiError(404, "Suggestion not found.", "not_found")
        if turn["state"] == "applied":
            return {"id": rid, "revision": turn["applied_revision"], "status": r["status"], "repeat": True}
        if turn["base_revision"] != r["revision"] or expected_revision != r["revision"]:
            turn["state"] = "stale"
            raise ApiError(409, "The requirement changed after this suggestion was made. Ask again to get a "
                                "fresh suggestion.", "stale_suggestion", current_revision=r["revision"])
        if not (rationale or "").strip():
            raise ApiError(422, "A rationale is required.", "validation", details={"rationale": "required"})
        if "target_scope.regions" in accepted_patch:
            r["target_scope"]["regions"] = accepted_patch["target_scope.regions"]["after"]
        if "assumptions[+]" in accepted_patch:
            r["assumptions"].append({"id": f"A-{len(r['assumptions']) + 10}",
                                     "text": accepted_patch["assumptions[+]"]["after"], "state": "open"})
        r["revision"] += 1
        if r["status"] == "APPROVED":
            r["status"], r["approved_revision"] = "PM_REVIEW", None   # applying never approves
        turn["state"], turn["applied_revision"] = "applied", r["revision"]
        self._event(rid, actor_id, "SUGGESTION_APPLIED", f"Applied {turn_id}", rationale, None, accepted_patch)
        return {"id": rid, "revision": r["revision"], "status": r["status"]}

    def discard_suggestion(self, thread_id, turn_id, actor_id, rationale, rid=None, expected_revision=None):
        self.calls.append(("discard_suggestion", thread_id, turn_id, actor_id, rationale))
        for rid, th in self.threads.items():
            if th["id"] == thread_id:
                for t in th["turns"]:
                    if t["id"] == turn_id:
                        t["state"] = "discarded"
                        self._event(rid, actor_id, "SUGGESTION_DISCARDED", f"Discarded {turn_id}", rationale, None, None)
                        return {"turn_id": turn_id, "state": "discarded"}
        raise ApiError(404, "Suggestion not found.", "not_found")

    def start_analysis(self, scope, max_drafts, refresh):
        self.calls.append(("start_analysis", json.dumps(scope, sort_keys=True), max_drafts, refresh))
        if any(r["state"] in ("queued", "running") for r in self.runs.values()):
            raise ApiError(409, "An analysis is already running. Wait for it to finish.", "run_in_progress")
        rid = f"RUN-FIXTURE-{len(self.runs) + 2}"
        self.runs[rid] = {"id": rid, "state": "running", "stage": "import_check", "polls": 0, "scope": scope,
                          "max_drafts": max_drafts, "mode": "cached" if not refresh else "live (synthetic)",
                          "failures": [], "result_ids": [], "progress": {"done": 0, "total": max_drafts}}
        return {"run_id": rid}

    def create_freeze(self, revisions, baseline_ranking_id, actor_id, rationale):
        self.calls.append(("create_freeze", json.dumps(revisions, sort_keys=True), actor_id, rationale))
        if not (rationale or "").strip():
            raise ApiError(422, "A rationale is required.", "validation")
        for sel in revisions:
            r = self._get(sel["id"])
            if r["status"] != "APPROVED" or r.get("revision") != sel["revision"]:
                raise ApiError(409, f"{sel['id']} is not the current approved revision.", "stale_revision")
        fid = f"FRZ-FIXTURE-{len(self.freezes) + 1}"
        self.freezes[fid] = {"id": fid, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                             "actor_id": actor_id, "rationale": rationale,
                             "requirements": [copy.deepcopy(self.reqs[s["id"]]) for s in revisions],
                             "checkpoint": "synthetic-checkpoint"}
        return {"freeze_id": fid}

    def download(self, what, fmt, ref=None):
        self.calls.append(("download", what, fmt, ref))
        if what == "sample_list":
            rows = list(self.reqs.values())
            if fmt == "csv":
                import csv
                import io
                buf = io.StringIO()
                w = csv.writer(buf)
                w.writerow(["id", "revision", "title", "description", "status", "priority", "scale", "eci",
                            "evidence_level", "evidence_links", "assumptions", "uncertainties"])
                for r in rows:
                    w.writerow([r["id"], r.get("revision"), r["title"], r.get("description"), r["status"],
                                r["score"].get("effective"), r["score"]["scale"]["policy"],
                                r["evidence"].get("eci"), r["evidence"].get("level"),
                                json.dumps(r["evidence"].get("quotes", []), ensure_ascii=False),
                                json.dumps(r.get("assumptions", []), ensure_ascii=False),
                                json.dumps(r.get("uncertainties", []), ensure_ascii=False)])
                return buf.getvalue().encode(), "text/csv", "sample_list_SYNTHETIC.csv"
            return (json.dumps({"synthetic": True, "requirements": rows}, ensure_ascii=False, indent=2).encode(),
                    "application/json", "sample_list_SYNTHETIC.json")
        if what in ("freeze", "audit"):
            data = self.freezes[ref] if what == "freeze" else {"synthetic": True, "freeze": ref,
                                                                "events": sum(self.history.values(), [])}
            return json.dumps(data, ensure_ascii=False, indent=2).encode(), "application/json", f"{what}-{ref}.json"
        if what == "history":
            return (json.dumps(self.history.get(ref, []), ensure_ascii=False, indent=2).encode(),
                    "application/json", f"history-{ref}.json")
        raise ApiError(404, "Unknown export.", "not_found")
