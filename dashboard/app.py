"""
DriveShieldX — Real-Time Traffic Overspeed Detection
Dashboard (Streamlit)  ·  "High-Vis Command Center" UI

Full visual + UX redesign of the dashboard. Every backend call
(db_manager, reporting, stream_manager, advanced_pipeline) is preserved
exactly — only presentation, layout and live-refresh behaviour are upgraded.

Aesthetic: tactical near-black base · signature safety-amber accent (#FFB23E,
the colour of speed-camera signage) · signal-red violations · jade safe flow.
Display: Sora · Body: Manrope · Telemetry numerals: JetBrains Mono.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.env_loader import load_env

load_env()  # .env -> os.environ (UPI_ID, Razorpay keys, SMS gateway, ...)

from backend.health import health_snapshot
from backend.reporting import generate_challan_pdf, generate_pdf_report
from backend.stream_manager import MANAGER
from database.db_manager import (
    add_camera,
    add_registered_vehicle,
    add_user,
    add_zone,
    apply_overdue_penalties,
    authenticate_owner,
    authenticate_user,
    bulk_issue_challans_for_all,
    create_manual_challan,
    create_notice,
    create_owner_account,
    delete_zone,
    generate_totp_secret,
    get_all_cameras,
    get_all_notices,
    get_all_users,
    get_camera,
    get_config,
    get_owner_notices,
    get_registered_vehicles,
    get_reports,
    get_speed_records_for_chart,
    get_system_events,
    get_system_health,
    get_unissued_violations,
    get_user_security,
    get_violation_stats,
    get_all_violations,
    init_database,
    issue_challan_for_violation,
    list_alerts,
    list_owner_vehicles,
    list_zones,
    pay_notice,
    set_owner_totp_secret,
    set_user_totp_secret,
    update_config,
    update_notice_payment_status,
    verify_totp,
)
from detection.advanced_pipeline import AdvancedOverspeedPipeline

try:
    from streamlit_autorefresh import st_autorefresh
    _HAS_AUTOREFRESH = True
except Exception:  # pragma: no cover
    _HAS_AUTOREFRESH = False

APP_DIR = Path(__file__).resolve().parents[1]
AUTHORITY_INVITE_CODE = "OVERSPEED-AUTH"

# --- Theme palette (single source of truth, shared by CSS + Plotly) ---
# Dark "command center" — warm gold on near-black, matching the reference motion style.
AMBER = "#F2A93B"        # primary gold (lines, accents)
AMBER_SOFT = "#FFD27D"   # soft gold
JADE = "#7BB0A8"         # muted teal (rolling-avg line, legible on dark)
RED = "#FF6B6B"          # bright alert red (limit / high severity)
VIOLET = "#C9A24B"       # bronze-gold (extreme severity)
INK = "#F4ECD8"          # light warm text on dark
TEXT = "#F4ECD8"         # primary text for charts
MUTED = "#A99B7C"        # muted warm grey
GRID = "rgba(242,169,59,0.10)"
SEV_COLORS = {"low": AMBER_SOFT, "medium": AMBER, "high": RED, "extreme": VIOLET}

st.set_page_config(
    page_title="DriveShieldX · Command Center",
    page_icon="🛰️",
    layout="wide",
    initial_sidebar_state="expanded",
)
init_database()


def role_label(role: str) -> str:
    return {"admin": "System Admin", "traffic_authority": "Traffic Authority"}.get(role, role)


def bootstrap_state() -> None:
    defaults = {
        "authority": None,
        "owner": None,
        "portal": None,           # None=landing, "authority", or "owner"
        "pending_login": None,
        "pending_totp_secret": None,
        "run_nonce": 0,
        "live_monitor_result": None,
        "live_refresh": True,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


bootstrap_state()


CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Sora:wght@400;600;700;800&family=Manrope:wght@400;500;600;700&family=JetBrains+Mono:wght@500;700&display=swap');

:root{
  --gold:#F2A93B; --gold-soft:#FFD27D; --gold-deep:#C9821B; --gold-hi:#FFC25A;
  --teal:#7BB0A8; --red:#FF6B6B;
  --bg:#08080A; --bg2:#0C0B0E; --ink:#F4ECD8; --text:#EDE3CD; --muted:#9D9079;
  --surface:rgba(28,24,18,0.55); --surface-2:rgba(34,29,21,0.65);
  --line:rgba(242,169,59,0.16); --line-soft:rgba(242,169,59,0.08);
  --glow:rgba(242,169,59,0.40);
}
html, body, [class*="css"]{ font-family:'Manrope',sans-serif; }

/* ===== near-black base with warm golden ambient glow halos (reference style) ===== */
.stApp{
  background:
    radial-gradient(820px 420px at 50% -8%, rgba(242,169,59,0.30), transparent 62%),
    radial-gradient(620px 380px at 8% 2%,  rgba(201,130,27,0.20), transparent 60%),
    radial-gradient(680px 420px at 96% 6%, rgba(255,194,90,0.16), transparent 58%),
    radial-gradient(900px 520px at 50% 116%, rgba(242,169,59,0.12), transparent 60%),
    linear-gradient(180deg, #08080A 0%, #0A090C 55%, #070608 100%);
  background-attachment:fixed;
  color:var(--text);
}
.block-container{ padding-top:3.2rem; padding-bottom:3rem; max-width:1500px; }
/* push content below Streamlit's floating toolbar so the navbar/brand isn't clipped */
[data-testid="stHeader"]{ background:transparent; }

/* ===== smooth, slow reveal: fade + lift + de-blur (no abrupt pop) ===== */
@keyframes reveal{
  0%{ opacity:0; transform:translateY(26px) scale(.985); filter:blur(8px); }
  100%{ opacity:1; transform:none; filter:blur(0); }
}
@keyframes haze{ 0%,100%{ opacity:.55; transform:translateX(-50%) scale(1);} 50%{ opacity:.9; transform:translateX(-50%) scale(1.06);} }
@keyframes ringspin{ to{ transform:rotate(360deg);} }
@keyframes glowpulse{
  0%{ box-shadow:0 0 0 0 var(--glow);} 70%{ box-shadow:0 0 0 14px rgba(242,169,59,0);} 100%{ box-shadow:0 0 0 0 rgba(242,169,59,0);}
}
@keyframes shimmer{ 0%{ background-position:-220% 0;} 100%{ background-position:220% 0;} }

/* ===== HERO: dark glass slab sitting under a golden dome of light ===== */
.hero{
  position:relative; overflow:hidden;
  padding:1.9rem 2.0rem; border-radius:22px; margin-bottom:1.3rem;
  background:linear-gradient(180deg, rgba(30,26,19,0.72), rgba(16,14,11,0.78));
  border:1px solid var(--line);
  box-shadow:0 26px 70px rgba(0,0,0,0.55), inset 0 1px 0 rgba(255,210,125,0.12);
  backdrop-filter:blur(8px);
  animation:reveal .9s cubic-bezier(.22,.61,.36,1) both;
}
.hero::before{
  content:""; position:absolute; left:50%; top:-180px; transform:translateX(-50%);
  width:620px; height:340px; border-radius:50%;
  background:radial-gradient(circle at 50% 50%, rgba(255,194,90,0.55), rgba(242,169,59,0.20) 42%, transparent 70%);
  filter:blur(18px); animation:haze 6s ease-in-out infinite; pointer-events:none;
}
.hero::after{
  content:""; position:absolute; inset:0; pointer-events:none;
  background:radial-gradient(420px 160px at 88% -10%, rgba(255,194,90,0.18), transparent 70%);
}
.hero > *{ position:relative; z-index:1; }
.hero .eyebrow{
  font-family:'JetBrains Mono',monospace; font-size:.72rem; letter-spacing:.30em;
  text-transform:uppercase; color:var(--gold-hi); margin:0 0 .5rem 0;
}
.hero h1{
  font-family:'Sora',sans-serif; font-weight:800; font-size:2.15rem; line-height:1.07;
  margin:0; letter-spacing:-0.02em;
  background:linear-gradient(92deg, #FFF4DD 0%, #FFD27D 45%, #F2A93B 100%);
  -webkit-background-clip:text; background-clip:text; -webkit-text-fill-color:transparent;
}
.hero p{ margin:.6rem 0 0 0; color:var(--muted); font-size:.98rem; max-width:900px; }

.livedot{ display:inline-flex; align-items:center; gap:.5rem;
  font-family:'JetBrains Mono',monospace; font-size:.72rem; letter-spacing:.18em;
  color:var(--gold-hi); text-transform:uppercase; }
.livedot i{ width:9px; height:9px; border-radius:50%; background:var(--gold);
  box-shadow:0 0 10px 1px var(--gold); animation:glowpulse 1.8s infinite; }

/* glowing orb/ring motif for the login screen */
.orbwrap{ display:flex; justify-content:center; margin:.4rem 0 .2rem; }
.orb{ position:relative; width:104px; height:104px; border-radius:50%;
  background:radial-gradient(circle at 50% 42%, #FFE6B0, #F2A93B 45%, #7a4d10 78%);
  box-shadow:0 0 40px 6px rgba(242,169,59,0.55), inset 0 -8px 20px rgba(0,0,0,0.45);
  display:flex; align-items:center; justify-content:center;
  font-family:'Sora'; font-weight:800; font-size:2.0rem; color:#1c1407; }
.orb::after{ content:""; position:absolute; inset:-14px; border-radius:50%;
  border:2px solid rgba(242,169,59,0.35); border-top-color:rgba(255,226,176,0.95);
  animation:ringspin 3.4s linear infinite; }

/* ===== KPI cards: dark glass, gold edge, staggered reveal ===== */
.kpi-row{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:.9rem; margin:.2rem 0 1.2rem; }
.kpi{ position:relative; padding:1.15rem 1.2rem; border-radius:18px; overflow:hidden;
  background:linear-gradient(180deg, rgba(30,26,19,0.7), rgba(15,13,10,0.72));
  border:1px solid var(--line);
  box-shadow:0 16px 40px rgba(0,0,0,0.45), inset 0 1px 0 rgba(255,210,125,0.10);
  animation:reveal .8s cubic-bezier(.22,.61,.36,1) both; }
.kpi::after{ content:""; position:absolute; top:-50px; right:-40px; width:130px; height:130px;
  background:radial-gradient(circle, rgba(255,194,90,0.30), transparent 68%); filter:blur(6px); }
.kpi:nth-child(1){ animation-delay:.05s } .kpi:nth-child(2){ animation-delay:.13s }
.kpi:nth-child(3){ animation-delay:.21s } .kpi:nth-child(4){ animation-delay:.29s }
.kpi:nth-child(5){ animation-delay:.37s } .kpi:nth-child(6){ animation-delay:.45s }
.kpi:hover{ border-color:var(--gold); transform:translateY(-3px);
  box-shadow:0 22px 50px rgba(242,169,59,0.22); transition:.22s ease; }
.kpi .label{ font-size:.7rem; letter-spacing:.13em; text-transform:uppercase; color:var(--muted); }
.kpi .value{ font-family:'JetBrains Mono',monospace; font-weight:700; font-size:1.95rem;
  color:var(--ink); margin-top:.32rem; line-height:1; }
.kpi .value.amber{ color:var(--gold-hi); } .kpi .value.jade{ color:var(--teal); } .kpi .value.red{ color:var(--red); }
.kpi .bar{ height:3px; border-radius:3px; margin-top:.8rem;
  background:linear-gradient(90deg,var(--gold),rgba(242,169,59,0)); }

.panel-card{ position:relative; background:linear-gradient(180deg, rgba(26,23,17,0.66), rgba(14,12,9,0.7));
  border:1px solid var(--line); border-radius:20px; padding:1.4rem 1.5rem;
  box-shadow:0 18px 50px rgba(0,0,0,0.42), inset 0 1px 0 rgba(255,210,125,0.08);
  backdrop-filter:blur(7px);
  animation:reveal .8s cubic-bezier(.22,.61,.36,1) both; }
.section-title{ font-family:'Sora',sans-serif; font-size:1.16rem; font-weight:700; color:var(--ink); margin-bottom:.55rem; }
.brand-mark{ font-family:'Sora',sans-serif; font-weight:800; font-size:1.4rem; letter-spacing:-.02em; color:var(--ink); }
.brand-mark span{ color:var(--gold-hi); }
.small-muted{ color:var(--muted); font-size:.88rem; }

/* challan / offence chips */
.chip{ display:inline-block; padding:.18rem .6rem; border-radius:999px; font-size:.72rem; font-weight:700;
  font-family:'JetBrains Mono',monospace; letter-spacing:.04em; border:1px solid transparent; }
.chip.first{ background:rgba(123,176,168,0.16); color:#9fe0d4; border-color:rgba(123,176,168,0.5); }
.chip.second{ background:rgba(242,169,59,0.16); color:var(--gold-hi); border-color:rgba(242,169,59,0.5); }
.chip.third{ background:rgba(255,107,107,0.16); color:#ffb0b0; border-color:rgba(255,107,107,0.55); }
.chip.paid{ background:rgba(123,176,168,0.18); color:#9fe0d4; border-color:rgba(123,176,168,0.5); }
.chip.pending{ background:rgba(242,169,59,0.16); color:var(--gold-hi); border-color:rgba(242,169,59,0.45); }
.chip.overdue{ background:rgba(255,107,107,0.18); color:#ffb0b0; border-color:rgba(255,107,107,0.55); }

.challan{ position:relative; border-radius:16px; padding:1.05rem 1.15rem; margin-bottom:.8rem;
  background:linear-gradient(180deg, rgba(30,26,19,0.72), rgba(15,13,10,0.74));
  border:1px solid var(--line); box-shadow:0 12px 30px rgba(0,0,0,0.4);
  animation:reveal .7s cubic-bezier(.22,.61,.36,1) both; }
.challan.susp{ border-color:rgba(255,107,107,0.55); box-shadow:0 0 0 1px rgba(255,107,107,0.25), 0 12px 30px rgba(0,0,0,0.45); }
.challan .amt{ font-family:'JetBrains Mono',monospace; font-weight:700; font-size:1.5rem; color:var(--gold-hi); }
.challan .meta{ color:var(--muted); font-size:.84rem; }
.susp-banner{ border-radius:14px; padding:.9rem 1.1rem; margin:.2rem 0 1rem;
  background:linear-gradient(180deg, rgba(255,107,107,0.14), rgba(255,107,107,0.06));
  border:1px solid rgba(255,107,107,0.45); color:#ffc9c9; font-weight:600; }

/* ===== sidebar: deep charcoal with gold edge ===== */
[data-testid="stSidebar"]{ background:linear-gradient(180deg,#0C0B0E,#0A090B); border-right:1px solid var(--line); }
section[data-testid="stSidebar"] *{ color:#C9BCA0; }
h1,h2,h3,h4{ font-family:'Sora',sans-serif; color:var(--ink); letter-spacing:-.01em; }
p, span, label, li, .stMarkdown{ color:var(--text); }

/* ===== buttons: gold gradient on dark, glow lift ===== */
.stButton>button{
  border-radius:12px; border:1px solid rgba(242,169,59,0.6);
  background:linear-gradient(135deg, #FFC25A, #F2A93B 60%, #D88A1E);
  color:#1a1207; font-weight:700; font-family:'Manrope'; transition:.2s ease;
  box-shadow:0 8px 22px rgba(242,169,59,0.28);
}
.stButton>button:hover{ transform:translateY(-2px);
  box-shadow:0 12px 30px rgba(242,169,59,0.5); filter:brightness(1.06); }
.stDownloadButton>button{ border-radius:12px; background:linear-gradient(135deg,#FFC25A,#F2A93B); color:#1a1207; border:none; font-weight:700; }

.stTextInput input, .stNumberInput input, .stTextArea textarea, .stDateInput input,
div[data-baseweb="select"]>div{
  background:rgba(16,14,11,0.85)!important; border:1px solid rgba(242,169,59,0.22)!important;
  border-radius:10px!important; color:var(--ink)!important;
}
.stTextInput input::placeholder, .stTextArea textarea::placeholder{ color:#7d735f!important; }
.stTextInput input:focus, .stNumberInput input:focus, .stTextArea textarea:focus{
  border-color:var(--gold)!important; box-shadow:0 0 0 3px rgba(242,169,59,0.18)!important; }

[data-baseweb="tab-list"]{ gap:.4rem; border-bottom:1px solid var(--line-soft); }
[data-baseweb="tab"]{ background:rgba(242,169,59,0.06); border-radius:10px 10px 0 0; padding:.4rem .9rem; color:var(--muted); }
[data-baseweb="tab"][aria-selected="true"]{ color:var(--gold-hi); border-bottom:2px solid var(--gold); }

[data-testid="stMetricValue"]{ font-family:'JetBrains Mono',monospace; color:var(--gold-hi); }
[data-testid="stMetricLabel"]{ color:var(--muted); }
.stDataFrame{ border:1px solid var(--line); border-radius:12px; overflow:hidden; }
hr{ border-color:var(--line-soft); }
.stAlert{ border-radius:12px; background:rgba(26,23,17,0.7); border:1px solid var(--line); color:var(--text); }
[data-testid="stToast"]{ background:#16130D; border:1px solid var(--gold); color:var(--ink); }
[data-testid="stExpander"]{ border:1px solid var(--line); border-radius:14px; background:rgba(20,17,12,0.5); }
code{ background:rgba(242,169,59,0.12)!important; color:var(--gold-hi)!important; border-radius:6px; }

/* ===== top navigation bar (real-website style: logo left, links right) ===== */
.navbar{ position:relative; display:flex; align-items:center; justify-content:space-between;
  padding:.7rem 1.3rem; margin:0 0 1.4rem; border-radius:16px;
  background:linear-gradient(180deg, rgba(22,19,14,0.92), rgba(12,11,9,0.92));
  border:1px solid var(--line); backdrop-filter:blur(10px);
  box-shadow:0 10px 30px rgba(0,0,0,0.45); animation:reveal .7s ease both; }
.navbar .brand{ display:flex; align-items:center; gap:.6rem; font-family:'Sora'; font-weight:800;
  font-size:1.18rem; letter-spacing:-.02em; color:var(--ink); }
.navbar .brand .dot{ width:30px; height:30px; border-radius:9px; display:inline-flex; align-items:center;
  justify-content:center; font-size:.95rem; color:#1a1207; font-weight:800;
  background:radial-gradient(circle at 40% 35%, #FFE6B0, #F2A93B 70%);
  box-shadow:0 0 16px 1px rgba(242,169,59,.5); }
.navbar .brand b{ color:var(--gold-hi); }
.navbar .links{ display:flex; align-items:center; gap:1.4rem; }
.navbar .links a{ color:var(--muted); text-decoration:none; font-size:.92rem; font-weight:600;
  transition:color .18s ease; }
.navbar .links a:hover{ color:var(--gold-hi); }
.navbar .links a.cta{ color:#1a1207; background:linear-gradient(135deg,#FFC25A,#F2A93B);
  padding:.45rem .95rem; border-radius:10px; box-shadow:0 6px 16px rgba(242,169,59,.3); }
.navbar .links .tag{ font-family:'JetBrains Mono'; font-size:.66rem; letter-spacing:.16em;
  text-transform:uppercase; color:var(--gold-hi); border:1px solid var(--line);
  padding:.3rem .6rem; border-radius:8px; }

/* portal selection cards on the landing page */
.portal{ position:relative; overflow:hidden; border-radius:20px; padding:1.6rem 1.6rem 1.4rem;
  background:linear-gradient(180deg, rgba(30,26,19,0.7), rgba(14,12,9,0.74));
  border:1px solid var(--line); box-shadow:0 18px 50px rgba(0,0,0,0.42);
  animation:reveal .85s cubic-bezier(.22,.61,.36,1) both; }
.portal::after{ content:""; position:absolute; top:-50px; right:-30px; width:160px; height:160px;
  background:radial-gradient(circle, rgba(255,194,90,0.22), transparent 68%); filter:blur(8px); }
.portal .ico{ font-size:1.8rem; }
.portal h3{ margin:.5rem 0 .3rem; font-size:1.25rem; }
.portal p{ color:var(--muted); font-size:.92rem; margin:0 0 .4rem; }
.portal ul{ color:var(--text); font-size:.88rem; margin:.5rem 0 0; padding-left:1.1rem; }
.portal ul li{ margin:.18rem 0; }
.lock-note{ font-size:.82rem; color:var(--gold-hi); margin-top:.6rem; }

/* ===== hide Streamlit's left sidebar — navigation now lives in the top bar ===== */
[data-testid="stSidebar"]{ display:none !important; }
[data-testid="collapsedControl"]{ display:none !important; }

/* ===== top-nav as a real menu: render a row of buttons styled like nav links ===== */
.navrow{ position:relative; display:flex; align-items:center; gap:.6rem; flex-wrap:wrap;
  padding:.55rem .8rem; margin:0 0 1.1rem; border-radius:16px;
  background:linear-gradient(180deg, rgba(22,19,14,0.92), rgba(12,11,9,0.92));
  border:1px solid var(--line); backdrop-filter:blur(10px);
  box-shadow:0 10px 30px rgba(0,0,0,0.45); animation:reveal .6s ease both; }
.navrow .nb-brand{ display:flex; align-items:center; gap:.55rem; font-family:'Sora'; font-weight:800;
  font-size:1.1rem; color:var(--ink); margin-right:.4rem; white-space:nowrap; }
.navrow .nb-brand .dot{ width:28px; height:28px; border-radius:8px; display:inline-flex; align-items:center;
  justify-content:center; font-size:.85rem; color:#1a1207; font-weight:800;
  background:radial-gradient(circle at 40% 35%, #FFE6B0, #F2A93B 70%);
  box-shadow:0 0 14px 1px rgba(242,169,59,.5); }
.navrow .nb-brand b{ color:var(--gold-hi); }

/* ===== nav links rendered as SHINY TEXT (no boxes) — real-website style ===== */
/* Streamlit attaches a class `st-key-<key>` to the element wrapping each widget.
   Our nav buttons use keys beginning with `navlink_`, so we target those reliably
   regardless of Streamlit's internal button markup. */
div[class*="st-key-navlink_"] button,
div[class*="st-key-navlink_"] button[kind],
div[class*="st-key-navlink_"] .stButton > button{
  background:transparent !important; background-image:none !important; background-color:transparent !important;
  border:none !important; box-shadow:none !important; outline:none !important;
  color:#F3E8CC !important; font-family:'Manrope',sans-serif !important; font-weight:600 !important;
  font-size:.97rem !important; letter-spacing:.015em !important;
  padding:.3rem .4rem !important; border-radius:8px !important; min-height:auto !important;
  transition:color .18s ease, text-shadow .18s ease, transform .12s ease !important;
  text-shadow:0 0 1px rgba(255,228,180,.35) !important;
}
div[class*="st-key-navlink_"] button:hover,
div[class*="st-key-navlink_"] .stButton > button:hover{
  color:#FFE0A3 !important; background:transparent !important; background-color:transparent !important;
  text-shadow:0 0 16px rgba(255,210,125,.85), 0 0 4px rgba(255,210,125,.6) !important;
  transform:translateY(-1px) !important;
}
div[class*="st-key-navlink_"] button:focus,
div[class*="st-key-navlink_"] button:active{ box-shadow:none !important; background:transparent !important; }
/* active link = the one whose label starts with the dot; brighter + glowing */
div[class*="st-key-navlink_active_"] button{
  color:#FFD27D !important; text-shadow:0 0 18px rgba(255,210,125,.9) !important; font-weight:700 !important;
}
/* sign-out keeps a subtle gold outline so it reads as the account action */
div[class*="st-key-navsignout_"] button{
  background:transparent !important; color:#FFC078 !important; font-weight:600 !important;
  border:1px solid rgba(242,169,59,.45) !important; border-radius:9px !important;
  box-shadow:none !important; min-height:auto !important; padding:.32rem .8rem !important;
}
div[class*="st-key-navsignout_"] button:hover{
  color:#1a1207 !important; background:linear-gradient(135deg,#FFC25A,#F2A93B) !important;
  text-shadow:none !important;
}

/* ===== page transition: each page body fades/slides in on every rerun ===== */
@keyframes pageIn{
  0%{ opacity:0; transform:translateY(16px) scale(.992); filter:blur(5px); }
  100%{ opacity:1; transform:none; filter:blur(0); }
}
.page-anim{ animation:pageIn .55s cubic-bezier(.22,.61,.36,1) both; }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# ---- UI helpers ----
def hero(title: str, subtitle: str, live: bool = False, eyebrow: str = "DriveShieldX · Enforcement Grid") -> None:
    live_html = "<div class='livedot' style='margin-top:.7rem'><i></i> Live feed</div>" if live else ""
    st.markdown(
        f"<div class='hero'><p class='eyebrow'>{eyebrow}</p>"
        f"<h1>{title}</h1><p>{subtitle}</p>{live_html}</div>",
        unsafe_allow_html=True,
    )


def kpi_cards(items) -> None:
    cards = "".join(
        f"<div class='kpi'><div class='label'>{lbl}</div>"
        f"<div class='value {tone}'>{val}</div><div class='bar'></div></div>"
        for lbl, val, tone in items
    )
    st.markdown(f"<div class='kpi-row'>{cards}</div>", unsafe_allow_html=True)


def _style_fig(fig: go.Figure, height: int = 380) -> go.Figure:
    fig.update_layout(
        height=height, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Manrope, sans-serif", color=MUTED, size=12),
        margin=dict(l=18, r=18, t=28, b=18),
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color=MUTED)),
        hoverlabel=dict(bgcolor="#16130D", font_color=TEXT, bordercolor=AMBER),
    )
    fig.update_xaxes(gridcolor=GRID, zeroline=False, linecolor=GRID)
    fig.update_yaxes(gridcolor=GRID, zeroline=False, linecolor=GRID)
    return fig


def logout_all() -> None:
    st.session_state.authority = None
    st.session_state.owner = None
    st.session_state.portal = None
    st.session_state.pending_login = None
    st.session_state.pending_totp_secret = None
    st.rerun()


def _severity_style(value: str) -> str:
    styles = {
        "low": f"color:{AMBER_SOFT};font-weight:600;",
        "medium": f"color:{AMBER};font-weight:700;",
        "high": f"color:{RED};font-weight:700;",
        "extreme": f"color:{VIOLET};font-weight:800;",
    }
    return styles.get(str(value).lower(), "")


def _prepare_speed_df(limit: int = 400, session_id: Optional[int] = None) -> pd.DataFrame:
    rows = get_speed_records_for_chart(limit=limit, session_id=session_id)
    df = pd.DataFrame(rows)
    if not df.empty:
        df["calculated_time"] = pd.to_datetime(df["calculated_time"], errors="coerce")
        df = df.sort_values("calculated_time")
        df["rolling_avg"] = df["speed_value"].rolling(window=10, min_periods=1).mean()
    return df


def _prepare_violation_df(limit: int = 300, camera_id: Optional[int] = None) -> pd.DataFrame:
    df = pd.DataFrame(get_all_violations(limit=limit, camera_id=camera_id))
    if not df.empty and "violation_time" in df.columns:
        df["violation_time"] = pd.to_datetime(df["violation_time"], errors="coerce")
    return df


def _speed_figure(df: pd.DataFrame, height: int = 400) -> go.Figure:
    fig = go.Figure()
    # Build per-point hover text with plate + vehicle so speed AND plate track together.
    plates = df["plate_text"] if "plate_text" in df.columns else None
    if plates is not None:
        customdata = [[(p or "—")] for p in plates]
        hovertemplate = "Speed %{y:.1f} km/h<br>Plate %{customdata[0]}<br>%{x}<extra></extra>"
    else:
        customdata = None
        hovertemplate = "Speed %{y:.1f} km/h<br>%{x}<extra></extra>"
    fig.add_trace(go.Scatter(x=df["calculated_time"], y=df["speed_value"], mode="lines+markers",
                             name="Measured", line=dict(color=AMBER, width=2),
                             marker=dict(size=5, color=AMBER), fill="tozeroy",
                             fillcolor="rgba(201,154,46,0.10)",
                             customdata=customdata, hovertemplate=hovertemplate))
    fig.add_trace(go.Scatter(x=df["calculated_time"], y=df["rolling_avg"], mode="lines",
                             name="Rolling avg", line=dict(color=JADE, width=2, dash="dot")))
    fig.add_trace(go.Scatter(x=df["calculated_time"], y=df["speed_limit"], mode="lines",
                             name="Limit", line=dict(color=RED, width=1.5, dash="dash")))
    return _style_fig(fig, height)


# ---- LOGIN (dual-panel) ----
def top_navbar(active: str = "home") -> None:
    """Real-website style top navbar. `active` is informational only (Streamlit
    can't intercept anchor clicks, so navigation is driven by the buttons below)."""
    st.markdown(
        "<div class='navbar'>"
        "<div class='brand'><span class='dot'>DS</span>DriveShield<b>X</b></div>"
        "<div class='links'>"
        "<span class='tag'>v2 · Dark</span>"
        "<a href='#how'>How it works</a>"
        "<a href='#authority'>Authority</a>"
        "<a href='#owner'>Vehicle owners</a>"
        "<a class='cta' href='#get-started'>Get started</a>"
        "</div></div>",
        unsafe_allow_html=True,
    )


def render_landing() -> None:
    """Public landing page: a real top navbar + two clearly-separated portals."""
    top_navbar("home")
    st.markdown(
        "<div class='hero'>"
        "<div class='orbwrap'><div class='orb'>DS</div></div>"

        "<h1 style='text-align:center'>Catch overspeeding. Issue challans. Keep roads safe.</h1>"

        "<p class='hero-sub'>"
        "DriveShieldX uses YOLOv8 detection, ByteTrack tracking and automatic number-plate "
        "recognition to measure vehicle speed from any camera, then issues graduated e-challans "
        "to offenders — automatically escalating repeat violations."
        "</p>"

        "<div class='livedot center-live'><i></i> Choose your portal below</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    left, right = st.columns(2, gap="large")
    with left:
        st.markdown(
            "<div class='portal'><div class='ico'>🛡️</div>"
            "<h3>Traffic Authority</h3>"
            "<p>For officers and administrators running enforcement.</p>"
            "<ul><li>Live monitoring & number-plate capture</li>"
            "<li>Auto-issue graduated e-challans</li>"
            "<li>Reports, analytics & system health</li></ul>"
            "<div class='lock-note'>🔒 Restricted — new accounts need an official invite code.</div></div>",
            unsafe_allow_html=True,
        )
        if st.button("Enter Authority Portal →", use_container_width=True, key="go_authority"):
            st.session_state.portal = "authority"
            st.rerun()
    with right:
        st.markdown(
            "<div class='portal'><div class='ico'>🚗</div>"
            "<h3>Vehicle Owner</h3>"
            "<p>For citizens tracking and paying their challans.</p>"
            "<ul><li>See every e-challan against your vehicle</li>"
            "<li>Pay securely by UPI / Paytm / GPay</li>"
            "<li>Track penalties, due dates & licence status</li></ul>"
            "<div class='lock-note'>✅ Open registration — sign up in seconds.</div></div>",
            unsafe_allow_html=True,
        )
        if st.button("Enter Owner Portal →", use_container_width=True, key="go_owner"):
            st.session_state.portal = "owner"
            st.rerun()


def _back_to_home_button() -> None:
    if st.button("← Back to home", key="back_home"):
        st.session_state.portal = None
        st.rerun()


def render_authority_portal() -> None:
    """Authority-only login/signup. Signup is gated by an invite code so the public
    cannot create admin / officer accounts."""
    top_navbar("authority")
    _back_to_home_button()
    st.markdown(
        "<div class='hero'><p class='eyebrow'>Restricted Access · Enforcement</p>"
        "<h1>Traffic Authority Portal</h1>"
        "<p>Sign in with your official credentials. Account creation is limited to personnel "
        "who hold the department invite code — this keeps enforcement data secure.</p></div>",
        unsafe_allow_html=True,
    )
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        st.markdown("<div class='panel-card'>", unsafe_allow_html=True)
        login_tab, signup_tab = st.tabs(["Officer / Admin Login", "Request Account (invite only)"])
        with login_tab:
            with st.form("authority_login"):
                email = st.text_input("Official email")
                password = st.text_input("Password", type="password")
                submitted = st.form_submit_button("Secure Sign In →", use_container_width=True)
            if submitted:
                user = authenticate_user(email, password)
                if user:
                    st.session_state.pending_login = {"actor_type": "authority", "actor": user}
                    st.session_state.pending_totp_secret = None
                    st.rerun()
                else:
                    st.error("Email or password not recognised. Check for typos, including extra spaces. "
                             "If this is your first time on this device/browser, make sure the database has been "
                             "initialised (run `streamlit run dashboard/app.py` and it seeds demo accounts).")
            st.caption("Demo · admin@speedcam.com / admin123 · officer@speedcam.com / officer123")
        with signup_tab:
            st.caption("🔒 Restricted: you must enter the official invite code issued by the "
                       "system administrator. Without it, an account cannot be created.")
            with st.form("authority_signup"):
                name = st.text_input("Full name")
                email = st.text_input("Official email", key="signup_email")
                phone = st.text_input("Phone number")
                password = st.text_input("Create password", type="password")
                role = st.selectbox("Role", ["traffic_authority", "admin"], format_func=role_label)
                invite = st.text_input("Invite code", type="password")
                create = st.form_submit_button("Create Authority Account", use_container_width=True)
            if create:
                if invite.strip() != AUTHORITY_INVITE_CODE:
                    st.error("Invalid invite code — account not created. "
                             "(For this build the code is OVERSPEED-AUTH.)")
                elif not name or not email or not password:
                    st.error("Name, email, and password are required.")
                elif add_user(name, email, password, role, phone):
                    st.success("Authority account created. Sign in using the login tab.")
                else:
                    st.error("That authority email already exists.")
        st.markdown("</div>", unsafe_allow_html=True)


def render_owner_portal() -> None:
    """Vehicle-owner login/signup. Open registration for citizens."""
    top_navbar("owner")
    _back_to_home_button()
    st.markdown(
        "<div class='hero'><p class='eyebrow'>Citizen Access · My Vehicles</p>"
        "<h1>Vehicle Owner Portal</h1>"
        "<p>Track challans issued against your vehicles, pay securely, and keep an eye on "
        "your penalties and licence status — all in real time.</p></div>",
        unsafe_allow_html=True,
    )
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        st.markdown("<div class='panel-card'>", unsafe_allow_html=True)
        login_tab, signup_tab = st.tabs(["Owner Login", "Owner Signup"])
        with login_tab:
            with st.form("owner_login"):
                email = st.text_input("Email", key="owner_login_email")
                password = st.text_input("Password", type="password", key="owner_login_password")
                submitted = st.form_submit_button("Owner Sign In →", use_container_width=True)
            if submitted:
                owner = authenticate_owner(email, password)
                if owner:
                    st.session_state.pending_login = {"actor_type": "owner", "actor": owner}
                    st.session_state.pending_totp_secret = None
                    st.rerun()
                else:
                    st.error("Email or password not recognised. If you're on a slow connection and the page "
                             "reloaded, just try signing in again — it should work.")
        with signup_tab:
            with st.form("owner_signup"):
                name = st.text_input("Full name")
                email = st.text_input("Email", key="owner_signup_email")
                phone = st.text_input("Phone number (linked for e-challan alerts)")
                password = st.text_input("Create password", type="password", key="owner_signup_password")
                plate = st.text_input("Primary vehicle plate number", placeholder="e.g. MH01AB1234")
                vehicle_type = st.selectbox("Vehicle type", ["car", "bike", "bus", "truck"], index=0)
                model_name = st.text_input("Vehicle model (optional)")
                create = st.form_submit_button("Create Vehicle Owner Account", use_container_width=True)
            if create:
                if not all([name, email, password, plate]):
                    st.error("Name, email, password, and plate number are required.")
                elif create_owner_account(name, email, password, phone):
                    owner = authenticate_owner(email, password)
                    add_registered_vehicle(owner["owner_id"], plate, vehicle_type, model_name)
                    st.success("Vehicle owner account created. Sign in using the login tab.")
                else:
                    st.error("That owner email already exists.")
        st.markdown("</div>", unsafe_allow_html=True)


def render_login_tabs() -> None:
    """Backwards-compatible entry point — now routes to the landing/portal flow."""
    portal = st.session_state.get("portal")
    if portal == "authority":
        render_authority_portal()
    elif portal == "owner":
        render_owner_portal()
    else:
        render_landing()


# ---- MFA GATE ----
def render_mfa_gate() -> None:
    pending = st.session_state.pending_login
    actor_type = pending["actor_type"]
    actor = pending["actor"]
    hero("Two-Factor Verification",
         "Open Google Authenticator or Microsoft Authenticator and enter the current 6-digit code. "
         "Use manual setup-key entry — no Google account is required.",
         eyebrow="Security · Step 2 of 2")

    if actor_type == "authority":
        security = get_user_security(actor["user_id"])
        is_enabled = bool(security.get("is_2fa_enabled"))
        secret = security.get("totp_secret")
    else:
        is_enabled = bool(actor.get("is_2fa_enabled"))
        secret = actor.get("totp_secret")

    st.markdown("<div class='panel-card'>", unsafe_allow_html=True)
    if not is_enabled or not secret:
        if not st.session_state.pending_totp_secret:
            st.session_state.pending_totp_secret = generate_totp_secret()
        secret = st.session_state.pending_totp_secret
        st.markdown("<div class='section-title'>Enrol 2FA for this account</div>", unsafe_allow_html=True)
        st.caption("In your authenticator app → Add account → **Enter setup key** → choose **Time based** → paste the key below.")
        st.code(secret, language=None)
        code = st.text_input("Enter the 6-digit code", max_chars=6, key="enroll_code")
        c1, c2 = st.columns(2)
        if c1.button("Enable 2FA & continue", use_container_width=True):
            if verify_totp(secret, code):
                if actor_type == "authority":
                    set_user_totp_secret(actor["user_id"], secret, enabled=True)
                    st.session_state.authority = actor
                else:
                    set_owner_totp_secret(actor["owner_id"], secret, enabled=True)
                    actor["totp_secret"] = secret
                    actor["is_2fa_enabled"] = 1
                    st.session_state.owner = actor
                st.session_state.pending_login = None
                st.session_state.pending_totp_secret = None
                st.success("2FA enabled.")
                st.rerun()
            else:
                st.error("Incorrect 6-digit code.")
        if c2.button("Cancel", use_container_width=True):
            st.session_state.pending_login = None
            st.session_state.pending_totp_secret = None
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)
        return

    st.markdown("<div class='section-title'>Enter your current code</div>", unsafe_allow_html=True)
    st.caption("Open your authenticator app and enter the current 6-digit code. "
               "The code refreshes every 30 seconds — if it just changed, wait for the next one. "
               "On a slow connection, use the code that's at least 10 seconds old.")
    code = st.text_input("6-digit authenticator code", max_chars=6, key="verify_code",
                         placeholder="000000")
    c1, c2 = st.columns([3, 1])
    if c1.button("Verify & continue →", use_container_width=True):
        if verify_totp(secret, code):
            if actor_type == "authority":
                st.session_state.authority = actor
            else:
                st.session_state.owner = actor
            st.session_state.pending_login = None
            st.success("Verification successful.")
            st.rerun()
        else:
            st.error("Code not accepted — the app accepts codes up to 90 seconds old. "
                     "If this keeps happening: (1) check your phone's clock is set to automatic/sync, "
                     "(2) wait for the next code in your authenticator app, then try again immediately.")
    if c2.button("Back", use_container_width=True):
        st.session_state.pending_login = None
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)


