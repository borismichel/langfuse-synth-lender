"""Meridian Commercial Bank — analyst copilot (the in-scene playground front-end).

An analyst-facing copilot: pick a prepared case question (the first two are the exact
patterns production got wrong), pick the **model** — the incumbent or the candidate —
and get a real answer back, emitted as an agent-graph trace at *now*. A thumbs-down
logs ``analyst_feedback`` with the analyst's comment: the same signal that feeds
certification-suite intake.

The staff-facing route ``/dossier`` (``dashboard.py``) renders the in-scene model
validation dossier. Launch with ``synth playground``.
"""
from __future__ import annotations

import html
import json

from ..config import Config
from langfuse_synth_core.live.paths import local
from .prefabs import build_prefabs, prefabs_by_key
from .submit import submit, thumbs_down
from langfuse_synth_core.live.theme import page

TITLE = "Meridian Commercial Bank — Analyst Copilot"

_HEADER = ("<div class='eyebrow'>Meridian Commercial Bank · credit analysis</div>"
           "<h1>Filing <span class='mark'>copilot</span></h1>"
           "<p class='sub'>Ask a question about a borrower's filed statements. Answers are "
           "extracted strictly from the cited extracts — and every answer is a trace.</p>")


def _form(cfg: Config) -> str:
    prefabs = build_prefabs(cfg.generation.seed)
    opts = "".join(f'<option value="{p.key}">{html.escape(p.label)}</option>' for p in prefabs)
    notes = json.dumps({p.key: p.note for p in prefabs})
    inc, cand = cfg.certification.incumbent_model, cfg.certification.candidate_a_model
    return f"""
    <form method="post" action="{local('/ask')}">
      <label>Case question</label>
      <select name="prefab" id="prefab" onchange="note()">{opts}</select>
      <div class="note" id="prefab-note"></div>
      <label>Model</label>
      <select name="model">
        <option value="{inc}" selected>{inc} · incumbent (EOL announced)</option>
        <option value="{cand}">{cand} · candidate under certification</option>
      </select>
      <button type="submit">Ask the copilot →</button>
    </form>
    <script>const N={notes};
      function note(){{document.getElementById('prefab-note').textContent=N[document.getElementById('prefab').value]||'';}}
      note();</script>"""


def _error_card(headline: str, exc: Exception) -> str:
    tech = f"{type(exc).__name__}: {exc}"
    return f"""
    <div class="eyebrow">Meridian Commercial Bank · service notice</div>
    <div class="card">
      <h2>{headline}</h2>
      <p class="sub" style="margin:6px 0 14px">The copilot had a momentary problem and nothing
        was recorded. Please try again.</p>
      <div class="kv"><span>Technical detail</span><span>{html.escape(tech[:160])}</span></div>
    </div>
    <a class="back" href="{local('/')}">← try again</a>"""


def create_app(cfg: Config):
    from fastapi import FastAPI, Form
    from fastapi.responses import HTMLResponse

    app = FastAPI(title=TITLE)

    # the branded governance layer (spec designer / runs / coverage / promote / evidence)
    from ..workbench.views import build_router

    app.include_router(build_router(cfg), prefix="/workbench")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        staff = (f"<a class='back' href='{local('/dossier')}'>staff · model validation dossier →</a> &nbsp; "
                 f"<a class='back' href='{local('/workbench')}'>staff · validation workbench →</a>")
        return page(_HEADER + _form(cfg) + staff, title=TITLE)

    @app.get("/dossier", response_class=HTMLResponse)
    def dossier() -> str:
        from .dashboard import render_dossier
        try:
            return render_dossier(cfg)
        except Exception as exc:  # noqa: BLE001 — render in-scene, never a raw 500
            return page(_error_card("The dossier is temporarily unavailable", exc), title=TITLE)

    @app.post("/ask", response_class=HTMLResponse)
    def ask(prefab: str = Form(...), model: str = Form(None)) -> str:
        p = prefabs_by_key(cfg.generation.seed).get(prefab)
        if p is None:
            return page(_error_card("Unknown question", ValueError(prefab)), title=TITLE)
        try:
            res = submit(cfg, p.question, model)
        except Exception as exc:  # noqa: BLE001 — render in-scene, never a raw 500
            return page(_error_card("We couldn't process that question", exc), title=TITLE)
        a, e = res["answer"], res["expected"]
        figures = "".join(f"<div class='kv'><span>{html.escape(k)}</span><span>€{v:,}</span></div>"
                          for k, v in a.figures.items())
        ratios = "".join(f"<div class='kv'><span>{html.escape(k)}</span><span>{v}</span></div>"
                         for k, v in a.ratios.items())
        match = (a.figures == e.figures and a.answer_type == e.answer_type)
        chip = ("<span class='chip green'>matches ground truth</span>" if match
                else "<span class='chip red'>differs from ground truth</span>")
        body = f"""
        <div class="eyebrow">Meridian Commercial Bank · copilot answer</div>
        <div class="card active">
          <h2>{html.escape(a.answer_type.upper())} <span class="pill">{html.escape(res['model'])}</span></h2>
          <p class="sub" style="margin:6px 0 14px">{html.escape(a.answer)}</p>
          {figures}{ratios}
          <div class="kv"><span>Citations</span><span>{html.escape(', '.join(a.citations) or '—')}</span></div>
          <div class="kv"><span>Basis</span><span>{html.escape(a.basis[:140])}</span></div>
          <div class="kv"><span>Deterministic ground truth</span><span>{chip}</span></div>
          <div class="kv"><span>Trace</span><span><a href="{res['trace_url']}" target="_blank">view →</a></span></div>
        </div>
        <form method="post" action="{local('/flag')}" class="ghost card">
          <input type="hidden" name="trace_id" value="{res['trace_id']}">
          <label>Something wrong? Flag it for review</label>
          <textarea name="comment" rows="3" placeholder="e.g. the filing prints (2,431) — that's a loss, not a profit"></textarea>
          <button type="submit">Flag this answer</button>
        </form>
        <a class="back" href="{local('/')}">← new question</a>"""
        return page(body, title=TITLE)

    @app.post("/flag", response_class=HTMLResponse)
    def flag(trace_id: str = Form(...), comment: str = Form("")) -> str:
        try:
            res = thumbs_down(cfg, trace_id, comment)
        except Exception as exc:  # noqa: BLE001 — render in-scene, never a raw 500
            return page(_error_card("We couldn't log the flag", exc), title=TITLE)
        body = f"""
        <div class="eyebrow">Meridian Commercial Bank · flagged for review</div>
        <div class="card active">
          <h2>Flag recorded</h2>
          <p class="sub" style="margin:6px 0 14px">Your comment is on the trace as an
            <code>analyst_feedback</code> score — exactly the signal certification-suite
            intake filters by.</p>
          <div class="kv"><span>Your comment</span><span>{html.escape(res['comment'])}</span></div>
          <div class="kv"><span>Trace</span><span><a href="{res['trace_url']}" target="_blank">view →</a></span></div>
        </div>
        <a class="back" href="{local('/')}">← new question</a>"""
        return page(body, title=TITLE)

    return app
