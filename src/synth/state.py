"""Run-state persistence.

``synth seed`` writes ``.synth_state.json`` capturing the concrete anchors of a run
(dates, prompt versions, suite/run/queue facts, golden-trace ids, project name).
``synth verify``, ``synth script``, ``synth memo`` and the playground read it back so
the runbook, DEMO_MAP and dossier can never drift from the seeded data. The file is
git-ignored — it is per-run output.

The IO is the core anchors mechanism (``langfuse_synth_core.anchors``, portal #199): the
canonical filename, the location resolved from ``SYNTH_STATE_DIR`` at call time (the
spool volume — the Contract's per-run anchors rules, ``langfuse-synth-core``
``CONTRACT.md`` §"Per-run anchors (opt-in)"), and ``save``/``load``/``exists`` inherited
via :class:`AnchorsIO` — including this kit's tolerant ``load`` (unknown keys dropped),
now the shared behavior. This module keeps only what is Lender's: the payload fields,
their convenience accessors, and the dev-checkout fallback location. (The live-container
writers — workbench/certify — still collide with the Contract's read-only live mount;
that migration debt is tracked in ``CONTRACT.md``, not here.)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from langfuse_synth_core.anchors import AnchorsIO
from langfuse_synth_core.anchors import state_dir as _state_dir
from langfuse_synth_core.anchors import state_path as _state_path

REPO_ROOT = Path(__file__).resolve().parents[2]
FALLBACK_STATE_DIR = REPO_ROOT / ".synth_spool"


def state_dir() -> Path:
    """Where ``.synth_state.json`` lives — ``SYNTH_STATE_DIR`` (the portal injects it)
    resolved at call time by core, else the dev checkout's spool dir."""
    return _state_dir(FALLBACK_STATE_DIR)


def state_path() -> str:
    return _state_path(FALLBACK_STATE_DIR)


@dataclass
class RunState(AnchorsIO):
    FALLBACK_STATE_DIR: ClassVar[Path] = FALLBACK_STATE_DIR

    base_url: str
    project_name: str
    run_date: str
    prompt_name: str
    prompt_versions: dict = field(default_factory=dict)   # {latest, production, staging}
    incumbent_model: str = ""
    candidate_a_model: str = ""
    candidate_b_model: str = ""
    judge_model: str = ""
    baseline_run_date: str = ""
    candidate_run_date: str = ""
    suites: dict = field(default_factory=dict)        # {"certification_suite": {name, items, scenarios, gates, runs}}
    queue: dict = field(default_factory=dict)         # {name, id, completed, pending}
    golden: list = field(default_factory=list)        # [{key, title, trace_id}]
    flagged_pending: list = field(default_factory=list)  # reserved thumbs-down examples
    summary: dict = field(default_factory=dict)
    project_id: str = ""
    dry_run: bool = False

    # -- convenience -------------------------------------------------------
    @property
    def suite(self) -> dict:
        return self.suites.get("certification_suite", {})

    @property
    def prompt_version(self) -> int | None:
        return (self.prompt_versions or {}).get("production")

    def golden_by_key(self, key: str) -> dict:
        return next((g for g in self.golden if g.get("key") == key), {})
