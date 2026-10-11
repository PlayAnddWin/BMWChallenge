"""
Read-only client over the complete backend export (BMW_OUTPUT_DIR/requirements.json).

Use: show the real pipeline results when the API and Neo4j are not running.
The file is the backend's output, so the UI still computes nothing. Both current
v1 exports and historical requirements_full.json files retain their original
policy and validation labels. Every write raises 501: decisions need the API.
"""
from __future__ import annotations

import copy
import csv
import io
import json
import os
from pathlib import Path
from typing import Optional

from ui.client import ApiError, normalize_detail, normalize_history, normalize_summary

def snapshot_path() -> Path:
    from config import get_settings, resolve_path

    override = os.environ.get("REQUIREMENTS_SNAPSHOT")
    if override:
        return resolve_path(override)
    return get_settings().output_dir / "requirements.json"


def _flatten(record: dict) -> tuple[dict, dict]:
    """requirement + source_signal into the legacy API detail shape."""
    req = dict(record.get("requirement") or {})
    sig = record.get("source_signal") or {}
    req["explainability"] = {
        "verbatim_samples": sig.get("verbatim_samples") or [],
        "score_breakdown": {
            "impact": {"value": req.get("impact"), "weight": "legacy", "source": "Pipeline impact (0-10)"},
            "market_reach": {"value": req.get("market_reach_norm"), "weight": "legacy",
                             "source": "Normalised market reach (0-10)"},
            "confidence": {"value": req.get("confidence"), "weight": "legacy", "source": "Evidence confidence (0-1)"},
            "effort": {"value": req.get("effort"), "weight": "legacy", "source": "Estimated effort (0-10, higher = harder)"},
        },
    }
    return req, sig


