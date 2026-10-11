"""
Pure rendering helpers: turn normalised API data into HTML fragments.
No requests, no state, no business rules. All text is HTML-escaped.
"""
from __future__ import annotations

from html import escape
from typing import Iterable, Optional

from ui.client import STATUS_LABELS
from ui.theme import FACTOR_COLORS


def e(x) -> str:
    return escape("" if x is None else str(x))


# ----------------------------------------------------------------------------- pills
STATUS_TONE = {"DRAFT": "neutral", "PM_REVIEW": "blue", "APPROVED": "green", "REJECTED": "red", "POSTPONED": "amber"}
EVIDENCE_TONE = {"High": "green", "Strong": "green", "Moderate": "amber", "Low": "red", "Weak": "red"}
PROVENANCE = {"SourceBacked": ("Source-backed", "green"), "AIProposed": ("AI proposal", "indigo"),
              "PMEntered": ("Entered by PM", "blue"), "legacy": ("Legacy", "neutral")}
REVIEW = {"reviewed": ("Reviewed", "green"), "unreviewed": ("Not reviewed", "amber"),
          "accepted": ("Accepted", "green"), "open": ("Open", "amber"), "rejected": ("Rejected", "red"),
          "resolved": ("Resolved", "green")}
RELATION = {"supports": ("Supports", "green"), "contradicts": ("Contradicts", "red"), "context": ("Context", "neutral"),
            "direct": ("Direct", "green"), "broad": ("Broad", "neutral")}
VALIDATION = {"verified": ("Verified against source", "green"), "unverified": ("Not verified", "amber"),
              "ambiguous": ("Ambiguous match", "amber"), "invalid": ("Does not match source", "red")}
FLAG = {"supported_conflict": ("Conflicting evidence", "red"), "evidence_gap": ("Evidence gap", "amber"),
        "coverage_gap": ("Coverage gap", "amber"), "proposed_target": ("Target needs review", "amber"),
        "legacy_unverified": ("Legacy · unverified", "neutral"), "provisional": ("Provisional score", "amber"),
        "cached": ("Cached result", "neutral")}


def pill(label: str, tone: str = "neutral", dot: bool = False) -> str:
    return f'<span class="cc-pill {tone}">{"<span class=dot></span>" if dot else ""}{e(label)}</span>'


def status_pill(status: str) -> str:
    return pill(STATUS_LABELS.get(status, status), STATUS_TONE.get(status, "neutral"), dot=True)


def evidence_pill(level: Optional[str], legacy: bool = False) -> str:
    if not level:
        return pill("Evidence unknown", "neutral")
    label = f"{level} evidence" + (" (legacy tier)" if legacy else "")
    return pill(label, EVIDENCE_TONE.get(level, "neutral"))


def lookup_pill(table: dict, key, fallback_tone="neutral") -> str:
    if key is None:
        return ""
    label, tone = table.get(key, (str(key), fallback_tone))
    return pill(label, tone)


def flags_html(req: dict) -> str:
    out = []
    if req.get("has_conflict") and "supported_conflict" not in req.get("flags", []):
        out.append(pill("Conflict flagged", "red"))
    for f in req.get("flags", []):
        out.append(lookup_pill(FLAG, f))
    return "".join(out)


# ----------------------------------------------------------------------------- text helpers
def scope_text(scope: dict) -> str:
    parts = []
    for key in ("derivatives", "regions", "powertrains"):
        vals = [v for v in (scope or {}).get(key, []) if v]
        if vals:
            parts.append(", ".join(vals))
    return " · ".join(parts) if parts else "Not specified"


def fmt_score(value: Optional[float], scale: dict) -> str:
    if value is None:
        return "—"
    return f"{value:.1f}" if scale.get("max", 100) > 10 else f"{value:.2f}"