# ---- SIDEBARS ----
def _sidebar_brand() -> None:
    st.markdown(
        "<div class='brand-mark'>🛰️ DriveShield<span>X</span></div>"
        "<div class='small-muted' style='margin-bottom:.6rem'>Enforcement Command Center</div>",
        unsafe_allow_html=True,
    )


def top_nav(portal_name: str, who: str, pages: list, state_key: str) -> str:
    """Render a real top navigation bar with the page links as shiny text links
    (no boxes), like a normal website. Returns the active page.

    Clicking a link sets the active page in session_state and reruns — the rerun is
    what plays the smooth page-in animation on the new content."""
    if state_key not in st.session_state or st.session_state[state_key] not in pages:
        st.session_state[state_key] = pages[0]
    active = st.session_state[state_key]

    # brand + account strip
    st.markdown(
        f"<div class='navrow' style='justify-content:space-between'>"
        f"<div class='nb-brand'><span class='dot'>DS</span>DriveShield<b>X</b>"
        f"<span class='tag' style='margin-left:.6rem'>{portal_name}</span></div>"
        f"<div style='display:flex;gap:.6rem;align-items:center'>"
        f"<span class='tag'>👤 {who}</span></div></div>",
        unsafe_allow_html=True,
    )

    # the clickable links: a row of buttons restyled to look like shiny text links
    cols = st.columns(len(pages) + 2)
    for i, name in enumerate(pages):
        is_active = (name == active)
        label = f"● {name}" if is_active else name   # active link gets a gold dot
        # key prefix drives the CSS: navlink_active_* for the current page, navlink_* otherwise
        prefix = "navlink_active_" if is_active else "navlink_"
        with cols[i]:
            if st.button(label, key=f"{prefix}{state_key}_{i}", use_container_width=True):
                st.session_state[state_key] = name
                active = name
                st.rerun()
    with cols[len(pages)]:
        st.session_state.live_refresh = st.toggle("Live", value=st.session_state.live_refresh,
                                                  key=f"{state_key}_live", help="Live auto-refresh")
    with cols[len(pages) + 1]:
        if st.button("Sign out", key=f"navsignout_{state_key}", use_container_width=True):
            logout_all()
    return active