class SnapshotClient:
    synthetic = False
    legacy = True
    snapshot = True

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else snapshot_path()
        if not self.path.exists():
            raise ApiError(404, f"No pipeline export at {self.path}. Run the pipeline export step "
                                "(09_export_report.py) or set REQUIREMENTS_SNAPSHOT.", "snapshot_missing")
        try:
            body = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ApiError(422, f"Cannot read the requirements snapshot at {self.path}: {exc}",
                           "snapshot_invalid") from exc
        if isinstance(body, list):
            self._raw, self.exported_at = body, None
        elif isinstance(body, dict) and isinstance(body.get("requirements"), list):
            self._raw, self.exported_at = body["requirements"], body.get("exported_at")
        else:
            raise ApiError(422, "The snapshot must be a complete backend export or a legacy requirements file.",
                           "snapshot_invalid")
        self.base_url = f"snapshot://{self.path.name}"
        self._details, self._summaries, self._history = {}, [], {}
        for rec in self._raw:
            if not isinstance(rec, dict):
                raise ApiError(422, "The snapshot contains a requirement that is not an object.", "snapshot_invalid")
            if "requirement" in rec:
                flat, sig = _flatten(rec)
            else:
                flat, sig = rec, {}
            rid = flat.get("id") or flat.get("req_id")
            if not rid or rid in self._details:
                raise ApiError(422, "The snapshot contains a missing or duplicate requirement ID.", "snapshot_invalid")
            d = normalize_detail(flat)
            if sig:
                d["evidence"]["feedback_count"] = sig.get("feedback_count")
                d["evidence"]["key_themes"] = sig.get("key_themes") or []
                d["evidence"]["study_avg_score"] = sig.get("study_avg_score")
            d["allowed_actions"] = []          # read-only: no server to decide
            self._details[rid] = d
            s = normalize_summary(flat)
            if sig:
                s["evidence"]["feedback_count"] = sig.get("feedback_count")
                s["kano"], s["moscow"] = flat.get("kano"), flat.get("moscow")
            s["data_gaps"] = len(flat.get("data_gaps") or [])
            self._summaries.append(s)
            self._history[rid] = normalize_history(rec.get("history") or [], legacy=d.get("legacy", False))
        self.legacy = bool(self._summaries) and all(r.get("legacy") for r in self._summaries)
        summary_file = self.path.with_name("summary_report.json")
        self._summary = json.loads(summary_file.read_text(encoding="utf-8")) if summary_file.exists() else {}

    # ---------------------------------------------------------------- reads
    def health(self):
        return {"mode": "snapshot", "base_url": self.base_url, "exported_at": self.exported_at}

    def list_requirements(self, filters=None, offset=0, limit=50):
        f = filters or {}
        rows = self._summaries
        if f.get("derivative"):
            rows = [r for r in rows if f["derivative"] in r["target_scope"].get("derivatives", [])]
        if f.get("status"):
            rows = [r for r in rows if r["status"] == f["status"]]
        for key, scope_key in (("region", "regions"), ("powertrain", "powertrains")):
            if f.get(key):
                rows = [r for r in rows if f[key] in r["target_scope"].get(scope_key, [])]
        if f.get("evidence"):
            rows = [r for r in rows if (r["evidence"].get("level") or "") == f["evidence"]]
        if f.get("q"):
            q = f["q"].lower()
            rows = [r for r in rows if q in (r["title"] + " " + r["id"] + " " + (r.get("topic") or "")).lower()]
        counts = {}
        for r in self._summaries:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        return {"items": copy.deepcopy(rows[offset:offset + limit]), "total": len(rows), "offset": offset,
                "limit": limit, "legacy": self.legacy, "counts": counts,
                "notice": "Read-only export. Connect the live API for current ranking and complete audit history."}

    def list_all_requirements(self, filters=None):
        return self.list_requirements(filters, limit=max(1, len(self._summaries)))

    def get_requirement(self, rid):
        if rid not in self._details:
            raise ApiError(404, f"Requirement {rid} not found.", "not_found")
        return copy.deepcopy(self._details[rid])

    def get_evidence(self, rid):
        return self.get_requirement(rid)["evidence"]

    def get_history(self, rid):
        self.get_requirement(rid)
        return copy.deepcopy(self._history.get(rid, []))

    def get_thread(self, rid):
        return {"turns": [], "unavailable": "The review assistant needs the live API."}

    def summary(self):
        return copy.deepcopy(self._summary)

    def list_sources(self):
        raise ApiError(501, "The sources overview needs the live API.", "unavailable")

    def get_source_record(self, record_id):
        raise ApiError(501, "Source records need the live API.", "unavailable")

    def get_run(self, run_id):
        raise ApiError(501, "Analysis runs need the live API.", "unavailable")

    def get_freeze(self, freeze_id):
        raise ApiError(501, "Review packages need the live API.", "unavailable")

    # ---------------------------------------------------------------- writes
    def _read_only(self, *a, **k):
        raise ApiError(501, "This is a read-only pipeline snapshot. Start the API to record decisions.",
                       "read_only")

    decide = edit = challenge = apply_suggestion = discard_suggestion = start_analysis = create_freeze = _read_only

    # ---------------------------------------------------------------- export: the whole file, never a page
    def download(self, what, fmt, ref=None):
        if what == "history":
            data = json.dumps(self.get_history(ref), ensure_ascii=False, indent=2).encode()
            return data, "application/json", f"history-{ref}.json"
        if what != "sample_list":
            self._read_only()
        if fmt == "json":
            return self.path.read_bytes(), "application/json", self.path.name
        buf = io.StringIO()
        cols = None
        w = None
        for rec in self._raw:
            row = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)
                   for k, v in (rec.get("requirement") or rec).items()}
            if "source_signal" in rec:
                row["feedback_count"] = (rec.get("source_signal") or {}).get("feedback_count")
            if w is None:
                cols = list(row)
                w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
                w.writeheader()
            w.writerow(row)
        return buf.getvalue().encode("utf-8"), "text/csv", "requirements_snapshot.csv"
