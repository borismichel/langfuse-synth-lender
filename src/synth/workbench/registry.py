"""Deterministic evaluator / task **code registry** — "inject code according to the
Langfuse design" (the v4 run_experiment contracts).

Evaluators live as plain Python files in ``workbench_evaluators/`` (committed, one
file per check). The contract is the SDK evaluator signature:

    NAME = "my_check"
    REQUIREMENT_IDS = ["MRM-ACC-1"]            # traceability into the register
    def evaluator(*, input, output, expected_output, metadata=None, **kwargs):
        return Evaluation(name=NAME, value="pass"|"fail"|<float>, comment="why")

Tasks the same way in ``workbench_tasks/``:

    def task(item, *, model, lf, llm): -> dict   # what run_experiment executes
                                                  # (`llm` is a synth.llm.LLMClient)

Adding code through the workbench runs a three-stage acceptance pipeline:
``compile()`` → AST contract check (the function exists with the keyword-only
signature) → **smoke-run** against a sample suite item (expected vs. expected must
return an Evaluation without raising). Accepted code is written to disk and
SHA-256-fingerprinted; the fingerprints ride in every run's metadata, so the evidence
pack can state *exactly which check code* produced each verdict.

Demo-honest note: code executes in-process. A production deployment needs sandboxing
and code review — listed in the WORKBENCH.md roadmap.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..state import REPO_ROOT

EVALUATORS_DIR = REPO_ROOT / "workbench_evaluators"
TASKS_DIR = REPO_ROOT / "workbench_tasks"

EVALUATOR_TEMPLATE = '''\
"""Workbench evaluator — fill in NAME, REQUIREMENT_IDS and the check logic.

Contract: keyword-only signature, return a langfuse Evaluation (value may be
"pass"/"fail" for categorical or a float for numeric)."""
from langfuse import Evaluation

NAME = "my_check"
REQUIREMENT_IDS = []            # e.g. ["MRM-ACC-1"] — links into the requirements register


def evaluator(*, input, output, expected_output, metadata=None, **kwargs):
    # `output` is the task's returned dict; `expected_output` the dataset item's.
    ok = bool(output)
    return Evaluation(name=NAME, value="pass" if ok else "fail",
                      comment="describe exactly why this passed or failed")
'''


@dataclass
class Registered:
    name: str
    kind: str                      # evaluator | task
    path: Path
    sha256: str
    requirement_ids: list[str] = field(default_factory=list)
    fn: object = None
    source: str = ""
    error: str = ""


def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location(f"workbench_dyn_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _discover(directory: Path, kind: str, attr: str) -> list[Registered]:
    out: list[Registered] = []
    if not directory.exists():
        return out
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        source = path.read_text()
        sha = hashlib.sha256(source.encode()).hexdigest()
        try:
            mod = _load_module(path)
            fn = getattr(mod, attr)
            name = getattr(mod, "NAME", path.stem)
            reqs = list(getattr(mod, "REQUIREMENT_IDS", []))
            out.append(Registered(name=name, kind=kind, path=path, sha256=sha,
                                  requirement_ids=reqs, fn=fn, source=source))
        except Exception as exc:  # noqa: BLE001 — broken file: list it, don't crash the tool
            out.append(Registered(name=path.stem, kind=kind, path=path, sha256=sha,
                                  source=source, error=f"{type(exc).__name__}: {exc}"))
    return out


def discover_evaluators() -> list[Registered]:
    return _discover(EVALUATORS_DIR, "evaluator", "evaluator")


def discover_tasks() -> list[Registered]:
    return _discover(TASKS_DIR, "task", "task")


def evaluator_by_name(name: str) -> Registered | None:
    return next((r for r in discover_evaluators() if r.name == name and not r.error), None)


# ---------------------------------------------------------------------------
# Acceptance pipeline for injected code
# ---------------------------------------------------------------------------
def validate_evaluator_code(code: str) -> list[str]:
    """Static acceptance: compiles, defines NAME, and defines ``evaluator`` with the
    keyword-only SDK signature (input/output/expected_output + **kwargs)."""
    errors: list[str] = []
    try:
        tree = ast.parse(code)
        compile(code, "<workbench-evaluator>", "exec")
    except SyntaxError as exc:
        return [f"syntax error: {exc}"]
    fns = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "evaluator"]
    if not fns:
        errors.append("no `def evaluator(...)` found")
    else:
        fn = fns[0]
        kwonly = {a.arg for a in fn.args.kwonlyargs}
        for required in ("input", "output", "expected_output"):
            if required not in kwonly:
                errors.append(f"`evaluator` must take keyword-only argument `{required}`")
        if fn.args.kwarg is None:
            errors.append("`evaluator` must accept `**kwargs` (forward-compatibility)")
        if fn.args.args:
            errors.append("`evaluator` must be keyword-only (use `*,` before the arguments)")
    if not any(isinstance(n, ast.Assign) and any(
            getattr(t, "id", "") == "NAME" for t in n.targets) for n in ast.walk(tree)):
        errors.append("module must define `NAME = \"...\"`")
    return errors


def _sample_item(cfg) -> tuple[dict, dict]:
    """A deterministic (input, expected_output) pair for the smoke-run."""
    from ..agent import answer_deterministic
    from ..content import flagged_cases
    from ..rng import Rng

    case = flagged_cases(Rng(cfg.generation.seed))[0]
    return case.question.model_dump(), answer_deterministic(case.question).model_dump()


def smoke_run_evaluator(cfg, code: str) -> str | None:
    """Dynamic acceptance: expected-vs-expected must return an Evaluation-like object
    (has ``name``) without raising. Returns an error string, or None on success."""
    namespace: dict = {}
    try:
        exec(compile(code, "<workbench-evaluator>", "exec"), namespace)  # noqa: S102 — demo tool, documented
        fn = namespace["evaluator"]
        inp, expected = _sample_item(cfg)
        result = fn(input=inp, output=expected, expected_output=expected, metadata={})
        items = result if isinstance(result, list) else [result]
        for ev in items:
            if not getattr(ev, "name", None):
                return "evaluator must return langfuse Evaluation object(s) with a name"
    except Exception as exc:  # noqa: BLE001
        return f"smoke-run failed: {type(exc).__name__}: {exc}"
    return None


def save_evaluator(cfg, filename: str, code: str) -> tuple[Registered | None, list[str]]:
    """Full acceptance pipeline; on success writes the file and returns its registration."""
    errors = validate_evaluator_code(code)
    if errors:
        return None, errors
    smoke = smoke_run_evaluator(cfg, code)
    if smoke:
        return None, [smoke]
    stem = re.sub(r"[^a-z0-9_]+", "_", filename.lower().removesuffix(".py")).strip("_")
    if not stem or stem.startswith("_"):
        return None, ["invalid filename"]
    EVALUATORS_DIR.mkdir(exist_ok=True)
    path = EVALUATORS_DIR / f"{stem}.py"
    path.write_text(code if code.endswith("\n") else code + "\n")
    reg = next((r for r in discover_evaluators() if r.path == path), None)
    if reg and reg.error:
        return None, [reg.error]
    return reg, []


def fingerprints(names: list[str]) -> dict[str, str]:
    """name -> sha256 for the selected evaluators (recorded in run metadata)."""
    return {r.name: r.sha256 for r in discover_evaluators() if r.name in names}