def authority_sidebar() -> str:
    return top_nav(
        "Traffic Authority", st.session_state.authority["name"],
        ["Command Center", "Live Monitor", "Notice Desk", "Rule Violations", "Road Safety", "Analytics",
         "Reports", "Configuration", "Users", "Security", "System Health"],
        "authority_page",
    )


def owner_sidebar() -> str:
    return top_nav(
        "Vehicle Owner", st.session_state.owner["name"],
        ["My Dashboard", "My Vehicles", "Security"],
        "owner_page",
    )


# ---- AUTHORITY PAGES ----
def page_command_center() -> None:
    if st.session_state.live_refresh and _HAS_AUTOREFRESH:
        st_autorefresh(interval=5000, key="cmd_refresh")
    hero("Command Center",
         "Real-time enforcement overview across all cameras — telemetry, severity mix, the notice pipeline and the live violation feed.",
         live=st.session_state.live_refresh)

    stats = get_violation_stats()
    kpi_cards([
        ("Total violations", f"{stats['total']:,}", "amber"),
        ("Today", f"{stats['today']:,}", "red"),
        ("Avg speed", f"{stats['avg_speed']:.1f}", "jade"),
        ("Active sessions", f"{stats['active_sessions']}", ""),
        ("Issued notices", f"{stats['total_notices']:,}", ""),
    ])

    left, right = st.columns([2, 1], gap="large")
    with left:
        st.markdown("<div class='section-title'>Live Speed Telemetry</div>", unsafe_allow_html=True)
        speed_df = _prepare_speed_df(limit=250)
        if speed_df.empty:
            st.info("No speed records yet — run the Live Monitor to populate telemetry.")
        else:
            st.plotly_chart(_speed_figure(speed_df, 420), use_container_width=True, key="cmd_speed_chart")
    with right:
        st.markdown("<div class='section-title'>Severity Mix</div>", unsafe_allow_html=True)
        sev = stats.get("by_severity", {})
        sev_df = pd.DataFrame({"severity": list(sev.keys()), "count": list(sev.values())})
        if sev_df.empty:
            st.info("No violations yet.")
        else:
            fig2 = px.bar(sev_df, x="severity", y="count", color="severity", color_discrete_map=SEV_COLORS)
            fig2.update_layout(showlegend=False, xaxis_title="", yaxis_title="")
            st.plotly_chart(_style_fig(fig2, 420), use_container_width=True, key="cmd_sev_chart")

    bottom_left, bottom_right = st.columns([1.4, 1], gap="large")
    with bottom_left:
        st.markdown("<div class='section-title'>Latest Violations</div>", unsafe_allow_html=True)
        vdf = _prepare_violation_df(limit=20)
        if vdf.empty:
            st.info("No violations stored yet.")
        else:
            show_cols = [c for c in ["violation_id", "violation_time", "severity_level", "status",
                                     "plate_text", "speed_value", "speed_limit", "location"] if c in vdf.columns]
            styled = vdf[show_cols].style.map(
                _severity_style, subset=["severity_level"] if "severity_level" in show_cols else None)
            st.dataframe(styled, use_container_width=True, hide_index=True)
    with bottom_right:
        st.markdown("<div class='section-title'>Notice Pipeline</div>", unsafe_allow_html=True)
        notices = pd.DataFrame(get_all_notices(200))
        if notices.empty:
            st.info("No notices issued yet.")
        else:
            pay_df = notices.groupby("payment_status").size().reset_index(name="count")
            fig3 = px.pie(pay_df, names="payment_status", values="count", hole=0.62,
                          color_discrete_sequence=[AMBER, JADE, RED, VIOLET])
            fig3.update_traces(textfont_color=TEXT)
            st.plotly_chart(_style_fig(fig3, 300), use_container_width=True, key="cmd_notice_chart")
        events_df = pd.DataFrame(get_system_events(6))
        if not events_df.empty:
            st.dataframe(events_df[[c for c in ["created_at", "event_type", "level", "message"]
                                    if c in events_df.columns]], use_container_width=True, hide_index=True)


