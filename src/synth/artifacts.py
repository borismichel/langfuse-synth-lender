"""Artifact output paths for portal-collected kit files."""
from __future__ import annotations

import os
from pathlib import Path

from .state import REPO_ROOT

DEFAULT_OUT_DIR = Path("/app/out")


def out_dir() -> Path:
    configured = Path(os.environ.get("SYNTH_OUT_DIR") or DEFAULT_OUT_DIR)
    try:
        configured.mkdir(parents=True, exist_ok=True)
        return configured
    except OSError:
        return REPO_ROOT


def artifact_path(name: str) -> Path:
    return out_dir() / name
