"""
Overview page: a portfolio view of what the server returned.

Only counts and plots the records it receives. Scores, tiers and statuses come
from the API (or the pipeline snapshot); nothing is recalculated here.
"""
from __future__ import annotations

from collections import Counter

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import ui.components as c
from ui.client import STATUS_LABELS, ApiError
from ui.theme import TOKENS

# Categorical order, validated for CVD and contrast (dataviz validator, light surface).
VEHICLE_COLORS = ["#0071E3", "#E8710A", "#7A4FD6"]
FONT = "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Segoe UI Variable', 'Segoe UI', Roboto, sans-serif"


def _html(s: str):
    st.markdown(s, unsafe_allow_html=True)


def _layout(fig: go.Figure, height: int, **kw) -> go.Figure:
    fig.update_layout(
        height=height, margin=dict(l=8, r=8, t=8, b=8), paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)", font=dict(family=FONT, size=12, color=TOKENS.get("label2", "#6E6E73")),
        hoverlabel=dict(bgcolor="white", bordercolor="#D2D2D7", font=dict(family=FONT, color="#1D1D1F")),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title=None), **kw)
    fig.update_xaxes(gridcolor="#ECECF0", zeroline=False, linecolor="#D2D2D7", ticks="")
    fig.update_yaxes(gridcolor="#ECECF0", zeroline=False, linecolor="#D2D2D7", ticks="")
    return fig


def _tile(label: str, value, sub: str = "", tone: str = "") -> str:
    return (f'<div class="cc-tile {tone}"><div class="cc-tile-label">{c.e(label)}</div>'
            f'<div class="cc-tile-value">{c.e(value)}</div>'
            f'<div class="cc-tile-sub">{sub}</div></div>')


def _vehicle(r: dict) -> str:
    d = r.get("target_scope", {}).get("derivatives") or []
    return d[0] if d else "Unspecified"


