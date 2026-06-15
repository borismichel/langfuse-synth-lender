"""Seeded experiment runs (spec v2 §5): baseline / candidate A / candidate B against
the one certification-suite, created via the **SDK ``run_experiment`` path**.

IMPORTANT (cloud-vs-v3 discrepancy, found 2026-06-13): the legacy REST
``POST /api/public/dataset-run-items`` endpoint creates dataset runs that the
**Experiments tab does NOT surface on newer Langfuse (≥ v3.185, incl. Cloud)** — the
runs exist via the runs API but the comparison grid is empty. Only runs created
through ``run_experiment`` register in that view. Older self-hosted (v3.179) showed
the REST runs fine — hence the discrepancy. So we seed the baseline/A/B runs the
blessed way: ``run_experiment`` with a **deterministic, no-model task** (replaying the
templated answers + each run's injected error modes) and code evaluators. Identical
mechanism to live ``synth certify`` — only the task differs (deterministic vs live
model). Run traces are timestamped at seed time (experiment runs are "recent" events;
the 30-day backdating applies to the production caseload, not the runs).

Score names are the same vocabulary as production traces (numeric_accuracy /
citation_format / escalation_correctness / groundedness / citation_coverage), so the
scores surface co-filters across both (spec v2 checklist row 5).
"""
from __future__ import annotations

import os
from typing import Callable

import requests

from ..agent import answer_deterministic
from ..content import answer_messages
from ..grading import grade, item_passes
from ..models import AnalystQuestion
from .certification import CertRunPlan
from .prompts import prompt_text


def _run_evaluators(run: CertRunPlan):
    """Code evaluators for one run, in the documented run_experiment shape: a LIST of
    evaluator functions, **each returning a single ``Evaluation``** (a single function
    returning a list is not accepted by the SDK — the run lands with zero items). Same
    score vocabulary as production. LLM-as-judge evaluators would slot into this list
    identically.

    The numeric judge scores (groundedness, citation_coverage) get **deterministic
    per-item variation** — a shared per-item difficulty (some questions are harder for
    every model) plus small per-run noise — instead of a flat per-run constant. Without
    it the comparison grid shows an identical value for every item, which reads as fake.
    The spread is centered on the run's mean, so the run-level ``mean_*`` rollups stay
    ≈ baseline 0.90 / candidate A 0.94 / candidate B 0.86 and the gate (categorical
    checks) is unaffected."""
    import hashlib

    from langfuse import Evaluation

    mu = run.groundedness_mu

    def _u(*parts: object) -> float:
        """Deterministic uniform [0,1) from the parts (stable across seeds/hosts)."""
        s = "|".join(str(p) for p in parts)
        return int.from_bytes(hashlib.blake2b(s.encode(), digest_size=6).digest(), "big") / (1 << 48)

    def _around(base: float, inp: object, tag: str, spread: float, lo: float, hi: float) -> float:
        # shared per-item difficulty (same across runs) + smaller per-run noise, centered ~0
        diff = (_u(inp, "diff", tag) - 0.5) * spread
        noise = (_u(inp, mu, tag) - 0.5) * (spread * 0.5)
        return round(min(hi, max(lo, base + diff + noise)), 3)

    def _det(check: str, score_name: str):
        def evaluator(*, input, output, expected_output, metadata=None, **kwargs):
            ok, detail = grade(expected_output, output)[check]
            return Evaluation(name=score_name, value="pass" if ok else "fail",
                              comment=None if ok else detail)
        evaluator.__name__ = score_name
        return evaluator

    def groundedness(*, input, output, expected_output, metadata=None, **kwargs):
        checks = grade(expected_output, output)
        ok = checks["grounded_ok"][0] and checks["figure_accuracy"][0]
        value = (_around(mu, input, "g", 0.12, 0.6, 0.99) if ok
                 else round(0.40 + _u(input, mu, "gf") * 0.12, 3))
        return Evaluation(name="groundedness", value=value)

    def citation_coverage(*, input, output, expected_output, metadata=None, **kwargs):
        ok = grade(expected_output, output)["citation_accuracy"][0]
        value = (_around(0.93, input, "c", 0.10, 0.75, 0.99) if ok
                 else round(0.30 + _u(input, mu, "cf") * 0.15, 3))
        return Evaluation(name="citation_coverage", value=value)

    return [_det("figure_accuracy", "numeric_accuracy"),
            _det("citation_accuracy", "citation_format"),
            _det("abstention_correct", "escalation_correctness"),
            groundedness, citation_coverage]


