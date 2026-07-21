"""Validation Workbench — server-rendered pages on the shared theme tokens.

Routes (mounted under /workbench by live/app.py):
  GET  /                overview: catalog, recent runs, coverage snapshot, role switcher
  GET  /designer        compose an experiment spec
  POST /specs           save spec        POST /evaluators   inject evaluator code
  POST /judges          ensure managed judge + experiment rule (unstable API)
  GET  /specs[/ref]     spec list / detail (canonical JSON + hash)
  POST /runs            trigger          GET /runs[/id]     progress + filterable results
  GET  /compare         side-by-side     GET /coverage      requirements matrix
  GET/POST /promote     queue→suite wizard
  GET  /evidence/{id}   evidence pack    POST /signoff      approver sign-off
"""
from __future__ import annotations

import html
import json
import time

# Module-level so postponed annotations (PEP 563) resolve for FastAPI's signature
# inspection. fastapi is the `playground` extra; this module is only imported by
# live/app.create_app, which requires it anyway.
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from ..config import Config
from ..live.paths import local
from ..live.theme import page
from . import runner as runner_mod
from .catalog import Catalog, fetch_catalog
from .links import Links
from .registry import EVALUATOR_TEMPLATE, discover_evaluators, save_evaluator
from .requirements import coverage_matrix
from .results import aggregates, compare, filter_rows, list_runs, load_run
from .signoff import ROLE_COOKIE, ROLES, can_sign, evidence_markdown, sign_off
from .specs import ExperimentSpec, Gates, Release, Target, list_specs, load_spec, save_spec

_CATALOG_CACHE: dict = {}
_CATALOG_TTL = 60.0


def _catalog(cfg: Config, *, force: bool = False) -> Catalog:
    now = time.monotonic()
    if not force and _CATALOG_CACHE.get("at", 0) > now - _CATALOG_TTL:
        return _CATALOG_CACHE["cat"]
    cat = fetch_catalog(cfg)
    _CATALOG_CACHE.update(at=now, cat=cat)
    return cat


