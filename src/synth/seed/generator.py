"""Assemble the full run plan (spec v2): sessions-driven ambient caseload + golden
traces + the certification objects (suite, three runs, review queue) + ambience.

Deterministic from ``(config, seed, run_date)``. Volume is sessions/day × log-normal
turns — total traces are **derived, not forced** (~10–12k at the full preset, scaled
by ``generation.volume.scale`` for Cloud).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..agent import answer_deterministic
from ..config import Config
from ..content import (
    ambient_kind,
    apply_archetype,
    build_question,
    germanize,
    user_population,
)
from ..filings import BORROWERS, case_id, desk_for, filing_type
from langfuse_synth_core.rng import Rng
from ..timegen import day_anchor, iso_date, sample_in_range, sample_session_times, window_start
from . import certification as cert_mod
from .certification import Certification
from .prompts import version_for_timestamp
from .traces import TraceSpec


@dataclass
class Plan:
    cfg: Config
    run_date: datetime
    rng: Rng
    cert: Certification
    specs: list[TraceSpec] = field(default_factory=list)          # all production traces
    ambient_specs: list[TraceSpec] = field(default_factory=list)
    golden_specs: list[TraceSpec] = field(default_factory=list)
    flagged_specs: list[TraceSpec] = field(default_factory=list)  # pending thumbs-down (live beat)
    curated_specs: list[TraceSpec] = field(default_factory=list)  # suite-source traces
    batch_specs: list[TraceSpec] = field(default_factory=list)    # nightly ambience
    run_trace_specs: list[TraceSpec] = field(default_factory=list)
    sessions: dict[str, list[str]] = field(default_factory=dict)
    queue_completed_ids: list[str] = field(default_factory=list)
    queue_pending_ids: list[str] = field(default_factory=list)
    users: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


def build_plan(cfg: Config, run_date: datetime) -> Plan:
    rng = Rng(cfg.generation.seed)
    users = user_population(rng, cfg.generation.population.users,
                            cfg.generation.population.power_user_share,
                            cfg.generation.german_share)
    cert = cert_mod.build(cfg, rng, run_date) if cfg.certification.enabled else Certification()

    golden = _build_golden(cfg, run_date, rng, users, cert)
    curated = _build_curated_sources(cfg, run_date, rng, users, cert, golden)
    flagged = _build_flagged_pending(cfg, run_date, rng, users, cert)
    ambient = _build_ambient(cfg, run_date, rng, users)
    batch = _build_nightly_batch(cfg, run_date, rng)
    # NOTE: experiment-run traces are no longer pre-generated — the baseline/A/B runs
    # are created online via run_experiment (cert_runs.seed_experiment_runs), the only
    # path the Experiments tab surfaces on newer Langfuse.

    plan = Plan(cfg=cfg, run_date=run_date, rng=rng, cert=cert,
                ambient_specs=ambient, golden_specs=golden, flagged_specs=flagged,
                curated_specs=curated, batch_specs=batch, run_trace_specs=[],
                users=users)
    plan.specs = ambient + curated + golden + flagged + batch
    plan.sessions = _collect_sessions(plan.specs)
    _pick_queue_items(cfg, rng, plan)
    plan.summary = _summarise(cfg, run_date, plan)
    return plan


# ---------------------------------------------------------------------------
# Ambient caseload — sessions/day × log-normal turns (spec v2 §3)
# ---------------------------------------------------------------------------
def _turns(r: Rng, vol) -> int:
    t = int(round(r.lognormal(vol.turns_median, vol.turns_sigma)))
    return max(1, min(vol.turns_max, t))


def _build_ambient(cfg: Config, run_date: datetime, rng: Rng, users: list[dict]) -> list[TraceSpec]:
    gen = cfg.generation
    r = rng.sub("ambient")
    starts = sample_session_times(r, run_date, gen.window_days,
                                  gen.volume.sessions_per_weekday,
                                  gen.volume.sessions_per_weekend_day,
                                  gen.volume.scale, gen.tz_offset_hours)
    weights = [u["weight"] for u in users]
    specs: list[TraceSpec] = []
    for g, start in enumerate(starts):
        user = r.choices(users, weights, k=1)[0]
        borrower = r.sub("amb_borrower", g).choice(BORROWERS).name
        case = case_id(r, ("amb", g))
        sid = r.trace_id("session", g)
        env = "production" if r.chance(gen.environments.production_share) else "staging"
        german = user.get("language") == "de"   # language follows the ANALYST
        filing = filing_type(r, borrower, 2025)
        desk = desk_for(borrower)
        ts = start
        history: list = []   # (question, answer) of earlier turns in THIS case review
        for turn in range(_turns(r.sub("turns", g), gen.volume)):
            if ts >= run_date:
                break
            kind = ambient_kind(r, (g, turn))
            q = build_question(r, ("amb", g, turn), borrower, case, 2025, kind)
            ans = answer_deterministic(q)
            if german:
                # full-coverage rendering: EVERY kind has a German form, so a German
                # analyst's session never mixes languages mid-chat
                q, ans = germanize(kind, q, ans)
                language = "de"
            else:
                language = "en"
                ans = apply_archetype(r, (g, turn), kind, q, ans)
            err = None
            es = r.sub("err", g, turn)
            if es.chance(cfg.ambience.error_rate):
                pool = ["filings_search", "document_fetch"]
                if kind in ("figure", "trend", "dscr", "covenant", "leverage"):
                    pool.append("table_extract")
                err = es.choice(pool)
            elif es.chance(0.003):
                err = "generation"  # a handful of failed generations (spec v2 §7)
            specs.append(TraceSpec(
                trace_id=r.trace_id("ambient", g, turn), timestamp=ts, question=q,
                answer=ans, user_id=user["userId"], session_id=sid, environment=env,
                kind="ambient", question_kind=kind, turn_index=turn,
                history=list(history),   # prior turns of this conversation
                prompt_version=version_for_timestamp(cfg, run_date, ts),
                language=language, filing=filing, desk=desk,
                ratings_call=r.sub("rate", g, turn).chance(0.06),
                error_step=err))
            if err != "generation":      # a failed turn produced no answer to carry forward
                history.append((q, ans))
            ts = ts + timedelta(seconds=40 + r.uniform(0, 240))
    return specs


# ---------------------------------------------------------------------------
# Golden traces (spec v2 §6) — tagged `golden`, findable in seconds
# ---------------------------------------------------------------------------
def _build_golden(cfg: Config, run_date: datetime, rng: Rng, users: list[dict],
                  cert: Certification) -> list[TraceSpec]:
    r = rng.sub("golden")
    seniors = [u for u in users if u["is_power"]] or users
    specs: list[TraceSpec] = []
    for i, g in enumerate(cert.golden):
        ts = day_anchor(run_date, g.day_offset).replace(hour=8 + 2 * (i % 4), minute=13 + 7 * i)
        tid = r.trace_id("golden", g.key)
        g.trace_id = tid
        specs.append(TraceSpec(
            trace_id=tid, timestamp=ts, question=g.question, answer=g.answer,
            user_id=r.sub("gsel", i).choice(seniors)["userId"],
            session_id=r.trace_id("gsession", g.key), environment="production",
            kind="golden", question_kind=g.question_kind, error_mode=g.error_mode,
            prompt_version=version_for_timestamp(cfg, run_date, ts),
            filing=filing_type(r, g.question.borrower, 2025), desk=desk_for(g.question.borrower),
            tags=["golden", f"golden:{g.key}"]))
    return specs


# ---------------------------------------------------------------------------
# Curated suite-source traces (provenance for `curated` items)
# ---------------------------------------------------------------------------
def _build_curated_sources(cfg: Config, run_date: datetime, rng: Rng, users: list[dict],
                           cert: Certification, golden_specs: list[TraceSpec]) -> list[TraceSpec]:
    r = rng.sub("curated")
    items = [it for it in cert.suite if it.curated]
    if not items:
        return []
    # the numeric-hallucination golden trace IS the provenance of one corrected item
    golden_numeric = next((g for g in cert.golden if g.key == "numeric_hallucination"), None)
    if golden_numeric is not None:
        first_numeric = next((it for it in items if it.scenario == "numeric_lookup"), None)
        if first_numeric is not None:
            first_numeric.source_trace_id = golden_numeric.trace_id
            items = [it for it in items if it is not first_numeric]

    start = day_anchor(run_date, -(cfg.generation.window_days - 3))
    end = day_anchor(run_date, -6)
    times = sample_in_range(r, start, end, len(items), label="curated")
    seniors = [u for u in users if u["is_power"]] or users
    specs: list[TraceSpec] = []
    for i, item in enumerate(items):
        tid = r.trace_id("curated", item.item_id)
        item.source_trace_id = tid
        ts = times[i]
        specs.append(TraceSpec(
            trace_id=tid, timestamp=ts, question=item.question,
            answer=answer_deterministic(item.question),
            user_id=r.sub("cursel", i).choice(seniors)["userId"],
            session_id=r.trace_id("cursession", i), environment="production",
            kind="curated", question_kind=_kind_for(item.question.question),
            prompt_version=version_for_timestamp(cfg, run_date, ts),
            filing=filing_type(r, item.question.borrower, 2025),
            desk=desk_for(item.question.borrower), tags=["curated-to-suite"]))
    return specs


# ---------------------------------------------------------------------------
# The reserved pending thumbs-down (the live intake beat)
# ---------------------------------------------------------------------------
def _build_flagged_pending(cfg: Config, run_date: datetime, rng: Rng, users: list[dict],
                           cert: Certification) -> list[TraceSpec]:
    r = rng.sub("flagged")
    seniors = [u for u in users if u["is_power"]] or users
    specs: list[TraceSpec] = []
    for i, case in enumerate(cert.flagged_pending):
        ts = day_anchor(run_date, -2 - i).replace(hour=11, minute=24 + 9 * i)
        tid = r.trace_id("flagged", case.key)
        cert.flagged_pending_trace_ids.append(tid)
        specs.append(TraceSpec(
            trace_id=tid, timestamp=ts, question=case.question, answer=case.wrong,
            user_id=r.sub("fsel", i).choice(seniors)["userId"],
            session_id=r.trace_id("fsession", case.key), environment="production",
            kind="flagged", question_kind=_kind_for(case.question.question),
            error_mode=case.error_mode,
            prompt_version=version_for_timestamp(cfg, run_date, ts),
            filing=filing_type(r, case.borrower, 2025), desk=desk_for(case.borrower),
            tags=["flagged"]))
    return specs


# ---------------------------------------------------------------------------
# Nightly batch ambience (never demoed) + seeded experiment-run traces
# ---------------------------------------------------------------------------
def _build_nightly_batch(cfg: Config, run_date: datetime, rng: Rng) -> list[TraceSpec]:
    nb = cfg.ambience.nightly_batch
    if not nb.enabled:
        return []
    r = rng.sub("batch")
    specs: list[TraceSpec] = []
    start = window_start(run_date, cfg.generation.window_days)
    for d in range(cfg.generation.window_days):
        night = start + timedelta(days=d, hours=1, minutes=10)  # 03:10 Berlin
        if night >= run_date:
            continue
        for i in range(nb.traces_per_night):
            borrower = r.sub("bborrower", d, i).choice(BORROWERS).name
            q = build_question(r, ("batch", d, i), borrower, case_id(r, ("batch", d, i)),
                               2025, "covenant")
            ts = night + timedelta(minutes=3 * i)
            specs.append(TraceSpec(
                trace_id=r.trace_id("batch", d, i), timestamp=ts, question=q,
                answer=answer_deterministic(q), user_id="svc_covenant_monitor",
                session_id=r.trace_id("batchsession", d), environment="production",
                kind="batch", question_kind="covenant",
                prompt_version=version_for_timestamp(cfg, run_date, ts),
                filing="annual-report", desk=desk_for(borrower), tags=[nb.tag]))
    return specs


def _kind_for(question_text: str) -> str:
    t = question_text.lower()
    if "develop over the last" in t or "across the last" in t:
        return "trend"
    if "covenant" in t and t.startswith("summari"):
        return "covenant"
    if "covenant" in t and "dscr" in t:
        return "covenant"
    if "coverage ratio" in t or "dscr" in t:
        return "dscr"
    if "net leverage" in t:
        return "leverage"
    if t.startswith("summari"):
        return "summary"
    if "should we" in t or "extend the" in t:
        return "advice"
    if "home address" in t:
        return "pii"
    if "do you expect" in t:
        return "speculation"
    if "order backlog" in t:
        return "unanswerable"
    if "how should i proceed" in t:
        return "escalation"
    return "figure"


def _collect_sessions(specs: list[TraceSpec]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for s in specs:
        if s.session_id:
            out.setdefault(s.session_id, []).append(s.trace_id)
    return {k: v for k, v in out.items() if len(v) > 1}


def _pick_queue_items(cfg: Config, rng: Rng, plan: Plan) -> None:
    """Completed = curated provenance traces (+ the corrected golden); pending = the
    reserved flagged case + recent ambient traces awaiting review."""
    q = cfg.certification.queue
    golden_numeric = next((g.trace_id for g in plan.cert.golden
                           if g.key == "numeric_hallucination"), None)
    completed = ([golden_numeric] if golden_numeric else [])
    completed += [s.trace_id for s in plan.curated_specs]
    plan.queue_completed_ids = completed[: q.n_completed]

    pending = list(plan.cert.flagged_pending_trace_ids)
    recent = sorted((s for s in plan.ambient_specs
                     if s.kind == "ambient" and s.error_step is None
                     and s.environment == "production"),
                    key=lambda s: s.timestamp, reverse=True)
    r = rng.sub("queuepending")
    pool = recent[:120]
    r.shuffle(pool)
    pending += [s.trace_id for s in pool[: max(0, q.n_pending - len(pending))]]
    plan.queue_pending_ids = pending[: q.n_pending]


def _summarise(cfg: Config, run_date: datetime, plan: Plan) -> dict:
    cert = plan.cert
    by_env: dict[str, int] = {}
    by_lang: dict[str, int] = {}
    errored = 0
    for s in plan.specs:
        by_env[s.environment] = by_env.get(s.environment, 0) + 1
        by_lang[s.language] = by_lang.get(s.language, 0) + 1
        errored += 1 if s.error_step else 0
    scenarios = {}
    for it in cert.suite:
        scenarios[it.scenario] = scenarios.get(it.scenario, 0) + 1
    n_events_est = len(plan.specs) * 8 + len(plan.specs) * 2
    return {
        "run_date": run_date.isoformat(),
        "window_start": window_start(run_date, cfg.generation.window_days).isoformat(),
        "preset_scale": cfg.generation.volume.scale,
        "total_traces": len(plan.specs),
        "ambient_traces": len(plan.ambient_specs),
        "sessions": len({s.session_id for s in plan.ambient_specs if s.session_id}),
        "multi_turn_sessions": len(plan.sessions),
        "golden_traces": len(plan.golden_specs),
        "curated_source_traces": len(plan.curated_specs),
        "flagged_pending_traces": len(plan.flagged_specs),
        "nightly_batch_traces": len(plan.batch_specs),
        "experiment_runs": len(cert.runs),
        "experiment_run_items": sum(len(r.items) for r in cert.runs),
        "estimated_events": n_events_est,
        "by_environment": by_env,
        "by_language": by_lang,
        "traces_with_error": errored,
        "suite": {"name": cfg.certification.dataset.name, "items": len(cert.suite),
                  "scenarios": scenarios,
                  "curated_from_production": sum(1 for it in cert.suite if it.curated)},
        "seeded_runs": [{"run": p.run_name, "model": p.model, "date": iso_date(p.run_date),
                         "verdict": p.verdict} for p in cert.runs],
        "queue": {"name": cfg.certification.queue.name,
                  "completed": len(plan.queue_completed_ids),
                  "pending": len(plan.queue_pending_ids)},
        "golden": [{"key": g.key, "title": g.title, "trace_id": g.trace_id}
                   for g in cert.golden],
        "incumbent_model": cfg.certification.incumbent_model,
        "candidate_a_model": cfg.certification.candidate_a_model,
        "candidate_b_model": cfg.certification.candidate_b_model,
    }
