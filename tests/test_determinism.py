"""Determinism law (spec §9): same (config, seed) => byte-identical output.

Two tiers, weakest to strongest:

* **plan-level repeatability** — two runs of the *same* code produce identical trace IDs
  and summary (the historical guarantee); and
* **the full-payload golden gate** (Spec A · Step 0 · #30) — a fresh materialization of the
  *entire pre-ingestion Spool* (traces + observations + scores) is byte-identical to a
  blessed golden snapshot, run offline in a subprocess under the deny-LLM egress block.
  This is the migration oracle: any refactor or story change that silently perturbs the
  deterministic pool fails here, loudly, before ingestion.
"""
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

from synth.config import load_config
from langfuse_synth_core.rng import Rng
from synth.seed.generator import build_plan

RUN_DATE = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)
CONFIG = "config/demo.yaml"


def _plan(seed=None, scale=0.04):
    cfg = load_config(CONFIG)
    cfg.generation.volume.scale = scale  # keep the test fast; determinism is scale-independent
    if seed is not None:
        cfg.generation.seed = seed
    return build_plan(cfg, RUN_DATE)


def test_same_seed_same_ids():
    a = _plan()
    b = _plan()
    assert [s.trace_id for s in a.specs] == [s.trace_id for s in b.specs]
    assert a.summary == b.summary
    assert ([it.item_id for it in a.cert.suite] == [it.item_id for it in b.cert.suite])
    assert a.cert.flagged_pending_trace_ids == b.cert.flagged_pending_trace_ids
    assert [g.trace_id for g in a.cert.golden] == [g.trace_id for g in b.cert.golden]


def test_different_seed_different_ids():
    a = _plan(seed=47)
    b = _plan(seed=48)
    assert [s.trace_id for s in a.specs] != [s.trace_id for s in b.specs]


def test_id_widths_are_w3c():
    rng = Rng(47)
    assert len(rng.trace_id("x")) == 32
    assert len(rng.obs_id("x")) == 16
    assert all(c in "0123456789abcdef" for c in rng.trace_id("x"))


# --------------------------------------------------------------------------------------
# Full-payload golden gate (Spec A · Step 0 · #30) — the byte-for-byte migration oracle.
# --------------------------------------------------------------------------------------
# The oracle is pinned at target_traces=150 (mapped to volume.scale by golden_seed's
# derivation). Coverage saturates well below this: all five golden keys, both languages,
# every filing-type/desk, the curated suite, flagged-pending, and the nightly batch are
# present — because the certification suite, experiment runs, and review queue are
# config-sized (UNSCALED), not ambient-scaled. Re-bless deliberately with:
#     synth-authoring freeze golden_seed:seed \
#         --golden tests/golden/lender_spool.ndjson --target-traces 150 --search-path tests
GOLDEN_TARGET_TRACES = 150
GOLDEN_PATH = Path(__file__).resolve().parent / "golden" / "lender_spool.ndjson"
TESTS_DIR = str(Path(__file__).resolve().parent)


def _authoring_installed() -> bool:
    """True iff langfuse-synth-core[authoring] (the golden gate) is importable.

    Probes ``jsonschema`` — the [authoring] extra's marker dep, the same signal
    langfuse-synth-core's own gate tests skip on — so this matches what the gate actually
    needs to import. Guarded per-test, not at module scope, so the plan-level determinism
    tests above keep running on a bare install that has not pulled the dev extra."""
    return importlib.util.find_spec("jsonschema") is not None


def _golden_spec():
    from langfuse_synth_core.authoring.golden import GoldenSpec

    return GoldenSpec(
        seed_ref="golden_seed:seed",
        target_traces=GOLDEN_TARGET_TRACES,
        golden_path=GOLDEN_PATH,
        params={},
        search_paths=(TESTS_DIR,),
    )


@pytest.mark.skipif(
    not _authoring_installed(),
    reason="golden gate ships in langfuse-synth-core[authoring]; install the dev extra to run it",
)
def test_full_payload_golden_is_byte_identical():
    """A fresh full-Spool materialization is byte-identical to the blessed oracle.

    Runs `seed` in a subprocess under PYTHONHASHSEED=0 and the deny-LLM egress block, so
    this simultaneously proves the seed is deterministic AND model-free-at-seed-runtime."""
    from langfuse_synth_core.authoring.golden import assert_golden

    assert_golden(_golden_spec())


@pytest.mark.skipif(not _authoring_installed(), reason="requires langfuse-synth-core[authoring]")
def test_golden_is_full_payload_not_ids_and_summary():
    """The blessed oracle is the whole Spool — observations and scores, not just IDs."""
    blob = GOLDEN_PATH.read_bytes()
    assert b'"type":"trace-create"' in blob
    assert b'"type":"generation-create"' in blob
    assert b'"type":"score-create"' in blob

