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
            evaluators=_run_evaluators(run))
        lf.flush()
        n_rl = seed_run_level_scores(cfg, run, log)
        rates = run_pass_rates(run)
        log(f"  · experiment run {run.run_name!r}: {len(run.items)} items "
            f"(numeric_lookup {rates.get('numeric_lookup', 1):.0%}, verdict {run.verdict}, "
            f"{n_rl} run-level scores)")
    return len(cert.runs)


def run_level_metrics(run: CertRunPlan) -> dict[str, float]:
    """Per-run AGGREGATE metrics, computed from the same grade() the per-item evaluators
    use — so the rollup can never disagree with the item cells. These are written as
    dataset-run-level scores (``datasetRunId``-linked) and render in the Experiments
    overview's "Experiment-Level Scores" column. Why we need them: the per-item scores
    aggregate columns in the comparison view are flaky on newer Langfuse (the "faster
    experiments" preview only surfaces a subset of identically-shaped item scores), so
    the headline deltas (groundedness 0.90/0.94/0.86, candidate B's numeric-accuracy
    miss) must live as explicit run-level scores to land in the narrative reliably."""
    from statistics import mean

    mu = run.groundedness_mu
    fig, cit, esc, gnd, cov = [], [], [], [], []
    for ri in run.items:
        checks = grade(ri.item.expected, ri.got)
        fig.append(1.0 if checks["figure_accuracy"][0] else 0.0)
        cit.append(1.0 if checks["citation_accuracy"][0] else 0.0)
        esc.append(1.0 if checks["abstention_correct"][0] else 0.0)
        ok = checks["grounded_ok"][0] and checks["figure_accuracy"][0]
        gnd.append(mu if ok else 0.45)
        cov.append(0.94 if checks["citation_accuracy"][0] else 0.4)
    return {
        "numeric_accuracy_rate": round(mean(fig), 3) if fig else 0.0,
        "citation_format_rate": round(mean(cit), 3) if cit else 0.0,
        "escalation_correctness_rate": round(mean(esc), 3) if esc else 0.0,
        "groundedness_mean": round(mean(gnd), 3) if gnd else 0.0,
        "citation_coverage_mean": round(mean(cov), 3) if cov else 0.0,
    }


def _run_level_score_id(run_id: str, name: str) -> str:
    """Deterministic UUID-shaped id so re-seeding upserts the run-level score in place."""
    import hashlib

    h = hashlib.blake2b(f"runlevel|{run_id}|{name}".encode(), digest_size=16).hexdigest()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def _resolve_run_id(cfg, run: CertRunPlan) -> str | None:
    """The SDK appends a ` - <timestamp>` suffix to the run name, so resolve the created
    run's id by name PREFIX (the runs-list endpoint is eventually consistent on Cloud —
    retry until it appears)."""
    import time

    base = cfg.target.base_url.rstrip("/")
    for _ in range(8):
        resp = requests.get(f"{base}/api/public/datasets/{cfg.certification.dataset.name}/runs",
                            params={"limit": 50}, auth=_auth(), timeout=20)
        if resp.status_code == 200:
            for r in resp.json().get("data", []):
                if (r.get("name") or "").startswith(run.run_name):
                    return r.get("id")
        time.sleep(4)
    return None


def seed_run_level_scores(cfg, run: CertRunPlan, log: Callable[[str], None] = print) -> int:
    """Write the per-run aggregate metrics as dataset-run-level scores via
    ``POST /api/public/scores`` (the score-create that accepts ``datasetRunId``; the
    batch-ingestion path silently drops run-level scores, and ``POST /v2/scores`` is
    405 — this REST route is the one that persists them). Idempotent on re-seed.
    Best-effort: a missing run id / older server is logged, never fatal."""
    import time

    run_id = _resolve_run_id(cfg, run)
    if not run_id:
        log(f"  · run-level scores: run id for {run.run_name!r} not found yet — skipped")
        return 0
    base = cfg.target.base_url.rstrip("/")
    delay = throttle_seconds(base)
    made = 0
    rows = [(k, float(v), "NUMERIC", f"run-level aggregate over {len(run.items)} items")
            for k, v in run_level_metrics(run).items()]
    rows.append(("verdict", run.verdict, "CATEGORICAL", "certification gate verdict"))
    for name, value, dtype, comment in rows:
        body = {"id": _run_level_score_id(run_id, name), "datasetRunId": run_id,
                "name": name, "value": value, "dataType": dtype, "comment": comment}
        resp = _post_retry(f"{base}/api/public/scores", body, _auth())
        if resp is not None and resp.status_code == 200:
            made += 1
        elif resp is not None:
            log(f"  · run-level score {name!r}: {resp.status_code} {resp.text[:100]}")
        if delay:
            time.sleep(delay)
    return made


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
