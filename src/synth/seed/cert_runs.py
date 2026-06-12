"""Seeded experiment runs (spec v2 §5): baseline / candidate A / candidate B against
the one certification-suite, landing as real backdated **Dataset Runs**.

Mechanics, in order:
1. the runs' traces are templated and batch-ingested backdated (generator + traces.py);
2. this module grades every item with the SAME deterministic grader the live runner
   uses and spools the assertion scores — plus procedurally-assigned judge scores
   (per-run groundedness means make the comparison deltas visible: baseline 0.91,
   candidate A 0.94, candidate B 0.88) — onto the run traces;
3. after import, ``create_run_items`` POSTs ``/api/public/dataset-run-items`` with a
   backdated ``createdAt``.

Score names are the same vocabulary as production traces (numeric_accuracy /
citation_format / escalation_correctness / groundedness / citation_coverage), so the
scores surface co-filters across both (spec v2 checklist row 5).
"""
from __future__ import annotations

import os
from typing import Callable

import requests

from ..grading import SCORE_NAME_FOR_CHECK, grade, item_passes
from ..rng import Rng
from ..timegen import iso
from .certification import CertRunPlan


def run_score_events(rng: Rng, run: CertRunPlan):
    from .events import score_event
    from .scores import _skewed

    for ri in run.items:
        checks = grade(ri.item.expected, ri.got)
        s = rng.sub("runscore", run.key, ri.item.item_id)
        # deterministic assertions (kind-aware: numeric checks on numeric scenarios etc.)
        emit = {"numeric_lookup": ("figure_accuracy", "citation_accuracy"),
                "trend": ("figure_accuracy", "citation_accuracy"),
                "covenant": ("figure_accuracy", "citation_accuracy"),
                "summary": ("citation_accuracy",),
                "out_of_scope": ("abstention_correct",)}[ri.item.scenario]
        for check in emit:
            ok, detail = checks[check]
            yield score_event(
                score_id=s.score_id(check, ri.trace_id), name=SCORE_NAME_FOR_CHECK[check],
                value="pass" if ok else "fail", data_type="CATEGORICAL",
                timestamp=ri.timestamp, trace_id=ri.trace_id, environment="default",
                comment=None if ok else detail)
        # judge scores, procedurally assigned per run (visible aggregate deltas)
        grounded_ok = checks["grounded_ok"][0] and checks["figure_accuracy"][0]
        gmu = run.groundedness_mu if grounded_ok else 0.45
        yield score_event(score_id=s.score_id("ground", ri.trace_id), name="groundedness",
                          value=_skewed(s, gmu, lo=0.3), data_type="NUMERIC",
                          timestamp=ri.timestamp, trace_id=ri.trace_id, environment="default")
        cmu = 0.94 if checks["citation_accuracy"][0] else 0.4
        yield score_event(score_id=s.score_id("citcov", ri.trace_id), name="citation_coverage",
                          value=_skewed(s, cmu, lo=0.2), data_type="NUMERIC",
                          timestamp=ri.timestamp, trace_id=ri.trace_id, environment="default")


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


def create_run_items(base_url: str, runs: list[CertRunPlan],
                     log: Callable[[str], None] = print) -> int:
    """POST one dataset-run-item per (run, item), backdated via ``createdAt``."""
    created = 0
    for run in runs:
        for ri in run.items:
            body = {
                "runName": run.run_name,
                "runDescription": run.description,
                "datasetItemId": ri.item.item_id,
                "traceId": ri.trace_id,
                "createdAt": iso(ri.timestamp),
                "metadata": {"model": run.model, "verdict": run.verdict,
                             "release": f"{run.model}+analyst-copilot", "seeded": True},
            }
            resp = requests.post(f"{base_url.rstrip('/')}/api/public/dataset-run-items",
                                 json=body, auth=_auth(), timeout=20)
            if resp.status_code in (200, 201):
                created += 1
                continue
            if resp.status_code in (400, 409) and "exist" in resp.text.lower():
                continue
            resp.raise_for_status()
        rates = run_pass_rates(run)
        log(f"  · run {run.run_name!r}: items linked "
            f"(numeric_lookup {rates.get('numeric_lookup', 1):.0%})")
    return created