def page_live_monitor() -> None:
    hero("Live Monitor",
         "Uploaded video, webcam and RTSP/IP camera sources · ByteTrack or centroid tracking · "
         "automatic number-plate recognition · zone-aware speed limits · streaming frame + telemetry updates.",
         live=True)

    # ---- ANPR demo: prove plate recognition + deblur works without a webcam ----
    with st.expander("🔍 Number-Plate Recognition demo (deblur + scan a photo / frame)", expanded=False):
        st.caption("Runs the SAME EasyOCR engine used in live detection, with a deblur + sharpen step so "
                   "even blurry CCTV frames become readable. Upload a vehicle/plate image, or try a sample "
                   "(including a deliberately blurry one) to see the recovery + recognition.")
        from detection.anpr_demo import scan_plate_image, make_synthetic_plate, deblur_preview
        import numpy as _np
        dc1, dc2 = st.columns([2, 1])
        plate_img = dc2.file_uploader("Plate / vehicle image", type=["jpg", "jpeg", "png"], key="anpr_img")
        use_sample = dc2.button("Sample plate", use_container_width=True, key="anpr_sample")
        use_blurry = dc2.button("Blurry sample (test deblur)", use_container_width=True, key="anpr_blurry")
        target = None
        try:
            import cv2 as _cv2
            if plate_img is not None:
                data = _np.frombuffer(plate_img.read(), _np.uint8)
                target = _cv2.imdecode(data, _cv2.IMREAD_COLOR)
            elif use_sample:
                target = make_synthetic_plate("MH12DE1433", blur=False)
            elif use_blurry:
                target = make_synthetic_plate("MH12DE1433", blur=True)
        except Exception as exc:  # pragma: no cover
            st.warning(f"Could not read the image: {exc}")
        if target is not None:
            res = scan_plate_image(target)
            shown = res.annotated if res.annotated is not None else target
            iv1, iv2 = dc1.columns(2)
            iv1.image(shown[:, :, ::-1], caption="Input (plate region boxed)", use_container_width=True)
            deb = deblur_preview(target)
            if deb is not None:
                iv2.image(deb, caption="After deblur + sharpen (fed to OCR)", use_container_width=True, clamp=True)
            if res.ok:
                dc1.success(f"Recognised plate: **{res.plate}**  ·  confidence {res.confidence:.0%}  ·  stored to database on live runs")
                if res.candidates:
                    dc1.caption("Other candidates: " + ", ".join(f"{t} ({c:.0%})" for t, c in res.candidates))
            else:
                dc1.info(res.message)

    cameras = get_all_cameras()
    if not cameras:
        st.warning("Add a camera first on the Configuration page.")
        return
    cam_options = {f"#{c['camera_id']} · {c['location']} ({c['camera_type']})": c for c in cameras}
    c1, c2 = st.columns([2, 1], gap="large")
    with c1:
        selected_label = st.selectbox("Camera", list(cam_options.keys()))
        selected_camera = cam_options[selected_label]
        mode = st.radio("Source type", ["Uploaded video", "RTSP/IP camera", "Webcam"], horizontal=True)
        uploaded = None
        source = ""
        source_type = "file"
        if mode == "Uploaded video":
            uploaded = st.file_uploader("Upload CCTV / UA-DETRAC video", type=["mp4", "avi", "mov", "mkv"])
            source_type = "file"
        elif mode == "RTSP/IP camera":
            source = st.text_input("RTSP / IP camera URL", value=selected_camera.get("rtsp_url") or "")
            source_type = "rtsp"
        else:
            source = st.text_input("Webcam index", value="0")
            source_type = "webcam"
    with c2:
        cfg = get_config(selected_camera["camera_id"]) or {}
        tracker_mode = st.selectbox("Tracker", ["bytetrack", "centroid"],
                                    index=0 if cfg.get("tracker_mode", "bytetrack") == "bytetrack" else 1)
        enable_npr = st.checkbox("Number Plate Recognition", value=bool(cfg.get("enable_npr", 1)))
        demo_plate_mode = st.checkbox("Demo plate fallback", value=False,
                                      help="If ANPR can't read a plate (e.g. distant CCTV), assign a "
                                           "consistent demo plate per vehicle so the full challan flow works. "
                                           "For a real read, use closer footage where plates are clearly visible.")
        gpu_enabled = st.checkbox("Use GPU if available", value=bool(cfg.get("gpu_enabled", 0)),
                                  help="Safe to leave on — if your PC has no NVIDIA GPU, the app "
                                       "automatically falls back to CPU instead of erroring.")
        frame_skip = st.number_input("Frame skip (higher = faster)", 1, 10, 2,
                                     help="Process every Nth frame. 2–3 is much faster and still accurate.")
        max_frames = st.number_input("Frames to process this run", 50, 5000, 400, step=50)
        resize_width = st.number_input("Resize width (0 = original)", 0, 1920, 960, step=10,
                                       help="Smaller = faster. 960 is a good balance of speed and clarity.")
        notes = st.text_area("Run notes", height=80)

    st.caption("⚡ Tip for speed: keep Frame skip at 2–3 and Resize width at 960. The video plays "
               "smoothly; the live graph + tables refresh every few frames so nothing lags.")

    start = st.button("▶  Start Live Processing", use_container_width=True)
    st.caption(f"Active config → Speed limit: {cfg.get('speed_limit', 60.0)} km/h · "
               f"Pixel scale: {cfg.get('pixel_to_meter_scale', 0.045)} m/px")

    frame_placeholder = st.empty()
    metrics_placeholder = st.empty()
    chart_placeholder = st.empty()
    table_placeholder = st.empty()
    event_placeholder = st.empty()

    if start:
        if mode == "Uploaded video" and not uploaded:
            st.error("Upload a video first.")
            return
        if mode == "Uploaded video":
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=Path(uploaded.name).suffix)
            tmp.write(uploaded.read())
            tmp.flush()
            source = tmp.name
        st.session_state.run_nonce += 1
        nonce = st.session_state.run_nonce
        pipeline = AdvancedOverspeedPipeline(
            camera_id=selected_camera["camera_id"],
            source=source,
            source_type=source_type,
            tracker_mode=tracker_mode,
            enable_npr=enable_npr,
            gpu_enabled=gpu_enabled,
            frame_skip=frame_skip,
            resize_width=(resize_width or None),
            demo_plate_mode=demo_plate_mode,
        )
        try:
            ui_every = 8  # refresh charts/tables every N frames (keeps video smooth & fast)
            for result in pipeline.run_generator(max_frames=int(max_frames)):
                metrics_placeholder.markdown(
                    f"<div class='panel-card' style='padding:.7rem 1rem'>"
                    f"<span class='small-muted'>Frame</span> <b>{result.frame_index}</b> &nbsp;·&nbsp; "
                    f"<span class='small-muted'>Tracked</span> <b style='color:{JADE}'>{result.tracked}</b> &nbsp;·&nbsp; "
                    f"<span class='small-muted'>Violations</span> <b style='color:{RED}'>{result.violations}</b> &nbsp;·&nbsp; "
                    f"<span class='small-muted'>FPS</span> <b style='color:{AMBER}'>{result.fps:.1f}</b></div>",
                    unsafe_allow_html=True)
                # Show every frame (crisp — no upscaling beyond native width)
                if result.annotated_frame is not None:
                    rgb = result.annotated_frame[:, :, ::-1]
                    frame_placeholder.image(rgb, use_container_width=True, channels="RGB")
                if result.latest_violation:
                    st.toast(f"⚠ Violation: {result.latest_violation['severity']} @ "
                             f"{result.latest_violation['speed']:.1f} km/h "
                             f"{result.latest_violation.get('plate_text') or ''}")
                # Heavy widgets (DB query + plotly + tables) only every ui_every frames,
                # and always on the final frame — this is the main speed win.
                is_refresh = (result.frame_index % ui_every == 0)
                if is_refresh:
                    latest_chart_df = _prepare_speed_df(limit=120, session_id=pipeline.session_id)
                    if not latest_chart_df.empty:
                        chart_placeholder.plotly_chart(
                            _speed_figure(latest_chart_df, 300), use_container_width=True,
                            key=f"live_speed_chart_{nonce}_{result.frame_index}")
                    latest_vdf = _prepare_violation_df(limit=12, camera_id=selected_camera["camera_id"])
                    if not latest_vdf.empty:
                        table_placeholder.dataframe(
                            latest_vdf[[c for c in ["violation_id", "plate_text", "severity_level", "speed_value",
                                                    "speed_limit", "violation_time", "location"] if c in latest_vdf.columns]],
                            use_container_width=True, hide_index=True)
            st.success("Monitoring completed successfully.")
        except Exception as exc:
            st.error(f"Monitoring stopped due to an error: {exc}")
        finally:
            if mode == "Uploaded video" and source and os.path.exists(source):
                try:
                    os.unlink(source)
                except OSError:
                    pass

    st.markdown("---")
    st.markdown("<div class='section-title'>Background multi-camera RTSP workers</div>", unsafe_allow_html=True)
    status = health_snapshot()
    cam_df = pd.DataFrame(status["cameras"])
    if cam_df.empty:
        st.info("No cameras configured.")
    else:
        st.dataframe(cam_df[[c for c in ["camera_id", "location", "camera_type", "status", "is_online",
                                         "last_frame_no", "last_fps", "last_latency_ms", "last_error"]
                             if c in cam_df.columns]], use_container_width=True, hide_index=True)
        cols = st.columns(min(3, len(cameras)))
        for idx, cam in enumerate(cameras):
            with cols[idx % len(cols)]:
                st.markdown(f"**Camera {cam['camera_id']}** · {cam['location']}")
                if st.button(f"Start RTSP worker {cam['camera_id']}", key=f"start_worker_{cam['camera_id']}"):
                    if cam.get("rtsp_url"):
                        MANAGER.start_camera(cam["camera_id"], cam["rtsp_url"], source_type="rtsp", max_frames=None)
                        st.success("Worker started.")
                    else:
                        st.warning("No RTSP URL configured for this camera.")
                if st.button(f"Stop worker {cam['camera_id']}", key=f"stop_worker_{cam['camera_id']}"):
                    MANAGER.stop_camera(cam["camera_id"])
                    st.info("Worker stop requested.")


