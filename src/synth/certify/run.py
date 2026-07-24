"""The button (spec v2): ``synth certify --model <id>``.

Runs a candidate model against the hosted **certification-suite** via the v4
``run_experiment`` API — the same live pipeline the seeded baseline/candidate runs
represent. The task is the shared agent function (``answer(item.input, model,
live=True)``) with the ``production``-labelled analyst-copilot prompt; the
deterministic assertions ride along as code evaluators under the same score names as
everywhere else (numeric_accuracy / citation_format / escalation_correctness); the
managed judges score the new run automatically.

``--gate`` applies the per-scenario thresholds from config and exits non-zero.
``--offline`` is the no-model smoke check (suite self-consistency).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..agent import answer, answer_deterministic
from ..config import Config
from ..grading import SCORE_NAME_FOR_CHECK, grade, item_passes


@dataclass
class CertifyResult:
    dataset_name: str
    run_name: str
    n_items: int = 0
    n_passed: int = 0
    by_scenario: dict = field(default_factory=dict)   # scenario -> {n, passed}
    failures: list[dict] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        return self.n_passed / self.n_items if self.n_items else 0.0


def _evaluators():
    from langfuse import Evaluation

    def _make(check: str, score_name: str):
        def evaluator(*, input, output, expected_output, metadata=None, **kwargs):
            ok, detail = grade(expected_output, output)[check]
            return Evaluation(name=score_name, value="pass" if ok else "fail", comment=detail)

        evaluator.__name__ = score_name
        return evaluator

    return [_make(check, name) for check, name in SCORE_NAME_FOR_CHECK.items()]


def certify(cfg: Config, model: str, *, run_name: str | None = None,
            log: Callable[[str], None] = print) -> CertifyResult:
    from langfuse_synth_core.lfclient import get_langfuse
    from ..llm import get_llm

    cert = cfg.certification
    lf = get_langfuse(cfg)
    llm = get_llm(model)
    model = llm.model  # the model actually resolved for the selected provider

    prompt = lf.get_prompt(cert.prompt_name, label="production", type="chat", cache_ttl_seconds=0)
    pver = getattr(prompt, "version", "?")
    name = run_name or f"cert-{model}-live"
    ds_name = cert.dataset.name
    dataset = lf.get_dataset(ds_name)
    log(f"· certifying {model} against {ds_name!r} as {name!r} "
        f"({cert.prompt_name} v{pver}, temperature 0) …")

    def task(*args, **kwargs):
        item = kwargs.get("item") if "item" in kwargs else (args[0] if args else None)
        got = answer(item.input, model, live=True, lf=lf, llm=llm,
                     prompt_name=cert.prompt_name)
        return got.model_dump()

    res = dataset.run_experiment(
        name=name,
        description=(f"Live certification run: model={model}, prompt={cert.prompt_name} "
                     f"v{pver}, temperature 0. Release = (model, prompt, params)."),
        metadata={"model": model, "prompt_version": pver,
                  "release": f"{model}+{cert.prompt_name}.v{pver}"},
        task=task,
        evaluators=_evaluators(),
    )
    log(res.format())
    lf.flush()

    out = CertifyResult(dataset_name=ds_name, run_name=name)
    for ir in getattr(res, "item_results", []) or []:
        item = getattr(ir, "item", None)
        output = getattr(ir, "output", None)
        if item is None or output is None:
            continue
        meta = getattr(item, "metadata", None) or {}
        scenario = meta.get("scenario") or meta.get("slice") or "numeric_lookup"
        ok, detail = item_passes(scenario, getattr(item, "expected_output", None) or {}, output)
        out.n_items += 1
        out.n_passed += 1 if ok else 0
        b = out.by_scenario.setdefault(scenario, {"n": 0, "passed": 0})
        b["n"] += 1
        b["passed"] += 1 if ok else 0
        if not ok:
            out.failures.append({"scenario": scenario, "detail": detail})
    _persist(cfg, model, out, pver)
    return out


def _persist(cfg: Config, model: str, result: CertifyResult, prompt_version) -> None:
    from ..state import RunState
    from ..timegen import iso_date, now_utc

    if not RunState.exists():
        return
    state = RunState.load()
    suite_state = state.suites.setdefault(
        "certification_suite", {"name": result.dataset_name, "runs": {}})
    ok, _scen = apply_gate_result(cfg, result)
    suite_state.setdefault("runs", {})[result.run_name] = {
        "model": model, "verdict": "live",
        "gate_ok": ok,
        "pass_rates": {k: round(v["passed"] / v["n"], 4)
                       for k, v in result.by_scenario.items() if v["n"]},
        "prompt_version": prompt_version,
        "date": iso_date(now_utc()),
    }
    state.save()


def apply_gate_result(cfg: Config, result: CertifyResult) -> tuple[bool, dict]:
    detail = {}
    ok_all = True
    for scenario, scfg in cfg.certification.dataset.scenarios.items():
        b = result.by_scenario.get(scenario, {"n": 0, "passed": 0})
        rate = b["passed"] / b["n"] if b["n"] else 1.0
        ok = rate >= scfg.gate
        ok_all = ok_all and ok
        detail[scenario] = {"rate": rate, "gate": scfg.gate, "ok": ok}
    return ok_all, detail


def apply_gate(cfg: Config, result: CertifyResult,
               log: Callable[[str], None] = print) -> bool:
    ok_all, detail = apply_gate_result(cfg, result)
    for scenario, d in detail.items():
        log(f"  [{'PASS' if d['ok'] else 'FAIL'}] {scenario}: {d['rate']:.1%} vs ≥{d['gate']:.0%}")
    for f in result.failures:
        log(f"        ✗ {f['scenario']}: {f['detail']}")
    log(f"\nGATE: {'CERTIFIED' if ok_all else 'REJECTED'}")
    return ok_all


def offline_check(cfg: Config, log: Callable[[str], None] = print) -> bool:
    """No-model CI smoke: every suite item's reference answer must pass its own
    scenario check (catches a drifted corpus before a demo)."""
    from ..models import AnalystQuestion
    from langfuse_synth_core.rng import Rng
    from ..seed.certification import build_suite

    suite = build_suite(cfg, Rng(cfg.generation.seed))
    bad_by_scenario: dict[str, int] = {}
    for it in suite:
        got = answer_deterministic(AnalystQuestion.from_input(it.question.model_dump()))
        ok, _ = item_passes(it.scenario, it.expected, got)
        if not ok:
            bad_by_scenario[it.scenario] = bad_by_scenario.get(it.scenario, 0) + 1
    for scenario, cfg_s in cfg.certification.dataset.scenarios.items():
        bad = bad_by_scenario.get(scenario, 0)
        n = cfg_s.n_items
        log(f"  [{'PASS' if bad == 0 else 'FAIL'}] {scenario}: {n - bad}/{n} self-consistent")
    return not bad_by_scenario
