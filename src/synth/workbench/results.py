"""Workbench results store — structured, filterable, comparable.

Langfuse remains the system of record (the runs land there as Dataset Runs with
scores); this store is the tool's own structured index of the same results so the
workbench can filter, aggregate, gate, and diff without round-tripping the API:
one JSON file per run under ``.workbench/runs/``.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..config import Config
from ..state import REPO_ROOT


@dataclass
class ItemRow:
    dataset: str
    item_id: str
    slice: str
    passed: bool
    scores: dict          # name -> {"value": ..., "comment": ...}
    trace_id: str = ""
    trace_url: str = ""
    detail: str = ""      # the gate-check comment for failures


@dataclass
class GateVerdict:
    dataset: str
    threshold: float
    pass_rate: float
    ok: bool
    slice_detail: dict = field(default_factory=dict)  # slice -> {"rate":, "threshold":, "ok":}


@dataclass
class WorkbenchRun:
    run_id: str
    spec_ref: str
    spec_hash: str
    spec: dict
    release: dict
    evaluator_shas: dict
    started: str
    finished: str = ""
    state: str = "running"          # running | done | error
    error: str = ""
    rows: list = field(default_factory=list)        # ItemRow dicts
    gates: list = field(default_factory=list)       # GateVerdict dicts
    langfuse_runs: list = field(default_factory=list)  # [{dataset, run_name}]
    signoff: dict = field(default_factory=dict)     # {by, role, note, at}

    @property
    def ok(self) -> bool:
        return self.state == "done" and all(g.get("ok") for g in self.gates)


def runs_dir(cfg: Config) -> Path:
    return REPO_ROOT / cfg.workbench.results_dir / "runs"


def save_run(cfg: Config, run: WorkbenchRun) -> None:
    d = runs_dir(cfg)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{run.run_id}.json").write_text(json.dumps(asdict(run), indent=2))


def load_run(cfg: Config, run_id: str) -> WorkbenchRun | None:
    if not re.fullmatch(r"[a-z0-9\-]+", run_id or ""):
        return None
    path = runs_dir(cfg) / f"{run_id}.json"
    if not path.exists():
        return None
    return WorkbenchRun(**json.loads(path.read_text()))


def list_runs(cfg: Config) -> list[WorkbenchRun]:
    d = runs_dir(cfg)
    if not d.exists():
        return []
    out = []
    for path in sorted(d.glob("*.json"), reverse=True):
        try:
            out.append(WorkbenchRun(**json.loads(path.read_text())))
        except Exception:  # noqa: BLE001
            continue
    out.sort(key=lambda r: r.started, reverse=True)
    return out


# ---------------------------------------------------------------------------
# Filtering / aggregation / compare
# ---------------------------------------------------------------------------
def filter_rows(run: WorkbenchRun, *, dataset: str = "", slice_name: str = "",
                verdict: str = "", evaluator: str = "") -> list[dict]:
    """In-memory filters driving the results table (all params optional).
    ``verdict``: pass|fail on the gate check; ``evaluator``: only rows where that
    evaluator failed."""
    rows = run.rows
    if dataset:
        rows = [r for r in rows if r["dataset"] == dataset]
    if slice_name:
        rows = [r for r in rows if r["slice"] == slice_name]
    if verdict == "pass":
        rows = [r for r in rows if r["passed"]]
    elif verdict == "fail":
        rows = [r for r in rows if not r["passed"]]
    if evaluator:
        rows = [r for r in rows
                if str((r["scores"].get(evaluator) or {}).get("value")) in ("fail", "0", "0.0")]
    return rows


def aggregates(run: WorkbenchRun) -> dict:
    """dataset -> {n, passed, rate, slices: {slice: {n, passed, rate}}}."""
    out: dict = {}
    for r in run.rows:
        d = out.setdefault(r["dataset"], {"n": 0, "passed": 0, "slices": {}})
        d["n"] += 1
        d["passed"] += 1 if r["passed"] else 0
        s = d["slices"].setdefault(r["slice"], {"n": 0, "passed": 0})
        s["n"] += 1
        s["passed"] += 1 if r["passed"] else 0
    for d in out.values():
        d["rate"] = d["passed"] / d["n"] if d["n"] else 0.0
        for s in d["slices"].values():
            s["rate"] = s["passed"] / s["n"] if s["n"] else 0.0
    return out


def gate_verdicts(run_rows: list[dict], spec: dict) -> list[dict]:
    """Apply the spec's gates to the captured rows."""
    gates = spec.get("gates") or {}
    threshold = gates.get("threshold", 0.95)
    overrides = gates.get("slice_overrides") or {}
    verdicts = []
    by_dataset: dict[str, list[dict]] = {}
    for r in run_rows:
        by_dataset.setdefault(r["dataset"], []).append(r)
    for ds, rows in by_dataset.items():
        rate = sum(1 for r in rows if r["passed"]) / len(rows) if rows else 0.0
        ok = rate >= threshold
        slice_detail = {}
        for sl, th in overrides.items():
            srows = [r for r in rows if r["slice"] == sl]
            if not srows:
                continue
            srate = sum(1 for r in srows if r["passed"]) / len(srows)
            sok = srate >= th
            ok = ok and sok
            slice_detail[sl] = {"rate": srate, "threshold": th, "ok": sok}
        verdicts.append(asdict(GateVerdict(dataset=ds, threshold=threshold,
                                           pass_rate=rate, ok=ok,
                                           slice_detail=slice_detail)))
    return verdicts


def compare(run_a: WorkbenchRun, run_b: WorkbenchRun) -> list[dict]:
    """Per-item alignment by (dataset, item_id) — e.g. baseline vs candidate."""
    bya = {(r["dataset"], r["item_id"]): r for r in run_a.rows}
    byb = {(r["dataset"], r["item_id"]): r for r in run_b.rows}
    out = []
    for key in sorted(set(bya) | set(byb)):
        a, b = bya.get(key), byb.get(key)
        out.append({
            "dataset": key[0], "item_id": key[1],
            "slice": (a or b)["slice"],
            "a": a, "b": b,
            "delta": ("=" if (a and b and a["passed"] == b["passed"]) else
                      "improved" if (b and b["passed"] and (not a or not a["passed"])) else
                      "regressed" if (a and a["passed"] and (not b or not b["passed"])) else "?"),
        })
    return out