def page_notice_desk() -> None:
    hero("Notice Desk",
         "Generate graduated e-challans for every overspeeding vehicle — registered or not. "
         "Repeat offenders escalate automatically: warning → higher fine → licence suspension.",
         eyebrow="Enforcement · E-Challan Engine")
    user = st.session_state.authority
    unissued = get_unissued_violations(500)
    all_notices = get_all_notices(500)

    # --- summary KPIs ---
    reg_pending = sum(1 for v in unissued if v.get("plate_text"))
    total_fine = sum(float(n.get("amount") or 0) + float(n.get("late_fee") or 0) for n in all_notices)
    suspended = sum(1 for n in all_notices if n.get("action_taken") == "license_suspension")
    kpi_cards([
        ("Awaiting challan", f"{len(unissued)}", "red"),
        ("Challans issued", f"{len(all_notices)}", "amber"),
        ("Licence actions", f"{suspended}", "red"),
        ("Total penalties", f"₹{total_fine:,.0f}", ""),
    ])

    # --- BULK: issue for ALL overspeeders ---
    st.markdown("<div class='panel-card'>", unsafe_allow_html=True)
    st.markdown("<div class='section-title'>⚡ Auto-issue e-challans for all overspeeders</div>", unsafe_allow_html=True)
    st.caption("Runs over every violation detected in your uploaded video / webcam sessions that doesn't yet have a "
               "challan. Each vehicle's offence count decides the fine tier and whether a licence-suspension applies.")
    c1, c2 = st.columns([2, 1])
    channel_label = c1.selectbox(
        "Where should notices be delivered?",
        ["Owner dashboard only", "Email outbox only", "Both dashboard + email"],
        help="Registered owners are matched by plate and notified; unregistered plates are still logged & fined.",
    )
    chan = {"Owner dashboard only": "dashboard", "Email outbox only": "email", "Both dashboard + email": "both"}[channel_label]
    if c2.button(f"Issue challans for all ({len(unissued)})", use_container_width=True, disabled=not unissued):
        res = bulk_issue_challans_for_all(user["user_id"], delivery_channel=chan)
        st.success(
            f"Issued {res['issued']} e-challan(s) · ₹{res['total_fine']:,.0f} total · "
            f"{res['matched_owners']} to registered owners · {res['unregistered']} unregistered · "
            f"{res['suspended']} licence suspension(s)."
        )
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

    # --- standalone manual issue: does NOT depend on an unissued detection ---
    with st.expander("📝 Issue a manual e-challan", expanded=False):
        st.caption(
            "Use this when an authority needs to issue a challan directly by vehicle plate. "
            "It works even when there is no detected overspeeding record."
        )

        mc1, mc2 = st.columns(2)
        with mc1:
            manual_plate = st.text_input(
                "Vehicle plate number",
                placeholder="e.g. MH01AB1234",
                key="manual_challan_plate",
            )
            manual_amount = st.number_input(
                "Fine amount (₹)",
                min_value=1.0,
                value=500.0,
                step=100.0,
                key="manual_challan_amount",
            )
            manual_due = st.date_input(
                "Due date",
                value=date.today() + timedelta(days=14),
                key="manual_challan_due",
            )

        with mc2:
            manual_channel_label = st.selectbox(
                "Notice delivery",
                ["Owner dashboard only", "Email outbox only", "Both dashboard + email"],
                key="manual_challan_channel",
            )
            manual_notes = st.text_area(
                "Authority notes",
                placeholder="Optional reason / remarks for this manual challan",
                height=125,
                key="manual_challan_notes",
            )

        manual_channel = {
            "Owner dashboard only": "dashboard",
            "Email outbox only": "email",
            "Both dashboard + email": "both",
        }[manual_channel_label]

        if manual_plate.strip():
            matched = next(
                (
                    v for v in get_registered_vehicles()
                    if str(v.get("plate_number", "")).strip().upper() == manual_plate.strip().upper()
                ),
                None,
            )
            if matched:
                st.success(
                    f"Registered owner found: **{matched.get('owner_name', 'Unknown')}** · "
                    f"{matched.get('vehicle_type', 'vehicle')}"
                )
            else:
                st.info("No registered owner found for this plate. The manual challan can still be issued.")

        preview = {
            "Plate": manual_plate.strip().upper() or "—",
            "Fine": f"₹{manual_amount:,.0f}",
            "Due": manual_due.isoformat(),
            "Delivery": manual_channel_label,
        }
        st.dataframe(pd.DataFrame([preview]), use_container_width=True, hide_index=True)

        if st.button("Issue Manual E-Challan", use_container_width=True, key="issue_manual_challan"):
            if not manual_plate.strip():
                st.error("Vehicle plate number is required.")
            else:
                try:
                    result = create_manual_challan(
                        plate_number=manual_plate,
                        amount=float(manual_amount),
                        issued_by=user["user_id"],
                        due_date=manual_due.isoformat(),
                        notes=manual_notes,
                        delivery_channel=manual_channel,
                    )
                    owner_text = (
                        f" · owner: {result['owner_name']}"
                        if result.get("owner_name")
                        else " · no registered owner matched"
                    )
                    st.success(
                        f"Manual e-challan #{result['notice_id']} issued for "
                        f"{result['plate_number']} · ₹{result['amount']:,.0f}"
                        f"{owner_text}."
                    )
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
                except Exception as exc:
                    st.error(f"Could not issue the manual e-challan: {exc}")

    # --- issued notices table ---
    if not all_notices:
        st.info("No challans issued yet. Process a video in Live Monitor, then auto-issue above.")
        return
    apply_overdue_penalties()  # keep statuses fresh on each view
    all_notices = get_all_notices(500)
    notices_df = pd.DataFrame(all_notices)
    st.markdown("<div class='section-title'>Issued e-challans</div>", unsafe_allow_html=True)
    show_cols = [c for c in ["notice_id", "plate_number", "registration", "offense_tier", "action_taken",
                             "suspension_months", "amount", "late_fee", "payment_status", "delivery_channel",
                             "speed_value", "speed_limit", "location", "due_date", "owner_name"]
                 if c in notices_df.columns]
    st.dataframe(notices_df[show_cols], use_container_width=True, hide_index=True)

    # --- payment status / challan PDF ---
    with st.expander("Update a challan · export PDF", expanded=False):
        options = {f"#{n['notice_id']} · {n['plate_number']} · {n['offense_tier']} · {n['payment_status']}": n for n in all_notices}
        label = st.selectbox("Select challan", list(options.keys()))
        selected = options[label]
        statuses = ["pending", "paid", "disputed", "exported", "overdue", "waived"]
        idx = statuses.index(selected["payment_status"]) if selected["payment_status"] in statuses else 0
        new_status = st.selectbox("Payment status", statuses, index=idx)
        c1, c2 = st.columns(2)
        if c1.button("Save payment status", use_container_width=True):
            update_notice_payment_status(selected["notice_id"], new_status)
            st.success("Status updated."); st.rerun()
        if c2.button("Generate challan PDF", use_container_width=True):
            pdf_path = generate_challan_pdf(selected)
            st.success(f"Challan exported: {pdf_path}")
            st.download_button("Download challan PDF", data=Path(pdf_path).read_bytes(),
                               file_name=Path(pdf_path).name, mime="application/pdf",
                               key=f"dl_challan_{selected['notice_id']}")

    try:
        from dashboard.payments_admin import render_payments_admin
        render_payments_admin()
    except Exception as _pay_exc:
        st.caption(f"(online-payments panel unavailable: {_pay_exc})")
    try:
        from dashboard.sms_admin import render_sms_admin
        render_sms_admin()
    except Exception as _sms_exc:
        st.caption(f"(SMS panel unavailable: {_sms_exc})")
    try:
        from dashboard.licence_panels import render_auto_issue_admin
        render_auto_issue_admin()
    except Exception as _ai_exc:
        st.caption(f"(automatic e-challan panel unavailable: {_ai_exc})")
    try:
        from dashboard.licence_panels import render_licence_admin
        render_licence_admin()
    except Exception as _lic_exc:
        st.caption(f"(licence panel unavailable: {_lic_exc})")
    try:
        from dashboard.escalation_panels import render_escalation_admin
        render_escalation_admin()
    except Exception as _esc_exc:
        st.caption(f"(escalation panel unavailable: {_esc_exc})")


def page_reports() -> None:
    hero("Reports", "Generate PDF reports from violation data and download them directly.",
         eyebrow="Analytics · Export")
    user = st.session_state.authority
    cameras = get_all_cameras()
    cam_map = {"All Cameras": None, **{f"{c['camera_id']} · {c['location']}": c['camera_id'] for c in cameras}}
    st.markdown("<div class='panel-card'>", unsafe_allow_html=True)
    with st.form("report_form"):
        from_date = st.date_input("From date", value=date.today() - timedelta(days=7))
        to_date = st.date_input("To date", value=date.today())
        camera_label = st.selectbox("Camera scope", list(cam_map.keys()))
        make = st.form_submit_button("Generate PDF report", use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)
    if make:
        pdf_path = generate_pdf_report(generated_by=user["user_id"], from_date=from_date.isoformat(),
                                       to_date=to_date.isoformat(), camera_id=cam_map[camera_label])
        st.success(f"Report generated: {pdf_path}")
        st.download_button("Download latest report", data=Path(pdf_path).read_bytes(),
                           file_name=Path(pdf_path).name, mime="application/pdf", key="download_latest_report")

    reports_df = pd.DataFrame(get_reports(50))
    if not reports_df.empty:
        st.markdown("<div class='section-title'>Generated report history</div>", unsafe_allow_html=True)
        st.dataframe(reports_df[[c for c in ["report_id", "report_date", "report_type", "from_date",
                                             "to_date", "generated_by_name", "file_path"]
                                 if c in reports_df.columns]], use_container_width=True, hide_index=True)