# ----------------------------------------------------------------------------- grouped list
def group(rows: Iterable[tuple]) -> str:
    """Inset grouped list. rows: (title_html, sub_html, accessory_html)."""
    items = []
    for row in rows:
        title, sub, acc = (list(row) + ["", ""])[:3]
        items.append(f'<div class="cc-row"><div class="cc-row-main"><div class="cc-row-title">{title}</div>'
                     f'{f"<div class=cc-row-sub>{sub}</div>" if sub else ""}</div>'
                     f'{f"<div class=cc-row-acc>{acc}</div>" if acc else ""}</div>')
    return f'<div class="cc-group">{"".join(items)}</div>' if items else ""


def section(label: str) -> str:
    return f'<div class="cc-sectionlabel">{e(label)}</div>'


def banner(html: str, tone: str = "amber") -> str:
    return f'<div class="cc-banner {tone}">{html}</div>'


# ----------------------------------------------------------------------------- score
def storage_bar(score: dict) -> str:
    """macOS 'Storage'-style composition bar. Uses the server's contributions; computes nothing."""
    scale = score.get("scale", {})
    top = float(scale.get("max", 100)) or 100
    segs = []
    for f in score.get("factors", []):
        c = f.get("contribution")
        if c is None:
            continue
        color = FACTOR_COLORS.get(f.get("key"), "#8E8E93")
        style = f"width:{max(0.0, 100 * float(c) / top):.3f}%;background:{color}"
        if f.get("provisional"):
            style += ";background-image:repeating-linear-gradient(135deg,rgba(255,255,255,.45) 0 4px,transparent 4px 8px)"
        segs.append(f'<span title="{e(f.get("label"))}: {float(c):.1f}" style="{style}"></span>')
    return f'<div class="cc-storage" role="img" aria-label="Score composition">{"".join(segs)}</div>'


def factor_legend(score: dict) -> str:
    rows = []
    for f in score.get("factors", []):
        color = FACTOR_COLORS.get(f.get("key"), "#8E8E93")
        val = f.get("value")
        weight = f.get("weight")
        contrib = f.get("contribution")
        w = f"{float(weight) * 100:.0f}%" if isinstance(weight, (int, float)) else e(weight or "")
        right = (f'<span class="cc-num">{float(contrib):.1f}</span>' if contrib is not None
                 else '<span class="cc-caption">not reported</span>')
        numeric_w = isinstance(weight, (int, float))
        if isinstance(val, (int, float)):
            detail = f"Value {float(val):.2f}" + (f" × weight {w}" if numeric_w else "")
        else:
            detail = f"Weight {w}" if numeric_w else "Not reported"
        lo, hi = f.get("lower"), f.get("upper")
        if lo is not None and hi is not None and lo != hi:   # bounds only when the server sends a real span
            detail += f" · range {float(lo):.2f}–{float(hi):.2f}"
        prov = pill("Provisional", "amber") if f.get("provisional") else ""
        rows.append((f'<span class="cc-swatch" style="background:{color}"></span>&nbsp; {e(f.get("label"))} {prov}',
                     f'{detail}{" · " + e(f.get("reason")) if f.get("reason") else ""}', right))
    return group(rows)


def target_text(t: dict) -> str:
    """One SMART target as a sentence: '<metric> <= 2 actions' or the milestone value."""
    if t.get("kind") == "milestone":
        return str(t.get("value") or "—")
    parts = [t.get("metric"), t.get("operator"), t.get("value"), t.get("unit")]
    return " ".join(str(p) for p in parts if p not in (None, "")) or str(t.get("target") or "—")


# ----------------------------------------------------------------------------- evidence
def quote_rows(quotes: list[dict]) -> str:
    rows = []
    for q in quotes:
        meta = " ".join(x for x in [lookup_pill(RELATION, q.get("relation")), lookup_pill(VALIDATION, q.get("validation"))] if x)
        src = e(q.get("id") or "No source ID")
        if q.get("locator"):
            src += f" · {e(q['locator'])}"
        if q.get("scope"):
            src += f" · {e(q['scope'])}"
        if q.get("validation_note"):
            src += f"<br>{e(q['validation_note'])}"
        text = (f'<div class="cc-quote">{e(q["text"])}</div>' if q.get("text") else
                '<div class="cc-footnote">Source text not included in this response. Open the record to read it.</div>')
        rows.append((text, f'<div class="cc-pills" style="margin:6px 0 2px">{meta}</div>{src}', ""))
    return group(rows)


