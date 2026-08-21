"""The operator's as-of date reaches the seed (portal #229).

The portal has sent ``--set generation.as_of_date=YYYY-MM-DD`` on every forward generate
since #72; until #229 this kit's config model silently dropped it, so the third leg of the
determinism law (``seed + target_traces + as-of → byte-identical Spool``) was a lie. These
pin the knob end to end through the REAL operator path: the ``--set`` override, the
pydantic model, and ``run_seed``'s default run anchor.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from synth.config import load_config
from synth.seed.run import run_seed
from synth.state import REPO_ROOT

CONFIG = str(REPO_ROOT / "config" / "demo.yaml")
SMALL = "generation.target_traces=1000"


def _seed(tmp_path: Path, *overrides: str):
    cfg = load_config(CONFIG, overrides=[SMALL, *overrides])
    return run_seed(cfg, dry_run=True, persist=False, do_import=False,
                    spool_path=tmp_path / "events.ndjson", log=lambda _m: None)


def test_the_override_lands_on_the_config_as_a_date():
    cfg = load_config(CONFIG, overrides=["generation.as_of_date=2026-09-04"])
    assert cfg.generation.as_of_date == date(2026, 9, 4)


def test_absent_means_no_tether():
    assert load_config(CONFIG).generation.as_of_date is None


def test_seed_anchors_the_window_on_the_as_of_date(tmp_path):
    state = _seed(tmp_path, "generation.as_of_date=2026-09-04")
    assert state.run_date == "2026-09-04T12:00:00+00:00"
    assert state.summary["run_date"] == "2026-09-04T12:00:00+00:00"


def test_a_future_as_of_date_is_honoured_not_clamped(tmp_path):
    """By design: an AE tethers next week's demo to the meeting. The window simply ends on
    that date — no error, no warning, no clamp to today."""
    fortnight_out = date.today() + timedelta(days=14)
    state = _seed(tmp_path, f"generation.as_of_date={fortnight_out.isoformat()}")
    assert datetime.fromisoformat(state.run_date).date() == fortnight_out


def test_no_as_of_date_seeds_up_to_now(tmp_path):
    before = datetime.now(timezone.utc).replace(microsecond=0)
    state = _seed(tmp_path)
    assert before <= datetime.fromisoformat(state.run_date) <= datetime.now(timezone.utc)


def test_same_three_inputs_give_identical_bytes_regardless_of_the_clock(tmp_path):
    """The determinism law, third leg included: two runs with the same seed, target_traces
    and as-of date produce identical Spool bytes — the wall clock is not an input."""
    a = _seed(tmp_path / "a", "generation.as_of_date=2026-09-04")
    b = _seed(tmp_path / "b", "generation.as_of_date=2026-09-04")
    assert a.run_date == b.run_date
    assert (tmp_path / "a" / "events.ndjson").read_bytes() == \
        (tmp_path / "b" / "events.ndjson").read_bytes()