def page_configuration() -> None:
    hero("Configuration", "Detection settings, RTSP/IP cameras, and per-zone speed limits per camera.",
         eyebrow="Admin · Setup")
    cameras = get_all_cameras()
    if not cameras:
        st.error("No cameras available.")
        return
    cam_options = {f"#{c['camera_id']} · {c['location']}": c for c in cameras}
    left, right = st.columns(2, gap="large")
    with left:
        selected_label = st.selectbox("Select camera", list(cam_options.keys()))
        selected = cam_options[selected_label]
        cfg = get_config(selected["camera_id"]) or {}
        st.markdown("<div class='section-title'>Detection Configuration</div>", unsafe_allow_html=True)
        speed_limit = st.number_input("Speed limit (km/h)", min_value=5.0, value=float(cfg.get("speed_limit", 60.0)), step=5.0)
        pixel_scale = st.number_input("Pixel-to-meter scale", min_value=0.001, value=float(cfg.get("pixel_to_meter_scale", 0.045)), step=0.001, format="%.4f")
        tracker_mode = st.selectbox("Tracker mode", ["bytetrack", "centroid"], index=0 if cfg.get("tracker_mode", "bytetrack") == "bytetrack" else 1)
        enable_npr = st.checkbox("Enable Number Plate Recognition", value=bool(cfg.get("enable_npr", 1)))
        frame_skip = st.number_input("Frame skip", min_value=1, max_value=10, value=int(cfg.get("frame_skip", 1)))
        gpu_enabled = st.checkbox("Use GPU if available", value=bool(cfg.get("gpu_enabled", 0)))
        batch_mode = st.checkbox("Batch mode for recorded videos", value=bool(cfg.get("batch_mode", 0)))
        if st.button("Save Configuration", use_container_width=True):
            update_config(selected["camera_id"], speed_limit, pixel_scale, st.session_state.authority["user_id"],
                          tracker_mode, enable_npr, frame_skip, gpu_enabled, batch_mode)
            st.success("Configuration saved.")
    with right:
        st.markdown("<div class='section-title'>Camera Inventory</div>", unsafe_allow_html=True)
        st.dataframe(pd.DataFrame(cameras), use_container_width=True, hide_index=True)
        with st.form("camera_form"):
            location = st.text_input("Camera location")
            camera_type = st.selectbox("Camera type", ["CCTV", "RTSP", "Webcam", "IP Camera"])
            rtsp_url = st.text_input("RTSP / stream URL (optional)")
            install_date = st.date_input("Installation date", value=date.today())
            status = st.selectbox("Status", ["active", "inactive", "maintenance"])
            add = st.form_submit_button("Add Camera", use_container_width=True)
        if add:
            if location.strip():
                add_camera(location, camera_type, install_date.isoformat(), status, rtsp_url.strip() or None)
                st.success("Camera added.")
            else:
                st.error("Location is required.")

    st.markdown("---")
    st.markdown("<div class='section-title'>Speed-Limit Zones</div>", unsafe_allow_html=True)
    zones = pd.DataFrame(list_zones(selected["camera_id"]))
    if zones.empty:
        st.info("No zones configured yet.")
    else:
        st.dataframe(zones[[c for c in ["zone_id", "zone_name", "x1", "y1", "x2", "y2", "speed_limit",
                                        "pixel_to_meter_scale", "is_active"] if c in zones.columns]],
                     use_container_width=True, hide_index=True)
    with st.form("zone_form"):
        zone_name = st.text_input("Zone name")
        z1, z2, z3, z4 = st.columns(4)
        x1 = z1.number_input("x1", min_value=0, value=0)
        y1 = z2.number_input("y1", min_value=0, value=0)
        x2 = z3.number_input("x2", min_value=1, value=1280)
        y2 = z4.number_input("y2", min_value=1, value=720)
        zone_limit = st.number_input("Zone speed limit", min_value=5.0, value=float(cfg.get("speed_limit", 60.0)), step=5.0)
        zone_scale = st.number_input("Zone pixel-to-meter scale (optional override)", min_value=0.0,
                                     value=float(cfg.get("pixel_to_meter_scale", 0.045)), step=0.001, format="%.4f")
        add_zone_btn = st.form_submit_button("Add / Save Zone", use_container_width=True)
    if add_zone_btn and zone_name.strip():
        add_zone(selected["camera_id"], zone_name.strip(), int(x1), int(y1), int(x2), int(y2),
                 float(zone_limit), float(zone_scale), True)
        st.success("Zone added.")
    if not zones.empty:
        zone_options = {f"#{row.zone_id} · {row.zone_name}": int(row.zone_id) for row in zones.itertuples()}
        label = st.selectbox("Delete zone", list(zone_options.keys()), key="del_zone_select")
        if st.button("Delete selected zone"):
            delete_zone(zone_options[label])
            st.success("Zone deleted.")


def page_users() -> None:
    hero("Users", "Manage authority accounts and review vehicle-owner registrations.", eyebrow="Admin · Identity")
    authority = st.session_state.authority
    users_df = pd.DataFrame(get_all_users())
    st.markdown("<div class='section-title'>Authority users</div>", unsafe_allow_html=True)
    st.dataframe(users_df, use_container_width=True, hide_index=True)
    if authority["role"] == "admin":
        with st.expander("Add authority user", expanded=False):
            with st.form("add_user_form"):
                name = st.text_input("Name")
                email = st.text_input("Email")
                phone = st.text_input("Contact number")
                password = st.text_input("Temporary password", type="password")
                role = st.selectbox("Role", ["traffic_authority", "admin"], format_func=role_label)
                create = st.form_submit_button("Create authority account", use_container_width=True)
            if create:
                st.success("Authority account created.") if add_user(name, email, password, role, phone) \
                    else st.error("Unable to create authority account.")
    st.markdown("<div class='section-title'>Vehicle-owner registry</div>", unsafe_allow_html=True)
    st.dataframe(pd.DataFrame(get_registered_vehicles()), use_container_width=True, hide_index=True)


def _totp_panel(save_fn, enabled: bool, secret: Optional[str], state_key: str) -> None:
    if not enabled or not secret:
        st.warning("2FA is not enabled for this account.")
        secret = st.session_state.get(state_key) or generate_totp_secret()
        st.session_state[state_key] = secret
        st.caption("Add this key in your authenticator app (manual / time-based), then confirm the code.")
        st.code(secret)
        code = st.text_input("Authenticator code", max_chars=6, key=state_key + "_code")
        if st.button("Enable 2FA", use_container_width=True, key=state_key + "_enable"):
            if verify_totp(secret, code):
                save_fn(secret)
                st.success("2FA enabled.")
                st.rerun()
            else:
                st.error("Incorrect code.")
    else:
        st.success("2FA is enabled for this account.")
        code = st.text_input("Test current 6-digit code", max_chars=6, key=state_key + "_verify")
        if st.button("Verify current code", use_container_width=True, key=state_key + "_verifybtn"):
            st.success("Code verified.") if verify_totp(secret, code) else st.error("Invalid or expired code.")


def page_security() -> None:
    hero("Security", "Manual TOTP 2FA via Google / Microsoft Authenticator. No real Google account is required.",
         eyebrow="Account · Protection")
    user = st.session_state.authority
    sec = get_user_security(user["user_id"])
    st.markdown("<div class='panel-card'>", unsafe_allow_html=True)
    _totp_panel(
        save_fn=lambda s: set_user_totp_secret(user["user_id"], s, True),
        enabled=bool(sec.get("is_2fa_enabled")), secret=sec.get("totp_secret"),
        state_key="manual_security_secret",
    )
    st.markdown("</div>", unsafe_allow_html=True)


def page_system_health() -> None:
    if st.session_state.live_refresh and _HAS_AUTOREFRESH:
        st_autorefresh(interval=6000, key="health_refresh")
    hero("System Health & Logging",
         "Camera availability, DB size, latency, recent alerts and operational error logs.",
         live=st.session_state.live_refresh, eyebrow="Operations · Observability")
    snap = get_system_health()
    kpi_cards([
        ("DB size", f"{snap['db_size_mb']:.2f} MB", "amber"),
        ("Active streams", f"{snap['active_streams']}", "jade"),
        ("Active sessions", f"{snap['active_sessions']}", ""),
    ])
    st.markdown("<div class='section-title'>Camera / stream status</div>", unsafe_allow_html=True)
    st.dataframe(pd.DataFrame(snap["cameras"]), use_container_width=True, hide_index=True)
    st.markdown("<div class='section-title'>Recent system events</div>", unsafe_allow_html=True)
    st.dataframe(pd.DataFrame(get_system_events(100)), use_container_width=True, hide_index=True)
    st.markdown("<div class='section-title'>Alert outbox</div>", unsafe_allow_html=True)
    st.dataframe(pd.DataFrame(list_alerts(100)), use_container_width=True, hide_index=True)


# ---- OWNER PAGES ----
def _offence_text(n: dict) -> str:
    """One-line offence description for a challan card (speed or helmet/triple/seat-belt)."""
    try:
        from backend.enforcement import RULE_POLICY
        rule = RULE_POLICY.get(n.get("violation_kind") or "")
    except Exception:
        rule = None
    if rule:
        return f"{rule['label']} · {rule['law']}"
    if n.get("speed_value") is not None:
        return (f"{n.get('speed_value')} km/h in a {n.get('speed_limit', '?')} km/h zone · "
                f"{n.get('location') or ''}")
    return (n.get("notes") or "Traffic violation")[:120]


def page_owner_notices() -> None:
    if st.session_state.live_refresh and _HAS_AUTOREFRESH:
        st_autorefresh(interval=8000, key="owner_refresh")
    owner = st.session_state.owner
    apply_overdue_penalties()
    _sync_owner_online_payments(owner)
    notices = get_owner_notices(owner["owner_id"])
    hero(f"Welcome, {owner.get('name','Driver')}",
         "Your e-challans, penalties and licence status — updated in real time. "
         "Pay securely by the due date to avoid late fees and escalation.",
         live=st.session_state.live_refresh, eyebrow="Citizen · My Dashboard")

    if not notices:
        st.markdown(
            "<div class='panel-card' style='text-align:center'>"
            "<div class='orbwrap'><div class='orb' style='width:84px;height:84px;font-size:1.5rem'>✓</div></div>"
            "<div class='section-title' style='margin-top:.6rem'>All clear — no violations on record</div>"
            "<p class='small-muted'>Your registered vehicles have a clean record. Keep driving within the limit "
            "and this dashboard stays green. Any future e-challan will appear here instantly.</p></div>",
            unsafe_allow_html=True,
        )
        return

    df = pd.DataFrame(notices)
    def _due(n):
        return float(n.get("amount") or 0) + float(n.get("late_fee") or 0)
    unpaid = [n for n in notices if n.get("payment_status") in ("pending", "overdue")]
    paid = [n for n in notices if n.get("payment_status") == "paid"]
    amount_due = sum(_due(n) for n in unpaid)
    has_suspension = any(n.get("action_taken") == "license_suspension" for n in notices)
    max_susp = max([int(n.get("suspension_months") or 0) for n in notices], default=0)

    kpi_cards([
        ("Total e-challans", f"{len(notices)}", "amber"),
        ("Unpaid", f"{len(unpaid)}", "red"),
        ("Paid", f"{len(paid)}", "jade"),
        ("Amount due", f"₹{amount_due:,.0f}", "red" if amount_due else ""),
    ])

    # ---- DriveShieldX layer-2 rule violations for THIS owner ----
    try:
        from database.db_manager import get_owner_rule_violations
        from detection.rule_violations import upi_intent, upi_qr_png
        rule_rows = get_owner_rule_violations(owner["owner_id"])
        if rule_rows:
            st.subheader("Helmet · Triple-riding · Seat-belt fines")
            st.caption("Additional violations detected by DriveShieldX. Pay via UPI QR — funds go to the traffic-authority VPA below.")
            for rec in rule_rows:
                cols = st.columns([2, 1])
                with cols[0]:
                    st.markdown(
                        f"**{rec['rule_type'].replace('_', ' ').title()}** — "
                        f"₹{int(rec['fine_amount'])} · plate `{rec.get('plate_text') or '—'}` · "
                        f"detected `{rec['detected_at']}` · status **{rec['status']}**"
                    )
                    if rec.get("snapshot_path"):
                        try: st.image(rec["snapshot_path"], width=280)
                        except Exception: pass
                with cols[1]:
                    if _razorpay_enabled():
                        if rec.get("status") == "issued":
                            st.info("Issued as an e-Challan — pay it under **Your e-challans** below.")
                        else:
                            st.caption("Detected — under officer review. Nothing to pay until an e-Challan is issued.")
                        continue
                    intent = upi_intent(
                        vpa=os.environ.get("UPI_ID", ""),
                        payee=os.environ.get("UPI_PAYEE_NAME", "DriveShieldX Traffic Authority"),
                        amount=int(rec["fine_amount"]),
                        tid=str(rec["rule_violation_id"]),
                        note=f"{rec['rule_type']} · {rec.get('plate_text') or ''}",
                    )
                    st.image(upi_qr_png(intent), width=200)
                    st.caption(f"UPI: {os.environ.get('UPI_ID', '') or 'set UPI_ID in .env'}")
                    st.caption(f"Call: {os.environ.get('CONTACT_PHONE', '') or '-'}")
            st.divider()
    except Exception as _rv_exc:
        st.caption(f"(rule-violation panel unavailable: {_rv_exc})")

    # ---- repeat-offender standing + real licence status (backend/enforcement.py) ----
    try:
        from dashboard.licence_panels import render_owner_standing
        render_owner_standing(owner)
    except Exception as _lic_exc:
        st.caption(f"(standing panel unavailable: {_lic_exc})")
    try:
        from dashboard.escalation_panels import render_owner_escalation
        render_owner_escalation(owner)
    except Exception as _esc_exc:
        st.caption(f"(contest panel unavailable: {_esc_exc})")

    # ---- receipts for challans settled in this session (UPI / card / Razorpay) ----
    paid_ids = {n["notice_id"] for n in paid}
    for _k in [k for k in list(st.session_state.keys()) if str(k).startswith("receipt_")]:
        try:
            _nid = int(str(_k).split("_", 1)[1])
        except ValueError:
            continue
        if _nid in paid_ids:
            _r = st.session_state.pop(_k)
            _ch = [c for c in ("email", "sms") if (_r.get("sent") or {}).get(c)]
            st.success(f"Payment confirmed · {_r['method']} · ref {_r['txn_ref']}. Challan #{_nid} is PAID."
                       + (f" Confirmation sent via {' and '.join(c.upper() if c == 'sms' else c for c in _ch)}." if _ch else ""))
            st.session_state.pop(f"checkout_{_nid}", None)

    # ---- challan cards with payment ----
    st.markdown("<div class='section-title'>Your e-challans</div>", unsafe_allow_html=True)
    tier_label = {"first": "1st offence", "second": "2nd offence", "third": "3rd+ offence"}
    for n in notices:
        tier = n.get("offense_tier", "first")
        status = n.get("payment_status", "pending")
        due_amt = _due(n)
        late = float(n.get("late_fee") or 0)
        susp = int(n.get("suspension_months") or 0)
        card_cls = "challan susp" if n.get("action_taken") == "license_suspension" else "challan"
        late_html = f" · <span style='color:#ffb0b0'>late fee ₹{late:,.0f}</span>" if late else ""
        susp_html = (f"<div class='meta' style='color:#ffb0b0;margin-top:.3rem'>Licence suspension: {susp} month(s)</div>"
                     if susp and status not in ("waived", "disputed") else "")
        st.markdown(
            f"<div class='{card_cls}'>"
            f"<div style='display:flex;justify-content:space-between;align-items:flex-start;gap:1rem'>"
            f"<div><span class='chip {tier}'>{tier_label.get(tier, tier)}</span> "
            f"<span class='chip {status}'>{status.upper()}</span>"
            f"<div style='font-family:Sora;font-weight:700;font-size:1.05rem;margin-top:.5rem'>{n.get('plate_number','—')} "
            f"<span class='small-muted'>· {n.get('vehicle_type','')}</span></div>"
            f"<div class='meta' style='margin-top:.25rem'>{_offence_text(n)}</div>"
            f"<div class='meta'>Challan #{n.get('notice_id')} · due {n.get('due_date','—')}{late_html}</div>"
            f"{susp_html}</div>"
            f"<div style='text-align:right;white-space:nowrap'><div class='amt'>₹{due_amt:,.0f}</div>"
            f"<div class='small-muted'>{ {'paid': 'PAID', 'waived': 'cancelled', 'disputed': 'on hold'}.get(status, 'payable') }</div></div>"
            f"</div></div>",
            unsafe_allow_html=True,
        )
        if status in ("pending", "overdue"):
            nid = n["notice_id"]
            checkout_key = f"checkout_{nid}"
            if not st.session_state.get(checkout_key):
                cpc = st.columns([1, 3])
                if cpc[0].button(f"Pay ₹{due_amt:,.0f}", key=f"open_{nid}", use_container_width=True):
                    st.session_state[checkout_key] = True
                    st.rerun()
                cpc[1].caption("Pay online with Razorpay (UPI, cards, netbanking) or scan the UPI QR — like any online checkout.")
            else:
                render_checkout(n, due_amt, owner)

    # ---- payment history ----
    if paid:
        with st.expander(f"Payment history ({len(paid)})", expanded=False):
            hist = pd.DataFrame(paid)
            st.dataframe(hist[[c for c in ["notice_id", "plate_number", "amount", "late_fee",
                                           "payment_method", "external_reference", "paid_at", "offense_tier"]
                                           if c in hist.columns]],
                         use_container_width=True, hide_index=True)


