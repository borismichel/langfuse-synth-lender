"""Where `.synth_state.json` lives (LAN-276).

Under the portal each pipeline step runs in its own ephemeral container. The spool
is the only surface mounted into all of them, so `seed` state must land there or
`verify` starts fresh and fails with "No .synth_state.json". The artifact dir is
lifted out of the exited container after each step and is NOT shared — state there
is stranded, which is exactly the bug this pins.
"""
from __future__ import annotations

from pathlib import Path

from synth.state import REPO_ROOT, RunState, state_dir, state_path


def test_state_dir_defaults_to_the_spool_not_the_repo_root(monkeypatch):
    monkeypatch.delenv("SYNTH_STATE_DIR", raising=False)
    assert state_dir() == REPO_ROOT / ".synth_spool"


def test_state_dir_honours_synth_state_dir(monkeypatch):
    monkeypatch.setenv("SYNTH_STATE_DIR", "/app/.synth_spool")
    assert state_dir() == Path("/app/.synth_spool")
    assert state_path() == "/app/.synth_spool/.synth_state.json"


def test_state_dir_is_resolved_at_call_time(monkeypatch):
    """The portal sets the env via container ENV — a value bound at import time
    would ignore it (the original defect: `path=STATE_PATH` default args)."""
    monkeypatch.setenv("SYNTH_STATE_DIR", "/first")
    assert state_dir() == Path("/first")
    monkeypatch.setenv("SYNTH_STATE_DIR", "/second")
    assert state_dir() == Path("/second")


def test_state_never_lands_in_the_artifact_dir(monkeypatch, tmp_path):
    """SYNTH_OUT_DIR is container-local; state must not follow it."""
    monkeypatch.setenv("SYNTH_OUT_DIR", str(tmp_path / "out"))
    monkeypatch.delenv("SYNTH_STATE_DIR", raising=False)
    assert state_dir() != tmp_path / "out"


def test_seed_state_round_trips_across_processes(monkeypatch, tmp_path):
    """The end-to-end shape: `seed` saves, a *fresh* `verify` process loads.

    What the verify container does when it starts with only the spool attached.
    """
    shared = tmp_path / "spool"
    monkeypatch.setenv("SYNTH_STATE_DIR", str(shared))

    _minimal_state().save()  # seed container writes...

    # ...verify container starts fresh: only the spool volume is there.
    assert RunState.exists()
    assert (shared / ".synth_state.json").is_file()
    assert RunState.load().project_name == "demo-x"


def test_save_creates_the_state_dir_if_absent(monkeypatch, tmp_path):
    """A fresh named volume is empty; save() must not require a pre-made dir."""
    monkeypatch.setenv("SYNTH_STATE_DIR", str(tmp_path / "nonexistent" / "deep"))
    _minimal_state().save()
    assert RunState.exists()


def _minimal_state() -> RunState:
    return RunState(
        base_url="http://lf",
        project_name="demo-x",
        run_date="2026-07-17T00:00:00+00:00",
        prompt_name="p",
    )
