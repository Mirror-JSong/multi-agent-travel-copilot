"""Local, dependency-free visual system for the travel workbench."""

from __future__ import annotations

import streamlit as st


WORKBENCH_CSS = r"""
<style>
:root {
  --travel-ink: #162434;
  --travel-muted: #5c6f7f;
  --travel-teal: #0f766e;
  --travel-teal-soft: #e4f3f0;
  --travel-coral: #d85a43;
  --travel-sun: #e3ad37;
  --travel-sky: #2e7899;
  --travel-paper: #f7faf9;
  --travel-mist: #eaf1f2;
  --travel-line: #d5e0e2;
  --travel-danger: #a93832;
}

[data-testid="stAppViewContainer"] {
  color: var(--travel-ink);
  background-color: var(--travel-paper);
  background-image:
    linear-gradient(rgba(15, 118, 110, .035) 1px, transparent 1px),
    linear-gradient(90deg, rgba(15, 118, 110, .035) 1px, transparent 1px);
  background-size: 32px 32px;
}

[data-testid="stHeader"] { background: rgba(247, 250, 249, .88); }
[data-testid="stSidebar"] {
  border-right: 1px solid var(--travel-line);
  background: #f1f7f6;
}
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap: .7rem; }
.block-container { max-width: 1380px; padding-top: 2rem; padding-bottom: 4rem; }

h1, h2, h3, .travel-display {
  color: var(--travel-ink);
  font-family: "Aptos Display", "Microsoft YaHei UI", sans-serif;
  letter-spacing: -.035em;
}
h1 { font-size: clamp(2.15rem, 5vw, 4rem) !important; line-height: 1.04 !important; }
h2 { letter-spacing: -.025em; }
p, label, li, td, th, button, input, textarea {
  font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
}

button:focus-visible, input:focus-visible, textarea:focus-visible, [role="tab"]:focus-visible {
  outline: 3px solid rgba(46, 120, 153, .5) !important;
  outline-offset: 2px !important;
}

.travel-brand { display:flex; align-items:center; gap:.75rem; padding:.25rem 0 .8rem; }
.travel-brand svg { width:42px; height:42px; color:var(--travel-teal); }
.travel-brand strong { display:block; font-size:1.35rem; letter-spacing:-.04em; }
.travel-brand small { color:var(--travel-muted); letter-spacing:.12em; font-size:.64rem; }

.travel-mock-stamp {
  border:1px dashed rgba(15,118,110,.6); border-radius:12px; padding:.7rem .8rem;
  color:#0d625d; background:rgba(255,255,255,.56); font: .72rem/1.45 Consolas, monospace;
}
.travel-route-caption { color:var(--travel-muted); font-size:.72rem; margin:-.4rem 0 .35rem 2.15rem; }
.travel-route-state { color:var(--travel-teal); font: .7rem Consolas, monospace; text-transform:uppercase; }

.travel-topline { display:flex; align-items:center; justify-content:space-between; gap:1rem; margin-bottom:.9rem; }
.travel-crumb { color:var(--travel-muted); font-size:.82rem; letter-spacing:.02em; }
.travel-badges { display:flex; flex-wrap:wrap; gap:.45rem; justify-content:flex-end; }
.travel-badge, .travel-status-pill {
  display:inline-flex; align-items:center; gap:.4rem; border-radius:999px; padding:.35rem .65rem;
  border:1px solid var(--travel-line); background:#fff; color:var(--travel-ink); font-size:.72rem; font-weight:700;
}
.travel-badge.mock { color:#0d625d; border-color:#b9dcd7; background:var(--travel-teal-soft); }
.travel-badge::before, .travel-status-pill::before { content:""; width:.45rem; height:.45rem; border-radius:50%; background:currentColor; }

.travel-page-heading { margin:.3rem 0 1.55rem; }
.travel-eyebrow { color:var(--travel-teal); font:700 .75rem/1.2 Consolas, monospace; letter-spacing:.14em; text-transform:uppercase; }
.travel-page-heading h1 { margin:.35rem 0 .45rem; }
.travel-lede { color:var(--travel-muted); max-width:850px; font-size:1rem; line-height:1.75; }

.travel-card {
  border:1px solid var(--travel-line); border-radius:20px; padding:1.15rem 1.25rem;
  background:rgba(255,255,255,.94); box-shadow:0 16px 38px rgba(22,36,52,.06); margin-bottom:1rem;
}
.travel-card h3 { margin:.05rem 0 .35rem; }
.travel-card p:last-child { margin-bottom:0; }
.travel-kicker { color:var(--travel-muted); font-size:.76rem; }

.travel-dossier { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:.65rem 1rem; }
.travel-field { border-bottom:1px solid var(--travel-line); padding:.45rem 0 .6rem; min-width:0; }
.travel-field span { display:block; color:var(--travel-muted); font-size:.7rem; margin-bottom:.2rem; }
.travel-field strong { display:block; overflow-wrap:anywhere; }
.travel-source { color:var(--travel-teal); font: .62rem Consolas, monospace; margin-left:.25rem; }
.travel-source.unknown { color:#946617; }

.travel-conversation { display:flex; flex-direction:column; gap:.75rem; margin-bottom:1rem; }
.travel-message { max-width:88%; padding:.8rem 1rem; border-radius:18px 18px 18px 5px; background:#fff; border:1px solid var(--travel-line); }
.travel-message.user { align-self:flex-end; border-radius:18px 18px 5px 18px; color:#fff; background:var(--travel-ink); border-color:var(--travel-ink); }
.travel-message small { display:block; margin-top:.35rem; opacity:.68; }

.travel-date-strip { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); border:1px solid var(--travel-line); border-radius:16px; overflow:hidden; }
.travel-date-cell { padding:.9rem; background:#fff; border-right:1px solid var(--travel-line); }
.travel-date-cell:last-child { border-right:0; }
.travel-date-cell span { display:block; color:var(--travel-muted); font-size:.7rem; margin-bottom:.25rem; }

.travel-status-banner { border-radius:16px; padding:1rem 1.1rem; border:1px solid var(--travel-line); background:#fff; margin-bottom:1rem; }
.travel-status-banner.success { background:#edf8f5; border-color:#b9dcd7; }
.travel-status-banner.warning { background:#fff8e9; border-color:#ebcf91; }
.travel-status-banner.error { background:#fff0ee; border-color:#e3b0aa; }
.travel-status-banner.info { background:#eef6fa; border-color:#bad4e1; }
.travel-status-banner strong { display:block; margin-bottom:.25rem; }

.travel-loading { text-align:center; padding:2.5rem 1rem; }
.travel-orbit { width:92px; height:92px; border:2px dashed #9cbfc1; border-radius:50%; margin:0 auto 1rem; position:relative; }
.travel-orbit::before { content:""; position:absolute; inset:24px; border-radius:50%; background:var(--travel-teal); }
.travel-orbit::after { content:""; position:absolute; width:14px; height:14px; border-radius:50%; background:var(--travel-coral); top:-4px; left:58px; box-shadow:0 0 0 6px rgba(216,90,67,.14); }
.travel-indeterminate { width:min(460px,90%); height:5px; margin:1rem auto; background:var(--travel-mist); border-radius:999px; overflow:hidden; }
.travel-indeterminate::after { content:""; display:block; width:42%; height:100%; background:var(--travel-teal); animation:travel-load 1.5s ease-in-out infinite alternate; }
@keyframes travel-load { from { transform:translateX(-15%); } to { transform:translateX(150%); } }

.travel-plan-header { display:flex; align-items:flex-start; justify-content:space-between; gap:1rem; }
.travel-money { font-size:1.55rem; font-weight:800; letter-spacing:-.03em; }
.travel-metric-row { display:grid; grid-template-columns:120px 1fr 48px; align-items:center; gap:.65rem; margin:.65rem 0; font-size:.78rem; }
.travel-meter { height:6px; border-radius:999px; background:var(--travel-mist); overflow:hidden; }
.travel-meter > span { display:block; height:100%; border-radius:inherit; background:var(--travel-teal); }

.travel-day { border-left:2px solid var(--travel-line); margin-left:.5rem; padding:0 0 1.2rem 1.35rem; position:relative; }
.travel-day::before { content:""; position:absolute; width:12px; height:12px; border:3px solid var(--travel-teal); background:#fff; border-radius:50%; left:-8px; top:.2rem; }
.travel-slot { display:grid; grid-template-columns:76px 1fr auto; gap:.85rem; padding:.8rem 0; border-top:1px solid var(--travel-mist); align-items:start; }
.travel-slot time { color:var(--travel-teal); font:700 .72rem Consolas, monospace; }
.travel-slot small { display:block; color:var(--travel-muted); margin-top:.25rem; }
.travel-price { font:700 .74rem Consolas, monospace; white-space:nowrap; }

.travel-no-score { color:var(--travel-muted); font-style:italic; }
.travel-footer-note { color:var(--travel-muted); font-size:.74rem; line-height:1.6; }

@media (max-width: 980px) {
  [data-testid="stSidebar"] { min-width:220px; }
  .travel-date-strip { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .travel-date-cell:nth-child(2) { border-right:0; }
  .travel-date-cell:nth-child(-n+2) { border-bottom:1px solid var(--travel-line); }
}

@media (max-width: 700px) {
  .block-container { padding:1.25rem .85rem 3rem; }
  h1 { font-size:2.15rem !important; }
  .travel-topline { align-items:flex-start; }
  .travel-badges { justify-content:flex-start; }
  .travel-dossier { grid-template-columns:1fr; }
  .travel-date-strip { grid-template-columns:1fr; }
  .travel-date-cell { border-right:0; border-bottom:1px solid var(--travel-line); }
  .travel-date-cell:last-child { border-bottom:0; }
  .travel-slot { grid-template-columns:60px 1fr; }
  .travel-price { grid-column:2; }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration:.01ms !important; animation-iteration-count:1 !important; scroll-behavior:auto !important; }
}
</style>
"""


def apply_workbench_theme() -> None:
    st.markdown(WORKBENCH_CSS, unsafe_allow_html=True)
