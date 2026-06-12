"""Experiment specs — the auditable unit of experiment design.

A spec is everything that defines a validation experiment: the **release** under test
(model + prompt version + params), the **targets** (suites, optionally narrowed to
slices), the **evaluators** (deterministic code by name + managed judges), the
**gates** (acceptance thresholds, incl. per-slice overrides), and an optional
**dataset freeze** timestamp. Specs are persisted as versioned JSON files with a
canonical SHA-256 hash; the hash rides in every run's metadata, so a result can always
be traced to the exact design that produced it — acceptance criteria as governed data,
not tribal knowledge.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

from ..config import Config
from ..state import REPO_ROOT


class Release(BaseModel):
    model: str
    prompt_name: str = "analyst-copilot"
    prompt_version: int | None = None      # pin by version (preferred for certification)
    prompt_label: str = "production"       # used when no version pin
    temperature: float = 0.0


class Target(BaseModel):
    dataset_name: str
    slices: list[str] = Field(default_factory=list)   # empty = all items


class Gates(BaseModel):
    threshold: float = 0.95                            # per-target pass-rate gate
    slice_overrides: dict[str, float] = Field(default_factory=dict)  # e.g. production_flagged: 1.0


class ExperimentSpec(BaseModel):
    name: str
    version: int = 1
    release: Release
    targets: list[Target]
    evaluators: list[str] = Field(default_factory=list)     # deterministic registry names
    judges: list[str] = Field(default_factory=list)         # managed evaluator names (scored via rules)
    gates: Gates = Field(default_factory=Gates)
    freeze_dataset_version: str | None = None                # ISO ts → datasetVersion pin
    created_by: str = "builder"
    notes: str = ""

    # --- identity ---------------------------------------------------------
    @property
    def slug(self) -> str:
        return re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-") or "spec"

    @property
    def spec_hash(self) -> str:
        canonical = json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    @property
    def ref(self) -> str:
        return f"{self.slug}-v{self.version}"


# ---------------------------------------------------------------------------
# Persistence (.workbench/specs/<slug>-v<N>.json)
# ---------------------------------------------------------------------------
def specs_dir(cfg: Config) -> Path:
    return REPO_ROOT / cfg.workbench.results_dir / "specs"


def save_spec(cfg: Config, spec: ExperimentSpec) -> ExperimentSpec:
    """Persist as the next version of its slug (specs are append-only: editing creates
    a new version — the audit trail keeps every shape the gate ever had)."""
    d = specs_dir(cfg)
    d.mkdir(parents=True, exist_ok=True)
    existing = sorted(d.glob(f"{spec.slug}-v*.json"))
    next_version = 1
    if existing:
        versions = [int(re.search(r"-v(\d+)\.json$", p.name).group(1)) for p in existing]
        next_version = max(versions) + 1
    spec = spec.model_copy(update={"version": next_version})
    (d / f"{spec.ref}.json").write_text(spec.model_dump_json(indent=2))
    return spec


def load_spec(cfg: Config, ref: str) -> ExperimentSpec | None:
    path = specs_dir(cfg) / f"{ref}.json"
    if not path.exists():
        return None
    return ExperimentSpec.model_validate_json(path.read_text())


def list_specs(cfg: Config) -> list[ExperimentSpec]:
    d = specs_dir(cfg)
    if not d.exists():
        return []
    out = []
    for path in sorted(d.glob("*.json")):
        try:
            out.append(ExperimentSpec.model_validate_json(path.read_text()))
        except Exception:  # noqa: BLE001 — skip corrupt files
            continue
    out.sort(key=lambda s: (s.slug, -s.version))
    return out
