"""Companion-local run summaries, separate from immutable imported seed anchors.

The existing workbench store lasts for this Companion instance. Langfuse retains the
experiments; this local index lets its dossier and memo include live runs. A seed-state
fingerprint prevents a different target or seed from inheriting another run's summaries.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

from .config import Config
from .state import REPO_ROOT, RunState


def _directory(cfg: Config, state: RunState) -> Path:
    identity = hashlib.sha256(json.dumps(asdict(state), sort_keys=True).encode()).hexdigest()
    return REPO_ROOT / cfg.workbench.results_dir / "live-runs" / identity


def save_live_run(cfg: Config, state: RunState, run_name: str, summary: dict) -> None:
    """Save one live run without modifying the seed state or another run's record."""
    directory = _directory(cfg, state)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / (hashlib.sha256(run_name.encode()).hexdigest() + ".json")
    # Atomic replacement makes concurrent dossier reads see either complete version.
    with tempfile.NamedTemporaryFile(mode="w", dir=directory, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump({"run_name": run_name, "summary": summary}, stream)
            stream.flush()
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)


def with_live_runs(cfg: Config, state: RunState) -> RunState:
    """Return a dossier view; seed verification continues to read RunState unchanged."""
    merged = deepcopy(state)
    for path in sorted(_directory(cfg, state).glob("*.json")):
        record = json.loads(path.read_text())
        merged.suites.setdefault("certification_suite", {}).setdefault("runs", {})[
            record["run_name"]] = record["summary"]
    return merged