def _seed_task(lf, run: CertRunPlan, error_map: dict, by_id: dict, prompt_obj, pver: int):
    """Deterministic, no-model task in the documented ``run_experiment`` shape.

    On SDK v4 the task runs INSIDE the item's ``experiment-item-run`` span, so a
    generation created here nests under that item trace — it does NOT detach it (the
    "create zero observations" rule was a v3 limitation, found 2026-06-13; v4 changed the
    model). We emit one prompt-linked ``answer`` generation per item so every run item
    references the production prompt (``analyst-copilot`` v{pver}) and carries a real
    token/cost column — same vocabulary and shape as the production traces. Still no model
    call: the answer + usage/cost are precomputed deterministically in ``build_runs``."""
    def task(*, item, **kwargs):
        iid = getattr(item, "id", None)
        ri = by_id.get(iid)
        if ri is not None:                       # the precomputed, error-injected answer
            q, ans = ri.item.question, ri.got
        else:                                    # fallback: structured Q from metadata
            meta = getattr(item, "metadata", None) or {}
            q = AnalystQuestion.from_input(meta.get("analyst_question") or item.input)
            ans = answer_deterministic(q, error_mode=error_map.get(iid))
        with lf.start_as_current_observation(
            as_type="generation", name="answer", model=run.model,
            input=answer_messages(prompt_text(pver), q, []), output=ans.model_dump(),
            usage_details=(ri.usage if ri else None),
            cost_details=(ri.cost if ri else None),
            model_parameters={"temperature": 0}, prompt=prompt_obj):
            pass
        return ans.model_dump()

    return task


def seed_experiment_runs(cfg, lf, cert, log: Callable[[str], None] = print) -> int:
    """Create baseline / candidate A / candidate B as SDK experiment runs (no model
    calls), using the documented run_experiment config that surfaces in the Experiments
    tab and supports both code and LLM-as-judge evaluators. Returns runs created."""
    cert_cfg = cfg.certification
    dataset = lf.get_dataset(cert_cfg.dataset.name)
    prompt_obj = None
    try:
        prompt_obj = lf.get_prompt(cert_cfg.prompt_name, label="production", type="chat",
                                   cache_ttl_seconds=0)
        pver = getattr(prompt_obj, "version", cert_cfg.production_version)
    except Exception:  # noqa: BLE001
        pver = cert_cfg.production_version

    for run in cert.runs:
        error_map = {it.item_id: it.run_errors.get(run.key) for it in cert.suite}
        by_id = {ri.item.item_id: ri for ri in run.items}
        dataset.run_experiment(
            name=run.run_name,
            description=run.description,
            metadata={"model": run.model, "verdict": run.verdict,
                      "release": f"{run.model}+{cert_cfg.prompt_name}.v{pver}", "seeded": True},
            task=_seed_task(lf, run, error_map, by_id, prompt_obj, pver),
            evaluators=_run_evaluators(run),
            run_evaluators=_run_level_evaluators(run))
        lf.flush()
        rates = run_pass_rates(run)
        log(f"  · experiment run {run.run_name!r}: {len(run.items)} items + run-level aggregates "
            f"(numeric_lookup {rates.get('numeric_lookup', 1):.0%}, verdict {run.verdict})")
    return len(cert.runs)


# Run-LEVEL aggregate score names. Distinct ``mean_`` / ``rate_`` prefixes so they read
# clearly as per-run rollups, sort/truncate unambiguously in the UI (the old
# ``citation_coverage_mean`` / ``citation_format_rate`` both truncated to "citation…"),
# and never CLASH with the per-item (observation) score names (``groundedness``,
# ``citation_coverage``, ``numeric_accuracy``, …). Langfuse shows them under the
# "Experiment: …" / Experiment-Level Scores column.
RUN_LEVEL_SCORES = {
    "mean_groundedness": ("groundedness", "mean"),
    "mean_citation_coverage": ("citation_coverage", "mean"),
    "rate_numeric_accuracy": ("numeric_accuracy", "rate"),
    "rate_citation_format": ("citation_format", "rate"),
    "rate_escalation_correctness": ("escalation_correctness", "rate"),
}


