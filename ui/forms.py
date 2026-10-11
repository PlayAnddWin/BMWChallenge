"""
Pure form helpers: turn what the PM entered into one API request. No Streamlit, no rules.
"""
from __future__ import annotations

import copy


def submit_decision(client, req: dict, action: str, *, actor: str, rationale: str, reviewed: list[str],
                    accepted: list[str], override_enabled: bool, override_value,
                    reviewed_sources: list[str] | None = None) -> dict:
    """One decision request. The server decides whether it is allowed."""
    return client.decide(req["id"], action, req.get("revision"), actor, rationale,
                         accepted_assumption_ids=accepted, reviewed_target_ids=reviewed,
                         score_override=override_value if override_enabled else None,
                         override_enabled=override_enabled, reviewed_source_ids=list(reviewed_sources or []))


def draft_key(req: dict) -> str:
    return f"draft:{req['id']}:{req.get('revision')}"


def is_v1(req: dict) -> bool:
    """v1 records (backend contract) carry SMART targets as a `metrics` list."""
    return isinstance((req.get("smart") or {}).get("metrics"), list)


def build_patch(req: dict, draft: dict) -> dict:
    """Only the fields the PM changed. Edited targets become PM-entered and need review again.

    v1: top-level RequirementDraft fields (PATCH /api/requirements/{id}); `smart` is sent whole,
    built from the server's own metrics with only the edited targets changed."""
    patch = {}
    for key in ("title", "description"):
        if draft.get(key) is not None and draft[key] != (req.get(key) or ""):
            patch[key] = draft[key]
    smart = req.get("smart") or {}
    if is_v1(req):
        edits = {mid: str(v).strip() for mid, v in (draft.get("metric_targets") or {}).items()}
        metrics, changed = copy.deepcopy(smart["metrics"]), False
        for m in metrics:
            new = edits.get(m.get("id"))
            if new is not None and new != str(m.get("target", "")):
                m.update(target=new, provenance="PMEntered", review_state="pending")
                m.pop("source_references", None)          # a PM value is no longer source-backed
                changed = True
        if changed:
            patch["smart"] = {k: copy.deepcopy(v) for k, v in smart.items() if k != "targets"}
            patch["smart"]["metrics"] = metrics
    else:
        m = smart.get("measure") or {}
        if draft.get("measure_value") is not None and str(draft["measure_value"]) != str(m.get("value", "")):
            patch["smart.measure"] = {"value": draft["measure_value"], "provenance": "PMEntered",
                                      "review_state": "unreviewed"}
    regions = draft.get("regions")
    if regions is not None and list(regions) != list(req.get("target_scope", {}).get("regions", [])):
        patch["target_scope"] = dict(req.get("target_scope", {}), regions=list(regions))
    return patch


def patch_diff(req: dict, patch: dict) -> dict:
    """Readable before/after rows for the edit sheet (one row per changed target)."""
    rows = {}
    for key, value in patch.items():
        if key == "smart":
            old = {m.get("id"): m for m in (req.get("smart") or {}).get("metrics", [])}
            for m in value.get("metrics", []):
                before = old.get(m.get("id"), {})
                if str(before.get("target")) != str(m.get("target")):
                    rows[f"Target {m.get('id')}"] = {"before": before.get("target"), "after": m.get("target")}
        elif key == "smart.measure":
            rows["Target value"] = {"before": ((req.get("smart") or {}).get("measure") or {}).get("value"),
                                    "after": value.get("value")}
        elif key == "target_scope":
            rows["Target regions"] = {"before": req.get("target_scope", {}).get("regions"),
                                      "after": value.get("regions")}
        else:
            rows[key.capitalize()] = {"before": req.get(key), "after": value}
    return rows
