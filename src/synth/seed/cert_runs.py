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
from ..grading import grade, item_passes
from ..models import AnalystQuestion
from .certification import CertRunPlan


def _run_evaluators(run: CertRunPlan):
    """Code evaluators for one run, in the documented run_experiment shape: a LIST of
    evaluator functions, **each returning a single ``Evaluation``** (a single function
    returning a list is not accepted by the SDK — the run lands with zero items). Same
    score vocabulary as production; per-run judge means make the comparison deltas
    visible (baseline ~0.91, candidate A ~0.94, candidate B ~0.88). LLM-as-judge
    evaluators would slot into this same list identically."""
    from langfuse import Evaluation

    mu = run.groundedness_mu

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
        return Evaluation(name="groundedness", value=round(mu if ok else 0.45, 3))

    def citation_coverage(*, input, output, expected_output, metadata=None, **kwargs):
        ok = grade(expected_output, output)["citation_accuracy"][0]
        return Evaluation(name="citation_coverage", value=round(0.94 if ok else 0.4, 3))

    return [_det("figure_accuracy", "numeric_accuracy"),
            _det("citation_accuracy", "citation_format"),
            _det("abstention_correct", "escalation_correctness"),
            groundedness, citation_coverage]


def _seed_task(run: CertRunPlan, error_map: dict):
    """Deterministic, no-model task in the EXACT documented run_experiment shape:
    ``task(*, item, **kwargs)`` returns the output; run_experiment owns the item trace
    (do NOT create observations inside — that detaches the item trace and the run lands
    with zero items). Replays the templated answer with this run's injected error mode."""
    def task(*, item, **kwargs):
        q = AnalystQuestion.from_input(item.input)
        err = error_map.get(getattr(item, "id", None))
        return answer_deterministic(q, error_mode=err).model_dump()

    return task


def seed_experiment_runs(cfg, lf, cert, log: Callable[[str], None] = print) -> int:
    """Create baseline / candidate A / candidate B as SDK experiment runs (no model
    calls), using the documented run_experiment config that surfaces in the Experiments
    tab and supports both code and LLM-as-judge evaluators. Returns runs created."""
    cert_cfg = cfg.certification
    dataset = lf.get_dataset(cert_cfg.dataset.name)
    try:
        pver = getattr(lf.get_prompt(cert_cfg.prompt_name, label="production", type="chat",
                                     cache_ttl_seconds=0), "version", cert_cfg.production_version)
    except Exception:  # noqa: BLE001
        pver = cert_cfg.production_version

    for run in cert.runs:
        error_map = {it.item_id: it.run_errors.get(run.key) for it in cert.suite}
        dataset.run_experiment(
            name=run.run_name,
            description=run.description,
            metadata={"model": run.model, "verdict": run.verdict,
                      "release": f"{run.model}+{cert_cfg.prompt_name}.v{pver}", "seeded": True},
            task=_seed_task(run, error_map),
            evaluators=_run_evaluators(run),
            run_evaluators=_run_level_evaluators(run))
        lf.flush()
        rates = run_pass_rates(run)
        log(f"  · experiment run {run.run_name!r}: {len(run.items)} items + run-level aggregates "
            f"(numeric_lookup {rates.get('numeric_lookup', 1):.0%}, verdict {run.verdict})")
    return len(cert.runs)


def _run_level_evaluators(run: CertRunPlan):
    """Run-level evaluators in the documented ``run_experiment`` shape:
    ``fn(*, item_results, **kwargs)`` returning a single ``Evaluation`` that the SDK
    attaches to the **full dataset run** (not the items). These render in the
    Experiments overview's **"Experiment-Level Scores"** column.

    Why we need them: the per-ITEM score aggregate columns in the comparison view are
    unreliable on newer Langfuse (the "faster experiments" preview surfaces only a
    subset of identically-shaped item scores — we observed only ``citation_coverage``,
    hiding the ``groundedness``/``numeric_accuracy`` deltas). Run-level scores carry the
    headline deltas (groundedness 0.90/0.94/0.86, candidate B's numeric-accuracy miss)
    reliably. They are computed from ``item_results`` — i.e. the very item evaluations
    seeded above — so the rollup can never disagree with the cells.

    This is the SDK-blessed path (docs: Experiments via SDK → Run-level Evaluators),
    replacing an earlier manual ``POST /api/public/scores`` workaround."""
    from langfuse import Evaluation

    def _num_mean(item_name: str, out_name: str):
        def ev(*, item_results, **kwargs):
            vals = [e.value for r in item_results for e in (r.evaluations or [])
                    if e.name == item_name and isinstance(e.value, (int, float))]
            return Evaluation(name=out_name,
                              value=round(sum(vals) / len(vals), 3) if vals else None,
                              comment=f"mean over {len(vals)} items")
        ev.__name__ = out_name
        return ev

    def _pass_rate(item_name: str, out_name: str):
        def ev(*, item_results, **kwargs):
            flags = [1.0 if str(e.value) == "pass" else 0.0 for r in item_results
                     for e in (r.evaluations or []) if e.name == item_name]
            return Evaluation(name=out_name,
                              value=round(sum(flags) / len(flags), 3) if flags else None,
                              comment=f"pass rate over {len(flags)} items")
        ev.__name__ = out_name
        return ev

    def verdict(*, item_results, **kwargs):
        return Evaluation(name="verdict", value=run.verdict,
                          comment="certification gate verdict")

    return [_num_mean("groundedness", "groundedness_mean"),
            _num_mean("citation_coverage", "citation_coverage_mean"),
            _pass_rate("numeric_accuracy", "numeric_accuracy_rate"),
            _pass_rate("citation_format", "citation_format_rate"),
            _pass_rate("escalation_correctness", "escalation_correctness_rate"),
            verdict]


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

# Per-request throttle on the one-at-a-time REST writes (dataset-run-items, queue
# items). **Cloud only** — Langfuse Cloud rate-limits these endpoints, so spacing
# requests keeps a 200+-item seed under the limit instead of relying on backoff to
# dig out of a 429 storm. Self-hosted has no such limit, so no delay there.
CLOUD_POST_THROTTLE_S = 0.35


def throttle_seconds(base_url: str) -> float:
    return CLOUD_POST_THROTTLE_S if "cloud.langfuse.com" in (base_url or "") else 0.0


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