def _run_level_evaluators(run: CertRunPlan):
    """Run-level evaluators (documented ``run_experiment`` shape:
    ``fn(*, item_results, **kwargs)`` returning one ``Evaluation`` attached to the FULL
    dataset run — the Experiment-Level Scores column). They give an at-a-glance per-run
    rollup of the headline deltas (candidate B's numeric miss, the groundedness spread)
    next to the per-item score columns. Computed from ``item_results`` — the very item
    evaluations seeded above — so the rollup can't disagree with the cells. Names use
    ``mean_`` / ``rate_`` prefixes (see RUN_LEVEL_SCORES) to stay clear and clash-free."""
    from langfuse import Evaluation

    def _agg(out_name: str, item_name: str, kind: str):
        def ev(*, item_results, **kwargs):
            evals = [e for r in item_results for e in (r.evaluations or []) if e.name == item_name]
            if kind == "mean":
                vals = [e.value for e in evals if isinstance(e.value, (int, float))]
                v = round(sum(vals) / len(vals), 3) if vals else None
                comment = f"mean over {len(vals)} items"
            else:  # pass rate over a categorical pass/fail item score
                flags = [1.0 if str(e.value) == "pass" else 0.0 for e in evals]
                v = round(sum(flags) / len(flags), 3) if flags else None
                comment = f"pass rate over {len(flags)} items"
            return Evaluation(name=out_name, value=v, comment=comment)
        ev.__name__ = out_name
        return ev

    def verdict(*, item_results, **kwargs):
        return Evaluation(name="verdict", value=run.verdict, comment="certification gate verdict")

    return [_agg(out, item, kind) for out, (item, kind) in RUN_LEVEL_SCORES.items()] + [verdict]


def run_pass_rates(run: CertRunPlan) -> dict[str, float]:
    """Per-scenario pass rates on the run's own gate checks (for state/DEMO_MAP)."""
    by_scenario: dict[str, list[bool]] = {}
    for ri in run.items:
        ok, _ = item_passes(ri.item.scenario, ri.item.expected, ri.got)
        by_scenario.setdefault(ri.item.scenario, []).append(ok)
    return {k: sum(v) / len(v) for k, v in by_scenario.items() if v}


def run_gate_verdict(cfg, run: CertRunPlan) -> tuple[bool, dict]:
    rates = run_pass_rates(run)
    detail = {}
    ok_all = True
    for scenario, scfg in cfg.certification.dataset.scenarios.items():
        rate = rates.get(scenario, 1.0)
        ok = rate >= scfg.gate
        ok_all = ok_all and ok
        detail[scenario] = {"rate": round(rate, 4), "gate": scfg.gate, "ok": ok}
    return ok_all, detail


def _auth():
    return (os.environ.get("LANGFUSE_PUBLIC_KEY", ""), os.environ.get("LANGFUSE_SECRET_KEY", ""))


# Per-request throttle on the one-at-a-time REST writes (dataset-run / queue items) lives
# in the central target profile (synth.target) — the ONE place that decides
# target-specific behaviour, so a clone never re-adds scattered URL checks.


def _post_retry(url: str, body: dict, auth, *, attempts: int = 8, timeout: int = 30):
    """POST with patient backoff that honours ``Retry-After`` — a transient blip or a
    Cloud rate-limit must not abort a seed (the batch ingestor already retries; the
    per-object REST writes need it too)."""
    import time

    backoff = 2.0
    for attempt in range(1, attempts + 1):
        try:
            resp = requests.post(url, json=body, auth=auth, timeout=timeout)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < attempts:
                wait = backoff
                if resp.status_code == 429:
                    try:
                        wait = max(wait, float(resp.headers.get("Retry-After", 0)))
                    except (TypeError, ValueError):
                        pass
                time.sleep(min(wait, 60))
                backoff = min(backoff * 2, 60)
                continue
            return resp
        except requests.RequestException:
            if attempt == attempts:
                raise
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)


# NOTE: the legacy REST ``create_run_items`` (POST /api/public/dataset-run-items) was
# removed — those runs don't surface in the Experiments tab on newer Langfuse. Runs are
# now created via ``seed_experiment_runs`` (SDK run_experiment) above. ``_post_retry`` /
# ``throttle_seconds`` remain — the annotation-queue writes (still REST) reuse them.