def coverage_html(cov: list[dict]) -> str:
    tone = {"covered": "green", "partial": "amber", "gap": "red", "not targeted": "neutral"}
    return '<div class="cc-pills">' + "".join(
        pill(f"{c.get('region')}: {c.get('type')}", tone.get(c.get("status"), "neutral"), dot=True) for c in cov) + "</div>"


# ----------------------------------------------------------------------------- history / assistant
KIND_CLASS = {"PM": "pm", "PM_USER": "pm", "AI": "ai", "AI_SYSTEM": "ai"}


def fmt_time(value) -> str:
    """ISO timestamp as '10 Oct 2026, 09:40 UTC'. Unknown formats pass through unchanged."""
    from datetime import datetime
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return "" if value is None else str(value)
    tz = " UTC" if dt.utcoffset() is not None and dt.utcoffset().total_seconds() == 0 else ""
    return f"{dt.day} {dt:%b %Y, %H:%M}{tz}"   # no %-d: it fails on Windows


def timeline(events: list[dict]) -> str:
    items = []
    for ev in events:
        cls = KIND_CLASS.get(str(ev.get("actor_kind", "")).upper(), "")
        rev = f" · revision {ev['revision']}" if ev.get("revision") is not None else ""
        why = f'<div class="cc-footnote">“{e(ev.get("rationale"))}”</div>' if ev.get("rationale") else \
            '<div class="cc-footnote" style="color:var(--red)">No rationale recorded</div>'
        items.append(f'<div class="cc-tl-item {cls}"><div class="cc-headline" style="font-size:15px">{e(ev.get("summary") or ev.get("action"))}</div>'
                     f'<div class="cc-caption">{e(fmt_time(ev.get("time")))} · {e(ev.get("actor_kind"))} {e(ev.get("actor_id"))}'
                     f'{rev} · {e(ev.get("action"))}</div>{why}</div>')
    return f'<div class="cc-tl">{"".join(items)}</div>'


def conversation(turns: list[dict]) -> str:
    out = []
    for t in turns:
        if not t.get("question") and not t.get("answer"):
            continue                     # suggestion-only proposal (e.g. regenerated draft): no messages
        if t.get("question"):
            out.append(f'<div class="cc-msg pm">{e(t.get("question"))}</div>'
                       f'<div class="cc-msg-meta pm">{e(t.get("actor_id"))} · {e(fmt_time(t.get("time")))}</div>')
        if t.get("answer"):
            cites = " ".join(pill(c, "neutral") for c in t.get("citations", []))
            gaps = " ".join(pill(f"Gap: {g}", "amber") for g in t.get("coverage_gaps", []))
            out.append(f'<div class="cc-msg ai">{e(t.get("answer"))}'
                       f'{f"<div class=cc-pills style=margin-top:8px>{cites} {gaps}</div>" if cites or gaps else ""}</div>'
                       f'<div class="cc-msg-meta">Review assistant · cited answer</div>')
    return f'<div class="cc-msgs">{"".join(out)}</div>'


def diff_table(patch: dict) -> str:
    rows = []
    for field, change in (patch or {}).items():
        before = change.get("before") if isinstance(change, dict) else None
        after = change.get("after") if isinstance(change, dict) else change
        rows.append(f'<tr><td>{e(field)}</td><td class="before">{e(_short(before))}</td>'
                    f'<td class="after">{e(_short(after))}</td></tr>')
    return ('<table class="cc-diff"><tr><th>Field</th><th>Before</th><th>After</th></tr>'
            + "".join(rows) + "</table>")


def _short(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v) or "—"
    return str(v)


def steps(all_stages: list[str], current: str, state: str) -> str:
    out = []
    reached = all_stages.index(current) if current in all_stages else -1
    for i, s in enumerate(all_stages):
        cls = "done" if i < reached or (state == "succeeded" and i <= reached) else ("now" if i == reached else "")
        out.append(f'<span class="cc-step {cls}">{e(s.replace("_", " "))}</span>')
    return f'<div class="cc-steps">{"".join(out)}</div>'
