"""Pull the building blocks from Langfuse: prompts, datasets (+items, slice rollup),
score configs, managed evaluators, and evaluation rules.

Everything degrades gracefully: if the instance is unreachable (or the unstable
evaluator endpoints don't exist on this server version), the workbench still renders —
the catalog falls back to what ``.synth_state.json`` and the deterministic plan know
(demo resilience; flagged in the UI as "offline catalog").
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import requests

from ..config import Config
from ..state import RunState


def _auth():
    return (os.environ.get("LANGFUSE_PUBLIC_KEY", ""), os.environ.get("LANGFUSE_SECRET_KEY", ""))


def _get(base: str, path: str, params: dict | None = None, timeout: int = 12) -> dict:
    resp = requests.get(f"{base.rstrip('/')}{path}", params=params or {}, auth=_auth(),
                        timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _paged(base: str, path: str, params: dict | None = None, max_pages: int = 10) -> list[dict]:
    rows: list[dict] = []
    page = 1
    while page <= max_pages:
        data = _get(base, path, {**(params or {}), "limit": 100, "page": page})
        batch = data.get("data", [])
        rows.extend(batch)
        if not batch or page >= data.get("meta", {}).get("totalPages", page):
            break
        page += 1
    return rows


@dataclass
class DatasetInfo:
    name: str
    id: str = ""
    description: str = ""
    n_items: int = 0
    slices: dict[str, int] = field(default_factory=dict)
    items: list[dict] = field(default_factory=list)  # raw dataset items (input/expected/metadata)


@dataclass
class Catalog:
    online: bool
    error: str = ""
    prompts: list[dict] = field(default_factory=list)       # {name, versions:[{version, labels}]}
    datasets: list[DatasetInfo] = field(default_factory=list)
    score_configs: list[dict] = field(default_factory=list)  # {id, name, dataType}
    judges: list[dict] = field(default_factory=list)         # unstable evaluators (incl. managed)
    rules: list[dict] = field(default_factory=list)          # unstable evaluation rules
    judges_api: bool = False                                 # unstable endpoints available?

    def dataset(self, name: str) -> DatasetInfo | None:
        return next((d for d in self.datasets if d.name == name), None)


def fetch_catalog(cfg: Config, *, with_items: bool = True) -> Catalog:
    base = cfg.target.base_url
    try:
        prompts = _fetch_prompts(base)
    except Exception as exc:  # noqa: BLE001 — offline: fall back entirely
        cat = offline_catalog(cfg)
        cat.error = f"{type(exc).__name__}: {exc}"
        return cat

    cat = Catalog(online=True, prompts=prompts)
    try:
        cat.datasets = _fetch_datasets(base, with_items=with_items)
    except Exception as exc:  # noqa: BLE001
        cat.datasets = offline_catalog(cfg).datasets
        cat.error = f"datasets: {exc}"
    try:
        cat.score_configs = [{"id": r.get("id"), "name": r.get("name"),
                              "dataType": r.get("dataType")}
                             for r in _paged(base, "/api/public/score-configs")]
    except Exception:  # noqa: BLE001
        pass
    # unstable evaluator surface — optional by server version
    try:
        cat.judges = _get(base, "/api/public/unstable/evaluators").get("data", [])
        cat.judges_api = True
        try:
            cat.rules = _get(base, "/api/public/unstable/evaluation-rules").get("data", [])
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001 — older self-hosted: judges stay UI-managed
        cat.judges_api = False
    return cat


def _fetch_prompts(base: str) -> list[dict]:
    rows = _paged(base, "/api/public/v2/prompts")
    out = []
    for r in rows:
        out.append({"name": r.get("name"),
                    "versions": r.get("versions") or [],
                    "labels": r.get("labels") or []})
    return out


def _fetch_datasets(base: str, *, with_items: bool) -> list[DatasetInfo]:
    out = []
    for d in _paged(base, "/api/public/v2/datasets"):
        info = DatasetInfo(name=d.get("name", ""), id=d.get("id", ""),
                           description=d.get("description") or "")
        if with_items:
            items = _paged(base, "/api/public/dataset-items", {"datasetName": info.name})
            info.items = items
            info.n_items = len(items)
            for it in items:
                sl = (it.get("metadata") or {}).get("slice") or "(none)"
                info.slices[sl] = info.slices.get(sl, 0) + 1
        out.append(info)
    return out


# ---------------------------------------------------------------------------
# Offline fallback — derive the catalog from run state + the deterministic plan
# ---------------------------------------------------------------------------
def offline_catalog(cfg: Config) -> Catalog:
    cat = Catalog(online=False)
    state = RunState.load() if RunState.exists() else None
    pname = cfg.certification.prompt_name
    pver = state.prompt_version if state else 1
    cat.prompts = [{"name": pname,
                    "versions": [{"version": pver, "labels": ["v1", "production"]}],
                    "labels": ["production"]}]
    # suites re-derived from the deterministic plan: items + slices, no network
    try:
        from ..rng import Rng
        from ..seed.certification import build_suite, requirement_ids_for

        suite = build_suite(cfg, Rng(cfg.generation.seed))
        info = DatasetInfo(name=cfg.certification.dataset.name,
                           description="(offline) certification suite")
        for it in suite:
            info.items.append({
                "id": it.item_id,
                "input": it.question.model_dump(),
                "expectedOutput": it.expected.model_dump(),
                "metadata": {"scenario": it.scenario, "slice": it.scenario,
                             "curated": it.curated,
                             "requirement_ids": requirement_ids_for(it.scenario)},
            })
            info.slices[it.scenario] = info.slices.get(it.scenario, 0) + 1
        info.n_items = len(info.items)
        cat.datasets.append(info)
    except Exception as exc:  # noqa: BLE001
        cat.error = f"offline plan derivation failed: {exc}"
    return cat
