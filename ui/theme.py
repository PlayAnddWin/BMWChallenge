"""
Visual system for the PM workbench, following Apple's Human Interface Guidelines:

- Hierarchy: content first; controls recede. Structure comes from spacing and grouping
  (inset grouped lists), not from heavy borders or decoration.
- Harmony: concentric radii (cards 14px, inner elements 10px, capsules for buttons/pills),
  one accent colour, translucent "material" only where it signals layering (header bar).
- Consistency: platform patterns people know: sidebar navigation, segmented controls,
  sheets for decisions, Messages-style conversation, a macOS "Storage"-style bar for
  what makes up a priority.

Typography uses the system font as Apple recommends (SF Pro on Apple devices,
Segoe UI Variable on Windows), so nothing is downloaded and the app works offline.
Colours are Apple's light-mode neutrals plus the higher-contrast system colours,
each with exactly one meaning.
"""

TOKENS = {
    "bg": "#F5F5F7",          # window background (apple.com grey)
    "panel": "#FFFFFF",       # grouped content background
    "label": "#1D1D1F",       # primary text
    "label2": "#6E6E73",      # secondary text
    "label3": "#86868B",      # tertiary text
    "sep": "#D2D2D7",         # separators
    "sep_soft": "#E8E8ED",
    "fill": "#F2F2F7",        # quaternary fill (systemGray6)
    "accent": "#0071E3",      # actions and selection
    "accent_soft": "#E8F1FC",
    "green": "#248A3D", "green_soft": "#E8F5EC",     # approved, verified, covered
    "amber": "#B25000", "amber_soft": "#FFF3E0",     # uncertainty, proposals awaiting review
    "red": "#D70015", "red_soft": "#FDECEE",         # rejected, conflict, contradicts, failed
    "indigo": "#3634A3", "indigo_soft": "#EEEEFA",   # AI-generated content
    "teal": "#0E7C86",
    # navy "control panel" (sidebar), matching the Overview hero. Contrast on the bare gradient
    # (worst case navy_800): on_navy 14.0, on_navy_2 7.9, on_navy_3 4.6, accent_on_navy 5.3.
    # on_navy_3 drops below 4.5:1 on hover/selected/field tints, so it is only used on the bare ground.
    "navy_900": "#0B1220", "navy_800": "#0B2A5B", "navy_line": "rgba(255,255,255,.10)",
    "on_navy": "#FFFFFF", "on_navy_2": "rgba(255,255,255,.72)", "on_navy_3": "rgba(255,255,255,.50)",
    "accent_on_navy": "#4DA3FF",
}

FACTOR_COLORS = {"customer_value": "#0071E3", "addressable_reach": "#30B0C7",
                 "strategic_fit": "#5856D6", "ease": "#FF9F0A",
                 # v1 backend factor keys (contracts/openapi.json score.factors)
                 "customer": "#0071E3", "reach": "#30B0C7", "strategy": "#5856D6",
                 # legacy v0 factors (pipeline 06), shown without contributions
                 "impact": "#0071E3", "market_reach": "#30B0C7", "confidence": "#5856D6", "effort": "#FF9F0A"}

FONT_STACK = ('-apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display", "Helvetica Neue", '
              '"Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif')


