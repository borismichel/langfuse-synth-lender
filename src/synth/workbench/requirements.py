"""Requirements register + coverage matrix — requirement traceability for MRM.

Links three things: the register (``workbench_requirements.yaml``), the suite items
(``metadata.requirement_ids``, derivable offline from the deterministic plan), and the
deterministic evaluators (``REQUIREMENT_IDS`` in their code). The coverage matrix
answers the auditor's first question — *"which requirements does this suite actually
test, and which are uncovered?"* — and the demo's seeded register deliberately
contains uncovered rows (adversarial robustness; fairness-of-decisioning boundary) so
the matrix visibly finds gaps rather than rubber-stamping.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import yaml

from ..config import Config
from ..state import REPO_ROOT
from .catalog import Catalog
from .registry import discover_evaluators

REGISTER_PATH = REPO_ROOT / "workbench_requirements.yaml"


@dataclass
class Requirement:
    id: str
    title: str
    source: str = ""
    description: str = ""


@dataclass
class Coverage:
    requirement: Requirement
    items: list[dict] = field(default_factory=list)       # {dataset, item_id, slice}
    evaluators: list[str] = field(default_factory=list)
    covered: bool = False
    latest: dict = field(default_factory=dict)            # {run_id, n, passed, rate} from results


def load_register() -> list[Requirement]:
    if not REGISTER_PATH.exists():
        return []
    rows = yaml.safe_load(REGISTER_PATH.read_text()) or []
    return [Requirement(id=r["id"], title=r.get("title", ""), source=r.get("source", ""),
                        description=r.get("description", "")) for r in rows]


def coverage_matrix(cfg: Config, catalog: Catalog) -> list[Coverage]:
    register = load_register()
    evals = [r for r in discover_evaluators() if not r.error]

    items_by_req: dict[str, list[dict]] = {}
    for ds in catalog.datasets:
        for it in ds.items:
            meta = it.get("metadata") or {}
            for rid in meta.get("requirement_ids") or []:
                items_by_req.setdefault(rid, []).append(
                    {"dataset": ds.name, "item_id": it.get("id", ""),
                     "slice": meta.get("slice", "")})

    latest_by_req = _latest_outcomes(cfg, items_by_req)

    out = []
    for req in register:
        cov = Coverage(requirement=req,
                       items=items_by_req.get(req.id, []),
                       evaluators=[e.name for e in evals if req.id in e.requirement_ids],
                       latest=latest_by_req.get(req.id, {}))
        cov.covered = bool(cov.items)
        out.append(cov)
    return out


def _latest_outcomes(cfg: Config, items_by_req: dict[str, list[dict]]) -> dict[str, dict]:
    """For each requirement: the latest workbench run's pass rate over its items."""
    from .results import list_runs

    runs = list_runs(cfg)
    done = next((r for r in runs if r.state == "done"), None)
    if done is None:
        return {}
    by_item = {(row["dataset"], row["item_id"]): row["passed"] for row in done.rows}
    out = {}
    for rid, items in items_by_req.items():
        hits = [(it["dataset"], it["item_id"]) for it in items
                if (it["dataset"], it["item_id"]) in by_item]
        if not hits:
            continue
        passed = sum(1 for h in hits if by_item[h])
        out[rid] = {"run_id": done.run_id, "n": len(hits), "passed": passed,
                    "rate": passed / len(hits)}
    return out
