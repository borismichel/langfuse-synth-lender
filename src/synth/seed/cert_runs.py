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
from ..grading import SCORE_NAME_FOR_CHECK, grade, item_passes
from ..models import AnalystQuestion
from .certification import CertRunPlan

# kind-aware deterministic checks per scenario (matches production score emission)
_EMIT = {"numeric_lookup": ("figure_accuracy", "citation_accuracy"),
         "trend": ("figure_accuracy", "citation_accuracy"),
         "covenant": ("figure_accuracy", "citation_accuracy"),
         "summary": ("citation_accuracy",),
         "out_of_scope": ("abstention_correct",)}


def _run_evaluators(run: CertRunPlan):
    """Code evaluators for one run — the same vocabulary as production, kind-aware,
    with per-run judge means so the comparison deltas are visible (baseline ~0.91,
    candidate A ~0.94, candidate B ~0.88)."""
    from langfuse import Evaluation

    mu = run.groundedness_mu

    def evaluator(*, input, output, expected_output, metadata=None, **kwargs):
        checks = grade(expected_output, output)
        scenario = (metadata or {}).get("scenario") or (metadata or {}).get("slice") \
            or "numeric_lookup"
        out = []
        for check in _EMIT.get(scenario, ("figure_accuracy",)):
            ok, detail = checks[check]
            out.append(Evaluation(name=SCORE_NAME_FOR_CHECK[check],
                                  value="pass" if ok else "fail",
                                  comment=None if ok else detail))
        grounded_ok = checks["grounded_ok"][0] and checks["figure_accuracy"][0]
        out.append(Evaluation(name="groundedness", value=round(mu if grounded_ok else 0.45, 3)))
        out.append(Evaluation(name="citation_coverage",
                              value=round(0.94 if checks["citation_accuracy"][0] else 0.4, 3)))
        return out

    return [evaluator]


def _seed_task(run: CertRunPlan, error_map: dict, lf, prompt, pver: int):
    """Deterministic, no-model task: replay the templated answer with this run's
    injected error mode, and log it as an ``answer`` generation (model + prompt link +
    natural-language chat input) so the experiment trace matches a live certify run."""
    from ..content import answer_messages

    def task(*args, **kwargs):
        item = kwargs.get("item") if "item" in kwargs else (args[0] if args else None)
        q = AnalystQuestion.from_input(item.input)
        err = error_map.get(getattr(item, "id", None))
        ans = answer_deterministic(q, error_mode=err)
        try:
            with lf.start_as_current_observation(
                as_type="generation", name="answer", model=run.model,
                input=answer_messages(_prompt_system(prompt, pver), q),
                model_parameters={"temperature": 0},
                prompt=prompt if prompt is not None else None) as gen:
                gen.update(output=ans.model_dump())
        except Exception:  # noqa: BLE001 — trace richness is best-effort; the run still records
            pass
        return ans.model_dump()

    return task


def _prompt_system(prompt, pver: int) -> str:
    from .prompts import prompt_text

    if prompt is not None:
        for m in (getattr(prompt, "prompt", None) or []):
            if m.get("role") == "system":
                return m.get("content", "")
    return prompt_text(pver)


def seed_experiment_runs(cfg, lf, cert, log: Callable[[str], None] = print) -> int:
    """Create baseline / candidate A / candidate B as SDK experiment runs (no model
    calls). Returns the number of runs created."""
    cert_cfg = cfg.certification
    dataset = lf.get_dataset(cert_cfg.dataset.name)
    try:
        prompt = lf.get_prompt(cert_cfg.prompt_name, label="production", type="chat",
                               cache_ttl_seconds=0)
        pver = getattr(prompt, "version", cert_cfg.production_version)
    except Exception:  # noqa: BLE001
        prompt, pver = None, cert_cfg.production_version

    for run in cert.runs:
        error_map = {it.item_id: it.run_errors.get(run.key) for it in cert.suite}
        dataset.run_experiment(
            name=run.run_name,
            description=run.description,
            metadata={"model": run.model, "verdict": run.verdict,
                      "release": f"{run.model}+{cert_cfg.prompt_name}.v{pver}", "seeded": True},
            task=_seed_task(run, error_map, lf, prompt, pver),
            evaluators=_run_evaluators(run))
        lf.flush()
        rates = run_pass_rates(run)
        log(f"  · experiment run {run.run_name!r}: {len(run.items)} items "
            f"(numeric_lookup {rates.get('numeric_lookup', 1):.0%}, verdict {run.verdict})")
    return len(cert.runs)


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
