"""Golden-gate seed adapter (Spec A · Step 0 · #30) — dev-only, never shipped.

The determinism golden gate in ``langfuse-synth-core[authoring]`` drives a kit through
one uniform contract::

    seed(target_traces: int, params: Mapping) -> bytes   # the full materialized Spool

This module is that adapter for the Lender kit. It materializes the kit **exactly as it is
on today's ``main``** — no plumbing extracted — and returns the byte-for-byte
pre-ingestion Spool (the NDJSON event stream the real ``synth seed`` writes to
``.synth_spool/events.ndjson``). That byte stream is the migration oracle every later ring
(and the lib-side ``count_spool``) must reproduce.

Why it lives in ``tests/`` and not ``src/synth/``: the golden gate is *authoring-time*
tooling behind the ``[authoring]`` extra. The deployed runtime image must never carry it
(Spec A §3), so the adapter is a dev-only test asset the gate imports via ``search_paths``,
not part of the shipped ``synth`` package.

Determinism note: the gate runs this in a subprocess under ``PYTHONHASHSEED=0`` and the
deny-LLM egress block. The Lender seed path is model-free (every CopilotAnswer is
templated), so it passes the block; the hash-seed pin makes incidental set/dict ordering
reproducible. (The baseline/A/B experiment runs are created online via the SDK, never
spooled — so they are correctly absent from this offline oracle.)
"""
from __future__ import annotations

import tempfile
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from synth.config import load_config
from synth.seed.run import run_seed
from synth.state import REPO_ROOT

CONFIG = REPO_ROOT / "config" / "demo.yaml"
RUN_DATE = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)

# Lender's canonical target_traces -> volume.scale derivation (Spec A §4 — "Lender: derive
# scale"). Lender has NO absolute trace-count knob: total traces are session-DERIVED, so
# the operator's uniform `generation.target_traces` maps to the one native `volume.scale`
# multiplier here. The reference yield was measured at scale=1.0, seed 47: ~10,111 traces.
#
# This is the STEP-0 derivation seam; Ring 2 (#33/#34) inherits and formalizes it. It is
# production-accurate (proportional near scale 1.0) and monotone; at demo-small volumes the
# realized count runs ABOVE target_traces because per-day session counts are rounded —
# `round(randint(lo,hi) * scale)` (timegen.sample_session_times) rounds weekday counts up to
# a ~1/weekday plateau rather than scaling to zero, so the realized trace count flattens
# (~252) below scale ≈0.025. target_traces is therefore an advisory volume dial for Lender,
# never an exact count — consistent with "total traces are DERIVED, not forced". Crucially,
# `volume.scale` drives ONLY ambient session volume: the certification suite, experiment
# runs, and review queue are config-sized and stay UNSCALED.
TRACES_PER_UNIT_SCALE = 10111


def seed(target_traces: int, params: Mapping[str, Any]) -> bytes:
    """Materialize the full pre-ingestion Spool for a fixed ``target_traces``; return bytes.

    ``params`` completes the ``seed(target_traces, params)`` gate contract; the Step-0 oracle
    pins the config defaults (seed 47), so nothing is read from it here — declared-param
    knobs land when Ring 2 (#33/#34) wires the real derivation.
    """
    cfg = load_config(str(CONFIG))
    cfg.generation.volume.scale = int(target_traces) / TRACES_PER_UNIT_SCALE

    with tempfile.TemporaryDirectory(prefix="lender-golden-") as tmp:
        spool_path = Path(tmp) / "events.ndjson"
        # dry_run: no network (model-free, no ingestion); persist=False: no fixtures/state
        # written to the repo; do_import=False: never touch Langfuse. Pure CPU generation.
        run_seed(
            cfg,
            dry_run=True,
            persist=False,
            run_date=RUN_DATE,
            spool_path=spool_path,
            do_import=False,
            log=lambda _m: None,
        )
        return spool_path.read_bytes()