def _settle_and_notify(nid: int, method: str, txn_ref: str, notice: dict, owner: dict, amount: float) -> None:
    """Shared settlement step for both UPI and card: marks the challan paid in the DB,
    then fires the email + SMS payment-confirmation (best-effort, never blocks the UI)."""
    from backend.notifications import send_payment_confirmation
    pay_notice(nid, method, txn_ref=txn_ref)
    sent = send_payment_confirmation(
        notice_id=nid,
        plate_number=notice.get("plate_number", "—"),
        amount=amount,
        method=method,
        txn_ref=txn_ref,
        owner_email=(owner or {}).get("email"),
        owner_phone=(owner or {}).get("phone"),
    )
    st.session_state[f"receipt_{nid}"] = {"method": method, "txn_ref": txn_ref, "sent": sent}


RAZORPAY_MODE_LABEL = "Razorpay · UPI / Card / NetBanking"


def _razorpay_enabled() -> bool:
    try:
        from backend import razorpay_gateway
        return razorpay_gateway.enabled()
    except Exception:
        return False


def _sync_owner_online_payments(owner: dict) -> None:
    """Reconcile this owner's open Razorpay links (at most every 20 s), so a challan paid
    from the SMS link on a phone shows PAID here even without a webhook."""
    if not _razorpay_enabled():
        return
    import time as _t
    last = st.session_state.get("_rzp_owner_sync", 0.0)
    if _t.time() - last < 20:
        return
    st.session_state["_rzp_owner_sync"] = _t.time()
    try:
        from backend import razorpay_gateway
        ids = [n["notice_id"] for n in get_owner_notices(owner["owner_id"])
               if n.get("payment_status") in ("pending", "overdue")]
        if ids:
            razorpay_gateway.sync_open_links(ids)
    except Exception as exc:
        st.caption(f"(could not reach Razorpay: {exc})")


def _render_razorpay_checkout(notice: dict, amount: float, owner: dict) -> None:
    """Real gateway: create a Razorpay Payment Link for this challan, let the owner pay on
    Razorpay's hosted page, then settle from the signed redirect / webhook / status check."""
    from backend import razorpay_gateway
    from backend.payments import qr_png_bytes
    nid = notice["notice_id"]
    cfg = razorpay_gateway.config()
    if cfg.mode == "test":
        st.caption("TEST mode: no real money moves. Use UPI ID success@razorpay or a Razorpay test card.")
    key = f"rzp_link_{nid}"
    if key not in st.session_state:
        if st.button(f"Generate secure payment link · ₹{amount:,.0f}", key=f"rzp_make_{nid}", use_container_width=True):
            try:
                with st.spinner("Creating payment link with Razorpay…"):
                    st.session_state[key] = razorpay_gateway.create_payment_link(notice, amount, owner)
                st.rerun()
            except PermissionError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"Could not create the payment link: {exc}")
        st.caption("Razorpay also sends this link to the owner's registered phone/e-mail.")
        return
    link = st.session_state[key]
    c1, c2 = st.columns([1, 1.3])
    with c1:
        png = qr_png_bytes(link["short_url"]) if link.get("short_url") else None
        if png:
            st.image(png, caption="Scan to open the payment page", width=200)
    with c2:
        st.markdown(f"<div class='small-muted'>Reference</div><div style='font-family:JetBrains Mono;"
                    f"color:var(--gold-hi);margin-bottom:.6rem'>{link['reference_id']}</div>",
                    unsafe_allow_html=True)
        st.link_button("Pay now on Razorpay", link["short_url"], use_container_width=True, type="primary")
        st.caption("After paying you return here automatically. Paid from your phone? Tap below.")
        if st.button("Check payment status", key=f"rzp_check_{nid}", use_container_width=True):
            try:
                with st.spinner("Asking Razorpay…"):
                    status = razorpay_gateway.sync_link(link["link_id"])
            except Exception as exc:
                st.error(str(exc)); return
            if status == "paid":
                from database import payments_db
                row = payments_db.get_link(link["link_id"]) or {}
                st.session_state[f"receipt_{nid}"] = {
                    "method": f"Razorpay {(row.get('method') or '').upper()}".strip(),
                    "txn_ref": row.get("payment_id") or link["link_id"],
                    "sent": {"email": bool((owner or {}).get("email")), "sms": bool((owner or {}).get("phone"))}}
                st.session_state.pop(key, None)
                st.rerun()
            elif status in ("expired", "cancelled"):
                st.warning(f"This link is {status}. Generate a new one.")
                st.session_state.pop(key, None)
            else:
                st.info(f"Not paid yet (Razorpay status: {status}).")


def _handle_razorpay_return() -> None:
    """Razorpay sends the browser back to DSX_PUBLIC_BASE_URL with signed query params.
    The signature (HMAC-SHA256 with the key secret) is verified before the challan is
    settled, so a forged URL cannot mark anything paid. Works even if the login session
    was lost during the redirect."""
    qp = st.query_params
    if "razorpay_payment_link_id" not in qp:
        return
    params = {k: qp.get(k) for k in ("razorpay_payment_id", "razorpay_payment_link_id",
                                      "razorpay_payment_link_reference_id",
                                      "razorpay_payment_link_status", "razorpay_signature")}
    try:
        from backend import razorpay_gateway
        ok, msg = razorpay_gateway.handle_callback(params)
    except Exception as exc:
        ok, msg = False, f"Could not verify the payment: {exc}"
    st.session_state["_rzp_return_msg"] = (ok, msg, params.get("razorpay_payment_link_reference_id"))
    st.query_params.clear()


def _show_razorpay_return_banner() -> None:
    info = st.session_state.pop("_rzp_return_msg", None)
    if not info:
        return
    ok, msg, ref = info
    if ok:
        st.success(f"Payment received for {ref}. {msg} Your e-challan is now marked PAID and a "
                   "confirmation has been sent to your registered phone/e-mail.")
    else:
        st.error(f"Payment not confirmed: {msg}")


def render_checkout(notice: dict, amount: float, owner: dict) -> None:
    """Checkout panel with two payment options — UPI (scannable QR + per-app deep links)
    and Credit/Debit card — each ending in a gateway-style verify/capture step that
    settles the challan (DB update) and sends an email + SMS confirmation.
    (Real money movement needs a registered payment-gateway merchant account; this
    models the exact same verify → settle → notify shape a real integration would use —
    see backend/payments.py and backend/card_payments.py for the honest scope note.)"""
    nid = notice["notice_id"]

    # already paid in this render cycle — show the receipt instead of the form
    receipt = st.session_state.get(f"receipt_{nid}")
    if receipt:
        st.success(
            f"Payment confirmed · {receipt['method']} · ref {receipt['txn_ref']}. "
            f"Challan #{nid} cleared. ✅"
        )
        notif_bits = []
        if receipt["sent"].get("email"):
            notif_bits.append("email")
        if receipt["sent"].get("sms"):
            notif_bits.append("SMS")
        if notif_bits:
            st.caption(f"Confirmation sent via {' and '.join(notif_bits)}.")
        st.session_state.pop(f"receipt_{nid}", None)
        st.session_state.pop(f"checkout_{nid}", None)
        return

    st.markdown("<div class='panel-card' style='border-color:rgba(242,169,59,.5)'>", unsafe_allow_html=True)
    st.markdown(f"<div class='section-title'>Secure Checkout · ₹{amount:,.0f}</div>", unsafe_allow_html=True)

    modes = ["UPI", "Credit / Debit Card"]
    if _razorpay_enabled():
        modes = [RAZORPAY_MODE_LABEL] + [f"{m} (demo)" for m in modes]
    pay_mode = st.radio("Pay using", modes, key=f"paymode_{nid}", horizontal=True)

    if pay_mode == RAZORPAY_MODE_LABEL:
        _render_razorpay_checkout(notice, amount, owner)
    elif pay_mode.startswith("UPI"):
        _render_upi_checkout(notice, amount, owner)
    else:
        _render_card_checkout(notice, amount, owner)

    if st.button("Cancel", key=f"cancel_{nid}"):
        st.session_state.pop(f"checkout_{nid}", None)
        st.session_state.pop(f"txnref_{nid}", None)
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)


def _render_upi_checkout(notice: dict, amount: float, owner: dict) -> None:
    from backend.payments import build_upi_uri, new_txn_ref, qr_png_bytes, gateway_simulate_verify, PAYEE_VPA
    nid = notice["notice_id"]
    ref_key = f"txnref_{nid}"
    if ref_key not in st.session_state:
        st.session_state[ref_key] = new_txn_ref(nid)
    txn_ref = st.session_state[ref_key]
    note = f"DriveShieldX challan #{nid}"
    upi_uri = build_upi_uri(amount, note, txn_ref)

    cc1, cc2 = st.columns([1, 1.3])
    with cc1:
        png = qr_png_bytes(upi_uri)
        if png:
            st.image(png, caption="Scan with any UPI app", width=210)
        else:
            st.code(upi_uri, language="text")
        st.caption(f"Payee: {PAYEE_VPA}")
    with cc2:
        st.markdown(
            f"<div class='small-muted'>Order reference</div><div style='font-family:JetBrains Mono;"
            f"color:var(--gold-hi);margin-bottom:.6rem'>{txn_ref}</div>",
            unsafe_allow_html=True,
        )
        st.caption("On a phone these open the app directly; on desktop, scan the QR instead.")
        # per-app deep links — all built from the same standards-compliant UPI URI, just
        # with the scheme each app's own intent filter listens for.
        app_schemes = {
            "Google Pay": upi_uri.replace("upi://", "tez://upi/", 1),
            "PhonePe": upi_uri.replace("upi://", "phonepe://", 1),
            "Paytm": upi_uri.replace("upi://", "paytmmp://", 1),
            "BHIM / other UPI app": upi_uri,
        }
        acols = st.columns(2)
        for i, (app_name, deep_link) in enumerate(app_schemes.items()):
            with acols[i % 2]:
                st.link_button(app_name, deep_link, use_container_width=True)
        st.caption("After paying, tap below to confirm settlement.")
        if st.button("I have paid — verify", key=f"verify_{nid}", use_container_width=True):
            with st.spinner("Verifying payment with gateway…"):
                ok, gateway_id = gateway_simulate_verify(txn_ref)
            if ok:
                _settle_and_notify(nid, "UPI", f"{txn_ref}/{gateway_id}", notice, owner, amount)
                st.rerun()
            else:
                st.error("Payment not found yet. Complete the UPI payment, then verify again.")