def render_overview(client, open_requirement):
    try:
        page = client.list_all_requirements()
    except ApiError as err:
        _html(c.banner(f"<b>Could not load requirements.</b> {c.e(err.message)} This is not an empty "
                       "portfolio; the data was not loaded.", "red"))
        return
    items = page["items"]
    source = (getattr(client, "source_label", "Synthetic preview") if client.synthetic else
              "Pipeline snapshot" if getattr(client, "snapshot", False) else "Live API")
    exported = getattr(client, "exported_at", None)

    _html(f'''<div class="cc-hero">
      <div class="cc-hero-eyebrow">{c.e(source)}{(" · exported " + c.e(c.fmt_time(exported))) if exported else ""}</div>
      <div class="cc-hero-title">Customer evidence, ranked for the next concept decision.</div>
      <div class="cc-hero-sub">{len(items)} requirements across {len({_vehicle(r) for r in items})} vehicle lines.
      Every score and tier comes from the backend; open a requirement to see its evidence and decide.</div>
    </div>''')
    if page.get("notice"):
        _html(c.banner(c.e(page["notice"]), "neutral"))
    if not items:
        _html(c.banner("The request succeeded and returned no requirements.", "neutral"))
        return

    status = Counter(r["status"] for r in items)
    conflicts = sum(1 for r in items if r.get("has_conflict"))
    gaps = sum(1 for r in items if r.get("data_gaps") or "evidence_gap" in r.get("flags", []))
    levels = Counter((r["evidence"].get("level") or "Unknown") for r in items)
    strong = levels.get("Strong", 0) + levels.get("High", 0)
    approved = status.get("APPROVED", 0)

    _html('<div class="cc-tiles">'
          + _tile("Requirements", len(items), "returned by the server")
          + _tile("Under review", status.get("PM_REVIEW", 0), f"{approved} approved", "blue")
          + _tile("Conflicting evidence", conflicts, "flagged by the pipeline", "red" if conflicts else "")
          + _tile("Data gaps", gaps, "missing regions or sources", "amber" if gaps else "")
          + _tile("Strong evidence", f"{100 * strong / len(items):.0f}%", f"{strong} of {len(items)}", "green")
          + "</div>")

    vehicles = sorted({_vehicle(r) for r in items})
    vcolor = {v: VEHICLE_COLORS[i] if i < len(VEHICLE_COLORS) else "#8E8E93" for i, v in enumerate(vehicles)}
    rows = [{"id": r["id"], "title": r["title"], "vehicle": _vehicle(r), "topic": r.get("topic") or "Other",
             "priority": r["score"].get("effective"), "feedback": r["evidence"].get("feedback_count"),
              "eci": r["evidence"].get("eci"), "level": r["evidence"].get("level") or "Unknown",
              "policy": r["score"].get("scale", {}).get("policy", "unknown"),
              "scale_label": r["score"].get("scale", {}).get("label", "Priority"),
              "scale_max": r["score"].get("scale", {}).get("max", 100),
              "scale_min": r["score"].get("scale", {}).get("min", 0),
              "status": STATUS_LABELS.get(r["status"], r["status"]), "conflict": bool(r.get("has_conflict"))}
             for r in items]
    df = pd.DataFrame(rows)

    left, right = st.columns([3, 2], gap="medium")
    with left:
        use_feedback = df["feedback"].notna().any()
        xcol = "feedback" if use_feedback else "eci"
        xlabel = "Customer comments behind the requirement (log scale)" if use_feedback else "Evidence confidence (ECI)"
        _html(f'<div class="cc-chart-title">Priority vs evidence</div><div class="cc-chart-sub">Top right: high '
              f'priority with broad customer backing. Top left: important but thinly evidenced, validate first.</div>')
        if df["policy"].nunique() > 1:
            st.caption("Different scoring policies are plotted separately; legacy and v1 priorities are not comparable.")
        for policy, portfolio in df.groupby("policy", sort=False):
            fig = go.Figure()
            for v in vehicles:
                sub = portfolio[(portfolio.vehicle == v) & portfolio[xcol].notna() & portfolio.priority.notna()]
                fig.add_trace(go.Scatter(
                    x=sub[xcol], y=sub["priority"], mode="markers", name=v,
                    marker=dict(size=9, color=vcolor[v], opacity=0.85, line=dict(width=1.5, color="white"),
                                symbol=["diamond" if k else "circle" for k in sub["conflict"]]),
                    customdata=sub[["id", "title", "level", "status"]].values,
                    hovertemplate="<b>%{customdata[1]}</b><br>%{customdata[0]} · " + v +
                                  "<br>Priority %{y:.2f} (" + c.e(policy) + ")<br>" +
                                  ("Comments %{x}" if use_feedback else "ECI %{x:.0f}") +
                                  "<br>%{customdata[2]} evidence · %{customdata[3]}<extra></extra>"))
            label = portfolio.iloc[0]["scale_label"]
            _layout(fig, 380, xaxis_title=xlabel, yaxis_title=label)
            fig.update_yaxes(range=[portfolio.iloc[0]["scale_min"], portfolio.iloc[0]["scale_max"]])
            if use_feedback:
                fig.update_xaxes(type="log", tickvals=[1, 3, 10, 30, 100, 300, 1000],
                                 ticktext=["1", "3", "10", "30", "100", "300", "1k"])
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, key=f"priority:{policy}")
            st.caption(f"{label}. Diamonds mark requirements with conflicting evidence.")
    with right:
        has_fb = df["feedback"].notna().any()
        tdf = df[~df["topic"].str.lower().isin(["no_class_found", "other", "unknown", ""])]
        if has_fb:
            topics = tdf.groupby("topic")["feedback"].sum().sort_values().tail(10)
            title, sub, unit = "Where customers speak up", "Customer comments per feature cluster (top 10)", "comments"
        else:
            topics = tdf.groupby("topic").size().sort_values().tail(10)
            title, sub, unit = "Most frequent topics", "Requirements per feature cluster (top 10)", "requirements"
        _html(f'<div class="cc-chart-title">{title}</div><div class="cc-chart-sub">{sub}</div>')
        short = [t if len(t) <= 26 else t[:25].rstrip(" ,") + "…" for t in topics.index]
        fig2 = go.Figure(go.Bar(x=topics.values, y=short, orientation="h", customdata=list(topics.index),
                                marker=dict(color="#0071E3", cornerradius=4),
                                text=[f"{int(v)}" for v in topics.values], textposition="outside",
                                cliponaxis=False, textfont=dict(color="#6E6E73", size=11),
                                hovertemplate="%{customdata}: %{x} " + unit + "<extra></extra>"))
        _layout(fig2, 380, showlegend=False, bargap=0.35)
        fig2.update_xaxes(showgrid=False, showticklabels=False,
                         range=[0, max(1, float(topics.max()) if not topics.empty else 1) * 1.18])
        fig2.update_yaxes(showgrid=False, automargin=True)
        st.plotly_chart(fig2, width="stretch", config={"displayModeBar": False})

    a, b = st.columns([1, 1], gap="medium")
    with a:
        _html('<div class="cc-chart-title">Review progress</div><div class="cc-chart-sub">Where the portfolio '
              'stands in PM review</div>')
        order = ["Proposed", "Under review", "Approved", "Deferred", "Rejected"]
        counts = df.groupby("status").size().reindex(order).fillna(0)
        tone = {"Proposed": "#8E8E93", "Under review": "#0071E3", "Approved": "#248A3D", "Deferred": "#B25000",
                "Rejected": "#D70015"}
        bars = "".join(
            f'<span title="{k}: {int(v)}" style="width:{100 * v / max(1, counts.sum()):.2f}%;background:{tone[k]}">'
            f'</span>' for k, v in counts.items() if v)
        legend = "".join(f'<div class="cc-legend-row"><span class="cc-swatch" style="background:{tone[k]}"></span>'
                         f'{k}<b>{int(v)}</b></div>' for k, v in counts.items())
        _html(f'<div class="cc-card"><div class="cc-storage" style="height:14px">{bars}</div>{legend}</div>')
    with b:
        _html('<div class="cc-chart-title">Evidence strength by vehicle</div><div class="cc-chart-sub">Tier '
              'assigned by the backend</div>')
        rows_html = []
        for v in vehicles:
            sub = df[df.vehicle == v]
            lv = Counter(sub["level"])
            pills = "".join(c.pill(f"{k} {n}", c.EVIDENCE_TONE.get(k, "neutral"), dot=True)
                            for k, n in lv.most_common())
            conf = int(sub["conflict"].sum())
            rows_html.append(f'<div class="cc-legend-row"><span class="cc-swatch" style="background:{vcolor[v]}">'
                             f'</span><span style="min-width:44px;color:var(--label);font-weight:600">{c.e(v)}</span>'
                             f'<span class="cc-pills">{pills}{c.pill(f"{conf} conflicts", "red") if conf else ""}'
                             f'</span><b>{len(sub)}</b></div>')
        _html(f'<div class="cc-card">{"".join(rows_html)}</div>')

    _html('<div class="cc-chart-title" style="margin-top:22px">Top priorities by vehicle</div>'
          '<div class="cc-chart-sub">Highest server scores per line, within each scoring policy. '
          'Select one to review its evidence.</div>')
    cols = st.columns(len(vehicles) or 1, gap="medium")
    for col, v in zip(cols, vehicles):
        with col:
            _html(f'<div class="cc-vehicle"><span class="cc-swatch" style="background:{vcolor[v]}"></span>'
                  f'{c.e(v)}</div>')
            for policy, portfolio in df[df.vehicle == v].groupby("policy", sort=False):
                _html(c.pill(policy, "neutral"))
                for _, r in portfolio.sort_values("priority", ascending=False).head(5).iterrows():
                    value = f"{r.priority:.1f}" if pd.notna(r.priority) else "—"
                    st.button(f"{value}   {r.title}", key=f"top:{r.id}", width="stretch",
                               on_click=open_requirement, args=(r.id,),
                               help=f"{r.id} · {policy} · {r.level} evidence · {r.status}")