def _e(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _lf(url: str, label: str = "open in Langfuse →") -> str:
    """An outbound deep link into the Langfuse UI — or nothing when the project id is
    unknown (never a broken link)."""
    if not url:
        return ""
    return (f"<a href='{_e(url)}' target='_blank' style='font-family:var(--font-mono);"
            f"font-size:11px;white-space:nowrap'>{_e(label)}</a>")


def _eyebrow(cfg: Config, section: str) -> str:
    return (f"<div class='eyebrow'>{_e(cfg.workbench.brand)} · validation workbench · "
            f"{_e(section)}</div>")


def _nav(active: str = "") -> str:
    links = [("overview", "/workbench"), ("designer", "/workbench/designer"),
             ("specs", "/workbench/specs"), ("runs", "/workbench/runs"),
             ("coverage", "/workbench/coverage"), ("promote", "/workbench/promote")]
    parts = []
    for label, href in links:
        style = "color:var(--text-primary);font-weight:600" if label == active else ""
        parts.append(f"<a href='{local(href)}' style='{style}'>{label}</a>")
    parts.append(f"<a href='{local('/')}'>copilot</a><a href='{local('/dossier')}'>dossier</a>")
    return ("<div style='display:flex;gap:16px;font-family:var(--font-mono);font-size:12px;"
            "margin:0 0 22px'>" + "".join(parts) + "</div>")


def _chip(text: str, tone: str = "") -> str:
    return f"<span class='chip {tone}'>{_e(text)}</span>"


def _offline_banner(cat: Catalog) -> str:
    if cat.online:
        return ""
    return ("<div class='memo'><b>Offline catalog.</b> Langfuse is unreachable — showing the "
            "deterministic plan's view of the suites. Designing works; running needs the "
            f"instance. <span class='chip red'>{_e(cat.error[:120])}</span></div>")


def _role_of(request) -> str:
    role = request.cookies.get(ROLE_COOKIE, "")
    return role if role in ROLES else "builder"


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
def build_router(cfg: Config):
    r = APIRouter()
    brand = cfg.workbench.brand

    # -- overview -----------------------------------------------------------
    @r.get("/", response_class=HTMLResponse)
    def overview(request: Request) -> str:
        cat = _catalog(cfg)
        lf = Links.from_cfg(cfg)
        role = _role_of(request)
        runs = list_runs(cfg)[:6]
        cov = coverage_matrix(cfg, cat)
        uncovered = [c for c in cov if not c.covered]

        ds_rows = "".join(
            f"<div class='kv'><span><code>{_e(d.name)}</code></span>"
            f"<span>{d.n_items} items · {len(d.slices)} slices {_lf(lf.dataset(d.id))}</span></div>"
            for d in cat.datasets)
        lf_bar = ""
        if lf.pid:
            lf_bar = ("<div class='note' style='margin:-14px 0 18px'>System of record: "
                      + " · ".join(filter(None, [
                          _lf(lf.datasets(), "datasets"),
                          _lf(lf.prompt(cfg.certification.prompt_name), "prompt"),
                          _lf(lf.queues(), "annotation queues"),
                          _lf(lf.evals(), "judge deployments"),
                          _lf(lf.project(), "project")])) + "</div>")
        ev_rows = "".join(
            f"<div class='kv'><span><code>{_e(e.name)}</code>"
            + (" <span class='chip red'>broken</span>" if e.error else "")
            + f"</span><span style='font-family:var(--font-mono);font-size:11px'>sha {e.sha256[:12]}…</span></div>"
            for e in discover_evaluators())
        judge_note = ("managed-judge API available" if cat.judges_api
                      else "managed judges via UI (older server)")
        run_rows = "".join(
            f"<div class='kv'><span><a href='{local('/workbench/runs/' + _e(x.run_id))}'>"
            f"<code>{_e(x.run_id)}</code></a> · {_e(x.spec_ref)}</span>"
            f"<span>{_chip(x.state, 'green' if x.ok else ('red' if x.state in ('done', 'error') else ''))}"
            f"{_chip('signed', 'green') if x.signoff else ''}</span></div>"
            for x in runs) or "<div class='kv'><span>no runs yet</span><span>—</span></div>"

        role_opts = "".join(f"<option value='{x}' {'selected' if x == role else ''}>{x}</option>"
                            for x in ROLES)
        body = f"""{_eyebrow(cfg, 'overview')}{_nav('overview')}
        <h1>Validation <span class='mark'>Workbench</span></h1>
        <p class='sub'>Design certification &amp; validation experiments on Langfuse: pull prompts,
        suites and evaluators; compose an auditable spec; run; filter the evidence.</p>
        {lf_bar}{_offline_banner(cat)}
        <div class='grid'>
          <div class='kpi'><div class='klabel'>Suites</div><div class='kvalue'>{len(cat.datasets)}</div>
            <div class='kdelta flat'>{sum(d.n_items for d in cat.datasets)} items</div></div>
          <div class='kpi'><div class='klabel'>Deterministic evaluators</div>
            <div class='kvalue'>{len([e for e in discover_evaluators() if not e.error])}</div>
            <div class='kdelta flat'>{judge_note}</div></div>
          <div class='kpi'><div class='klabel'>Requirement coverage</div>
            <div class='kvalue'>{len(cov) - len(uncovered)}/{len(cov)}</div>
            <div class='kdelta {"bad" if uncovered else "good"}'>
              {f"{len(uncovered)} uncovered → coverage" if uncovered else "all covered"}</div></div>
        </div>
        <div class='card'><div class='klabel' style='font-family:var(--font-mono);font-size:10.5px;
          text-transform:uppercase;letter-spacing:.1em;color:var(--text-tertiary)'>Suites (from Langfuse)</div>{ds_rows}</div>
        <div class='card'><div class='klabel' style='font-family:var(--font-mono);font-size:10.5px;
          text-transform:uppercase;letter-spacing:.1em;color:var(--text-tertiary)'>Evaluator code registry</div>{ev_rows}</div>
        <div class='card'><div class='klabel' style='font-family:var(--font-mono);font-size:10.5px;
          text-transform:uppercase;letter-spacing:.1em;color:var(--text-tertiary)'>Recent runs</div>{run_rows}</div>
        <form method='post' action='{local('/workbench/role')}' class='ghost card'>
          <label>Acting role (demo switcher — Builder designs, Approver signs)</label>
          <select name='role'>{role_opts}</select>
          <button type='submit'>Switch role</button>
        </form>"""
        return page(body, title=f"{brand} — Validation Workbench", wide=True)

    @r.post("/role")
    def set_role(role: str = Form("builder")):
        resp = RedirectResponse(local("/workbench"), status_code=303)
        resp.set_cookie(ROLE_COOKIE, role if role in ROLES else "builder")
        return resp

    # -- designer -----------------------------------------------------------
    @r.get("/designer", response_class=HTMLResponse)
    def designer(request: Request, err: str = "", ok: str = "") -> str:
        cat = _catalog(cfg)
        lf = Links.from_cfg(cfg)
        cert = cfg.certification

        prompt_opts = []
        for p in cat.prompts:
            for v in (p.get("versions") or [{"version": 1, "labels": ["production"]}]):
                ver = v.get("version") if isinstance(v, dict) else v
                labels = ",".join((v.get("labels") or [])) if isinstance(v, dict) else ""
                prompt_opts.append(
                    f"<option value='{_e(p['name'])}::{ver}'>"
                    f"{_e(p['name'])} v{ver}{(' · ' + _e(labels)) if labels else ''}</option>")

        target_blocks = []
        for d in cat.datasets:
            slice_boxes = "".join(
                f"<label style='display:inline-flex;gap:6px;margin:2px 12px 2px 0;font-family:var(--font-sans);"
                f"font-size:13.5px;text-transform:none;letter-spacing:0'>"
                f"<input type='checkbox' name='slices__{_e(d.name)}' value='{_e(sl)}' style='width:auto'>"
                f"{_e(sl)} ({n})</label>"
                for sl, n in sorted(d.slices.items()))
            target_blocks.append(
                f"<div class='kv' style='display:block'>"
                f"<label style='display:inline-flex;gap:8px;text-transform:none;font-size:14px'>"
                f"<input type='checkbox' name='datasets' value='{_e(d.name)}' checked style='width:auto'>"
                f"<b>{_e(d.name)}</b> · {d.n_items} items</label> {_lf(lf.dataset(d.id))}"
                f"<div style='margin:6px 0 4px 24px'>{slice_boxes or '—'}"
                f"<div class='note'>no slices ticked = all items</div></div></div>")

        eval_boxes = "".join(
            f"<label style='display:inline-flex;gap:6px;margin:2px 14px 2px 0;text-transform:none;"
            f"font-size:13.5px;letter-spacing:0'>"
            f"<input type='checkbox' name='evaluators' value='{_e(e.name)}' checked style='width:auto'>"
            f"<code>{_e(e.name)}</code></label>"
            for e in discover_evaluators() if not e.error)

        judge_section = _judges_panel(cfg, cat)
        models = sorted({cert.candidate_a_model, cert.candidate_b_model, cert.incumbent_model})
        model_datalist = "".join(f"<option value='{_e(m)}'>" for m in models)

        notice = ""
        if err:
            notice = f"<div class='memo'><span class='chip red'>error</span> {_e(err)}</div>"
        elif ok:
            notice = f"<div class='memo'><span class='chip green'>ok</span> {_e(ok)}</div>"

        body = f"""{_eyebrow(cfg, 'experiment designer')}{_nav('designer')}
        <h1>Design a <span class='mark'>certification experiment</span></h1>
        <p class='sub'>The spec is the auditable unit: release × suites × evaluators × gates.
        Saved specs are versioned and hashed; the hash rides in every run's metadata.</p>
        {_offline_banner(cat)}{notice}
        <form method='post' action='{local('/workbench/specs')}'>
          <div class='card'>
            <h2>1 · Release under test</h2>
            <label>Experiment name</label>
            <input name='name' value='cert-{_e(cert.candidate_a_model)}' required>
            <label>Model (the lever)</label>
            <input name='model' list='models' value='{_e(cert.candidate_a_model)}' required>
            <datalist id='models'>{model_datalist}</datalist>
            <label>Prompt (pinned by version — from Langfuse prompt management)
              {_lf(lf.prompt(cert.prompt_name))}</label>
            <select name='prompt'>{''.join(prompt_opts)}</select>
            <label>Temperature</label>
            <input name='temperature' type='number' value='0' step='0.1' min='0' max='1'>
          </div>
          <div class='card'>
            <h2>2 · Targets — suites &amp; slices (from Langfuse datasets)</h2>
            {''.join(target_blocks)}
            <label style='display:inline-flex;gap:8px;margin-top:10px;text-transform:none;font-size:14px'>
              <input type='checkbox' name='freeze' value='1' style='width:auto'>
              Freeze dataset version at run time (pin <code>datasetVersion</code> for reproducibility)</label>
          </div>
          <div class='card'>
            <h2>3 · Evaluators</h2>
            <label>Deterministic checks (version-controlled code; SHAs recorded per run)</label>
            <div>{eval_boxes}</div>
            {judge_section}
          </div>
          <div class='card'>
            <h2>4 · Gates (acceptance criteria as governed data)</h2>
            <label>Pass-rate threshold per suite</label>
            <input name='threshold' type='number' value='0.98' step='0.01' min='0' max='1'>
            <label>out_of_scope scenario must reach</label>
            <input name='flagged_threshold' type='number' value='1.0' step='0.01' min='0' max='1'>
            <label>Notes (rationale for the thresholds — lands in the spec)</label>
            <textarea name='notes' rows='2' placeholder='e.g. tightened after the Q2 flagged cases'></textarea>
          </div>
          <button type='submit'>Save spec →</button>
        </form>
        <div class='card ghost'>
          <h2>New deterministic evaluator (inject code)</h2>
          <p class='sub' style='margin-bottom:8px'>Scaffolded to the Langfuse v4 evaluator contract.
          Acceptance: compile → signature check → smoke-run on a sample item. Accepted code is
          committed to <code>workbench_evaluators/</code> and SHA-fingerprinted.</p>
          <form method='post' action='{local('/workbench/evaluators')}'>
            <label>Filename</label>
            <input name='filename' placeholder='my_check.py' required>
            <label>Code</label>
            <textarea name='code' rows='14' style='font-family:var(--font-mono);font-size:12.5px'>{_e(EVALUATOR_TEMPLATE)}</textarea>
            <button type='submit'>Validate &amp; add evaluator</button>
          </form>
        </div>"""
        return page(body, title=f"{brand} — Designer", wide=True)

    def _judges_panel(cfg: Config, cat: Catalog) -> str:
        from .judges import JUDGE_TEMPLATES

        lf = Links.from_cfg(cfg)
        deploy_link = _lf(lf.evals(), "judge deployments in Langfuse →")
        if not cat.judges_api:
            return ("<label>Managed LLM judges</label><div class='note'>The unstable evaluator "
                    "API isn't available on this server — create the judges once in the UI "
                    "(prompts + scopes in DEMO_SCRIPT.md beat 4). They score new runs automatically.</div>")
        existing = {j.get("name") for j in cat.judges}
        rows = []
        for name in JUDGE_TEMPLATES:
            status = (_chip("configured", "green") if name in existing
                      else f"<button type='submit' name='judge' value='{_e(name)}' "
                           f"formaction='{local('/workbench/judges')}' formmethod='post'>create + scope to suites</button>")
            rows.append(f"<div class='kv'><span><code>{_e(name)}</code> (managed LLM judge)</span>"
                        f"<span>{status}</span></div>")
        return ("<label style='margin-top:18px'>Managed LLM judges (created via API, scoped to "
                "the suites' experiment runs — fire automatically on every new run) "
                + deploy_link + "</label>" + "".join(rows))

    @r.post("/evaluators")
    def add_evaluator(filename: str = Form(...), code: str = Form(...)):
        reg, errors = save_evaluator(cfg, filename, code)
        if errors:
            return RedirectResponse(local(f"/workbench/designer?err={'; '.join(errors)[:300]}"),
                                    status_code=303)
        return RedirectResponse(
            local(f"/workbench/designer?ok=evaluator {reg.name} added (sha {reg.sha256[:12]}…)"),
            status_code=303)

    @r.post("/judges")
    async def add_judge(request: Request):
        from .judges import ensure_judge, ensure_rule

        form = await request.form()
        name = form.get("judge", "")
        cat = _catalog(cfg, force=True)
        judge, err = ensure_judge(cfg, name)
        if err:
            return RedirectResponse(local(f"/workbench/designer?err={err[:300]}"), status_code=303)
        ds_ids = [d.id for d in cat.datasets if d.id]
        _rule, rerr = ensure_rule(cfg, judge, ds_ids)
        _catalog(cfg, force=True)
        msg = f"judge {name} configured" + (f"; rule: {rerr[:200]}" if rerr else " + scoped to suites")
        return RedirectResponse(local(f"/workbench/designer?ok={msg}"), status_code=303)

    # -- specs ----------------------------------------------------------------
    @r.post("/specs")
    async def create_spec(request: Request):
        form = await request.form()
        cat = _catalog(cfg)
        prompt_name, _, prompt_ver = (form.get("prompt") or "analyst-copilot::7").partition("::")
        targets = []
        for ds in form.getlist("datasets"):
            targets.append(Target(dataset_name=ds, slices=form.getlist(f"slices__{ds}")))
        if not targets:
            return RedirectResponse(local("/workbench/designer?err=pick at least one suite"),
                                    status_code=303)
        gates = Gates(threshold=float(form.get("threshold") or 0.98),
                      slice_overrides={"out_of_scope":
                                       float(form.get("flagged_threshold") or 1.0)})
        spec = ExperimentSpec(
            name=form.get("name") or "experiment",
            release=Release(model=form.get("model") or cfg.certification.candidate_a_model,
                            prompt_name=prompt_name or "analyst-copilot",
                            prompt_version=int(prompt_ver) if prompt_ver.isdigit() else None,
                            temperature=float(form.get("temperature") or 0)),
            targets=targets,
            evaluators=form.getlist("evaluators"),
            judges=[j.get("name") for j in cat.judges] if cat.judges_api else [],
            gates=gates,
            freeze_dataset_version=("(at-run-time)" if form.get("freeze") else None),
            created_by=_role_of(request),
            notes=form.get("notes") or "")
        spec = save_spec(cfg, spec)
        return RedirectResponse(local(f"/workbench/specs/{spec.ref}"), status_code=303)

    @r.get("/specs", response_class=HTMLResponse)
    def specs_list() -> str:
        rows = "".join(
            f"<div class='kv'><span><a href='{local('/workbench/specs/' + _e(s.ref))}'><code>{_e(s.ref)}</code></a>"
            f" · {_e(s.release.model)}</span>"
            f"<span style='font-family:var(--font-mono);font-size:11px'>hash {s.spec_hash[:12]}…</span></div>"
            for s in list_specs(cfg)) or "<div class='kv'><span>no specs yet</span><span>—</span></div>"
        body = f"""{_eyebrow(cfg, 'experiment specs')}{_nav('specs')}
        <h1>Experiment <span class='mark'>specs</span></h1>
        <p class='sub'>Append-only and hashed — the audit trail keeps every shape the gate ever had.</p>
        <div class='card'>{rows}</div>"""
        return page(body, title=f"{brand} — Specs", wide=True)

    @r.get("/specs/{ref}", response_class=HTMLResponse)
    def spec_detail(ref: str, err: str = "") -> str:
        spec = load_spec(cfg, ref)
        if spec is None:
            return page(f"{_eyebrow(cfg, 'specs')}{_nav('specs')}<h2>Unknown spec</h2>",
                        title=brand, wide=True)
        notice = f"<div class='memo'><span class='chip red'>error</span> {_e(err)}</div>" if err else ""
        body = f"""{_eyebrow(cfg, 'experiment spec')}{_nav('specs')}
        <h1><span class='mark'>{_e(spec.ref)}</span></h1>
        <p class='sub'>sha256 <code>{spec.spec_hash}</code></p>{notice}
        <form method='post' action='{local('/workbench/runs')}'>
          <input type='hidden' name='spec_ref' value='{_e(spec.ref)}'>
          <button type='submit'>Run this experiment →</button>
        </form>
        <div class='card'><pre style='font-family:var(--font-mono);font-size:12px;overflow:auto'>{_e(json.dumps(spec.model_dump(), indent=2))}</pre></div>"""
        return page(body, title=f"{brand} — {spec.ref}", wide=True)

    # -- runs -------------------------------------------------------------------
    @r.post("/runs")
    def trigger(spec_ref: str = Form(...)):
        spec = load_spec(cfg, spec_ref)
        if spec is None:
            return RedirectResponse(local("/workbench/specs"), status_code=303)
        if spec.freeze_dataset_version == "(at-run-time)":
            from datetime import datetime, timezone

            spec = spec.model_copy(update={
                "freeze_dataset_version": datetime.now(timezone.utc)
                .strftime("%Y-%m-%dT%H:%M:%SZ")})
        run_id, err = runner_mod.start_run(cfg, spec)
        if err:
            return RedirectResponse(local(f"/workbench/specs/{spec_ref}?err={err}"), status_code=303)
        return RedirectResponse(local(f"/workbench/runs/{run_id}"), status_code=303)

    @r.get("/runs", response_class=HTMLResponse)
    def runs_list() -> str:
        rows = []
        for x in list_runs(cfg):
            gate = ("—" if x.state != "done" else
                    _chip("CERTIFIED", "green") if x.ok else _chip("REJECTED", "red"))
            rows.append(
                f"<div class='kv'><span><a href='{local('/workbench/runs/' + _e(x.run_id))}'>"
                f"<code>{_e(x.run_id)}</code></a> · {_e(x.spec_ref)} · {_e(x.release.get('model', ''))}</span>"
                f"<span>{gate}{_chip('signed', 'green') if x.signoff else ''}</span></div>")
        body = f"""{_eyebrow(cfg, 'runs')}{_nav('runs')}
        <h1>Certification <span class='mark'>runs</span></h1>
        <div class='card'>{''.join(rows) or "<div class='kv'><span>no runs yet</span><span>—</span></div>"}</div>"""
        return page(body, title=f"{brand} — Runs", wide=True)

    @r.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_detail(request: Request, run_id: str, dataset: str = "", slice: str = "",
                   verdict: str = "", evaluator: str = "", msg: str = "") -> str:
        live = runner_mod.status(run_id)
        run = load_run(cfg, run_id)
        if run is None and not live:
            return page(f"{_eyebrow(cfg, 'runs')}{_nav('runs')}<h2>Unknown run</h2>",
                        title=brand, wide=True)
        if run is None or (run.state == "running" and live.get("state") == "running"):
            prog = live or {"progress": 0, "total": "?", "message": "starting"}
            body = f"""{_eyebrow(cfg, 'run in progress')}{_nav('runs')}
            <h1>Running <span class='mark'>{_e(run_id)}</span></h1>
            <div class='card active'><h2>{_e(prog.get('message', ''))}</h2>
              <p class='sub'>{prog.get('progress', 0)} / {prog.get('total', '?')} items —
              deterministic checks score as code evaluators; managed judges fire on the run
              automatically. This page refreshes.</p></div>
            <meta http-equiv='refresh' content='3'>"""
            return page(body, title=f"{brand} — running", wide=True)

        role = _role_of(request)
        lf = Links.from_cfg(cfg)
        cat = _catalog(cfg)

        def _runs_url(ds_name: str) -> str:
            stored = next((x.get("runs_url") for x in run.langfuse_runs
                           if x["dataset"] == ds_name and x.get("runs_url")), "")
            if stored:
                return stored
            d = cat.dataset(ds_name)
            return lf.dataset_runs(d.id) if d and d.id else ""

        aggs = aggregates(run)
        rows = filter_rows(run, dataset=dataset, slice_name=slice, verdict=verdict,
                           evaluator=evaluator)
        datasets = sorted({x["dataset"] for x in run.rows})
        slices = sorted({x["slice"] for x in run.rows})
        evaluators = sorted({k for x in run.rows for k in x["scores"]})

        def opts(values, current):
            return "<option value=''>all</option>" + "".join(
                f"<option {'selected' if v == current else ''}>{_e(v)}</option>" for v in values)

        gate_banner = ""
        for g in run.gates:
            tone = "green" if g["ok"] else "red"
            sl = " · ".join(f"{k} {d['rate']:.0%}/{d['threshold']:.0%}"
                            for k, d in (g.get("slice_detail") or {}).items())
            gate_banner += (f"<div class='kv'><span><b>{_e(g['dataset'])}</b>"
                            f"{(' · ' + _e(sl)) if sl else ''}</span>"
                            f"<span>{g['pass_rate']:.1%} vs ≥{g['threshold']:.0%} "
                            f"{_chip('PASS' if g['ok'] else 'FAIL', tone)} "
                            f"{_lf(_runs_url(g['dataset']), 'run in Langfuse →')}</span></div>")

        agg_html = ""
        for ds, a in aggs.items():
            cells = ""
            for sl, s in sorted(a["slices"].items()):
                tone = "green" if s["passed"] == s["n"] else "red"
                rate_txt = f"{s['rate']:.0%}"
                cells += (f"<div class='kv'><span style='padding-left:14px'><code>{_e(sl)}</code></span>"
                          f"<span>{s['passed']}/{s['n']} {_chip(rate_txt, tone)}</span></div>")
            agg_html += (f"<div class='kv'><span><b>{_e(ds)}</b></span>"
                         f"<span>{a['passed']}/{a['n']} ({a['rate']:.1%})</span></div>{cells}")

        table = []
        for x in rows[:300]:
            tone = "green" if x["passed"] else "red"
            scores = " ".join(
                f"<span class='chip {'green' if str(v.get('value')) not in ('fail', '0', '0.0') else 'red'}'"
                f" title='{_e(v.get('comment', ''))}'>{_e(k)}: {_e(v.get('value'))}</span>"
                for k, v in x["scores"].items())
            link = f" <a href='{_e(x['trace_url'])}' target='_blank'>trace →</a>" if x["trace_url"] else ""
            table.append(
                f"<div class='kv' style='display:block'>"
                f"<div style='display:flex;justify-content:space-between;gap:10px'>"
                f"<span><code>{_e(x['item_id'][:14])}…</code> · {_e(x['dataset'])} · `{_e(x['slice'])}`</span>"
                f"<span>{_chip('pass' if x['passed'] else 'fail', tone)}{link}</span></div>"
                f"<div style='margin-top:4px'>{scores}</div>"
                + (f"<div class='note'>{_e(x['detail'])}</div>" if x["detail"] else "")
                + "</div>")

        others = [x.run_id for x in list_runs(cfg) if x.run_id != run_id and x.state == "done"]
        compare_html = ""
        if others:
            copts = "".join(f"<option value='{_e(o)}'>{_e(o)}</option>" for o in others)
            compare_html = (f"<form method='get' action='{local('/workbench/compare')}' style='margin:10px 0'>"
                            f"<input type='hidden' name='a' value='{_e(run_id)}'>"
                            f"<label>Compare against</label><select name='b'>{copts}</select>"
                            f"<button type='submit'>Compare →</button></form>")

        sign_html = ""
        if run.signoff:
            sign_html = (f"<div class='memo'><b>Signed off</b> by {_e(run.signoff.get('by'))} "
                         f"({_e(run.signoff.get('role'))}) at {_e(run.signoff.get('at', '')[:19])}."
                         f" {_e(run.signoff.get('note', ''))} — "
                         f"<a href='{local('/workbench/evidence/' + _e(run_id))}'>evidence pack →</a></div>")
        elif run.state == "done":
            if can_sign(role):
                sign_html = (f"<form method='post' action='{local('/workbench/signoff')}' class='ghost card'>"
                             f"<input type='hidden' name='run_id' value='{_e(run_id)}'>"
                             f"<label>Sign off this certification run (recorded in Langfuse:"
                             f" review queue + human-annotation score)</label>"
                             f"<input name='name' placeholder='reviewer name' required>"
                             f"<textarea name='note' rows='2' placeholder='basis for approval'></textarea>"
                             f"<button type='submit'>Sign off (Approver)</button></form>")
            else:
                sign_html = (f"<div class='note'>Sign-off requires the <b>Approver</b> role "
                             f"(current: {role}) — switch on the overview page. "
                             f"<a href='{local('/workbench/evidence/' + _e(run_id))}'>preview evidence pack →</a></div>")

        notice = f"<div class='memo'>{_e(msg)}</div>" if msg else ""
        err_html = (f"<div class='memo'><span class='chip red'>run error</span> {_e(run.error)}</div>"
                    if run.error else "")
        records = ""
        if run.langfuse_runs:
            record_rows = "".join(
                f"<div class='kv'><span>Dataset Run <code>{_e(x['run_name'])}</code> on "
                f"<code>{_e(x['dataset'])}</code></span>"
                f"<span>{_lf(_runs_url(x['dataset']))}</span></div>"
                for x in run.langfuse_runs)
            records = (f"<div class='card'><h2>Records in Langfuse (system of record)</h2>"
                       f"{record_rows}</div>")
        body = f"""{_eyebrow(cfg, 'run results')}{_nav('runs')}
        <h1><span class='mark'>{_e(run_id)}</span></h1>
        <p class='sub'>{_e(run.spec_ref)} · {_e(run.release.get('model'))} + {_e(run.release.get('prompt_name'))}
          v{_e(run.release.get('prompt_version'))} · spec <code>{run.spec_hash[:16]}…</code></p>
        {notice}{err_html}
        <div class='card'><h2>Gate</h2>{gate_banner or '—'}</div>
        {records}
        {sign_html}
        <div class='card'><h2>By suite &amp; slice</h2>{agg_html}</div>
        {compare_html}
        <div class='card'>
          <h2>Items ({len(rows)})</h2>
          <form method='get' style='display:flex;gap:10px;flex-wrap:wrap;margin:8px 0 14px'>
            <span><label>suite</label><select name='dataset'>{opts(datasets, dataset)}</select></span>
            <span><label>slice</label><select name='slice'>{opts(slices, slice)}</select></span>
            <span><label>verdict</label><select name='verdict'>{opts(['pass', 'fail'], verdict)}</select></span>
            <span><label>failed evaluator</label><select name='evaluator'>{opts(evaluators, evaluator)}</select></span>
            <span style='align-self:flex-end'><button type='submit' style='margin-top:0;width:auto;padding:10px 16px'>Filter</button></span>
          </form>
          {''.join(table) or '—'}
        </div>"""
        return page(body, title=f"{brand} — {run_id}", wide=True)

    # -- compare ---------------------------------------------------------------
    @r.get("/compare", response_class=HTMLResponse)
    def compare_view(a: str, b: str) -> str:
        ra, rb = load_run(cfg, a), load_run(cfg, b)
        if ra is None or rb is None:
            return page(f"{_eyebrow(cfg, 'compare')}{_nav('runs')}<h2>Unknown run(s)</h2>",
                        title=brand, wide=True)
        rows = compare(ra, rb)
        diff = [x for x in rows if x["delta"] not in ("=",)]
        html_rows = []
        for x in rows:
            tone = {"improved": "green", "regressed": "red"}.get(x["delta"], "")
            pa = "—" if x["a"] is None else ("pass" if x["a"]["passed"] else "fail")
            pb = "—" if x["b"] is None else ("pass" if x["b"]["passed"] else "fail")
            html_rows.append(
                f"<div class='kv'><span><code>{_e(x['item_id'][:14])}…</code> · {_e(x['dataset'])} · `{_e(x['slice'])}`</span>"
                f"<span>{_e(pa)} → {_e(pb)} {_chip(x['delta'], tone) if tone else ''}</span></div>")
        body = f"""{_eyebrow(cfg, 'compare runs')}{_nav('runs')}
        <h1><span class='mark'>{_e(a)}</span> vs <span class='mark'>{_e(b)}</span></h1>
        <p class='sub'>{_e(ra.release.get('model'))} vs {_e(rb.release.get('model'))} ·
          {len(diff)} differing items of {len(rows)}</p>
        <div class='card'>{''.join(html_rows) or '—'}</div>"""
        return page(body, title=f"{brand} — compare", wide=True)

    # -- coverage ----------------------------------------------------------------
    @r.get("/coverage", response_class=HTMLResponse)
    def coverage_view() -> str:
        cat = _catalog(cfg)
        lf = Links.from_cfg(cfg)
        cov = coverage_matrix(cfg, cat)
        rows = []
        for c in cov:
            if c.covered:
                latest = ""
                if c.latest:
                    tone = "green" if c.latest["passed"] == c.latest["n"] else "red"
                    latest = _chip(f"latest {c.latest['passed']}/{c.latest['n']}", tone)
                ds_links = ""
                for ds in sorted({it["dataset"] for it in c.items}):
                    info = cat.dataset(ds)
                    ds_links += " " + _lf(lf.dataset(info.id if info else ""), ds)
                rows.append(
                    f"<div class='kv'><span><b>{_e(c.requirement.id)}</b> — {_e(c.requirement.title)}"
                    f"<div class='note'>{_e(c.requirement.source)}</div></span>"
                    f"<span>{len(c.items)} items · {', '.join(f'`{e}`' for e in c.evaluators) or 'judge-graded'} {latest} {ds_links}</span></div>")
            else:
                rows.append(
                    f"<div class='kv'><span><b>{_e(c.requirement.id)}</b> — {_e(c.requirement.title)}"
                    f"<div class='note'>{_e(c.requirement.description)}</div></span>"
                    f"<span>{_chip('UNCOVERED', 'red')}</span></div>")
        n_unc = sum(1 for c in cov if not c.covered)
        body = f"""{_eyebrow(cfg, 'requirement coverage')}{_nav('coverage')}
        <h1>Coverage <span class='mark'>matrix</span></h1>
        <p class='sub'>Requirement → suite items (via item metadata) → evaluators (via code) →
          latest run outcome. {n_unc} uncovered requirement{'s' if n_unc != 1 else ''} — the
          matrix finds gaps; it doesn't rubber-stamp.</p>
        {_offline_banner(cat)}
        <div class='card'>{''.join(rows)}</div>"""
        return page(body, title=f"{brand} — Coverage", wide=True)

    # -- promote -------------------------------------------------------------------
    @r.get("/promote", response_class=HTMLResponse)
    def promote_view(err: str = "", ok: str = "") -> str:
        from .promote import list_candidates

        cat = _catalog(cfg)
        cands, lerr = ([], "offline — promotion needs the live instance") if not cat.online \
            else list_candidates(cfg, cat)
        notice = ""
        if err:
            notice = f"<div class='memo'><span class='chip red'>error</span> {_e(err)}</div>"
        elif ok:
            notice = f"<div class='memo'><span class='chip green'>promoted</span> {_e(ok)}</div>"
        if lerr:
            notice += f"<div class='memo'>{_e(lerr)}</div>"
        ds_opts = "".join(f"<option>{_e(d.name)}</option>" for d in cat.datasets)
        lf = Links.from_cfg(cfg)
        cards = []
        for c in cands:
            comments = "".join(f"<div class='note'>“{_e(x[:160])}”</div>" for x in c.reviewer_comments)
            cards.append(f"""
            <form method='post' action='{local('/workbench/promote')}' class='card'>
              <h2>{_e(c.borrower or 'trace')} · <code>{_e(c.case_id or c.trace_id[:12])}</code>
                {_lf(lf.trace(c.trace_id), 'trace in Langfuse →')}</h2>
              <div class='kv'><span>Question</span><span>{_e((c.question or {}).get('question', ''))}</span></div>
              <div class='kv'><span>Production answered</span>
                <span>{_e(json.dumps((c.produced or {}).get('figures', {})))}</span></div>
              {comments}
              <input type='hidden' name='trace_id' value='{_e(c.trace_id)}'>
              <label>Target suite</label><select name='dataset_name'>{ds_opts}</select>
              <label>Slice</label><input name='slice_name' value='production_flagged'>
              <label>Requirement ids (comma-separated)</label>
              <input name='requirement_ids' value='MRM-ACC-1, MRM-ACC-2'>
              <label>Expected output — the REVIEWER'S ground truth (prefilled from the
                conventions; never the production answer)</label>
              <textarea name='expected_output_json' rows='8'
                style='font-family:var(--font-mono);font-size:12px'>{_e(json.dumps(c.suggested_expected, indent=2))}</textarea>
              <button type='submit'>Promote to suite →</button>
            </form>""")
        body = f"""{_eyebrow(cfg, 'suite intake')}{_nav('promote')}
        <h1>Promote reviewed traces <span class='mark'>into the suite</span></h1>
        <p class='sub'>Completed {_e(cfg.certification.queues.intake.name)} reviews
          {_lf(Links.from_cfg(cfg).queues(), 'queues in Langfuse →')} that are not yet
          suite items. The reviewer's corrected figures become the expected output — with
          provenance (<code>sourceTraceId</code>) and requirement links.</p>
        {notice}{''.join(cards) or "<div class='card'><h2>Queue is clear</h2><p class='sub'>No completed reviews awaiting promotion.</p></div>"}"""
        return page(body, title=f"{brand} — Promote", wide=True)

    @r.post("/promote")
    def do_promote(trace_id: str = Form(...), dataset_name: str = Form(...),
                   slice_name: str = Form("production_flagged"),
                   requirement_ids: str = Form(""),
                   expected_output_json: str = Form(...)):
        from .promote import promote as _promote

        reqs = [x.strip() for x in requirement_ids.split(",") if x.strip()]
        item_id, err = _promote(cfg, trace_id=trace_id, dataset_name=dataset_name,
                                slice_name=slice_name,
                                expected_output_json=expected_output_json,
                                requirement_ids=reqs)
        _catalog(cfg, force=True)
        if err:
            return RedirectResponse(local(f"/workbench/promote?err={err[:300]}"), status_code=303)
        return RedirectResponse(local(f"/workbench/promote?ok=item {item_id} in {dataset_name}"),
                                status_code=303)

    # -- sign-off + evidence ---------------------------------------------------------
    @r.post("/signoff")
    def do_signoff(request: Request, run_id: str = Form(...), name: str = Form(""),
                   note: str = Form("")):
        run = load_run(cfg, run_id)
        if run is None:
            return RedirectResponse(local("/workbench/runs"), status_code=303)
        ok, msg = sign_off(cfg, run, role=_role_of(request), name=name, note=note)
        return RedirectResponse(local(f"/workbench/runs/{run_id}?msg={'signed off. ' + msg if ok else msg}"),
                                status_code=303)

    @r.get("/evidence/{run_id}")
    def evidence(run_id: str, download: int = 0):
        run = load_run(cfg, run_id)
        if run is None:
            return PlainTextResponse("unknown run", status_code=404)
        md = evidence_markdown(cfg, run)
        if download:
            if not run.signoff:
                return PlainTextResponse(
                    "evidence download requires sign-off (4-eyes) — preview without ?download=1",
                    status_code=403)
            return PlainTextResponse(md, headers={
                "Content-Disposition": f"attachment; filename=evidence-{run_id}.md"})
        banner = ("" if run.signoff else
                  "<div class='memo'><span class='chip red'>UNSIGNED</span> Preview only — "
                  "download is enabled after Approver sign-off.</div>")
        dl = (f"<a class='back' href='{local('/workbench/evidence/' + _e(run_id) + '?download=1')}'>download .md →</a>"
              if run.signoff else "")
        body = (f"{_eyebrow(cfg, 'evidence pack')}{_nav('runs')}{banner}"
                f"<div class='card'><pre style='font-family:var(--font-mono);font-size:12px;"
                f"white-space:pre-wrap'>{_e(md)}</pre></div>{dl}")
        return HTMLResponse(page(body, title=f"{brand} — evidence", wide=True))

    return r
