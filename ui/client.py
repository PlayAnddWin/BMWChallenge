"""
HTTP adapter between the Streamlit UI and Role 3's API.

Rules this module enforces (Role 4 guide):
- One place that knows API_BASE_URL and API_PREFIX from the shared config.
- One normalisation layer: the UI only ever sees the planned contract shape
  (plan section 6), whether the server speaks the new /api contract or the
  legacy routes of pipeline/07_pm_review_api.py (gate vs gate_color etc.).
- No business rules: no scoring, no decision guards, no database or model calls.
  The client sends exactly what the PM submitted and reports what the server says.
- API failure and an empty successful result stay distinct (ApiError vs []).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

# ----------------------------------------------------------------------------- vocabulary
STATUS_LABELS = {
    "DRAFT": "Proposed",
    "PM_REVIEW": "Under review",
    "APPROVED": "Approved",
    "REJECTED": "Rejected",
    "POSTPONED": "Deferred",
}
ACTION_LABELS = {
    "start_review": "Start review",
    "approve": "Approve",
    "reject": "Reject",
    "defer": "Defer",
    "reopen": "Reopen",
}
LEGACY_ACTION_TO_STATUS = {
    "start_review": "PM_REVIEW",
    "approve": "APPROVED",
    "reject": "REJECTED",
    "defer": "POSTPONED",
    "reopen": "PM_REVIEW",
}
LEGACY_SCALE = {"policy": "legacy-v0", "min": 0, "max": 10,
                "label": "Legacy v0 score (0–10), not comparable with v1"}
V1_SCALE = {"policy": "v1", "min": 0, "max": 100, "label": "Priority v1 (0–100)"}
FACTOR_LABELS = {"customer": "Customer value", "reach": "Addressable reach", "strategy": "Strategic fit",
                 "ease": "Ease of implementation"}
RANGE_LABELS = {
    "provisional_sensitivity": "Provisional sensitivity range: unknown inputs evaluated at their bounds",
    "rubric_unknown_inputs": "Range from unknown rubric inputs",
}
# The backend's review_state values, mapped onto the UI's review vocabulary (components.REVIEW).
TARGET_REVIEW = {"pending": "unreviewed"}
ASSUMPTION_REVIEW = {"pending": "open"}
PM_ROLE = "PM_USER"   # every mutation from this UI is a named human PM


# ----------------------------------------------------------------------------- configuration
def resolve_base_url() -> str:
    """API origin from the shared configuration (README.md#configuration).

    Order: config.API_BASE_URL, then the API_BASE_URL environment variable (so a PM can
    point at a teammate's server without editing files), then localhost:config.API_PORT.
    """
    cfg = None
    try:
        import config  # root config.py, owned by Role 3
        cfg = config
    except Exception:  # config import problems must not break fixture mode
        pass
    url = getattr(cfg, "API_BASE_URL", None) if cfg else None
    if url:
        return str(url).rstrip("/")
    url = os.environ.get("API_BASE_URL")
    if url and "${" not in url:
        return url.rstrip("/")
    port = (getattr(cfg, "API_PORT", None) if cfg else None) or os.environ.get("API_PORT", "8000")
    return f"http://localhost:{int(port)}"


def resolve_api_prefix() -> str:
    """Use the same route prefix as the backend, including an explicitly empty prefix."""
    try:
        import config
        prefix = getattr(config, "API_PREFIX", None)
    except Exception:
        prefix = None
    return str(prefix if prefix is not None else os.environ.get("API_PREFIX", "/api"))


# ----------------------------------------------------------------------------- errors
@dataclass
class ApiError(Exception):
    """A failed request, kept separate from 'successful but empty'."""
    status: Optional[int]               # None = server unreachable
    message: str
    code: str = "error"
    details: Any = None
    current_revision: Optional[int] = None

    def __str__(self) -> str:
        return self.message

    @property
    def is_conflict(self) -> bool:      # stale revision, reload/compare
        return self.status == 409

    @property
    def is_validation(self) -> bool:    # invalid review input
        return self.status == 422

    @property
    def is_unreachable(self) -> bool:
        return self.status is None

    @property
    def problems(self) -> list[str]:
        """Human-readable list of validation problems (FastAPI or custom envelope)."""
        d = self.details
        if isinstance(d, list):
            out = []
            for item in d:
                if isinstance(item, dict):
                    loc = ".".join(str(x) for x in item.get("loc", []) if x != "body")
                    out.append(f"{loc}: {item.get('msg', item)}" if loc else str(item.get("msg", item)))
                else:
                    out.append(str(item))
            return out
        if isinstance(d, dict):
            return [f"{k}: {v}" for k, v in d.items()]
        return [self.message] if self.message else []


def parse_error(response: httpx.Response) -> ApiError:
    """Understands {"error": {...}} envelopes and FastAPI {"detail": ...}."""
    try:
        body = response.json()
    except ValueError:
        return ApiError(response.status_code, response.text[:300] or response.reason_phrase)
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        e = body["error"]
        return ApiError(response.status_code, e.get("message") or "Request failed", e.get("code", "error"),
                        e.get("details"), e.get("current_revision"))
    detail = body.get("detail") if isinstance(body, dict) else body
    if isinstance(detail, str):
        return ApiError(response.status_code, detail)
    if isinstance(detail, dict):
        return ApiError(response.status_code, detail.get("message", "Request failed"), detail.get("code", "error"),
                        detail.get("details", detail), detail.get("current_revision"))
    return ApiError(response.status_code, "The server rejected the request.", details=detail)


# ----------------------------------------------------------------------------- normalisation
def _num(v) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def normalize_status(raw: Optional[str], has_conflict: Optional[bool] = None) -> tuple[str, bool]:
    """Legacy CONFLICTED becomes PM_REVIEW plus an explicit conflict flag (plan 8.1)."""
    s = (raw or "DRAFT").upper()
    if s == "CONFLICTED":
        return "PM_REVIEW", True
    if s == "POSTPONE":
        s = "POSTPONED"
    return (s if s in STATUS_LABELS else "DRAFT"), bool(has_conflict)


def normalize_summary(item: dict) -> dict:
    """List row from either contract into one shape. `gate` and `gate_color` are unified here."""
    if "req_id" in item and "id" not in item:  # legacy row
        status, conflict = normalize_status(item.get("status"), item.get("has_conflict"))
        return {
            "id": item["req_id"], "revision": None, "title": item.get("title") or item["req_id"],
            "target_scope": {"derivatives": [item.get("model")] if item.get("model") else [],
                             "regions": [], "powertrains": []},
            "topic": item.get("vfc2"),
            "score": {"effective": _num(item.get("bmw_priority")), "calculated": _num(item.get("bmw_priority")),
                      "scale": LEGACY_SCALE, "provisional": True},
            "evidence": {"level": (item.get("evidence_tier") or "").title() or None, "eci": None,
                         "legacy_tier": item.get("evidence_tier")},
            "gate": item.get("gate") or item.get("gate_color"),
            "status": status, "has_conflict": conflict or bool(item.get("has_conflict")),
            "flags": [], "legacy": True,
        }
    out = dict(item)
    out.setdefault("target_scope", {})
    out["gate"] = item.get("gate") or item.get("gate_color")
    status, conflict = normalize_status(item.get("status"), item.get("has_conflict"))
    out["status"], out["has_conflict"] = status, conflict
    out["score"] = normalize_score(item.get("score") or {}, item.get("prioritization_rationale"),
                                   item.get("scoring_policy"))
    out["evidence"] = normalize_evidence(item.get("evidence") or {}, item.get("evidence_links"),
                                         item.get("source_snapshots"))
    out.setdefault("flags", [])
    out["legacy"] = bool(item.get("legacy", False) or item.get("provenance_state") == "unresolved_legacy"
                         or out["score"].get("scale", {}).get("policy") == "legacy-v0")
    return out


def normalize_detail(item: dict) -> dict:
    """Detail payload into the plan section 6 shape. Legacy records are labelled, never upgraded."""
    if "req_id" in item and "id" not in item:
        out = _legacy_detail(item)
        out["smart"] = normalize_smart(out["smart"])
        return out
    out = normalize_summary(item)
    # TODO(Role 3): RequirementDetail has no allowed_actions. Until it does, all five actions are
    # offered and the server's 409/422 decides.
    out["actions_published"] = "allowed_actions" in item
    out.setdefault("allowed_actions", list(ACTION_LABELS))
    out["smart"] = normalize_smart(item.get("smart") or {})
    out["assumptions"] = [normalize_assumption(a) for a in item.get("assumptions") or []]
    out["uncertainties"] = [_text(u) for u in item.get("uncertainties") or []]
    out.setdefault("evidence_links", [])
    return out


# --- v1 contract (contracts/openapi.json, RequirementDetail) -------------------------------------
def _is_v1_score(score: dict) -> bool:
    return "effective_score" in score or "calculated_score" in score


def normalize_score(score: dict, rationale: Optional[str] = None, policy: Optional[dict] = None) -> dict:
    """Backend score -> UI score. Renames and reshapes only; every number comes from the server."""
    if not _is_v1_score(score):          # synthetic fixtures already use the UI shape
        return dict(score, scale=score.get("scale") or V1_SCALE)
    raw_scale = score.get("scale")
    version = score.get("policy_version") or "v1"
    if isinstance(raw_scale, (list, tuple)) and len(raw_scale) == 2:
        lo, hi = raw_scale
        scale = {"policy": version, "min": lo, "max": hi, "label": f"Priority {version} ({lo}–{hi})"}
    else:
        scale = dict(V1_SCALE, policy=version)
    factors = []
    for key, f in (score.get("factors") or {}).items():
        f = f or {}
        factors.append({"key": key, "label": FACTOR_LABELS.get(key, key.replace("_", " ").capitalize()),
                        "value": _num(f.get("value")), "weight": f.get("weight"),
                        "contribution": _num(f.get("contribution")), "provisional": bool(f.get("provisional")),
                        "reason": f.get("reason"), "references": f.get("references") or [],
                        # bounds are shown only when the backend sends them; never derived here
                        "lower": _num(f.get("lower")), "upper": _num(f.get("upper"))})
    rng = score.get("range") or None
    if rng and (rng.get("lower") is not None and rng.get("upper") is not None):
        rng = {"low": _num(rng["lower"]), "high": _num(rng["upper"]), "kind": rng.get("kind"),
               "label": RANGE_LABELS.get(rng.get("kind"), "Sensitivity range")
                        + ("" if rng.get("statistical_confidence_interval")
                           else ", not a statistical confidence interval")}
    else:
        rng = None
    override = score.get("override")
    return {"calculated": _num(score.get("calculated_score")), "effective": _num(score.get("effective_score")),
            "override": {"value": _num(override)} if isinstance(override, (int, float)) else override,
            "scale": scale, "factors": factors, "range": rng,
            "provisional": any(f["provisional"] for f in factors),
            "warnings": list(score.get("warnings") or []), "assumptions": list(score.get("assumptions") or []),
            "rationale": rationale, "config_id": (policy or {}).get("id") or version}


def normalize_evidence(ev: dict, links: Optional[list] = None, snapshots: Optional[list] = None) -> dict:
    """Backend evidence summary (+ evidence_links) -> UI evidence. ECI is the server's value."""
    if "value" not in ev and "policy_version" not in ev:   # fixture / legacy shape
        return dict(ev)
    rng = ev.get("range") or None
    rows = source_rows(links or [], snapshots)
    return {"eci": _num(ev.get("value")), "level": ev.get("level"), "policy": ev.get("policy_version"),
            "components": dict(ev.get("components") or {}),
            "distinct_support_comments": ev.get("distinct_support_comments"),
            "range": ({"low": _num(rng.get("lower")), "high": _num(rng.get("upper")), "kind": rng.get("kind"),
                       "label": RANGE_LABELS.get(rng.get("kind"), "Range")} if rng else None),
            "unknown_components": list(ev.get("unknown_components") or []),
            "warnings": list(ev.get("warnings") or []),
            "is_probability": ev.get("is_probability", False),
            **{group: [row for row in rows if _source_group(row) == group]
               for group in ("quotes", "facts", "documents")}}


def _source_group(row: dict) -> str:
    kind = row.get("kind") or ""
    if kind in {"sales", "survey"} or kind.startswith("survey_"):
        return "facts"
    if kind in {"document", "corporate_report", "option_catalog"}:
        return "documents"
    return "quotes"


def source_rows(links: list, snapshots: Optional[list] = None) -> list[dict]:
    """SourceReference list -> the quote rows the Evidence tab renders. Text only from the server."""
    rows = []
    for link in links:
        loc = link.get("locator") or {}
        snapshot = next((s for s in snapshots or [] if all(
            s.get(key) == link.get(key) for key in ("source_id", "record_id", "source_version", "locator"))), {})
        source = snapshot.get("source_file") or {}
        fact = snapshot.get("fact") or {}
        value = fact.get("volume", fact.get("value", fact.get("distribution")))
        if value is not None and fact.get("unit"):
            value = f"{value} {fact['unit']}"
        rows.append({"id": link.get("record_id"), "text": _snapshot_text(snapshot),
                      "kind": source.get("kind"), "label": source.get("filename") or link.get("record_id"),
                      "value": value,
                      "relation": link.get("relation"), "validation": link.get("validation_state"),
                     "locator": " · ".join(f"{k} {v}" for k, v in loc.items()) or None,
                     "scope": f"claim: {link['claim_key']}" if link.get("claim_key") else None,
                     "source_id": link.get("source_id"), "source_version": link.get("source_version"),
                     "source_record_id": link.get("record_id")})
    return rows


def _snapshot_text(snapshot: dict) -> Optional[str]:
    """Decode original text from the native page/feedback-row snapshot, without paraphrasing it."""
    chunk = snapshot.get("chunk") or {}
    payload = snapshot.get("raw_payload") or {}
    text = chunk.get("original_text") or chunk.get("text") or payload.get("text")
    if isinstance(text, str):
        return text
    values = payload.get("values_by_header") or {
        c.get("header"): c.get("value") for c in payload.get("columns") or [] if isinstance(c, dict)}
    feedback_headers = {"customerfeedback", "feedback", "feedbacktext", "comment", "commenttext",
                        "originaltext", "text", "verbatim"}
    for header, value in values.items():
        if "".join(ch for ch in str(header).casefold() if ch.isalnum()) in feedback_headers:
            if isinstance(value, str):
                return value
    return None


def fill_quote_text(ev: dict, req: dict) -> dict:
    """Reuse pinned detail snapshots and source types without mixing different versions/locators."""
    groups = ("quotes", "facts", "documents")
    identity = lambda row: tuple(row.get(k) for k in ("id", "source_id", "source_version", "locator", "scope"))
    known = {identity(row): (group, row) for group in groups
             for row in (req.get("evidence") or {}).get(group) or []}
    result = {group: [] for group in groups}
    for group in groups:
        for row in ev.get(group) or []:
            destination, saved = known.get(identity(row), (group, {}))
            for key in ("text", "kind", "label", "value"):
                if row.get(key) is None and saved.get(key) is not None:
                    row[key] = saved[key]
            result[destination].append(row)
    ev.update(result)
    return ev


def normalize_evidence_response(body: dict) -> dict:
    """GET /requirements/{rid}/evidence -> UI evidence. {links, evidence, assumptions, uncertainties}."""
    if not isinstance(body, dict) or "evidence" not in body:
        return body or {}
    ev = normalize_evidence(body.get("evidence") or {}, body.get("links"))
    ev["gaps"] = [_text(u) for u in body.get("uncertainties") or []]
    return ev


def normalize_list(body, offset: int = 0, limit: int = 50) -> dict:
    """GET /requirements {items, total, offset, ranking_id}. ranking_id is kept for the freeze request."""
    body = body or {}
    if isinstance(body, list):
        body = {"items": body, "total": len(body), "offset": offset}
    items = body.get("items", [])
    return {"items": [normalize_summary(x) for x in items], "total": body.get("total"),
            "offset": body.get("offset", offset), "limit": limit, "legacy": False,
            "notice": body.get("notice"), "ranking_id": body.get("ranking_id"), "counts": body.get("counts")}


def _target(t: dict, kind: str) -> dict:
    out = dict(t)
    out["kind"] = kind
    out["review_state"] = TARGET_REVIEW.get(t.get("review_state"), t.get("review_state"))
    if kind == "metric":           # v1 metrics carry no name; the row shows operator, target and unit
        out["value"] = t.get("target", t.get("value"))
        out["verification"] = t.get("verification_method", t.get("verification"))
    return out


def normalize_smart(smart: dict) -> dict:
    """smart.metrics (list) + milestone -> one `targets` list; every metric is its own row.

    The server's own `metrics` and `milestone` stay untouched, so an edit can send them back."""
    out = dict(smart)
    targets = []
    if isinstance(smart.get("metrics"), list):
        targets += [_target(m, "metric") for m in smart["metrics"]]
    elif isinstance(smart.get("measure"), dict):     # fixtures and legacy: a single measure
        targets.append(_target(smart["measure"], "measure"))
    if isinstance(smart.get("milestone"), dict):
        targets.append(_target(smart["milestone"], "milestone"))
    out["targets"] = targets
    return out


def normalize_sources(body) -> dict:
    """GET /sources returns the imported SourceFile nodes (Role 1 ingestion) as a list."""
    if isinstance(body, dict):
        return body
    files = []
    for s in body or []:
        kind = s.get("kind") or "source"
        if s.get("document_kind"):
            kind += f" ({s['document_kind']})"
        files.append({"file": s.get("filename") or s.get("id"), "kind": kind, "id": s.get("id"),
                      "version": (s.get("source_version") or s.get("sha256") or "")[:12] or None,
                      "size_bytes": s.get("size_bytes"), "issues": list(s.get("issues") or [])})
    return {"files": files, "coverage": None}


def normalize_source_record(body: dict) -> dict:
    """SourceRecord / DocumentChunk node -> {id, kind, file, locator, values} for the source sheet."""
    body = dict(body or {})
    if "values" in body and "file" in body:              # fixture shape
        return body
    loc = dict(body.get("locator") or {})
    payload = body.get("raw_payload") or (body.get("source_record") or {}).get("raw_payload") or {}
    if isinstance(payload.get("columns"), list):         # spreadsheet row: header/value pairs
        values = {c.get("header"): c.get("value") for c in payload["columns"] if isinstance(c, dict)}
    else:
        values = {k: v for k, v in payload.items() if not isinstance(v, (dict, list))}
    text = body.get("original_text") or body.get("text")
    if text:                                           # the exact chunk, rather than its entire parent page
        values["text"] = text
    return {"id": body.get("id"), "kind": body.get("document_kind") or body.get("kind") or "source record",
            "file": loc.pop("filename", None) or (body.get("source_file") or {}).get("filename")
                    or body.get("source_file_id"),
            "locator": {k: v for k, v in loc.items() if v is not None} or
                       ({"page": body["page"]} if body.get("page") is not None else {}),
            "values": values, "source_version": body.get("source_version")}


def normalize_assumption(a: dict) -> dict:
    if not isinstance(a, dict):
        return {"id": None, "text": str(a), "state": "open"}
    out = dict(a)
    out["text"] = a.get("text") or a.get("description") or ""
    raw = a.get("state") or a.get("review_state") or "pending"
    out["state"] = ASSUMPTION_REVIEW.get(raw, raw)
    return out


def _text(u) -> str:
    if isinstance(u, dict):
        return str(u.get("description") or u.get("text") or u.get("message") or json.dumps(u, ensure_ascii=False))
    return str(u)


def _legacy_detail(r: dict) -> dict:
    status, conflict = normalize_status(r.get("status"), r.get("has_conflict"))
    expl = r.get("explainability") or {}
    p, lo, hi = _num(r.get("bmw_priority")), _num(r.get("bmw_priority_low")), _num(r.get("bmw_priority_high"))
    factors = []
    for key, f in (expl.get("score_breakdown") or {}).items():
        factors.append({"key": key, "label": key.replace("_", " ").capitalize(), "value": _num(f.get("value")),
                        "weight": f.get("weight"), "contribution": None, "reason": f.get("source"),
                        "provenance": "legacy"})
    quotes = [{"id": None, "text": q, "relation": "supports", "validation": "unverified",
               "validation_note": "Legacy quote without an original feedback ID; not verified against the source.",
               "locator": None, "source_record_id": None}
              for q in (expl.get("verbatim_samples") or [])]
    smart = {
        "description": r.get("jtbd"),
        "measure": {"current": r.get("metric_current"), "target": r.get("metric_target"),
                    "unit": r.get("metric_unit"), "timeframe": r.get("metric_timeframe"),
                    "provenance": "AIProposed", "review_state": "unreviewed"},
        "model_flags": {k: r.get(f"smart_{k}") for k in "smart"},
        "notes": r.get("smart_notes"),
    }
    gaps = list(r.get("data_gaps") or [])
    return {
        "id": r["req_id"], "revision": None, "legacy": True,
        "title": r.get("title") or r["req_id"], "description": r.get("jtbd"), "problem_statement": r.get("rationale"),
        "topic": r.get("vfc2"),
        "source_scope": {"derivatives": [r.get("model")] if r.get("model") else [], "signals": [r.get("source_signal")]},
        "target_scope": {"derivatives": [r.get("model")] if r.get("model") else [], "regions": [], "powertrains": []},
        "smart": smart,
        "score": {"effective": p, "calculated": p, "override": None, "scale": LEGACY_SCALE, "provisional": True,
                  "factors": factors,
                  "range": ({"low": lo, "high": hi, "kind": "legacy_sensitivity",
                             "label": "Legacy ±15% impact sensitivity, not a statistical confidence interval"}
                            if lo is not None and hi is not None else None),
                  "rationale": r.get("rationale")},
        "evidence": {"level": (r.get("evidence_tier") or "").title() or None, "eci": None,
                     "legacy_tier": r.get("evidence_tier"), "gaps": gaps,
                     "quotes": quotes, "regional_coverage": [], "facts": []},
        "evidence_links": [],
        "gate": r.get("gate_color") or r.get("gate"),
        "kano": r.get("kano"), "moscow": r.get("moscow"),
        "assumptions": [], "uncertainties": gaps,
        "conflict_detail": r.get("conflict_detail") or expl.get("conflict_detail"),
        "status": status, "has_conflict": conflict or bool(r.get("has_conflict")),
        "approved_revision": None,
        "allowed_actions": list(LEGACY_ACTION_TO_STATUS) if status != "APPROVED" else ["reopen"],
        "flags": ["legacy_unverified"],
    }


def normalize_history(events, legacy: bool) -> list[dict]:
    """Event list into timeline rows. v1 sends {revisions, events, audit}; only events are shown."""
    if isinstance(events, dict):
        events = events.get("events") or []
    out = []
    for e in events or []:
        if not legacy and "timestamp" in e and "time" not in e:      # v1 audit event
            after = e.get("after") if isinstance(e.get("after"), dict) else {}
            out.append(dict(e, time=e.get("timestamp"), actor_kind=e.get("actor_role"),
                            revision=after.get("revision") if e.get("entity_type") == "Requirement" else None,
                            summary=_event_summary(e)))
            continue
        if legacy or "from_status" in e:
            out.append({"time": e.get("timestamp"), "actor_kind": e.get("actor_role"), "actor_id": e.get("actor"),
                        "action": "STATUS_CHANGED",
                        "summary": f"{STATUS_LABELS.get(normalize_status(e.get('from_status'))[0])} → "
                                   f"{STATUS_LABELS.get(normalize_status(e.get('to_status'))[0])}",
                        "rationale": e.get("rationale"), "before": {"status": e.get("from_status")},
                        "after": {"status": e.get("to_status"), "score_override": e.get("score_after")},
                        "revision": None})
        else:
            out.append(e)
    return out


def _event_summary(e: dict) -> str:
    before = e.get("before") if isinstance(e.get("before"), dict) else {}
    after = e.get("after") if isinstance(e.get("after"), dict) else {}
    if e.get("action") == "STATUS_CHANGED" and before.get("status") and after.get("status"):
        return f"{STATUS_LABELS.get(before['status'], before['status'])} → " \
               f"{STATUS_LABELS.get(after['status'], after['status'])}"
    return str(e.get("action") or "").replace("_", " ").capitalize()


def normalize_turn(turn: dict, thread: Optional[dict] = None, rid: Optional[str] = None) -> dict:
    """Backend review turn or proposal -> the turn the Assistant tab renders.

    The raw `patch` is kept for the apply request; `proposed_patch` is the before/after view.
    `before` is filled from the loaded requirement by fill_patch_before (display only)."""
    thread = thread or {}
    patch = turn.get("patch")
    preview = turn.get("change_preview") or {}
    proposed = {k: {"before": None, "after": v} for k, v in patch.items()} if isinstance(patch, dict) else None
    sp = None
    if isinstance(preview, dict) and preview.get("new_score"):
        rid = rid or turn.get("entity_id")
        sp = {"before": (preview.get("old_score") or {}).get("effective_score"),
              "after": preview["new_score"].get("effective_score"),
              "rank_before": (preview.get("old_ranks") or {}).get(rid),
              "rank_after": (preview.get("new_ranks") or {}).get(rid)}
    state = turn.get("state")
    if state == "pending" and turn.get("stale"):
        state = "stale"
    return {"id": turn.get("id"), "thread_id": turn.get("thread_id") or thread.get("id"),
            "state": state, "base_revision": turn.get("expected_revision"),
            "time": turn.get("timestamp") or thread.get("timestamp"),
            "actor_id": turn.get("actor_id") or thread.get("actor_id"),
            "question": turn.get("question"), "answer": turn.get("answer"),
            "citations": [c.get("record_id") if isinstance(c, dict) else str(c) for c in turn.get("citations") or []],
            "coverage_gaps": [_text(g) for g in turn.get("coverage_gaps") or []],
            "patch": patch, "proposed_patch": proposed, "score_preview": sp,
            "generation_metadata": turn.get("generation_metadata") or turn.get("model_metadata")}


def normalize_thread(thread: dict, proposals: Optional[list] = None, rid: Optional[str] = None) -> dict:
    """One or more backend threads plus the requirement's proposals -> {id, turns}.

    Proposals that are not part of a loaded thread (e.g. regenerated drafts) are added as
    suggestion-only turns, so every pending proposal can be applied or discarded."""
    turns = [normalize_turn(t, thread, rid) for t in (thread or {}).get("turns") or []]
    seen = {t["id"] for t in turns}
    for p in proposals or []:
        if p.get("id") not in seen:
            turns.append(normalize_turn(p, None, rid))
    return {"id": (thread or {}).get("id"), "turns": turns,
            "current_revision": (thread or {}).get("current_revision")}


def fill_patch_before(thread: dict, req: dict) -> dict:
    """Show the requirement's current value as `before` for v1 patches (the server sends only `after`)."""
    for t in (thread or {}).get("turns") or []:
        if t.get("patch") and t.get("proposed_patch"):
            for key, change in t["proposed_patch"].items():
                if change.get("before") is None and key in req:
                    change["before"] = req[key]
    return thread


# ----------------------------------------------------------------------------- request bodies
def decision_payload(rid: str, action: str, expected_revision: Optional[int], actor_id: str, rationale: str,
                     accepted_assumption_ids=None, reviewed_target_ids=None, reviewed_source_ids=None,
                     score_override: Optional[float] = None, override_enabled: bool = False) -> dict:
    """DecisionRequest (contracts/openapi.json). score_override is a plain number 0-100.

    Disabled override: the field is absent, so the server keeps the current score. Sending null
    would clear an existing override, which the PM did not ask for. An explicit 0 stays 0 (R03)."""
    body = {"id": rid, "action": action, "expected_revision": expected_revision, "actor_id": actor_id,
            "actor_role": PM_ROLE, "rationale": rationale,
            "reviewed_target_ids": list(reviewed_target_ids or []),
            "accepted_assumption_ids": list(accepted_assumption_ids or []),
            "reviewed_source_ids": list(reviewed_source_ids or [])}
    if override_enabled:
        body["score_override"] = None if score_override is None else float(score_override)
    return body


def freeze_payload(revisions: list[dict], expected_ranking_id: Optional[str], actor_id: str,
                   rationale: str) -> dict:
    """FreezeRequest: {selections: {id: revision}, expected_ranking_id, actor_id, actor_role, rationale}."""
    return {"selections": {r["id"]: r["revision"] for r in revisions},
            "expected_ranking_id": expected_ranking_id, "actor_id": actor_id, "actor_role": PM_ROLE,
            "rationale": rationale}


def mutation_payload(expected_revision: Optional[int], actor_id: str, rationale: str, **extra) -> dict:
    """MutationRequest base shared by edit, apply, discard and challenge."""
    return {"expected_revision": expected_revision, "actor_id": actor_id, "actor_role": PM_ROLE,
            "rationale": rationale, **extra}


def normalize_run(run: dict) -> dict:
    """The backend reports progress as a fraction 0-1; the UI shows drafts done when it has them."""
    out = dict(run or {})
    out.setdefault("run_id", out.get("id"))
    p = out.get("progress")
    if isinstance(p, (int, float)) and not isinstance(p, bool):
        out["progress"] = {"fraction": float(p), "done": len(out.get("result_ids") or []), "total": None}
    return out


# ----------------------------------------------------------------------------- client
@dataclass
class ApiClient:
    """Live client. `mode` is detected once: 'v1' (/api contract) or 'legacy' (step 07 routes)."""
    base_url: str = field(default_factory=resolve_base_url)
    timeout: float = 15.0
    mode: Optional[str] = None
    synthetic: bool = False
    api_prefix: str = field(default_factory=resolve_api_prefix)
    # Session threads are supplemented by the server's persisted review-thread audit events.
    _thread_ids: dict = field(default_factory=dict, repr=False)

    # -- transport -------------------------------------------------------------
    def _request(self, method: str, path: str, *, params=None, json_body=None, raw: bool = False):
        if path == "/api" or path.startswith("/api/"):
            path = self.api_prefix + path[4:]
        url = f"{self.base_url.rstrip('/')}{path}"
        try:
            r = httpx.request(method, url, params=_clean(params), json=json_body, timeout=self.timeout)
        except httpx.HTTPError as e:
            raise ApiError(None, f"Cannot reach the API at {self.base_url} ({type(e).__name__}).", "unreachable")
        if r.status_code >= 400:
            raise parse_error(r)
        if raw:
            return r.content, r.headers.get("content-type", "")
        if not r.content:
            return None
        try:
            return r.json()
        except ValueError:
            raise ApiError(r.status_code, "The API returned a response that is not JSON.", "bad_response")

    def _detect(self) -> str:
        if self.mode:
            return self.mode
        try:
            self._request("GET", "/api/requirements", params={"limit": 1})
            self.mode = "v1"
        except ApiError as e:
            if e.status == 404:
                self._request("GET", "/requirements", params={"limit": 1})  # raises if legacy is down too
                self.mode = "legacy"
            else:
                raise
        return self.mode

    @property
    def legacy(self) -> bool:
        return self._detect() == "legacy"

    # -- reads -----------------------------------------------------------------
    def health(self) -> dict:
        mode = self._detect()
        status = self._request("GET", "/api/health") if mode == "v1" else {}
        return dict(status or {}, mode=mode, base_url=self.base_url)

    def list_requirements(self, filters: dict | None = None, offset: int = 0, limit: int = 50) -> dict:
        f = filters or {}
        if self.legacy:
            params = {"model": f.get("derivative"), "status": f.get("status"), "limit": min(limit + offset, 200)}
            rows = self._request("GET", "/requirements", params=params) or []
            items = [normalize_summary(x) for x in rows]
            q = (f.get("q") or "").lower()
            if q:  # display filter only; no business logic
                items = [x for x in items if q in (x["title"] + " " + x["id"]).lower()]
            page = items[offset:offset + limit]
            return {"items": page, "total": None, "offset": offset, "limit": limit, "legacy": True,
                    "notice": "Legacy API returns at most 200 rows and no total (repair R19 pending)."}
        params = {"status": f.get("status"), "derivative": f.get("derivative"), "region": f.get("region"),
                  "powertrain": f.get("powertrain"), "evidence_level": f.get("evidence"),
                  "search": f.get("q")}
        body = self._paged("/api/requirements", params, offset, limit)
        return normalize_list(body, offset, limit)

    def _paged(self, path: str, params: dict, offset: int, limit: Optional[int]) -> dict:
        """Combine server pages without exceeding its 200-row request limit.

        A ranking change between pages is an error, not a mixed-revision package selection.
        """
        items, first = [], None
        while limit is None or len(items) < limit:
            page_limit = min(200, limit - len(items)) if limit is not None else 200
            body = self._request("GET", path, params=dict(params, offset=offset + len(items), limit=page_limit)) or {}
            if first is None:
                first = body
            elif body.get("ranking_id") != first.get("ranking_id"):
                raise ApiError(409, "The ranking changed while loading pages. Refresh and review again.",
                               "stale_ranking")
            rows = body.get("items") or []
            items.extend(rows)
            total = body.get("total")
            if not rows or len(rows) < page_limit or (total is not None and offset + len(items) >= total):
                break
        return dict(first or {}, items=items, offset=offset)

    def list_all_requirements(self, filters: dict | None = None) -> dict:
        """Read the full portfolio for overview/package selection; exports still use their own endpoint."""
        if self.legacy:
            return self.list_requirements(filters, limit=200)
        f = filters or {}
        params = {"status": f.get("status"), "derivative": f.get("derivative"), "region": f.get("region"),
                  "powertrain": f.get("powertrain"), "evidence_level": f.get("evidence"), "search": f.get("q")}
        body = self._paged("/api/requirements", params, 0, None)
        return normalize_list(body, 0, len(body["items"]))

    def get_requirement(self, rid: str) -> dict:
        if self.legacy:
            return normalize_detail(self._request("GET", f"/requirements/{rid}"))
        return normalize_detail(self._request("GET", f"/api/requirements/{rid}"))

    def get_evidence(self, rid: str) -> dict:
        if self.legacy:
            return self.get_requirement(rid)["evidence"]
        return normalize_evidence_response(self._request("GET", f"/api/requirements/{rid}/evidence") or {})

    def get_history(self, rid: str) -> list[dict]:
        if self.legacy:
            return normalize_history(self._request("GET", f"/requirements/{rid}/history") or [], legacy=True)
        return normalize_history(self._request("GET", f"/api/requirements/{rid}/history") or {}, legacy=False)

    def get_thread(self, rid: str) -> dict:
        """Recover persisted threads, including read-only answers made in other sessions."""
        if self.legacy:
            return {"turns": [], "unavailable": "The legacy API has no review assistant."}
        proposals = self._paged(f"/api/requirements/{rid}/proposals", {}, 0, None)["items"]
        history = self._request("GET", f"/api/requirements/{rid}/history") or {}
        persisted_ids = [(e.get("after") or {}).get("id") for e in history.get("events") or []
                         if e.get("action") == "REVIEW_THREAD_CREATED"
                         and isinstance(e.get("after"), dict)
                         and e["after"].get("entity_kind") == "requirement"
                         and e["after"].get("entity_id") == rid]
        ids = list(dict.fromkeys([p["thread_id"] for p in proposals if p.get("thread_id")]
                                 + [tid for tid in persisted_ids if tid] + self._thread_ids.get(rid, [])))
        merged = {"id": None, "turns": []}
        for tid in ids:
            t = normalize_thread(self._request("GET", f"/api/review-threads/{tid}") or {}, None, rid)
            merged["id"], merged["turns"] = t["id"], merged["turns"] + t["turns"]
        extra = normalize_thread({}, proposals, rid)["turns"]
        seen = {t["id"] for t in merged["turns"]}
        merged["turns"] += [t for t in extra if t["id"] not in seen]
        return merged

    def get_source_record(self, record_id: str) -> dict:
        self._require_v1("Source records")
        # Role 1 IDs: SR-… (source records), DOC-… (document chunks)
        kind = ("document-chunks" if record_id.upper().startswith(("DOC-", "CHUNK")) else
                "facts" if record_id.upper().startswith("FACT-") else "source-records")
        return normalize_source_record(self._request("GET", f"/api/{kind}/{record_id}") or {})

    def list_sources(self) -> dict:
        self._require_v1("The sources overview")
        return normalize_sources(self._request("GET", "/api/sources") or [])

    def summary(self) -> dict:
        if self.legacy:
            return self._request("GET", "/dashboard/summary") or {}
        try:
            return self._request("GET", "/api/dashboard/summary") or {}
        except ApiError as e:
            if e.status == 404:
                return {}
            raise

    def get_run(self, run_id: str) -> dict:
        self._require_v1("Analysis runs")
        return normalize_run(self._request("GET", f"/api/analysis-runs/{run_id}") or {})

    def get_freeze(self, freeze_id: str) -> dict:
        self._require_v1("Review packages")
        return self._request("GET", f"/api/freezes/{freeze_id}") or {}

    # -- mutations: every one is an explicit PM action ----------------------------
    def decide(self, rid: str, action: str, expected_revision: Optional[int], actor_id: str, rationale: str,
               accepted_assumption_ids: list[str] | None = None, reviewed_target_ids: list[str] | None = None,
               score_override: Optional[float] = None, override_enabled: bool = False,
               reviewed_source_ids: list[str] | None = None) -> dict:
        if self.legacy:
            body = {"req_id": rid, "to_status": LEGACY_ACTION_TO_STATUS[action], "actor": actor_id,
                    "actor_role": PM_ROLE, "rationale": rationale,
                    "score_override": score_override if override_enabled else None}
            return self._request("POST", f"/requirements/{rid}/transition", json_body=body)
        body = decision_payload(rid, action, expected_revision, actor_id, rationale, accepted_assumption_ids,
                                reviewed_target_ids, reviewed_source_ids, score_override, override_enabled)
        return self._request("POST", f"/api/requirements/{rid}/decisions", json_body=body)

    def edit(self, rid: str, expected_revision: int, patch: dict, actor_id: str, rationale: str) -> dict:
        self._require_v1("Editing requirement content")
        return self._request("PATCH", f"/api/requirements/{rid}",
                             json_body=mutation_payload(expected_revision, actor_id, rationale, id=rid, patch=patch))

    def challenge(self, rid: str, question: str, expected_revision: int, actor_id: str) -> dict:
        """Starts a review run in a new thread. The question doubles as the required rationale."""
        self._require_v1("The review assistant")
        run = self._request("POST", f"/api/requirements/{rid}/challenges",
                            json_body=mutation_payload(expected_revision, actor_id, question, id=rid,
                                                       question=question)) or {}
        if run.get("thread_id"):
            self._thread_ids.setdefault(rid, []).append(run["thread_id"])
        return normalize_run(run)

    def apply_suggestion(self, rid: str, turn_id: str, expected_revision: int, accepted_patch: dict,
                         actor_id: str, rationale: str) -> dict:
        self._require_v1("Applying suggestions")
        return self._request("POST", f"/api/requirements/{rid}/challenge-applications",
                             json_body=mutation_payload(expected_revision, actor_id, rationale, id=rid,
                                                        turn_id=turn_id, patch=accepted_patch))

    def discard_suggestion(self, thread_id: Optional[str], turn_id: str, actor_id: str, rationale: str,
                           rid: Optional[str] = None, expected_revision: Optional[int] = None) -> dict:
        self._require_v1("Discarding suggestions")
        body = mutation_payload(expected_revision, actor_id, rationale, id=rid)
        path = (f"/api/requirements/{rid}/proposals/{turn_id}/discard" if rid
                else f"/api/review-threads/{thread_id}/turns/{turn_id}/discard")
        return self._request("POST", path, json_body=body)

    def start_analysis(self, scope: dict, max_drafts: int, refresh: bool) -> dict:
        self._require_v1("Starting an analysis")
        return normalize_run(self._request("POST", "/api/analysis-runs", json_body={
            **scope, "max_drafts": max_drafts, "refresh": refresh}))

    def create_freeze(self, revisions: list[dict], baseline_ranking_id: Optional[str], actor_id: str,
                      rationale: str) -> dict:
        self._require_v1("Freezing a review package")
        body = self._request("POST", "/api/freezes",
                             json_body=freeze_payload(revisions, baseline_ranking_id, actor_id, rationale)) or {}
        return dict(body, freeze_id=body.get("id"))

    # -- downloads: always the server's complete export, never the visible table (R19)
    def download(self, what: str, fmt: str, ref: Optional[str] = None) -> tuple[bytes, str, str]:
        if self.legacy:
            raise ApiError(501, "The legacy API has no complete export endpoint. The table is capped at 200 "
                                "rows, so the UI does not rebuild an export from it (repair R19).", "unavailable")
        paths = {
            "sample_list": (f"/api/requirements/export", {"format": fmt}),
            "freeze": (f"/api/freezes/{ref}/export", {"format": fmt}),
            "audit": (f"/api/freezes/{ref}/audit", None),
            "history": (f"/api/requirements/{ref}/history/export", {"format": "json"}),
        }
        path, params = paths[what]
        data, ctype = self._request("GET", path, params=params, raw=True)
        ext = {"csv": "csv", "json": "json", "markdown": "md"}.get(fmt, "json")
        return data, ctype or "application/octet-stream", f"{what}{'-' + ref if ref else ''}.{ext}"

    def _require_v1(self, feature: str):
        if self.legacy:
            raise ApiError(501, f"{feature} needs the new /api contract; the connected server only offers the "
                                f"legacy routes.", "unavailable")


def _clean(params: Optional[dict]) -> Optional[dict]:
    if not params:
        return None
    return {k: v for k, v in params.items() if v not in (None, "", [], "All")}


def pretty(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)