def css() -> str:
    t = TOKENS
    return f"""
<style>
:root {{
  --bg:{t['bg']}; --panel:{t['panel']}; --label:{t['label']}; --label2:{t['label2']}; --label3:{t['label3']};
  --sep:{t['sep']}; --sep-soft:{t['sep_soft']}; --fill:{t['fill']};
  --accent:{t['accent']}; --accent-soft:{t['accent_soft']};
  --green:{t['green']}; --green-soft:{t['green_soft']}; --amber:{t['amber']}; --amber-soft:{t['amber_soft']};
  --red:{t['red']}; --red-soft:{t['red_soft']}; --indigo:{t['indigo']}; --indigo-soft:{t['indigo_soft']};
  --navy-900:{t['navy_900']}; --navy-800:{t['navy_800']}; --navy-line:{t['navy_line']};
  --on-navy:{t['on_navy']}; --on-navy-2:{t['on_navy_2']}; --on-navy-3:{t['on_navy_3']};
  --accent-on-navy:{t['accent_on_navy']};
  --sf:{FONT_STACK};
  --r-card:14px; --r-inner:10px;
}}

/* ---------- base ---------- */
.stApp, .stApp p, .stApp li, .stApp label, .stApp input, .stApp textarea, .stApp button,
.stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp [data-testid="stMarkdownContainer"],
.stApp [data-baseweb] {{ font-family: var(--sf) !important; }}
[data-testid="stIconMaterial"] {{ font-family: "Material Symbols Rounded" !important; }}
.stApp {{ background: var(--bg); color: var(--label); -webkit-font-smoothing: antialiased; }}
[data-testid="stHeader"] {{ background: rgba(245,245,247,.72); backdrop-filter: saturate(180%) blur(20px);
  -webkit-backdrop-filter: saturate(180%) blur(20px); }}
[data-testid="stMainBlockContainer"], .block-container {{ padding-top: 4.5rem; padding-bottom: 4rem; max-width: 1480px; }}
.cc-storage {{ margin-bottom: 12px; }}
.cc-largetitle + .cc-subhead {{ margin: 2px 0 18px; }}
.cc-card + .cc-group, .cc-group + .cc-group {{ margin-top: 12px; }}

/* Apple text styles */
.cc-largetitle {{ font-size: 34px; line-height: 41px; font-weight: 700; letter-spacing: -0.4px; margin: 0; }}
.cc-title {{ font-size: 24px; line-height: 30px; font-weight: 600; letter-spacing: -0.3px; margin: 2px 0 6px; }}
.cc-headline {{ font-size: 17px; line-height: 22px; font-weight: 600; letter-spacing: -0.2px; margin: 0; }}
.cc-body {{ font-size: 15px; line-height: 21px; }}
.cc-callout {{ font-size: 17px; line-height: 24px; color: var(--label); }}
.cc-subhead {{ font-size: 15px; line-height: 20px; color: var(--label2); }}
.cc-footnote {{ font-size: 13px; line-height: 18px; color: var(--label2); }}
.cc-caption {{ font-size: 12px; line-height: 16px; color: var(--label3); }}
.cc-sectionlabel {{ font-size: 13px; color: var(--label2); margin: 22px 0 6px 4px; font-weight: 500; }}
.cc-muted {{ color: var(--label2); }}
.cc-num {{ font-variant-numeric: tabular-nums; }}

/* ---------- surfaces ---------- */
.cc-card {{ background: var(--panel); border-radius: var(--r-card); padding: 18px 20px;
  box-shadow: 0 1px 2px rgba(0,0,0,.04), 0 0 0 .5px rgba(0,0,0,.06); }}
[data-testid="stVerticalBlockBorderWrapper"] {{ border-radius: var(--r-card) !important; }}
div[data-testid="stVerticalBlockBorderWrapper"]:has(> div > div > .cc-panel-marker) {{
  background: var(--panel); border: none !important; box-shadow: 0 1px 2px rgba(0,0,0,.04), 0 0 0 .5px rgba(0,0,0,.06); }}

/* inset grouped list */
.cc-group {{ background: var(--panel); border-radius: var(--r-card); overflow: hidden;
  box-shadow: 0 0 0 .5px rgba(0,0,0,.06); margin-bottom: 4px; }}
.cc-row {{ display: flex; gap: 14px; align-items: flex-start; padding: 12px 16px; position: relative; }}
.cc-row + .cc-row::before {{ content: ""; position: absolute; top: 0; left: 16px; right: 0; height: .5px; background: var(--sep); }}
.cc-row .cc-row-main {{ flex: 1; min-width: 0; }}
.cc-row .cc-row-title {{ font-size: 15px; line-height: 20px; }}
.cc-row .cc-row-sub {{ font-size: 13px; line-height: 18px; color: var(--label2); margin-top: 2px; }}
.cc-row .cc-row-acc {{ flex: none; text-align: right; font-size: 15px; color: var(--label2); }}

/* pills (capsules) */
.cc-pill {{ display: inline-flex; align-items: center; gap: 5px; border-radius: 999px; padding: 2px 10px;
  font-size: 12px; line-height: 18px; font-weight: 600; white-space: nowrap; vertical-align: middle; }}
.cc-pill.neutral {{ background: var(--fill); color: var(--label2); }}
.cc-pill.blue {{ background: var(--accent-soft); color: var(--accent); }}
.cc-pill.green {{ background: var(--green-soft); color: var(--green); }}
.cc-pill.amber {{ background: var(--amber-soft); color: var(--amber); }}
.cc-pill.red {{ background: var(--red-soft); color: var(--red); }}
.cc-pill.indigo {{ background: var(--indigo-soft); color: var(--indigo); }}
.cc-pill .dot {{ width: 6px; height: 6px; border-radius: 50%; background: currentColor; }}
.cc-pills {{ display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }}

/* banners */
.cc-banner {{ border-radius: var(--r-inner); padding: 10px 14px; font-size: 14px; line-height: 20px; margin: 6px 0 12px; }}
.cc-banner.amber {{ background: var(--amber-soft); color: #7A3700; }}
.cc-banner.red {{ background: var(--red-soft); color: #8A0010; }}
.cc-banner.indigo {{ background: var(--indigo-soft); color: #2A2880; }}
.cc-banner.blue {{ background: var(--accent-soft); color: #08498F; }}
.cc-banner b {{ font-weight: 600; }}

/* macOS-style storage bar for score composition */
.cc-storage {{ display: flex; height: 22px; border-radius: 6px; overflow: hidden; background: var(--fill); }}
.cc-storage span {{ display: block; height: 100%; }}
.cc-storage span + span {{ box-shadow: inset 1.5px 0 0 var(--panel); }}
.cc-legend {{ display: grid; grid-template-columns: 1fr; gap: 0; margin-top: 12px; }}
.cc-swatch {{ width: 10px; height: 10px; border-radius: 3px; display: inline-block; margin-top: 5px; flex: none; }}
.cc-bignum {{ font-size: 48px; line-height: 52px; font-weight: 700; letter-spacing: -1px; font-variant-numeric: tabular-nums; }}
.cc-bignum small {{ font-size: 17px; font-weight: 500; color: var(--label2); letter-spacing: 0; margin-left: 4px; }}
.cc-meter {{ height: 6px; background: var(--fill); border-radius: 3px; overflow: hidden; }}
.cc-meter i {{ display: block; height: 100%; background: var(--accent); border-radius: 3px; }}

/* quotes */
.cc-quote {{ font-size: 16px; line-height: 23px; color: var(--label); }}
.cc-quote::before {{ content: "“"; color: var(--label3); }}
.cc-quote::after {{ content: "”"; color: var(--label3); }}

/* Messages-style conversation */
.cc-msgs {{ display: flex; flex-direction: column; gap: 8px; }}
.cc-msg {{ max-width: 78%; padding: 9px 14px; border-radius: 18px; font-size: 15px; line-height: 20px; }}
.cc-msg.pm {{ align-self: flex-end; background: var(--accent); color: #fff; border-bottom-right-radius: 6px; }}
.cc-msg.ai {{ align-self: flex-start; background: #E9E9EB; color: var(--label); border-bottom-left-radius: 6px; }}
.cc-msg-meta {{ font-size: 11px; color: var(--label3); margin: 0 8px; }}
.cc-msg-meta.pm {{ align-self: flex-end; }}

/* timeline */
.cc-tl {{ position: relative; padding-left: 22px; }}
.cc-tl::before {{ content: ""; position: absolute; left: 6px; top: 6px; bottom: 6px; width: 1.5px; background: var(--sep); }}
.cc-tl-item {{ position: relative; padding: 0 0 18px; }}
.cc-tl-item::before {{ content: ""; position: absolute; left: -20px; top: 5px; width: 10px; height: 10px; border-radius: 50%;
  background: var(--panel); box-shadow: 0 0 0 2px var(--label3); }}
.cc-tl-item.pm::before {{ box-shadow: 0 0 0 2px var(--green); }}
.cc-tl-item.ai::before {{ box-shadow: 0 0 0 2px var(--indigo); }}

/* diff */
.cc-diff {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
.cc-diff td, .cc-diff th {{ padding: 8px 10px; text-align: left; vertical-align: top; border-top: .5px solid var(--sep); }}
.cc-diff th {{ font-size: 12px; color: var(--label2); font-weight: 600; border-top: none; }}
.cc-diff .before {{ color: var(--red); text-decoration: line-through; text-decoration-color: rgba(215,0,21,.4); }}
.cc-diff .after {{ color: var(--green); }}

/* stage stepper */
.cc-steps {{ display: flex; gap: 6px; flex-wrap: wrap; }}
.cc-step {{ font-size: 12px; padding: 4px 10px; border-radius: 999px; background: var(--fill); color: var(--label3); }}
.cc-step.done {{ background: var(--green-soft); color: var(--green); }}
.cc-step.now {{ background: var(--accent); color: #fff; }}

/* ---------- Streamlit widgets, nudged toward macOS controls ---------- */
.stButton button, .stDownloadButton button, [data-testid="stFormSubmitButton"] button, .stPopover button {{
  border-radius: 999px !important; font-weight: 500 !important; padding: 4px 16px !important; min-height: 34px; }}
[data-testid="stBaseButton-primary"], [data-testid="stBaseButton-primaryFormSubmit"] {{
  background: var(--accent) !important; border-color: var(--accent) !important; }}
[data-testid="stBaseButton-secondary"], [data-testid="stBaseButton-secondaryFormSubmit"] {{
  background: var(--panel) !important; border: .5px solid var(--sep) !important; color: var(--accent) !important; }}
[data-testid="stBaseButton-secondary"]:disabled {{ color: var(--label3) !important; }}
[data-baseweb="input"], [data-baseweb="select"] > div, [data-baseweb="textarea"] {{
  border-radius: var(--r-inner) !important; background: var(--panel) !important; }}
[data-testid="stDataFrame"] {{ border-radius: var(--r-card); overflow: hidden; box-shadow: 0 0 0 .5px rgba(0,0,0,.08); }}
[data-testid="stExpander"] details {{ border-radius: var(--r-card) !important; border: none !important;
  background: var(--panel); box-shadow: 0 0 0 .5px rgba(0,0,0,.08); }}
[data-testid="stButtonGroup"] button {{ border-radius: 8px !important; }}
div[role="dialog"] {{ border-radius: 18px !important; }}
[data-testid="stBaseButton-primary"]:hover, [data-testid="stBaseButton-primaryFormSubmit"]:hover {{
  background: var(--navy-800) !important; border-color: var(--navy-800) !important; }}
[data-testid="stBaseButton-segmented_controlActive"] {{
  background: var(--navy-800) !important; border-color: var(--navy-800) !important; color: var(--on-navy) !important; }}
[data-testid="stBaseButton-segmented_controlActive"] * {{ color: var(--on-navy) !important; }}
[data-testid="stToast"] {{ border-radius: 14px; }}
hr {{ border-color: var(--sep-soft) !important; }}


/* ---------- sidebar: navy control panel ---------- */
[data-testid="stSidebar"] {{ background: linear-gradient(180deg, var(--navy-900) 0%, var(--navy-800) 100%) !important;
  border-right: none !important; box-shadow: inset -1px 0 0 rgba(255,255,255,.06); color: var(--on-navy-2); }}
[data-testid="stSidebar"] > div, [data-testid="stSidebarContent"], [data-testid="stSidebarHeader"] {{
  background: transparent !important; }}
[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {{ padding-top: 0.4rem; }}
[data-testid="stSidebar"] [data-testid="stIconMaterial"], [data-testid="stSidebar"] svg,
[data-testid="stSidebarCollapseButton"] button, [data-testid="stSidebar"] [data-testid="stTooltipIcon"] {{
  color: var(--on-navy) !important; fill: currentColor; }}
[data-testid="stSidebar"] a {{ color: var(--accent-on-navy); }}

.cc-nav-brand {{ display: flex; align-items: center; gap: 10px; margin: 2px 0 22px; }}
.cc-nav-mark {{ flex: none; width: 32px; height: 32px; border-radius: 9px; display: grid; place-items: center;
  background: linear-gradient(135deg, #0071E3 0%, #4DA3FF 100%); color: #fff; font-size: 15px; line-height: 1;
  box-shadow: 0 6px 14px -6px rgba(77,163,255,.6), inset 0 1px 0 rgba(255,255,255,.25); }}
.cc-nav-name {{ font-size: 20px; line-height: 24px; font-weight: 700; letter-spacing: -.3px; color: var(--on-navy); }}
.cc-nav-tag {{ font-size: 12px; line-height: 16px; color: var(--on-navy-3); }}
.cc-nav-label {{ font-size: 11px; line-height: 14px; font-weight: 600; text-transform: uppercase; letter-spacing: .08em;
  color: var(--on-navy-3); margin: 6px 0 8px 2px; }}
.cc-nav-label.spaced {{ margin-top: 22px; padding-top: 18px; border-top: 1px solid var(--navy-line); }}
.cc-nav-foot {{ font-size: 12px; line-height: 16px; color: var(--on-navy-3); margin-top: 28px; padding-top: 14px;
  border-top: 1px solid var(--navy-line); }}

/* navigation rows (st.radio with the circles hidden) */
[data-testid="stSidebar"] [role="radiogroup"] {{ gap: 2px; width: 100%; }}
[data-testid="stSidebar"] [role="radiogroup"] label {{ position: relative; display: flex; align-items: center;
  width: 100%; min-height: 38px; margin: 0; padding: 0 12px; border-radius: 10px; color: var(--on-navy-2);
  cursor: pointer; transition: background .15s ease, color .15s ease; }}
[data-testid="stSidebar"] [role="radiogroup"] label > div:first-child {{ display: none; }}
[data-testid="stSidebar"] [role="radiogroup"] label p {{ color: inherit !important; font-size: 14px; }}
[data-testid="stSidebar"] [role="radiogroup"] label [data-testid="stIconMaterial"] {{ color: inherit !important;
  font-size: 19px; margin-right: 8px; vertical-align: -4px; }}
[data-testid="stSidebar"] [role="radiogroup"] label:hover {{ background: rgba(255,255,255,.06); color: var(--on-navy); }}
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {{ background: rgba(255,255,255,.12);
  color: var(--on-navy); }}
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) p {{ font-weight: 600; }}
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked)::before {{ content: ""; position: absolute;
  left: 0; top: 9px; bottom: 9px; width: 3px; border-radius: 0 3px 3px 0; background: var(--accent-on-navy); }}
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:focus-visible) {{
  outline: 2px solid var(--accent-on-navy); outline-offset: 1px; }}

/* session fields */
[data-testid="stSidebar"] [data-testid="stWidgetLabel"], [data-testid="stSidebar"] [data-testid="stWidgetLabel"] p {{
  color: var(--on-navy-2) !important; font-size: 13px; }}
[data-testid="stSidebar"] [data-baseweb="select"] > div {{ background: rgba(255,255,255,.08) !important;
  border: 1px solid var(--navy-line) !important; border-radius: var(--r-inner) !important; color: var(--on-navy) !important; }}
[data-testid="stSidebar"] [data-baseweb="select"] > div:hover {{ border-color: rgba(255,255,255,.25) !important; }}
[data-testid="stSidebar"] [data-baseweb="select"] > div:focus-within {{ border-color: var(--accent-on-navy) !important; }}
[data-testid="stSidebar"] [data-baseweb="select"] *, [data-testid="stSidebar"] [data-baseweb="select"] input {{
  color: var(--on-navy) !important; -webkit-text-fill-color: var(--on-navy); }}

/* status pill and banners on navy */
[data-testid="stSidebar"] .cc-pill {{ background: rgba(255,255,255,.08); color: var(--on-navy);
  box-shadow: inset 0 0 0 1px var(--navy-line); font-weight: 500; padding: 3px 10px; }}
[data-testid="stSidebar"] .cc-pill .dot {{ width: 7px; height: 7px; }}
[data-testid="stSidebar"] .cc-pill.green .dot {{ background: #30D158; box-shadow: 0 0 0 3px rgba(48,209,88,.18); }}
[data-testid="stSidebar"] .cc-pill.blue .dot {{ background: var(--accent-on-navy); box-shadow: 0 0 0 3px rgba(77,163,255,.2); }}
[data-testid="stSidebar"] .cc-pill.red .dot {{ background: #FF6961; box-shadow: 0 0 0 3px rgba(255,105,97,.2); }}
[data-testid="stSidebar"] .cc-pill.amber .dot {{ background: #FFB340; box-shadow: 0 0 0 3px rgba(255,179,64,.2); }}
[data-testid="stSidebar"] .cc-caption {{ color: var(--on-navy-2); word-break: break-all; }}
[data-testid="stSidebar"] .cc-banner {{ margin: 10px 0 4px; box-shadow: 0 0 0 1px rgba(255,255,255,.12); }}
[data-testid="stSidebar"] .cc-banner.amber {{ border-left: 3px solid #FFB340; }}

/* ghost buttons */
[data-testid="stSidebar"] .stButton button {{ background: transparent !important;
  border: 1px solid rgba(255,255,255,.25) !important; color: var(--on-navy) !important;
  border-radius: 999px !important; box-shadow: none !important; transform: none !important; }}
[data-testid="stSidebar"] .stButton button p {{ color: var(--on-navy) !important; }}
[data-testid="stSidebar"] .stButton button:hover {{ background: rgba(255,255,255,.08) !important;
  border-color: rgba(255,255,255,.4) !important; }}
[data-testid="stSidebar"] .stButton button:focus-visible {{ outline: 2px solid var(--accent-on-navy); outline-offset: 2px; }}

/* ---- depth and motion (macOS Sonoma / visionOS-inspired) ---- */
.stApp {{ background: radial-gradient(1200px 600px at 85% -10%, #E9F0FB 0%, rgba(233,240,251,0) 60%),
                      radial-gradient(900px 500px at -10% 110%, #F1ECFA 0%, rgba(241,236,250,0) 55%), var(--bg); }}
.cc-card, .cc-group {{ box-shadow: 0 1px 1px rgba(0,0,0,.03), 0 8px 24px -12px rgba(16,24,40,.12),
                       0 0 0 .5px rgba(0,0,0,.06) !important; }}
.stButton button, .stDownloadButton button {{ transition: transform .15s ease, box-shadow .15s ease, background .15s ease; }}
.stButton button:hover {{ transform: translateY(-1px); box-shadow: 0 6px 16px -8px rgba(0,113,227,.45); }}

.cc-hero {{ position: relative; overflow: hidden; border-radius: 22px; padding: 30px 34px 28px; margin: 4px 0 18px;
  color: #fff; background: linear-gradient(135deg, #0B1220 0%, #0B2A5B 45%, #0071E3 100%);
  box-shadow: 0 20px 40px -24px rgba(0,60,140,.55); }}
.cc-hero::after {{ content: ""; position: absolute; right: -80px; top: -120px; width: 380px; height: 380px;
  background: radial-gradient(circle, rgba(255,255,255,.22) 0%, rgba(255,255,255,0) 65%); }}
.cc-hero-eyebrow {{ font-size: 12px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase;
  color: rgba(255,255,255,.72); }}
.cc-hero-title {{ font-size: 32px; line-height: 38px; font-weight: 700; letter-spacing: -.5px; margin: 8px 0 8px;
  max-width: 760px; }}
.cc-hero-sub {{ font-size: 15px; line-height: 22px; color: rgba(255,255,255,.82); max-width: 720px; }}

.cc-tiles {{ display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 12px; margin-bottom: 22px; }}
@media (max-width: 1100px) {{ .cc-tiles {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} }}
.cc-tile {{ background: rgba(255,255,255,.78); backdrop-filter: blur(20px) saturate(160%);
  -webkit-backdrop-filter: blur(20px) saturate(160%); border-radius: 16px; padding: 16px 18px;
  box-shadow: 0 1px 1px rgba(0,0,0,.03), 0 8px 24px -14px rgba(16,24,40,.18), 0 0 0 .5px rgba(0,0,0,.06);
  border-top: 3px solid transparent; }}
.cc-tile.blue {{ border-top-color: var(--accent); }} .cc-tile.green {{ border-top-color: var(--green); }}
.cc-tile.amber {{ border-top-color: var(--amber); }} .cc-tile.red {{ border-top-color: var(--red); }}
.cc-tile-label {{ font-size: 12px; font-weight: 600; color: var(--label2); }}
.cc-tile-value {{ font-size: 30px; line-height: 36px; font-weight: 700; letter-spacing: -.6px; margin-top: 4px;
  font-variant-numeric: tabular-nums; color: var(--label); }}
.cc-tile-sub {{ font-size: 12px; color: var(--label3); margin-top: 2px; }}

.cc-chart-title {{ font-size: 17px; font-weight: 600; letter-spacing: -.2px; color: var(--label); }}
.cc-chart-sub {{ font-size: 13px; color: var(--label2); margin: 2px 0 8px; }}
[data-testid="stPlotlyChart"] {{ background: var(--panel); border-radius: var(--r-card); padding: 10px 8px 4px;
  box-shadow: 0 1px 1px rgba(0,0,0,.03), 0 8px 24px -12px rgba(16,24,40,.12), 0 0 0 .5px rgba(0,0,0,.06); }}
.cc-legend-row {{ display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--label2);
  padding: 7px 0; border-bottom: .5px solid var(--sep-soft); }}
.cc-legend-row:last-child {{ border-bottom: none; }}
.cc-legend-row b {{ margin-left: auto; color: var(--label); font-variant-numeric: tabular-nums; }}
.cc-vehicle {{ display: flex; align-items: center; gap: 8px; font-size: 15px; font-weight: 600; margin: 6px 0 8px; }}

@media (prefers-reduced-motion: reduce) {{
  * {{ transition: none !important; animation: none !important; }}
  .stButton button:hover {{ transform: none !important; }}
}}
</style>
"""
