"""Seed orchestrator (spec v2, order-of-creation):

    score configs -> analyst-copilot prompt versions -> backdated traces + scores
    (sessions, golden, curated, flagged-pending, nightly batch, cert-run) -> the
    certification-suite + items -> backdated dataset-run-items (baseline / candidate
    A / candidate B) -> the certification-review queue (completed + pending)

The seed path makes **no model calls**: every trace is a deterministic, templated
CopilotAnswer ingested backdated via the batch API. Writes ``.synth_state.json`` and
the committed golden-case fixtures on the way out.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable

from ..config import Config
from ..rng import Rng
from ..state import REPO_ROOT, RunState
from ..timegen import day_anchor, iso_date, now_utc
from .annotation import seed_queue
from .cert_runs import run_gate_verdict, run_pass_rates
from .generator import Plan, build_plan
from .ingest import Ingestor, assert_demo_project, ensure_score_config
from .scores import (
    SCORE_CONFIGS,
    analyst_feedback_score,
    deterministic_scores,
    human_annotation_scores,
    human_judge_pair,
    judge_scores,
)
from .traces import build_trace_events

FIXTURES_DIR = REPO_ROOT / "fixtures"
DEFAULT_SPOOL = REPO_ROOT / ".synth_spool" / "events.ndjson"


def run_seed(cfg: Config, *, dry_run: bool = False, persist: bool = True,
             run_date: datetime | None = None, spool_path: str | Path | None = None,
             do_import: bool = True, log: Callable[[str], None] = print) -> RunState:
    run_date = run_date or now_utc()
    base_url = cfg.target.base_url
    spool_path = Path(spool_path) if spool_path else DEFAULT_SPOOL

    # -- guardrail: never touch a non-demo project --------------------------
    project_name = "(dry-run)"
    project_id = ""
    if not dry_run:
        project_id, project_name = assert_demo_project(base_url, cfg.target.project_hint)
        log(f"✓ guardrail passed: project {project_name!r} matches hint {cfg.target.project_hint!r}")

    # -- plan (deterministic) ------------------------------------------------
    log("· building deterministic plan …")
    plan = build_plan(cfg, run_date)
    s = plan.summary
    log(f"  {s['total_traces']} traces in {s['sessions']} sessions "
        f"({s['golden_traces']} golden, {s['by_language'].get('de', 0)} German), "
        f"{s['experiment_runs']} experiment runs ({s['experiment_run_items']} items), "
        f"~{s['estimated_events']} events")

    # -- 1. score configs ------------------------------------------------------
    if not dry_run:
        for sc in SCORE_CONFIGS:
            ensure_score_config(base_url, sc)
        from .annotation import score_config_ids
        from . import scores as scores_mod

        names = [c["name"] for c in SCORE_CONFIGS]
        scores_mod.CONFIG_IDS.update(dict(zip(names, score_config_ids(base_url, names))))
        log(f"✓ {len(SCORE_CONFIGS)} score configs ensured (ids linked)")

    # -- 2. the analyst-copilot prompt history ----------------------------------
    versions = {"latest": cfg.certification.n_prompt_versions,
                "production": cfg.certification.production_version,
                "staging": cfg.certification.staging_version}
    lf = None
    if cfg.certification.enabled and not dry_run:
        from ..lfclient import get_langfuse
        from .prompts import register_prompts

        lf = get_langfuse(cfg)
        versions = register_prompts(lf, cfg)
        log(f"✓ prompt {cfg.certification.prompt_name!r}: {versions['latest']} versions "
            f"(production=v{versions['production']}, staging=v{versions['staging']})")

    # -- 3. backdated traces + scores --------------------------------------------
    ing = Ingestor.from_env(base_url, dry_run=dry_run, spool_path=spool_path)
    _spool_all(cfg, plan, ing)
    log(f"✓ generated {ing.spooled} events across {len(plan.specs)} traces "
        f"→ spooled to {spool_path}")
    if dry_run:
        log("  dry-run: spool written, nothing imported")
    elif not do_import:
        log("  --no-import: spool written, skipping batch import (resume with `synth import-spool`)")
    else:
        log(f"· batch-importing {ing.spooled} events from disk (chunks of {ing.chunk_size}) …")
        ing.import_spool(log=lambda m: None)
        log(f"✓ batch-imported {ing.sent} events")

    # -- 4. the hosted certification-suite ----------------------------------------
    suite_info = {"name": cfg.certification.dataset.name, "items_created": len(plan.cert.suite)}
    if cfg.certification.enabled and not dry_run:
        from .datasets import create_suite

        suite_info = create_suite(lf, cfg, plan.cert)
        lf.flush()
        log(f"✓ suite {suite_info['name']!r}: {suite_info['items_created']} items "
            f"({suite_info['curated']} curated from production)")

    # -- 5. experiment runs (baseline / candidate A / candidate B) -----------------
    # Created via the SDK run_experiment path (deterministic, no model calls) so they
    # surface in the Experiments tab on every Langfuse version (cert_runs.py header).
    if cfg.certification.enabled and not dry_run:
        from .cert_runs import seed_experiment_runs

        n = seed_experiment_runs(cfg, lf, plan.cert, log=log)
        log(f"✓ seeded {n} experiment runs via run_experiment")

    # -- 5b. managed evaluators (Cloud / newer self-hosted with the unstable API) ---
    # ORDERING INVARIANT (do NOT move before step 5): judges + their experiment-target
    # evaluation rules must be created AFTER the experiment runs are seeded and flushed.
    # Rules are live-ingestion only and never backfill, so a rule created here cannot
    # fire on the already-ingested seeded runs — guaranteeing "no real judge runs" on
    # the demo data. Creating the rule BEFORE the runs would let it judge them live.
    if cfg.certification.enabled and not dry_run:
        lf.flush()  # ensure step-5 run traces/items are committed before any rule arms
        try:
            _populate_managed_evaluators(cfg, log)
        except Exception as exc:  # noqa: BLE001 — evaluators are best-effort (self-hosted gap)
            log(f"· evaluators: skipped (self-hosted gap or transient error: {exc})")

    # -- 6. the certification-review queue ------------------------------------------
    queue_info = {"name": cfg.certification.queue.name,
                  "completed": len(plan.queue_completed_ids),
                  "pending": len(plan.queue_pending_ids)}
    if cfg.certification.enabled and not dry_run and do_import:
        queue_info = seed_queue(cfg, plan.queue_completed_ids, plan.queue_pending_ids,
                                base_url, log=log)

    # -- fixtures + state --------------------------------------------------------
    state = _build_state(cfg, plan, versions, project_name, queue_info, dry_run)
    state.project_id = project_id
    if persist:
        _write_fixtures(plan)
        state.save()
        log("✓ wrote run state and golden-case fixtures")
    return state


def _spool_all(cfg: Config, plan: Plan, ing: Ingestor) -> None:
    """Phase 3a — every trace/score event to the NDJSON spool on disk. No network."""
    rng = plan.rng
    sc = cfg.scoring
    run_date = plan.run_date
    dip_cfg = cfg.ambience.quality_dip
    t_dip_start = day_anchor(run_date, cfg.certification.prompt_transition_day_offset)
    t_dip_end = day_anchor(run_date, cfg.certification.prompt_fix_day_offset)
    completed = set(plan.queue_completed_ids)
    flagged_comments = {tid: case.analyst_comment
                        for tid, case in zip(plan.cert.flagged_pending_trace_ids,
                                             plan.cert.flagged_pending)}
    golden_by_id = {g.trace_id: g for g in plan.cert.golden}

    ing.open_spool()
    try:
        for spec in plan.specs:
            ing.extend(build_trace_events(rng, cfg, spec))
            ing.extend(deterministic_scores(rng, spec, sc))

            dip = (dip_cfg.dip if dip_cfg.enabled
                   and t_dip_start <= spec.timestamp < t_dip_end else 0.0)
            golden = golden_by_id.get(spec.trace_id)
            if golden is not None:
                ing.extend(_golden_scores(rng, cfg, spec, golden))
            elif spec.trace_id in completed:
                # queue-completed traces: the reviewer scored the certification
                # criteria themselves (the ground truth) + judge pair (agreement story)
                ing.extend(human_judge_pair(rng, spec, sc))
            else:
                ing.extend(judge_scores(rng, spec, sc, dip=dip))

            if spec.kind == "flagged":
                events, _ = analyst_feedback_score(
                    rng, spec.trace_id, spec.timestamp, spec.environment,
                    sc.feedback_response_ratio, sc.feedback_down_rate,
                    force=True, force_down=True,
                    comment=flagged_comments.get(spec.trace_id))
            elif spec.kind in ("ambient", "golden"):
                force_down = golden is not None and golden.key == "numeric_hallucination"
                events, _ = analyst_feedback_score(
                    rng, spec.trace_id, spec.timestamp, spec.environment,
                    sc.feedback_response_ratio, sc.feedback_down_rate,
                    force=force_down, force_down=force_down,
                    comment=golden.analyst_comment if force_down else None)
            else:
                events = []
            ing.extend(events)
        # NOTE: the baseline/A/B experiment runs are NOT spooled here — they are
        # created online via the SDK run_experiment path (seed_experiment_runs), which
        # is the only path the Experiments tab surfaces on newer Langfuse (see
        # cert_runs.py header for the cloud-vs-v3 discrepancy).
    finally:
        ing.close_spool()


def _populate_managed_evaluators(cfg: Config, log: Callable[[str], None]) -> None:
    """Populate the project's Evaluators section, scoped to the suite's experiment runs:
    - **code evaluators** (numeric_accuracy, citation_format, escalation_correctness) —
      deterministic, **no LLM connection needed**, created always;
    - **LLM-as-judge** (groundedness, citation_coverage) — need an LLM connection
      (ANTHROPIC_API_KEY upserts one), else logged and skipped.
    Best-effort: the unstable evaluator API is Cloud / newer-self-hosted only; anything
    missing is logged, never fatal."""
    import os

    import requests

    from ..workbench.judges import (
        CODE_EVALUATORS,
        JUDGE_TEMPLATES,
        ensure_code_evaluator,
        ensure_judge,
        ensure_llm_connection,
        ensure_rule,
        list_judges,
    )

    from ..target import TargetProfile

    base = cfg.target.base_url
    profile = TargetProfile.detect(base)
    _, available = list_judges(base)
    log(f"· evaluators: target = {profile.label}; unstable evaluator API "
        f"{'present → creating programmatically' if available else 'absent'}")
    if not available:
        log("· evaluators: unstable evaluator API not present (older self-hosted) "
            "— create evaluators in the UI per DEMO_SCRIPT (skipped)")
        return
    auth = (os.environ.get("LANGFUSE_PUBLIC_KEY", ""), os.environ.get("LANGFUSE_SECRET_KEY", ""))
    ds_ids = []
    try:
        data = requests.get(f"{base.rstrip('/')}/api/public/v2/datasets",
                            params={"limit": 100}, auth=auth, timeout=15).json().get("data", [])
        ds_ids = [d["id"] for d in data
                  if d.get("name") == cfg.certification.dataset.name and d.get("id")]
    except Exception:  # noqa: BLE001
        pass

    # 1. code evaluators — no LLM connection required
    code_made, notes = 0, []
    for name, source in CODE_EVALUATORS.items():
        ev, err = ensure_code_evaluator(cfg, name, source)
        if err:
            notes.append(f"{name}: {err[:90]}")
            continue
        code_made += 1
        if ds_ids:
            _r, rerr = ensure_rule(cfg, ev, ds_ids)
            if rerr:
                notes.append(f"{name} rule: {rerr[:90]}")
    log(f"✓ code evaluators: {code_made}/{len(CODE_EVALUATORS)} created"
        + (f" (notes: {'; '.join(notes)})" if notes else ""))

    # 2. LLM-as-judge evaluators — need an LLM connection. We create the evaluator
    # definitions AND scope an evaluation rule to the suite (target=experiment). The
    # rule is SAFE w.r.t. "no real judge runs now": evaluation rules are live-ingestion
    # only — they do NOT backfill the already-seeded experiment runs, so creating one
    # fires zero judge calls today. It simply arms FUTURE experiment runs (e.g. a live
    # `synth certify`). The groundedness/citation_coverage SCORES on the seeded runs are
    # already present (deterministic, same score vocabulary), so the Evaluators page
    # shows the judges as governed objects with matching historical scores.
    conn_ok, conn_msg = ensure_llm_connection(cfg)
    if not conn_ok and "in env" in conn_msg:
        # No key in env, but a connection may already be configured in project settings.
        try:
            conns = requests.get(f"{base.rstrip('/')}/api/public/llm-connections",
                                 params={"limit": 50}, auth=auth, timeout=15).json().get("data", [])
        except Exception:  # noqa: BLE001
            conns = []
        if conns:
            conn_msg = f"using existing project connection(s): {[c.get('provider') for c in conns]}"
    log(f"· LLM connection: {conn_msg}")
    judge_made, jnotes = 0, []
    for name in JUDGE_TEMPLATES:
        judge, err = ensure_judge(cfg, name)
        if err:
            jnotes.append(f"{name}: {err[:90]}")
            continue
        judge_made += 1
        if ds_ids:
            _rule, rerr = ensure_rule(cfg, judge, ds_ids)  # experiment, sampling 1.0
            if rerr:
                jnotes.append(f"{name} exp-rule: {rerr[:80]}")
        # Live production-trace monitoring with the SAME judge (target=observation).
        # sampling=0.0 → created DEACTIVATED (paused, zero triggers). Either way, rules
        # never backfill the backdated seed, so this fires zero judge calls on the seed.
        s = cfg.certification.trace_judge_sampling
        _trule, trerr = ensure_rule(cfg, judge, ds_ids, target="observation",
                                    sampling=max(s, 0.01), enabled=s > 0)
        if trerr:
            jnotes.append(f"{name} trace-rule: {trerr[:80]}")
    if judge_made:
        s = cfg.certification.trace_judge_sampling
        live = (f"live trace monitoring @ {s:.0%} sampling" if s > 0
                else "trace monitoring created PAUSED (set trace_judge_sampling>0 to opt in)")
        log(f"✓ LLM judges: {judge_made}/{len(JUDGE_TEMPLATES)} created + scoped to "
            f"experiments (1.0) and traces ({live}); rules never backfill, so the seed "
            "triggers zero judge runs"
            + (f" (notes: {'; '.join(jnotes)})" if jnotes else ""))
    else:
        log("· LLM judges: not created (" + ("; ".join(jnotes) or "unknown")
            + ") — add an LLM connection (ANTHROPIC_API_KEY or project settings) and re-run `synth evaluators`")


def _golden_scores(rng: Rng, cfg: Config, spec, golden) -> list[dict]:
    """Every relevant score on a golden trace — the click-in moments must show the
    measurement, not just the content (spec v2 §4, §6)."""
    from .events import score_event

    s = rng.sub("goldscore", spec.trace_id)
    ts, tid, env = spec.timestamp, spec.trace_id, spec.environment
    events: list[dict] = []

    from .scores import config_id

    def cat(name, value, comment=None):
        events.append(score_event(score_id=s.score_id(name, tid), name=name, value=value,
                                  data_type="CATEGORICAL", timestamp=ts, trace_id=tid,
                                  environment=env, comment=comment,
                                  config_id=config_id(name)))

    def num(name, value):
        events.append(score_event(score_id=s.score_id(name, tid), name=name, value=value,
                                  data_type="NUMERIC", timestamp=ts, trace_id=tid,
                                  observation_id=spec.answer_obs_id, environment=env,
                                  config_id=config_id(name)))

    if golden.key == "covenant_summary":
        num("groundedness", 0.96)
        num("citation_coverage", 0.97)
        cat("citation_format", "pass")
    elif golden.key == "numeric_hallucination":
        wrong = next(iter(golden.answer.figures.values()), 0)
        right = next(iter(golden.expected.figures.values()), 0)
        cat("numeric_accuracy", "fail",
            f"answer states {wrong:,} but the cited table prints {right:,}")
        num("groundedness", 0.41)
        cat("citation_format", "pass")
        # the human annotation scores the same criteria — that IS the ground truth
        events.extend(human_annotation_scores(
            rng, spec, wrong_numeric=True,
            ground_truth_note=f"correct value is EUR {right:,}. {golden.analyst_comment}"))
    elif golden.key == "correct_escalation":
        cat("escalation_correctness", "pass",
            "out-of-scope request correctly routed to a human")
        num("groundedness", 0.93)
    elif golden.key == "dscr_trend":
        cat("numeric_accuracy", "pass")
        num("groundedness", 0.95)
        num("citation_coverage", 0.96)
        cat("citation_format", "pass")
    elif golden.key == "citation_gap":
        num("citation_coverage", 0.32)
        num("groundedness", 0.85)   # the content itself is fine — that's the trap
        cat("citation_format", "fail", "no machine-readable citations attached")
    return events


def import_spool_file(cfg: Config, spool_path: str | Path | None = None,
                      log: Callable[[str], None] = print) -> int:
    base_url = cfg.target.base_url
    path = Path(spool_path) if spool_path else DEFAULT_SPOOL
    _pid, project_name = assert_demo_project(base_url, cfg.target.project_hint)
    log(f"✓ guardrail passed: project {project_name!r} matches hint {cfg.target.project_hint!r}")
    ing = Ingestor.from_env(base_url, spool_path=path)
    log(f"· batch-importing from {path} (chunks of {ing.chunk_size}) …")
    ing.import_spool(log=lambda m: None)
    log(f"✓ batch-imported {ing.sent} events")
    return ing.sent


def _write_fixtures(plan: Plan) -> None:
    FIXTURES_DIR.mkdir(exist_ok=True)
    rows = []
    for g in plan.cert.golden:
        rows.append({
            "kind": "golden_trace", "key": g.key, "title": g.title, "trace_id": g.trace_id,
            "question": g.question.model_dump(), "answer": g.answer.model_dump(),
            "expected": g.expected.model_dump(), "error_mode": g.error_mode,
            "analyst_comment": g.analyst_comment,
        })
    for it in plan.cert.suite:
        if it.run_errors:
            rows.append({
                "kind": "run_red_cell", "scenario": it.scenario, "item_id": it.item_id,
                "run_errors": it.run_errors,
                "question": it.question.model_dump(), "expected": it.expected.model_dump(),
            })
    (FIXTURES_DIR / "golden_cases.json").write_text(json.dumps(rows, indent=2))


def _build_state(cfg: Config, plan: Plan, versions: dict, project_name: str,
                 queue_info: dict, dry_run: bool) -> RunState:
    cert = plan.cert
    runs_state = {}
    for run in cert.runs:
        ok, detail = run_gate_verdict(cfg, run)
        runs_state[run.run_name] = {
            "model": run.model, "verdict": run.verdict,
            "gate_ok": ok, "scenarios": detail,
            "pass_rates": {k: round(v, 4) for k, v in run_pass_rates(run).items()},
            "groundedness_mu": run.groundedness_mu,
            "date": iso_date(run.run_date),
        }
    suite_state = {
        "certification_suite": {
            "name": cfg.certification.dataset.name,
            "items": len(cert.suite),
            "scenarios": {k: v.n_items for k, v in cfg.certification.dataset.scenarios.items()},
            "gates": {k: v.gate for k, v in cfg.certification.dataset.scenarios.items()},
            "slices": sorted({it.scenario for it in cert.suite}),
            "runs": runs_state,
        }
    }
    flagged_examples = []
    for tid, case in zip(cert.flagged_pending_trace_ids, cert.flagged_pending):
        wrong = next(iter(case.wrong.figures.values()), None)
        right = next(iter(case.correct.figures.values()), None)
        flagged_examples.append({
            "trace_id": tid, "borrower": case.borrower, "case_id": case.question.case_id,
            "question": case.question.question, "error_mode": case.error_mode,
            "incumbent_figure_eur": wrong, "correct_figure_eur": right,
            "analyst_comment": case.analyst_comment,
        })
    return RunState(
        base_url=cfg.target.base_url,
        project_name=project_name,
        run_date=plan.run_date.isoformat(),
        prompt_name=cfg.certification.prompt_name,
        prompt_versions=versions,
        incumbent_model=cfg.certification.incumbent_model,
        candidate_a_model=cfg.certification.candidate_a_model,
        candidate_b_model=cfg.certification.candidate_b_model,
        judge_model=cfg.certification.judge_model,
        baseline_run_date=iso_date(cert.baseline_date) if cert.baseline_date else "",
        candidate_run_date=iso_date(cert.candidate_date) if cert.candidate_date else "",
        suites=suite_state,
        queue=queue_info,
        golden=[{"key": g.key, "title": g.title, "trace_id": g.trace_id} for g in cert.golden],
        flagged_pending=flagged_examples,
        summary=plan.summary,
        dry_run=dry_run,
    )