def _render_card_checkout(notice: dict, amount: float, owner: dict) -> None:
    from backend.card_payments import validate_card, mask_card, new_txn_ref, gateway_simulate_charge
    nid = notice["notice_id"]
    ref_key = f"txnref_{nid}"
    if ref_key not in st.session_state:
        st.session_state[ref_key] = new_txn_ref(nid)
    txn_ref = st.session_state[ref_key]

    st.caption(
        "Demo checkout — no real card is charged. Any card number that passes a basic "
        "checksum works (e.g. 4111 1111 1111 1111, any future expiry, any CVV)."
    )
    with st.form(key=f"card_form_{nid}", border=False):
        name_on_card = st.text_input("Name on card", key=f"card_name_{nid}")
        card_number = st.text_input("Card number", key=f"card_num_{nid}", placeholder="4111 1111 1111 1111")
        cc1, cc2 = st.columns(2)
        expiry = cc1.text_input("Expiry (MM/YY)", key=f"card_exp_{nid}", placeholder="12/29")
        cvv = cc2.text_input("CVV", key=f"card_cvv_{nid}", placeholder="123", type="password")
        pay_clicked = st.form_submit_button(f"Pay ₹{amount:,.0f}", use_container_width=True)

    if pay_clicked:
        ok, err = validate_card(card_number, expiry, cvv, name_on_card)
        if not ok:
            st.error(err)
            return
        masked = mask_card(card_number)
        with st.spinner(f"Charging {masked}…"):
            charged, gateway_id = gateway_simulate_charge(amount, masked, txn_ref)
        if charged:
            _settle_and_notify(nid, f"Card ({masked})", f"{txn_ref}/{gateway_id}", notice, owner, amount)
            st.rerun()
        else:
            st.error("Card declined. Please try another card.")


def page_owner_vehicles() -> None:
    hero("My Vehicles", "Your registered vehicles and their safety record.", eyebrow="Citizen · Garage")
    owner = st.session_state.owner
    vehicles = list_owner_vehicles(owner["owner_id"])
    notices = get_owner_notices(owner["owner_id"])

    # per-vehicle record summary
    if vehicles:
        cards = []
        for v in vehicles:
            plate = v.get("plate_number", "")
            vns = [n for n in notices if str(n.get("plate_number", "")).upper() == str(plate).upper()]
            unpaid = sum(1 for n in vns if n.get("payment_status") in ("pending", "overdue"))
            susp = any(n.get("action_taken") == "license_suspension" for n in vns)
            tone = "#ffb0b0" if susp else ("var(--gold-hi)" if unpaid else "#9fe0d4")
            status_txt = "Licence action" if susp else (f"{unpaid} unpaid" if unpaid else "Clean record")
            cards.append(
                f"<div class='kpi'><div class='label'>{v.get('vehicle_type','vehicle')} · {v.get('model_name') or '—'}</div>"
                f"<div class='value' style='font-size:1.3rem'>{plate}</div>"
                f"<div class='meta' style='color:{tone};margin-top:.4rem;font-weight:700'>{status_txt}</div>"
                f"<div class='small-muted'>{len(vns)} challan(s) on record</div></div>"
            )
        st.markdown(f"<div class='kpi-row'>{''.join(cards)}</div>", unsafe_allow_html=True)
    else:
        st.info("No vehicles registered yet. Add your first vehicle below.")

    st.markdown("<div class='panel-card'>", unsafe_allow_html=True)
    st.markdown("<div class='section-title'>Register a vehicle</div>", unsafe_allow_html=True)
    with st.form("owner_add_vehicle"):
        plate = st.text_input("Plate number", placeholder="e.g. MH01AB1234")
        vtype = st.selectbox("Vehicle type", ["car", "bike", "bus", "truck"])
        model_name = st.text_input("Model (optional)")
        add_btn = st.form_submit_button("Add vehicle", use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)
    if add_btn:
        if not plate.strip():
            st.error("Plate number is required.")
        elif add_registered_vehicle(owner["owner_id"], plate.strip(), vtype, model_name):
            st.success("Vehicle added."); st.rerun()
        else:
            st.error("That plate is already registered.")


def page_owner_security() -> None:
    hero("Owner Security", "Manage 2FA for your vehicle-owner account.", eyebrow="Account · Protection")
    owner = st.session_state.owner

    def _save(secret: str) -> None:
        set_owner_totp_secret(owner["owner_id"], secret, True)
        st.session_state.owner["totp_secret"] = secret
        st.session_state.owner["is_2fa_enabled"] = 1

    st.markdown("<div class='panel-card'>", unsafe_allow_html=True)
    _totp_panel(save_fn=_save, enabled=bool(owner.get("is_2fa_enabled")),
                secret=owner.get("totp_secret"), state_key="owner_security_secret")
    st.markdown("</div>", unsafe_allow_html=True)


# ---- ROUTER ----
def app_navbar(portal_name: str, who: str, current: str) -> None:
    """Compact top navbar shown inside the logged-in dashboards for a consistent
    real-website feel. The left shows the brand+portal, the right the signed-in user."""
    st.markdown(
        f"<div class='navbar'>"
        f"<div class='brand'><span class='dot'>DS</span>DriveShield<b>X</b>"
        f"<span class='tag' style='margin-left:.7rem'>{portal_name}</span></div>"
        f"<div class='links'>"
        f"<span style='color:var(--muted);font-size:.9rem'>Section · <b style='color:var(--gold-hi)'>{current}</b></span>"
        f"<span class='tag'>👤 {who}</span>"
        f"</div></div>",
        unsafe_allow_html=True,
    )



# ---- DriveShieldX rule violations (helmet / triple-riding / seat-belt) ----
def page_rule_violations() -> None:
    """Layer-2 violation dashboard: helmet / triple-riding / seat-belt records
    produced by the DriveShieldX YOLO detectors on top of the existing tracker.
    """
    from database.db_manager import (
        get_rule_violation_summary,
        get_rule_violations,
    )
    from detection.rule_violations import RuleViolationDetector, upi_intent, upi_qr_png

    hero("Rule Violations",
         "Helmet · Triple-riding · Seat-belt · Number-plate — layer-2 detectors fused with the existing centroid/ByteTrack pipeline.",
         live=st.session_state.live_refresh)

    summary = get_rule_violation_summary()
    total = sum(summary.values())
    c1, c2, c3, c4 = st.columns(4)
    with c1: st.metric("Total confirmed", total)
    with c2: st.metric("No helmet", summary.get("no_helmet", 0))
    with c3: st.metric("Triple riding", summary.get("three_seater", 0))
    with c4: st.metric("No seat belt", summary.get("no_seatbelt", 0))

    # model status card
    loaded = RuleViolationDetector().loaded()
    st.markdown("<div class='panel'><b>Model status</b><br>"
                + " · ".join(f"{k}: {'✅' if v else '❌'}" for k, v in loaded.items())
                + "</div>", unsafe_allow_html=True)

    # ---- Officer review queue (disagreement gate, paper Sec. 4.4) ----
    from database.db_manager import get_gate_reviews, update_gate_review
    st.subheader("Officer review queue (disagreement gate)")
    st.caption("Helmet events where the standalone helmet model and the combined two-wheeler model disagreed. "
               "No challan is issued for these unless an officer confirms them.")
    reviews = get_gate_reviews(limit=100)
    pending = [r for r in reviews if r["status"] == "pending"]
    g1, g2, g3 = st.columns(3)
    with g1: st.metric("Pending review", len(pending))
    with g2: st.metric("Confirmed by officer", sum(r["status"] == "confirmed" for r in reviews))
    with g3: st.metric("Rejected by officer", sum(r["status"] == "rejected" for r in reviews))
    if pending:
        import pandas as pd
        st.dataframe(pd.DataFrame(pending)[["review_id", "created_at", "rule_type", "tracker_id",
                                            "p_standalone", "p_combined", "theta"]],
                     use_container_width=True, hide_index=True)
        rid = st.selectbox("Review item", [r["review_id"] for r in pending], key="gate_pick")
        item = next(r for r in pending if r["review_id"] == rid)
        if item.get("snapshot_path"):
            try: st.image(item["snapshot_path"], caption="Evidence (withheld)", width=320)
            except Exception: pass
        b1, b2 = st.columns(2)
        with b1:
            if st.button("Confirm violation", key=f"gate_ok_{rid}"):
                update_gate_review(rid, "confirmed"); st.rerun()
        with b2:
            if st.button("Reject (no violation)", key=f"gate_no_{rid}"):
                update_gate_review(rid, "rejected"); st.rerun()

    st.subheader("Latest confirmed records")
    rows = get_rule_violations(limit=100)
    if not rows:
        st.info("No rule violations yet. Upload a real video in **Live Monitor** — helmet, triple-riding and seat-belt checks run on every tracked vehicle.")
        return

    import pandas as pd
    df = pd.DataFrame(rows)[[
        "rule_violation_id", "detected_at", "rule_type", "tracker_id",
        "plate_text", "fine_amount", "status",
    ]].rename(columns={
        "rule_violation_id": "ID", "detected_at": "When",
        "rule_type": "Rule", "tracker_id": "Track", "plate_text": "Plate",
        "fine_amount": "Fine (₹)", "status": "Status",
    })
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.subheader("Issue e-challan (UPI)")
    ids = [r["rule_violation_id"] for r in rows]
    picked = st.selectbox("Choose a record", ids, key="rv_pick",
                          format_func=lambda i: f"#{i} · {next(r for r in rows if r['rule_violation_id']==i)['rule_type']}")
    rec = next(r for r in rows if r["rule_violation_id"] == picked)
    left, right = st.columns([1, 1])
    with left:
        st.write(f"**Rule:** {rec['rule_type']}")
        st.write(f"**Track ID:** {rec['tracker_id']}")
        st.write(f"**Plate:** {rec.get('plate_text') or '—'}")
        st.write(f"**Detected at:** {rec['detected_at']}")
        st.write(f"**Fine amount:** ₹{int(rec['fine_amount'])}")
        if rec.get("snapshot_path"):
            try: st.image(rec["snapshot_path"], caption="Evidence", width=320)
            except Exception: pass
    with right:
        intent = upi_intent(
            vpa=os.environ.get("UPI_ID", ""),
            payee=os.environ.get("UPI_PAYEE_NAME", "DriveShieldX Traffic Authority"),
            amount=int(rec["fine_amount"]),
            tid=str(rec["rule_violation_id"]),
            note=f"{rec['rule_type']} · {rec.get('plate_text') or rec['tracker_id']}",
        )
        st.image(upi_qr_png(intent), caption="Scan with any UPI app", width=240)
        st.caption(f"UPI ID: {os.environ.get('UPI_ID', '') or 'set UPI_ID in .env'}")
        st.caption(f"Contact: {os.environ.get('CONTACT_PHONE', '') or '-'}")
        st.code(intent, language="text")

    # ---- issue a real e-challan (fine + licence action + SMS + online payment) ----
    st.subheader("Issue e-challan for this record")
    st.caption("Statutory fine ₹1000 (MV Act s.194D helmet, s.194C triple riding, s.194B seat belt); helmet and "
               "triple riding also disqualify the licence for 3 months. Repeat offences escalate further.")
    plate_in = st.text_input("Number plate", value=rec.get("plate_text") or "", key=f"rv_plate_{picked}")
    if st.button("Issue e-challan", key=f"rv_issue_{picked}", disabled=not plate_in.strip()):
        try:
            from backend import enforcement
            nid = enforcement.issue_rule_challan(int(picked), int(st.session_state.authority["user_id"]), plate_in)
            st.success(f"e-Challan #{nid} issued. The owner is notified by SMS if the plate is registered.")
        except Exception as exc:
            st.error(str(exc))


def _hotlist_toasts() -> None:
    """Live 'wanted vehicle' pop-ups for officers when a hotlisted plate is read by a camera."""
    if st.session_state.get("authority") is None:
        return
    try:
        from backend import escalation
        seen = st.session_state.setdefault("_seen_sightings", set())
        for s in escalation.sightings(unacknowledged_only=True, limit=5):
            if s["sighting_id"] not in seen:
                seen.add(s["sighting_id"])
                st.toast(f"🚨 Hotlisted vehicle {s['plate_key']} seen at camera {s['camera_id']} "
                         f"({str(s['seen_at'])[:19]}). See Notice Desk → Unpaid challans.")
    except Exception:
        pass


def _safety_pages():
    from dashboard import safety_pages
    return safety_pages


def main() -> None:
    _handle_razorpay_return()
    _show_razorpay_return_banner()
    try:  # repeat-offender policy: schedule fine + licence suspension for every new challan
        from backend import enforcement
        enforcement.process_new()
    except Exception:
        pass
    try:  # one SMS per newly issued challan (backend/challan_sms.py); throttled, never blocks the UI
        from backend import challan_sms
        challan_sms.notify_pending()
    except Exception:
        pass
    try:  # background worker: auto-record payments + unpaid-challan escalation (reminders, hotlist, court)
        from backend import payment_worker
        payment_worker.start_background()
    except Exception:
        pass
    _hotlist_toasts()
    if st.session_state.pending_login is not None:
        render_mfa_gate()
        return
    if st.session_state.authority is None and st.session_state.owner is None:
        render_login_tabs()  # landing → portal → login/signup
        return
    if st.session_state.authority is not None:
        page = authority_sidebar()  # renders the real top navbar, returns active page
        st.markdown("<div class='page-anim'>", unsafe_allow_html=True)
        {
            "Command Center": page_command_center,
            "Live Monitor": page_live_monitor,
            "Notice Desk": page_notice_desk,
            "Rule Violations": page_rule_violations,
            "Road Safety": lambda: _safety_pages().page_road_safety(hero),
            "Analytics": lambda: _safety_pages().page_analytics(hero),
            "Reports": page_reports,
            "Configuration": page_configuration,
            "Users": page_users,
            "Security": page_security,
            "System Health": page_system_health,
        }.get(page, page_command_center)()
        st.markdown("</div>", unsafe_allow_html=True)
        return
    if st.session_state.owner is not None:
        page = owner_sidebar()
        st.markdown("<div class='page-anim'>", unsafe_allow_html=True)
        {
            "My Dashboard": page_owner_notices,
            "My Vehicles": page_owner_vehicles,
            "Security": page_owner_security,
        }.get(page, page_owner_notices)()
        st.markdown("</div>", unsafe_allow_html=True)


if __name__ == "__main__":
    main()