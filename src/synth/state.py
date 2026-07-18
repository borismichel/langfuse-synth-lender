"""Run-state persistence.

``synth seed`` writes ``.synth_state.json`` capturing the concrete anchors of a run
(dates, prompt versions, suite/run/queue facts, golden-trace ids, project name).
``synth verify``, ``synth script``, ``synth memo`` and the playground read it back so
the runbook, DEMO_MAP and dossier can never drift from the seeded data. The file is
git-ignored — it is per-run output.

It lives in the spool dir, not the repo root: under the portal each step runs in its
own ephemeral container, and the spool is the only surface mounted (as a named volume)
into all of them, so state written by ``seed`` survives to be read by ``verify``. The
artifact dir (``SYNTH_OUT_DIR``) is NOT shared — it is lifted from the exited container
after each step — so state must not live there. ``SYNTH_STATE_DIR`` overrides.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
STATE_FILENAME = ".synth_state.json"


def state_dir() -> Path:
    """Where ``.synth_state.json`` lives — resolved at call time so a container ``ENV``
    or a shell export both work (the portal injects ``SYNTH_STATE_DIR``)."""
    env = os.environ.get("SYNTH_STATE_DIR")
    return Path(env) if env else REPO_ROOT / ".synth_spool"


def state_path() -> str:
    return str(state_dir() / STATE_FILENAME)


@dataclass
class RunState:
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

    def save(self, path: str | None = None) -> None:
        p = Path(path or state_path())
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def load(cls, path: str | None = None) -> "RunState":
        data = json.loads(Path(path or state_path()).read_text())
        known = {f for f in cls.__dataclass_fields__}  # tolerate older state files
        return cls(**{k: v for k, v in data.items() if k in known})

    @staticmethod
    def exists(path: str | None = None) -> bool:
        return Path(path or state_path()).exists()
