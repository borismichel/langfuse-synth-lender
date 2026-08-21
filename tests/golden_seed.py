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
from pathlib import Path
from typing import Any

from synth.config import load_config
from synth.seed.run import run_seed
from synth.state import REPO_ROOT

CONFIG = REPO_ROOT / "config" / "demo.yaml"
# The as-of date is pinned HERE (the dev-only gate), never in `src/`: production resolves
# it from the operator's `--set generation.as_of_date` or the clock (portal #229), and the
# oracle pins it so the backdated timestamps are reproducible on any day. It anchors at
# noon UTC — identical to the `run_date=` this adapter passed before the knob was honoured,
# so the golden bytes did not move.
AS_OF_DATE = "2026-06-10"


def seed(target_traces: int, params: Mapping[str, Any]) -> bytes:
    """Materialize the full pre-ingestion Spool for a fixed ``target_traces``; return bytes.

    Lender derivation hook (Spec A §4 — "Lender: derive scale"): ``target_traces`` maps to
    the kit's internal ``generation.volume.scale`` multiplier (Lender has no absolute
    trace-count knob; total traces are session-DERIVED). Ring 2 (#34) wired that mapping
    through the REAL operator path — the canonical ``generation.target_traces`` knob is set
    exactly as the portal sets it (``--set``), and Lender's kit-side derive-scale derivation
    hook (``config.resolve_target_traces``) derives ``volume.scale`` at load. So this gate
    now proves the acceptance criterion directly: turning ``target_traces`` through the hook
    yields a Spool byte-identical to pre-migration Lender at the same effective volume.

    The as-of date travels the same way (``--set generation.as_of_date=YYYY-MM-DD``), so
    the gate proves the portal's third input reaches the Spool's bytes too (portal #229).

    ``params`` completes the ``seed(target_traces, params)`` gate contract; Lender's
    derive-scale reads only the knob, so the Step-0 oracle (config defaults, seed 47) reads
    nothing from it.
    """
    cfg = load_config(
        str(CONFIG),
        overrides=[
            f"generation.target_traces={int(target_traces)}",
            f"generation.as_of_date={AS_OF_DATE}",
        ],
    )

    with tempfile.TemporaryDirectory(prefix="lender-golden-") as tmp:
        spool_path = Path(tmp) / "events.ndjson"
        # dry_run: no network (model-free, no ingestion); persist=False: no fixtures/state
        # written to the repo; do_import=False: never touch Langfuse. Pure CPU generation.
        run_seed(
            cfg,
            dry_run=True,
            persist=False,
            spool_path=spool_path,
            do_import=False,
            log=lambda _m: None,
        )
        return spool_path.read_bytes()
