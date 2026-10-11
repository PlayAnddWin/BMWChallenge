"""
Step 08: ConceptCraft PM workbench (Role 4).

A thin Streamlit client over Role 3's API. It renders what the server returns and
sends exactly what the PM submits. It does not score, guard transitions, call
models or touch the database.

Run against the configured API:
    streamlit run pipeline/08_streamlit_ui.py
Run on the labelled SYNTHETIC fixtures (no API needed):
    streamlit run pipeline/08_streamlit_ui.py -- --fixtures
Run on Role 3's own contract examples (contracts/fixture_case.json, synthetic):
    streamlit run pipeline/08_streamlit_ui.py -- --mock

Design: Apple Human Interface Guidelines adapted to the web (system font stack,
inset grouped lists, segmented controls, sheets, one accent colour). Startup
instructions are in README.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ui.components as c  # noqa: E402
from ui.client import (ACTION_LABELS, STATUS_LABELS, ApiClient, ApiError, fill_patch_before,  # noqa: E402
                       fill_quote_text, pretty)
from ui.forms import build_patch, draft_key, is_v1, patch_diff, submit_decision  # noqa: E402
from ui.theme import css  # noqa: E402

st.set_page_config(page_title="ConceptCraft · Requirement Workbench", page_icon="◆", layout="wide",
                   initial_sidebar_state="expanded")
st.markdown(css(), unsafe_allow_html=True)

PAGE_SIZE = 50
VEHICLES = ["F70", "G60", "G68", "G70"]
POWERTRAINS = ["BEV", "ICE", "PHEV", "HEV", "MHEV"]
ACTORS = ["pm-demo-1 · PM", "pm-demo-2 · PM"]
STAGES = ["import_check", "consolidate", "retrieve", "draft", "validate", "score", "completed"]
STATUS_FILTER = ["All", "DRAFT", "PM_REVIEW", "APPROVED", "REJECTED", "POSTPONED"]
TERMINAL_RUN_STATES = {"succeeded", "partial", "failed"}


# ============================================================================ session helpers
SOURCES = {"live": "Live API", "snapshot": "Pipeline snapshot", "mock": "Backend contract mock (synthetic)",
           "synthetic": "Synthetic preview"}


def make_client(source: str):
    if source == "synthetic":
        from ui.fixtures import FixtureClient
        return FixtureClient()
    if source == "mock":
        from ui.contract_mock import ContractMockClient
        return ContractMockClient()
    if source == "snapshot":
        from ui.snapshot import SnapshotClient
        return SnapshotClient()
    return ApiClient()


def get_client():
    """Live API by default. Snapshot or synthetic data only on explicit choice (flag or sidebar).

    Tests inject a client through st.session_state["client"].
    """
    if "client" not in st.session_state:
        flags = {"--fixtures": "synthetic", "--snapshot": "snapshot", "--mock": "mock"}
        source = next((v for k, v in flags.items() if k in sys.argv), "live")
        try:
            st.session_state.client = make_client(source)
        except ApiError as err:
            st.session_state.client = ApiClient()
            st.session_state["_source_error"] = err.message
    return st.session_state.client


def client_source(client) -> str:
    if getattr(client, "contract_mock", False):
        return "mock"
    if getattr(client, "synthetic", False):
        return "synthetic"
    if getattr(client, "snapshot", False):
        return "snapshot"
    return "live"


def switch_source():
    choice = st.session_state.get("source_choice")
    if not choice or choice == client_source(st.session_state.get("client")):
        return
    try:
        st.session_state.client = make_client(choice)
        st.session_state.pop("_source_error", None)
    except ApiError as err:
        st.session_state["_source_error"] = err.message
        return
    for k in list(st.session_state):
        if k.startswith(("export:", "review_run:", "finished_run:")):
            st.session_state.pop(k, None)
    for k in ("selected", "req_table", "run_id", "run_done", "freeze_id", "pages", "_filter_sig"):
        st.session_state.pop(k, None)


def use_fixtures():
    st.session_state["source_choice"] = "synthetic"
    switch_source()


def actor_id() -> str:
    return st.session_state.get("actor", ACTORS[0]).split(" · ")[0]


def flash(msg: str, icon: str = "✅"):
    st.session_state["_flash"] = (msg, icon)


def show_flash():
    if "_flash" in st.session_state:
        msg, icon = st.session_state.pop("_flash")
        st.toast(msg, icon=icon)


def show_error(err: ApiError, what: str = "load this"):
    """Failures are never shown as empty results."""
    if err.is_unreachable:
        st.markdown(c.banner(f"<b>The API is not reachable.</b> Could not {c.e(what)}. {c.e(err.message)} "
                             "Nothing below is an empty result; it simply was not loaded.", "red"),
                    unsafe_allow_html=True)
    elif err.status == 501:
        st.markdown(c.banner(f"<b>Not available on this server.</b> {c.e(err.message)}", "neutral"),
                    unsafe_allow_html=True)
    elif err.is_conflict:
        st.markdown(c.banner(f"<b>Someone changed this first.</b> {c.e(err.message)}", "amber"),
                    unsafe_allow_html=True)
    elif err.is_validation:
        items = "".join(f"<li>{c.e(p)}</li>" for p in err.problems)
        st.markdown(c.banner(f"<b>{c.e(err.message)}</b><ul style='margin:6px 0 0 18px'>{items}</ul>", "red"),
                    unsafe_allow_html=True)
    else:
        st.markdown(c.banner(f"<b>Request failed ({c.e(err.status)}).</b> {c.e(err.message)}", "red"),
                    unsafe_allow_html=True)


def safe(fn, *args, what: str = "load this", **kwargs):
    """Run a read; on failure show the error and return None (never a fake empty value)."""
    try:
        return fn(*args, **kwargs)
    except ApiError as err:
        show_error(err, what)
        return None


def html(s: str):
    st.markdown(s, unsafe_allow_html=True)


# ============================================================================ sheets (dialogs)
@st.dialog("Record decision", width="large")
def decision_sheet(req: dict, action: str):
    client = get_client()
    label = ACTION_LABELS.get(action, action)
    html(f'<div class="cc-title">{c.e(label)}</div>'
         f'<div class="cc-subhead">{c.e(req["id"])} · revision {c.e(req.get("revision") or "legacy")} · '
         f'{c.e(req["title"])}</div>')

    reviewed, accepted, sources = [], [], []
    review_key = f"{req['id']}:{req.get('revision')}"
    if action == "approve":
        # Every target, source and unresolved assumption is listed; the server checks completeness.
        targets = [t for t in (req.get("smart") or {}).get("targets", []) if t.get("id")]
        if targets:
            html(c.section("SMART targets"))
            for t in targets:
                if st.checkbox(f"I reviewed {t['id']}: {c.target_text(t)}", key=f"rv:{review_key}:{t['id']}",
                               value=t.get("review_state") == "reviewed"):
                    reviewed.append(t["id"])
        refs = list(dict.fromkeys(
            [link["record_id"] for link in req.get("evidence_links") or [] if link.get("record_id")]
            + [row["source_record_id"] for group in ("quotes", "facts", "documents")
               for row in req.get("evidence", {}).get(group) or [] if row.get("source_record_id")]))
        if refs:
            html(c.section("Evidence sources"))
            for ref in refs:
                if st.checkbox(f"I reviewed source {ref}", key=f"src_rv:{review_key}:{ref}"):
                    sources.append(ref)
        unresolved = [a for a in req.get("assumptions", []) if a.get("state") in ("open", "accepted")]
        if unresolved:
            html(c.section("Assumptions"))
            for a in unresolved:
                if st.checkbox(f"Accept {a['id']}: {a['text']}", key=f"acc:{review_key}:{a['id']}",
                               value=a.get("state") == "accepted"):
                    accepted.append(a["id"])

    scale = req.get("score", {}).get("scale", {})
    with st.expander("Score override", expanded=False):
        st.caption("Off leaves the current effective score unchanged. On sends your value, including 0. "
                   "The calculated score is kept beside it.")
        override_enabled = st.toggle("Override the calculated priority", key=f"ovr_on:{req['id']}")
        override_value = st.number_input(
            f"Override value ({scale.get('min', 0)}–{scale.get('max', 100)})",
            min_value=float(scale.get("min", 0)), max_value=float(scale.get("max", 100)),
            value=float(req.get("score", {}).get("effective") or 0), step=0.5,
            disabled=not override_enabled, key=f"ovr_val:{req['id']}")

    rationale = st.text_area("Rationale", key=f"why:{req['id']}:{action}",
                             placeholder="Why are you making this decision? This goes into the audit history.")
    if action == "approve":
        html(c.banner("Approval records this exact revision. Later edits move it back to review.", "blue"))

    col_a, col_b = st.columns([1, 1])
    if col_a.button("Cancel", width="stretch"):
        st.rerun()
    if col_b.button(label, type="primary", width="stretch", key=f"go:{action}"):
        try:
            res = submit_decision(client, req, action, actor=actor_id(), rationale=rationale, reviewed=reviewed,
                                  accepted=accepted, override_enabled=override_enabled,
                                  override_value=override_value, reviewed_sources=sources)
            flash(f"{label}: {req['id']} is now {STATUS_LABELS.get(res.get('status'), res.get('status'))}"
                  f"{' at revision ' + str(res['revision']) if res.get('revision') else ''}.")
            st.rerun()
        except ApiError as err:  # the typed rationale stays in the text area
            show_error(err, "record the decision")
            if err.is_conflict:
                st.caption("Your rationale is kept. Reload to see the latest revision before deciding again.")
                if st.button("Reload the latest revision", key="reload409"):
                    st.rerun()


@st.dialog("Edit requirement", width="large")
def edit_sheet(req: dict):
    client = get_client()
    key = draft_key(req)
    smart = req.get("smart") or {}
    draft = st.session_state.setdefault(key, {
        "title": req.get("title") or "", "description": req.get("description") or "",
        "measure_value": str((smart.get("measure") or {}).get("value", "")),
        "metric_targets": {m["id"]: str(m.get("target", "")) for m in smart.get("metrics") or [] if m.get("id")},
        "regions": list(req.get("target_scope", {}).get("regions", [])), "rationale": ""})
    html(f'<div class="cc-subhead">{c.e(req["id"])} · editing revision {c.e(req.get("revision"))}</div>')
    draft["title"] = st.text_input("Title", draft["title"])
    draft["description"] = st.text_area("Description", draft["description"], height=90)
    if is_v1(req):
        for m in smart["metrics"]:
            draft["metric_targets"][m["id"]] = st.text_input(
                f"Target {m['id']} ({m.get('operator', '')} … {m.get('unit', '')}); becomes PM-entered and "
                "needs review", draft["metric_targets"].get(m["id"], ""), key=f"edit_t:{req['id']}:{m['id']}")
    else:
        draft["measure_value"] = st.text_input("Target value (becomes PM-entered and needs review)",
                                               draft["measure_value"])
    draft["regions"] = st.multiselect("Target regions", ["EU", "US", "CN", "RoW"], draft["regions"])
    draft["rationale"] = st.text_area("Why this change?", draft["rationale"])
    patch = build_patch(req, draft)
    if patch:
        html(c.diff_table(patch_diff(req, patch)))
    if req.get("status") == "APPROVED":
        html(c.banner("Saving creates a new revision and returns this requirement to review.", "amber"))
    a, b = st.columns(2)
    if a.button("Close (keep draft)", width="stretch"):
        st.rerun()
    if b.button("Save new revision", type="primary", disabled=not patch, width="stretch"):
        try:
            res = client.edit(req["id"], req.get("revision"), patch, actor_id(), draft["rationale"])
            st.session_state.pop(key, None)
            flash(f"Saved {req['id']} as revision {res.get('revision')}.")
            st.rerun()
        except ApiError as err:
            show_error(err, "save the edit")
            if err.is_conflict:
                st.caption("Your draft is kept. Reload to see the latest revision, then reapply your changes.")


@st.dialog("Apply suggestion", width="large")
def apply_sheet(req: dict, turn: dict):
    client = get_client()
    html(c.banner("Applying creates a new revision with these changes. <b>It does not approve the "
                  "requirement.</b> Approval stays a separate decision.", "indigo"))
    html(c.diff_table(turn.get("proposed_patch") or {}))
    rationale = st.text_area("Why accept this change?", key=f"apply_why:{turn['id']}")
    a, b = st.columns(2)
    if a.button("Cancel", width="stretch"):
        st.rerun()
    if b.button("Apply as new revision", type="primary", width="stretch"):
        try:
            res = client.apply_suggestion(req["id"], turn["id"], req.get("revision"),
                                          turn.get("patch") or turn.get("proposed_patch") or {}, actor_id(),
                                          rationale)
            flash(f"Applied {turn['id']}. {req['id']} is now revision {res.get('revision')} and still needs a "
                  "decision.")
            st.rerun()
        except ApiError as err:
            show_error(err, "apply the suggestion")


@st.dialog("Discard suggestion")
def discard_sheet(thread_id: str, turn: dict, req: dict):
    client = get_client()
    rationale = st.text_input("Reason", key=f"disc_why:{turn['id']}",
                              help="Recorded in the audit history. The server requires a reason.")
    if st.button("Discard", type="primary", width="stretch"):
        try:
            client.discard_suggestion(turn.get("thread_id") or thread_id, turn["id"], actor_id(), rationale,
                                      rid=req["id"], expected_revision=req.get("revision"))
            flash(f"Discarded {turn['id']}.", "🗑️")
            st.rerun()
        except ApiError as err:
            show_error(err, "discard the suggestion")


@st.dialog("Source record", width="large")
def source_sheet(record_id: str):
    rec = safe(get_client().get_source_record, record_id, what=f"open {record_id}")
    if rec is None:
        return
    loc = rec.get("locator") or {}
    html(f'<div class="cc-headline">{c.e(rec.get("id"))}</div>'
         f'<div class="cc-subhead">{c.e(rec.get("file"))} · '
         f'{c.e(" · ".join(f"{k} {v}" for k, v in loc.items()))}</div>')
    vals = rec.get("values") or {}
    rows = []
    for k, v in vals.items():
        if isinstance(v, dict):
            v = ", ".join(f"{a}: {b}" for a, b in v.items())
        rows.append((c.e(k), "", f'<span class="cc-callout">{c.e(v)}</span>'))
    html(c.group(rows))


# ============================================================================ sidebar
NAV_ICONS = {"Overview": "space_dashboard", "Requirements": "checklist", "Sources": "database",
             "Analysis": "play_circle", "Review package": "inventory_2"}


def sidebar(client):
    with st.sidebar:
        html('<div class="cc-nav-brand"><div class="cc-nav-mark" aria-hidden="true">◆</div>'
             '<div><div class="cc-nav-name">ConceptCraft</div>'
             '<div class="cc-nav-tag">Requirement workbench</div></div></div>'
             '<div class="cc-nav-label">Workspace</div>')
        st.radio("Navigate", list(NAV_ICONS), key="page", label_visibility="collapsed",
                 format_func=lambda p: f":material/{NAV_ICONS[p]}: {p}")
        html('<div class="cc-nav-label spaced">Session</div>')
        st.session_state["source_choice"] = client_source(client)
        st.selectbox("Data source", list(SOURCES), key="source_choice", format_func=SOURCES.get,
                     on_change=switch_source,
                     help="Live API is the default. The snapshot is the pipeline's last export (read-only). "
                          "The synthetic preview is invented data for demos and tests.")
        if st.session_state.get("_source_error"):
            html(c.banner(c.e(st.session_state["_source_error"]), "red"))
        st.selectbox("Acting as", ACTORS, key="actor")
        if getattr(client, "contract_mock", False):
            html(c.banner(f"<b>{c.e(client.source_label)}.</b> Role 3's contract examples, served in memory. "
                          "Not BMW findings; decisions are not saved.", "amber"))
        elif client.synthetic:
            html(c.banner("<b>Synthetic preview.</b> Invented data for UI development. Not BMW findings.",
                          "amber"))
        elif getattr(client, "snapshot", False):
            html(c.pill("Pipeline snapshot · read-only", "blue", dot=True)
                 + f'<div class="cc-caption" style="margin-top:6px">{c.e(client.path.name)}'
                   f'{" · " + c.e(c.fmt_time(client.exported_at)) if client.exported_at else ""}'
                    '. Start the API to record decisions.</div>')
        else:
            try:
                mode = client.health()["mode"]
                html(c.pill("Connected" if mode == "v1" else "Connected · legacy API", "green" if mode == "v1"
                            else "amber", dot=True)
                     + f'<div class="cc-caption" style="margin-top:6px">{c.e(client.base_url)}</div>')
            except ApiError as err:
                html(c.pill("API unreachable", "red", dot=True)
                     + f'<div class="cc-caption" style="margin-top:6px">{c.e(err.message)}</div>')
                st.button("Open pipeline snapshot", width="stretch",
                           on_click=lambda: (st.session_state.update(source_choice="snapshot"), switch_source()),
                           help="Read-only view of the full backend export in BMW_OUTPUT_DIR.")
                st.button("Use synthetic preview", on_click=use_fixtures, width="stretch",
                          help="Explicitly switch to labelled synthetic data. Nothing is mixed with live data.")
        if st.button("Refresh", icon=":material/refresh:", width="stretch"):
            st.rerun()
        html('<div class="cc-nav-foot">Role 4 · PM workbench</div>')


# ============================================================================ overview
def overview_page(client):
    from ui.overview import render_overview
    render_overview(client, open_requirement)


def open_requirement(rid: str):
    st.session_state["page"] = "Requirements"
    st.session_state["selected"] = rid
    st.session_state.pop("req_table", None)


# ============================================================================ requirements list
def requirements_page(client):
    if st.session_state.get("selected"):
        return workbench(client, st.session_state["selected"])

    head, act = st.columns([3, 2], vertical_alignment="bottom")
    head.markdown('<div class="cc-largetitle">Requirements</div>'
                  '<div class="cc-subhead">Ranked by the server. Select a row to review it.</div>',
                  unsafe_allow_html=True)
    with act:
        b1, b2 = st.columns(2)
        with b1.popover("Export sample list", width="stretch"):
            export_controls(client, "sample_list", None)
        b2.button("Run analysis", type="primary", width="stretch",
                  on_click=lambda: st.session_state.update(page="Analysis"))

    html('<div style="height:14px"></div>')
    f1, f2, f3, f4, f5 = st.columns([2.6, 1.3, 1.3, 1.3, 1.3])
    filters = {
        "q": f1.text_input("Search", placeholder="Search title or ID", label_visibility="collapsed"),
        "derivative": f2.selectbox("Vehicle", ["All", *VEHICLES], label_visibility="collapsed",
                                   format_func=lambda v: "All vehicles" if v == "All" else v),
        "region": f3.selectbox("Region", ["All", "EU", "US", "CN", "RoW"], label_visibility="collapsed",
                               format_func=lambda v: "All regions" if v == "All" else v),
        "powertrain": f4.selectbox("Powertrain", ["All", *POWERTRAINS], label_visibility="collapsed",
                                   format_func=lambda v: "All powertrains" if v == "All" else v),
        "evidence": f5.selectbox("Evidence", ["All", "High", "Strong", "Moderate", "Low", "Weak"], label_visibility="collapsed",
                                 format_func=lambda v: "Any evidence" if v == "All" else f"{v} evidence"),
    }
    st.session_state.setdefault("status_filter", "All")
    status = st.segmented_control("Status", STATUS_FILTER, key="status_filter",
                                  format_func=lambda s: "All" if s == "All" else STATUS_LABELS[s],
                                  label_visibility="collapsed")
    filters["status"] = None if status in (None, "All") else status
    filters = {k: v for k, v in filters.items() if v not in (None, "", "All")}

    sig = repr(sorted(filters.items()))
    if st.session_state.get("_filter_sig") != sig:
        st.session_state["_filter_sig"] = sig
        st.session_state["pages"] = 1
    limit = PAGE_SIZE * st.session_state.get("pages", 1)

    page = safe(client.list_requirements, filters, 0, limit, what="load requirements")
    if page is None:
        return
    if page.get("notice"):
        html(c.banner(c.e(page["notice"]), "neutral"))
    items = page["items"]
    if not items:
        html(c.banner("No requirements match these filters. The request succeeded; there is simply nothing "
                      "to show.", "neutral"))
        return

    rows = []
    for r in items:
        legacy = r.get("legacy") or r["score"].get("scale", {}).get("max") == 10
        eff = r["score"].get("effective")
        rows.append({
            "Rank": r.get("rank"), "ID": r["id"], "Requirement": r["title"],
            "Scope": c.scope_text(r.get("target_scope")),
            "Priority": None if legacy else eff,
            "Legacy 0–10": f"{eff:.2f}" if legacy and eff is not None else "",
            "Evidence": (r["evidence"].get("level") or "—") + (" (legacy)" if legacy else ""),
            "Flags": ", ".join([c.FLAG.get(f, (f,))[0] for f in r.get("flags", []) if f != "legacy_unverified"]
                               + (["Conflict"] if r.get("has_conflict")
                                  and "supported_conflict" not in r.get("flags", []) else [])),
            "Status": STATUS_LABELS.get(r["status"], r["status"]),
        })
    df = pd.DataFrame(rows)
    all_legacy = all(r.get("legacy") or r["score"].get("scale", {}).get("max") == 10 for r in items)
    if all_legacy:   # one scale on screen: show the legacy score as the bar, clearly labelled
        df["Priority"] = pd.to_numeric(df["Legacy 0–10"], errors="coerce")
        df = df.drop(columns=["Legacy 0–10"])
    for col in ("Rank", "Priority"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    event = st.dataframe(
        df, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
        key="req_table",
        column_order=[x for x in ["Rank", "ID", "Requirement", "Status", "Priority", "Legacy 0–10", "Evidence",
                                  "Flags", "Scope"] if x in df.columns],
        column_config={
            "Rank": st.column_config.NumberColumn(width=56),
            "ID": st.column_config.TextColumn(width=120),
            "Scope": st.column_config.TextColumn(width=190),
            "Priority": (st.column_config.ProgressColumn("Priority (legacy 0–10)", min_value=0, max_value=10,
                                                         format="%.2f", width=150,
                                                         help="Legacy v0 score. Not comparable with v1.")
                         if all_legacy else
                         st.column_config.ProgressColumn("Priority v1", min_value=0, max_value=100, format="%.1f",
                                                         width=130, help="Server-calculated priority, 0–100.")),
            "Legacy 0–10": st.column_config.TextColumn(width=90,
                                                         help="Legacy v0 score. Not comparable with v1."),
            "Evidence": st.column_config.TextColumn(width=110),
            "Flags": st.column_config.TextColumn(width=150),
            "Status": st.column_config.TextColumn(width=105),
        })
    sel = (event.selection.rows if event and hasattr(event, "selection") else []) or []
    if sel:
        st.session_state["selected"] = df.iloc[sel[0]]["ID"]
        st.rerun()

    total = page.get("total")
    shown = f"Showing {len(items)}" + (f" of {total}" if total is not None else " (total unknown)")
    cap, more = st.columns([4, 1], vertical_alignment="center")
    cap.caption(shown + ". Exports always come from the server and include every record.")
    if total is None or len(items) < total:
        if more.button("Load more", width="stretch", disabled=total is None and len(items) < limit):
            st.session_state["pages"] = st.session_state.get("pages", 1) + 1
            st.rerun()


def export_controls(client, what: str, ref, formats=("csv", "json")):
    """Two steps: prepare (one server request), then download. Nothing is rebuilt from the table."""
    fmt = formats[0] if len(formats) == 1 else st.radio(
        "Format", formats, horizontal=True, key=f"fmt:{what}:{ref}", format_func=lambda f: f.upper())
    k = f"export:{what}:{ref}:{fmt}"
    if st.button(f"Prepare {fmt.upper()} export", key=f"prep:{k}"):
        try:
            st.session_state[k] = client.download(what, fmt, ref)
        except ApiError as err:
            show_error(err, "prepare the export")
    if k in st.session_state:
        data, mime, name = st.session_state[k]
        st.download_button(f"Download {name}", data, file_name=name, mime=mime, key=f"dl:{k}",
                           type="primary")


# ============================================================================ workbench
def workbench(client, rid: str):
    if st.button("‹ Requirements", key="back"):
        st.session_state.pop("selected", None)
        st.session_state.pop("req_table", None)   # forget the table selection
        st.rerun()
    req = safe(client.get_requirement, rid, what=f"load {rid}")
    if req is None:
        return

    rank = f"Rank {req['rank']} · " if req.get("rank") else ""
    html(f'<div class="cc-caption">{c.e(rank)}{c.e(req["id"])} · revision {c.e(req.get("revision") or "—")}</div>'
         f'<div class="cc-largetitle">{c.e(req["title"])}</div>'
         f'<div class="cc-pills" style="margin:8px 0 4px">{c.status_pill(req["status"])}'
         f'{c.evidence_pill(req["evidence"].get("level"), req.get("legacy"))}{c.flags_html(req)}</div>')
    if req.get("approved_revision"):
        st.caption(f"Approved revision: {req['approved_revision']}")
    if req.get("legacy"):
        html(c.banner("<b>Legacy record.</b> Scored 0–10 by the old pipeline, quotes are not verified against "
                      "the source, and the score is not comparable with v1 priorities.", "neutral"))

    if getattr(client, "snapshot", False):
        html(c.banner("<b>Read-only snapshot.</b> Start the API to approve, reject or defer this requirement.",
                      "blue"))
    # Prefer the server's allowed actions; older v1 servers validate actions on submission.
    if not req.get("legacy") and not getattr(client, "snapshot", False) and not req.get("actions_published", True):
        st.caption("Decisions are validated by the backend for the current revision.")
    actions = [a for a in ACTION_LABELS if a in req.get("allowed_actions", [])]
    widths = [len(ACTION_LABELS[x]) + 6 for x in actions] + [10, 19]
    cols = st.columns(widths + [max(10, 110 - sum(widths))])
    for i, action in enumerate(actions):
        if cols[i].button(ACTION_LABELS[action], key=f"act:{action}", width="stretch",
                          type="primary" if action == "approve" else "secondary"):
            decision_sheet(req, action)
    cols = cols[len(actions):]
    read_only = bool(req.get("legacy") or getattr(client, "snapshot", False))
    if cols[0].button("Edit", width="stretch", disabled=read_only):
        edit_sheet(req)
    cols[1].button("Ask assistant", width="stretch", disabled=read_only,
                    on_click=lambda: st.session_state.update({f"tab:{rid}": "Assistant"}))

    st.session_state.setdefault(f"tab:{rid}", "Requirement")
    tab = st.segmented_control("Section", ["Requirement", "Priority", "Evidence", "Assistant", "History"],
                               key=f"tab:{rid}", label_visibility="collapsed") or "Requirement"
    {"Requirement": tab_requirement, "Priority": tab_priority, "Evidence": tab_evidence,
     "Assistant": tab_assistant, "History": tab_history}[tab](client, req)


def tab_requirement(client, req):
    left, right = st.columns([3, 2], gap="large")
    smart = req.get("smart") or {}
    with left:
        html(c.section("Description"))
        html(f'<div class="cc-card"><div class="cc-body">{c.e(req.get("description") or "—")}</div>'
              + (f'<div class="cc-footnote" style="margin-top:8px">Problem: {c.e(req["problem_statement"])}</div>'
                 if req.get("problem_statement") else "") + "</div>")
        if req.get("jtbd"):
            html(c.section("Job to be done"))
            html(c.group([(c.e(req["jtbd"]), "", "")]))
        if req.get("claims"):
            html(c.section("Claims and provenance"))
            for claim in req["claims"]:
                refs = ", ".join(str(ref) for ref in claim.get("evidence_ids") or [])
                meta = " · ".join(str(value) for value in (claim.get("id"), claim.get("kind"), refs) if value)
                html(c.group([(c.e(claim.get("text")), c.e(meta),
                               c.lookup_pill(c.PROVENANCE, claim.get("provenance")) + " "
                               + c.lookup_pill(c.REVIEW, claim.get("review_state")))]))
        if req.get("alternatives"):
            html(c.section("Alternatives considered"))
            html(c.group([(c.e(option), "", "") for option in req["alternatives"]]))
        html(c.section("SMART"))
        rows = []
        if smart.get("specific"):
            rows.append(("Specific", c.e(smart["specific"]), ""))
        for t in smart.get("targets", []):           # every metric is its own row, then the milestone
            sub = " · ".join(str(x) for x in [t.get("conditions"), t.get("verification")] if x)
            acc = c.lookup_pill(c.PROVENANCE, t.get("provenance")) + " " + c.lookup_pill(c.REVIEW,
                                                                                         t.get("review_state"))
            rows.append((f'{"Time-bound" if t.get("kind") == "milestone" else "Measurable"}'
                         f'{" · " + c.e(t["id"]) if t.get("id") else ""}<br><span class="cc-callout">'
                         f'{c.e(c.target_text(t))}</span>', c.e(sub), acc))
        for key, label in (("attainability", "Achievable"), ("relevance", "Relevant")):
            if smart.get(key):
                rows.append((label, c.e(smart[key]), ""))
        html(c.group(rows) or c.banner("No SMART details returned.", "neutral"))
        if smart.get("model_flags"):
            flags = " ".join(c.pill(f"{k.upper()}: {'yes' if v else 'no'}", "neutral")
                             for k, v in smart["model_flags"].items())
            html(f'<div class="cc-footnote" style="margin-top:8px">Model self-assessment (assertions, not checks): '
                 f'{flags}</div>')

    with right:
        html(c.section("Scope"))
        ts = req.get("target_scope", {})
        rows = [("Target", c.e(c.scope_text(ts)), ""),
                ("Evidence from", c.e(c.scope_text(req.get("source_scope", {}))), "")]
        if ts.get("carryover_assumption"):
            rows.append(("Carry-over", c.e(ts["carryover_assumption"]), ""))
        html(c.group(rows))
        html(c.section("Assumptions"))
        html(c.group([(c.e(a["text"]), c.e(a["id"]) + (f" · {c.e(a['accepted_rationale'])}"
                                                       if a.get("accepted_rationale") else ""),
                       c.lookup_pill(c.REVIEW, a.get("state"))) for a in req.get("assumptions", [])])
             or '<div class="cc-footnote">None recorded.</div>')
        if req.get("uncertainties"):
            html(c.section("Uncertainties"))
            html(c.group([(c.e(u), "", "") for u in req["uncertainties"]]))
        if req.get("has_conflict"):
            html(c.banner(f"<b>Conflicting evidence.</b> {c.e(req.get('conflict_detail') or '')}", "red"))
        if req.get("kano") or req.get("moscow"):
            html(c.section("AI assessment"))
            html(c.group([("Kano", "", c.pill(req.get("kano") or "—", "indigo")),
                          ("MoSCoW", "", c.pill(req.get("moscow") or "—", "indigo"))]))


def tab_priority(client, req):
    score = req.get("score", {})
    scale = score.get("scale", {})
    left, right = st.columns([2, 3], gap="large")
    with left:
        html(f'<div class="cc-card"><div class="cc-sectionlabel" style="margin-top:0">'
             f'{c.e(scale.get("label", "Priority"))}</div>'
             f'<div class="cc-bignum">{c.fmt_score(score.get("effective"), scale)}'
             f'<small>/ {c.e(scale.get("max", 100))}</small></div>'
             + (c.pill("Provisional", "amber") if score.get("provisional") else "")
             + (f'<div class="cc-footnote" style="margin-top:8px">Rank {c.e(req["rank"])}</div>'
                if req.get("rank") else "") + "</div>")
        ov = score.get("override")
        if ov:
            html(c.banner(f"<b>PM override in force: {c.e(ov.get('value'))}.</b> Calculated score "
                          f"{c.fmt_score(score.get('calculated'), scale)}. {c.e(ov.get('rationale') or '')}",
                          "blue"))
        rng = score.get("range")
        if rng:
            html(c.section("Sensitivity range"))
            html(c.group([(f'{c.fmt_score(rng.get("low"), scale)} – {c.fmt_score(rng.get("high"), scale)}',
                           c.e(rng.get("label")) + ("" if "not a statistical confidence interval" in
                                                    (rng.get("label") or "") else
                                                    "<br>This is a sensitivity / provisional range, "
                                                    "not a statistical confidence interval."), "")]))
    with right:
        if score.get("factors"):
            html(c.section("What makes up the score"))
            if any(f.get("contribution") is not None for f in score["factors"]):
                html(c.storage_bar(score))
            html(c.factor_legend(score))
        else:
            html(c.banner("The server returned no factor breakdown for this score.", "neutral"))
        if score.get("warnings"):
            html(c.section("Scoring warnings"))
            html(c.group([(c.e(w), "", c.pill("Warning", "amber")) for w in score["warnings"]]))
        if score.get("rationale"):
            html(f'<div class="cc-footnote" style="margin-top:10px">{c.e(score["rationale"])}</div>')
        if score.get("config_id"):
            st.caption(f"Scoring config: {score['config_id']}")


def tab_evidence(client, req):
    ev = safe(client.get_evidence, req["id"], what="load evidence") if not req.get("legacy") else req["evidence"]
    if ev is None:
        return
    ev = fill_quote_text(ev, req)
    left, right = st.columns([2, 3], gap="large")
    with left:
        eci = ev.get("eci")
        comp = ev.get("components") or {}
        meters = "".join(
            f'<div style="margin-top:10px"><div class="cc-footnote">{c.e(lbl)} '
            f'<span class="cc-num" style="float:right">{float(comp[k]) * 100:.0f}%</span></div>'
            f'<div class="cc-meter"><i style="width:{float(comp[k]) * 100:.1f}%"></i></div></div>'
            for k, lbl in (("support", "Support"), ("coverage", "Coverage"), ("corroboration", "Corroboration"),
                           ("assignment_validation", "Assignment review")) if comp.get(k) is not None)
        if eci is None and ev.get("feedback_count") is not None:
            themes = "".join(c.pill(t, "neutral") for t in ev.get("key_themes") or [])
            html(f'<div class="cc-card"><div class="cc-sectionlabel" style="margin-top:0">Customer comments behind '
                 f'this requirement</div><div class="cc-bignum">{c.e(ev["feedback_count"])}</div>'
                 f'{c.evidence_pill(ev.get("level"), req.get("legacy"))}'
                 f'<div class="cc-footnote" style="margin-top:10px">No evidence confidence index on legacy records; '
                 f'the tier comes from the pipeline.</div>'
                 + (f'<div class="cc-sectionlabel">Key themes</div><div class="cc-pills">{themes}</div>' if themes
                    else "") + '</div>')
        else:
            html(f'<div class="cc-card"><div class="cc-sectionlabel" style="margin-top:0">Evidence confidence</div>'
                 f'<div class="cc-bignum">{"—" if eci is None else f"{eci:.0f}"}<small>/ 100</small></div>'
                 f'{c.evidence_pill(ev.get("level"), req.get("legacy"))}{meters}</div>')
        if ev.get("bucket_comment_count") is not None:
            html(c.group([
                ("Comments in topic", "", f'<span class="cc-num">{c.e(ev.get("bucket_comment_count"))}</span>'),
                ("Comments supporting this issue", "Topic volume is not issue support.",
                 f'<span class="cc-num">{c.e(ev.get("issue_support_count"))}</span>'),
            ]))
        if ev.get("distinct_support_comments") is not None:
            html(c.group([("Distinct supporting comments", "Counted by the server, after de-duplication.",
                           f'<span class="cc-num">{c.e(ev["distinct_support_comments"])}</span>')]))
        rng = ev.get("range")
        if rng and rng.get("low") is not None:
            html(c.group([(f'ECI range {rng["low"]:.1f} – {rng["high"]:.1f}',
                           c.e(rng.get("label")) + ". The ECI is an evidence rubric, not a probability.", "")]))
        if ev.get("warnings"):
            html(c.section("Evidence warnings"))
            html(c.group([(c.e(w), "", c.pill("Warning", "amber")) for w in ev["warnings"]]))
        if ev.get("regional_coverage"):
            html(c.section("Regional coverage"))
            html(c.coverage_html(ev["regional_coverage"]))
            html('<div class="cc-footnote" style="margin-top:6px">A gap means no data, not zero demand.</div>')
        if ev.get("gaps"):
            html(c.section("Evidence gaps"))
            html(c.group([(c.e(g), "", c.pill("Gap", "amber")) for g in ev["gaps"]]))
    with right:
        quotes = ev.get("quotes") or []
        html(c.section(f"Customer quotes ({len(quotes)})"))
        if not quotes:
            html(c.banner("<b>No customer comments support this requirement.</b> Treat any score as provisional.",
                          "amber"))
        for index, q in enumerate(quotes):
            html(c.quote_rows([q]))
            if q.get("source_record_id"):
                if st.button(f"Open {q['id']}", key=f"src:{q['source_record_id']}:{index}"):
                    source_sheet(q["source_record_id"])
        for title, key in (("Source facts (native values)", "facts"), ("Documents", "documents")):
            items = ev.get(key) or []
            if items:
                html(c.section(title))
                for index, it in enumerate(items):
                    if key == "documents":
                        html(c.quote_rows([it]))
                    else:
                        value = it.get("value") if it.get("value") is not None else it.get("id")
                        html(c.group([(c.e(it.get("label")), c.e(value),
                                       c.lookup_pill(c.RELATION, it.get("relation")))]))
                    if it.get("source_record_id") and st.button(f"Open {it['id']}", key=f"src:{key}:{it['id']}:{index}"):
                        source_sheet(it["source_record_id"])


def tab_assistant(client, req):
    if req.get("legacy"):
        html(c.banner("The review assistant needs the new /api contract.", "neutral"))
        return
    run_key = f"review_run:{req['id']}"
    review_run = st.session_state.get(run_key)
    active = run_status(client, review_run) if review_run else False
    thread = safe(client.get_thread, req["id"], what="load the review thread")
    if thread is None:
        return
    if thread.get("unavailable"):
        html(c.banner(c.e(thread["unavailable"]), "neutral"))
        return
    thread = fill_patch_before(thread, req)
    turns = thread.get("turns", [])
    if any(t.get("question") or t.get("answer") for t in turns):
        html(c.conversation(turns))
    else:
        html('<div class="cc-footnote">No questions yet. Ask the assistant to challenge this requirement; '
             'answers cite source IDs.</div>')

    for t in turns:
        if not t.get("proposed_patch") or t.get("state") in ("applied", "discarded"):
            continue
        stale = t.get("base_revision") != req.get("revision") or t.get("state") == "stale"
        html(c.section(f"Suggested change · {t['id']}"))
        html(c.diff_table(t["proposed_patch"]))
        sp = t.get("score_preview")
        if sp:
            scale = req.get("score", {}).get("scale", {})
            html(f'<div class="cc-footnote" style="margin-top:6px">Server preview: priority '
                 f'{c.fmt_score(sp.get("before"), scale)} → {c.fmt_score(sp.get("after"), scale)}, '
                 f'rank {c.e(sp.get("rank_before"))} → {c.e(sp.get("rank_after"))}</div>')
        if stale:
            html(c.banner("This suggestion was made for an older revision. Ask again for a fresh one.", "amber"))
        a, b, _ = st.columns([1, 1, 6])
        if a.button("Apply…", key=f"apply:{t['id']}", type="primary", disabled=stale):
            apply_sheet(req, t)
        if b.button("Discard", key=f"discard:{t['id']}"):
            discard_sheet(thread.get("id"), t, req)

    with st.form(f"ask:{req['id']}", clear_on_submit=False):
        q = st.text_area("Ask the review assistant", placeholder="e.g. Does the evidence support EU as a target?")
        if st.form_submit_button("Ask", type="primary", disabled=bool(active)):
            if not q.strip():
                st.warning("Type a question first.")
            else:
                try:
                    with st.spinner("Asking…"):
                        result = client.challenge(req["id"], q, req.get("revision"), actor_id())
                    if result.get("run_id") or result.get("id"):
                        st.session_state[run_key] = result.get("run_id") or result["id"]
                    st.rerun()
                except ApiError as err:
                    show_error(err, "ask the assistant")


def tab_history(client, req):
    events = safe(client.get_history, req["id"], what="load history")
    if events is None:
        return
    if not events:
        html(c.banner("<b>No history returned.</b> An empty history is not proof that nothing changed: the "
                      "server may not record events for this record yet.", "amber"))
        return
    html(c.timeline(list(reversed(events))))
    with st.expander("Before / after details"):
        st.code(pretty([{k: e.get(k) for k in ("time", "action", "before", "after")} for e in events]),
                language="json")
    if not req.get("legacy"):
        export_controls(client, "history", req["id"], formats=("json",))


# ============================================================================ other pages
def sources_page(client):
    html('<div class="cc-largetitle">Sources</div><div class="cc-subhead">What was imported and what is '
         'missing.</div>')
    data = safe(client.list_sources, what="load sources")
    if data is None:
        return
    rows = []
    for f in data.get("files", []):
        counts = " · ".join(f"{v} {k}" for k, v in f.items() if k in ("rows", "comments", "facts", "chunks",
                                                                         "pages", "annotations"))
        issues = f.get("issues") or []
        meta = " · ".join(x for x in [f.get("kind"), counts, f"version {f['version']}" if f.get("version") else ""] if x)
        rows.append((c.e(f.get("file")), c.e(meta) +
                     "".join(f"<br><span style='color:var(--amber)'>{c.e(i)}</span>" for i in issues),
                      c.pill("Issues", "amber") if issues else c.pill("No issues reported", "neutral")))
    html(c.section("Files"))
    html(c.group(rows) or c.banner("No source files reported.", "neutral"))
    if data.get("coverage"):
        html(c.section("Comment coverage by region"))
        st.dataframe(pd.DataFrame(data["coverage"]), hide_index=True, width="stretch")
        st.caption("Zero means no comments were supplied, not zero demand.")
    html(c.section("Open a source record"))
    rid = st.text_input("Record ID", placeholder="e.g. SR-… (spreadsheet row) or DOC-… (document chunk)",
                        label_visibility="collapsed")
    if rid and st.button("Open", type="primary"):
        source_sheet(rid.strip())


def analysis_page(client):
    html('<div class="cc-largetitle">Analysis</div><div class="cc-subhead">Draft new requirements from the '
         'imported evidence. Drafts arrive as Proposed and need PM review.</div>')
    run_id = st.session_state.get("run_id")
    active = False
    if run_id:
        active = run_status(client, run_id)
    with st.form("run"):
        a, b, cc_ = st.columns(3)
        derivs = a.multiselect("Vehicles", VEHICLES, ["G60"])
        regions = b.multiselect("Regions", ["EU", "US", "CN", "RoW"], ["EU", "US", "CN"])
        max_drafts = cc_.number_input("Max drafts", 1, 18, 10)
        powertrains = st.multiselect("Powertrains", POWERTRAINS, ["BEV"])
        query = st.text_input("Feedback search", placeholder="e.g. temperature")
        topic_lines = st.text_area("Exact topics (one per line, optional)", height=68,
                                   placeholder="Air conditioning, setting",
                                   help="Use the native topic names; commas within a name are preserved.")
        refresh = st.toggle("Call the model again (otherwise reuse cached results)")
        if st.form_submit_button("Start analysis", type="primary", disabled=bool(active)):
            try:
                scope = {"derivatives": derivs, "regions": regions, "powertrains": powertrains}
                if query.strip():
                    scope["query"] = query.strip()
                selected_topics = [t.strip() for t in topic_lines.splitlines() if t.strip()]
                if selected_topics:
                    scope["topics"] = selected_topics
                res = client.start_analysis(scope, int(max_drafts), refresh)
                st.session_state["run_id"] = res["run_id"]
                st.rerun()
            except ApiError as err:
                show_error(err, "start the analysis")


def render_run(run_id, run):
    prog = run.get("progress") or {}
    done = (f'{prog["fraction"]:.0%} done · {prog.get("done")} drafts so far' if prog.get("fraction") is not None
            else f'{c.e(prog.get("done"))} of {c.e(prog.get("total"))} drafts')
    stages = ["retrieve", "review", "completed"] if run.get("kind") == "challenge" else STAGES
    html(f'<div class="cc-card"><div class="cc-headline">{c.e(run_id)} · {c.e(run.get("state"))}</div>'
         f'{c.steps(stages, run.get("stage"), run.get("state"))}'
         f'<div class="cc-footnote" style="margin-top:8px">{c.e(run.get("mode") or "")} · {done}</div></div>')
    st.caption(f"Stage: {run.get('stage') or 'not reported'}")
    for fail in run.get("failures") or []:
        html(c.banner(c.e(fail), "red"))
    state = run.get("state")
    if state in TERMINAL_RUN_STATES:
        tone = {"succeeded": "green", "partial": "amber", "failed": "red"}[state]
        html(c.banner(f"{c.e(run_id)} · {c.e(state)}"
                      + (" · cached result" if run.get("mode") == "cached" else ""), tone))
        if run.get("result_ids"):
            st.caption("Results: " + ", ".join(run["result_ids"]))


@st.fragment(run_every=2)
def _run_fragment(client, run_id):
    run = safe(client.get_run, run_id, what="load the run")
    if run is None:
        return
    render_run(run_id, run)
    if run.get("state") in TERMINAL_RUN_STATES:
        st.session_state[f"finished_run:{run_id}"] = run
        st.rerun(scope="app")


def run_status(client, run_id) -> bool:
    finished = st.session_state.get(f"finished_run:{run_id}")
    if finished is not None:
        render_run(run_id, finished)
        return False
    _run_fragment(client, run_id)
    return True


def package_page(client):
    html('<div class="cc-largetitle">Review package</div><div class="cc-subhead">Freeze approved revisions '
         'into an auditable package.</div>')
    page = safe(client.list_all_requirements, {"status": "APPROVED"}, what="load approved requirements")
    if page is None:
        return
    approved = page["items"]
    if not approved:
        html(c.banner("No approved requirements yet.", "neutral"))
    else:
        options = {f"{r['id']} · rev {r.get('revision')} · {r['title']}": r for r in approved}
        chosen = st.multiselect("Approved revisions to include", list(options), list(options))
        rationale = st.text_area("Rationale for this package")
        if st.button("Create package", type="primary", disabled=not chosen):
            try:
                # expected_ranking_id: the ranking this list was read under; the server rejects a stale one
                res = client.create_freeze([{"id": options[k]["id"], "revision": options[k].get("revision")}
                                            for k in chosen], page.get("ranking_id"), actor_id(), rationale)
                st.session_state["freeze_id"] = res["freeze_id"]
                flash(f"Package {res['freeze_id']} created.")
                st.rerun()
            except ApiError as err:
                show_error(err, "create the package")
    fid = st.session_state.get("freeze_id")
    if fid:
        html(c.section(f"Package {fid}"))
        a, b = st.columns(2)
        with a:
            st.caption("Package export")
            export_controls(client, "freeze", fid, formats=("json",))
        with b:
            st.caption("Audit trail")
            export_controls(client, "audit", fid, formats=("json",))
    html(c.section("Complete sample list"))
    export_controls(client, "sample_list", "all")


# ============================================================================ router
def main():
    client = get_client()
    sidebar(client)
    show_flash()
    if client.synthetic:
        label = getattr(client, "source_label", "Synthetic preview").upper()
        html(f'<div class="cc-pill amber" style="margin-bottom:8px">{c.e(label)} · not BMW data</div>')
    {"Overview": overview_page, "Requirements": requirements_page, "Sources": sources_page, "Analysis": analysis_page,
     "Review package": package_page}[st.session_state.get("page", "Overview")](client)


main()
