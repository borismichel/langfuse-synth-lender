"""Meridian Commercial Bank — the in-scene validation dossier (``/dossier``).

The staff-facing rendering of CERT_MEMO.md: the three-run comparison on the one
certification suite (baseline / candidate A / candidate B-rejected), per-scenario
gates, the review-queue record, and the recommendation — filled from
``.synth_state.json``, so the page and the data in Langfuse always agree.
"""
from __future__ import annotations

import html

from ..config import Config
from ..state import RunState
from .theme import page

TITLE = "Meridian Commercial Bank — Model Validation"

_VERDICT = {
    "baseline": ("baseline · passes", "chip"),
    "pass": ("PASS · recommend", "chip green"),
    "fail": ("REJECTED", "chip red"),
    "live": ("live run", "chip green"),
}


def render_dossier(cfg: Config) -> str:
    if not RunState.exists():
        return page("<div class='eyebrow'>Meridian Commercial Bank · model risk</div>"
                    "<div class='card'><h2>No dossier yet</h2><p class='sub'>Run <code>synth seed"
                    "</code> first — the dossier is generated from the seeded certification "
                    "record.</p></div>", title=TITLE, wide=True)
    state = RunState.load()
    suite = state.suite
    runs = sorted((suite.get("runs") or {}).items(),
                  key=lambda kv: (kv[1].get("date", ""), kv[0]))
    gates = suite.get("gates") or {}

    kpis = []
    for run_name, r in runs:
        label, cls = _VERDICT.get(r.get("verdict", ""), (r.get("verdict", "?"), "chip"))
        num = (r.get("pass_rates") or {}).get("numeric_lookup", 1.0)
        tone = "good" if r.get("verdict") in ("baseline", "pass", "live") else "bad"
        kpis.append(
            f"<div class='kpi'><div class='klabel'>{html.escape(r.get('model', ''))}</div>"
            f"<div class='kvalue'>{num:.0%}</div>"
            f"<div class='kdelta {tone}'>numeric_lookup · <span class='{cls}'>{label}</span></div></div>")

    run_rows = ""
    for run_name, r in runs:
        label, cls = _VERDICT.get(r.get("verdict", ""), (r.get("verdict", "?"), "chip"))
        scen = " · ".join(f"{k} {v:.0%}" for k, v in (r.get("pass_rates") or {}).items())
        run_rows += (f"<div class='kv'><span><code>{html.escape(run_name)}</code>"
                     f" · {html.escape(r.get('model', ''))} · {html.escape(r.get('date', ''))}"
                     f"<div class='note'>{html.escape(scen)}</div></span>"
                     f"<span><span class='{cls}'>{label}</span></span></div>")

    gate_rows = "".join(
        f"<div class='kv'><span><code>{html.escape(s)}</code></span>"
        f"<span>≥ {g:.0%}</span></div>" for s, g in gates.items())

    golden_rows = "".join(
        f"<div class='kv'><span>{html.escape(g.get('title', ''))}</span>"
        f"<span><code>{html.escape((g.get('trace_id') or '')[:16])}…</code></span></div>"
        for g in state.golden)

    fb = state.flagged_pending[0] if state.flagged_pending else {}
    pending_html = ""
    if fb:
        pending_html = (
            f"<div class='kv'><span>{html.escape(fb.get('borrower', ''))} · "
            f"<code>{html.escape(fb.get('case_id', ''))}</code> (PENDING review)</span>"
            f"<span>reported €{fb.get('incumbent_figure_eur', 0):,} — filing shows "
            f"€{fb.get('correct_figure_eur', 0):,}</span></div>")

    body = f"""
    <div class="eyebrow">Meridian Commercial Bank · model risk management · internal</div>
    <h1>Analyst copilot — <span class="mark">certification dossier</span></h1>
    <p class="sub">Candidate <b>{state.candidate_a_model}</b> + <code>{state.prompt_name}</code>
      v{(state.prompt_versions or {}).get('production', '?')} · baseline {state.incumbent_model}
      · also evaluated: {state.candidate_b_model} (rejected) · prepared by second-line validation</p>

    <div class="memo">
      <b>Basis.</b> One certification suite ({suite.get('items', '?')} items, scenario-gated),
      curated from annotated production traces via {state.queue.get('name', 'the review queue')}
      ({state.queue.get('completed', '—')} completed, {state.queue.get('pending', '—')} pending).
      Deterministic assertions + managed judges ({state.judge_model}) + human review on one
      scores surface. Seeded runs: baseline {state.baseline_run_date}, candidates {state.candidate_run_date}.
    </div>

    <div class='grid'>{''.join(kpis)}</div>
    <div class='card'><h2>Runs on {html.escape(suite.get('name', ''))}</h2>{run_rows}</div>
    <div class='card'><h2>Per-scenario gates</h2>{gate_rows}</div>
    <div class='card'><h2>Golden traces (tag: golden)</h2>{golden_rows}{pending_html}</div>

    <div class='card active'><h2>Recommendation</h2>
      <p class='sub'>Certify <b>{state.candidate_a_model}</b> + {state.prompt_name}
      v{(state.prompt_versions or {}).get('production', '?')}. Candidate B rejected on the
      numeric-accuracy gate (evidence retained). Recertification triggers: model, prompt,
      parameter, or suite change.</p></div>

    <a class="back" href="/">← analyst copilot</a> &nbsp;
    <a class="back" href="/workbench">validation workbench →</a>"""
    return page(body, title=TITLE, wide=True)
