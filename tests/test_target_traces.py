"""Lender's operator surface is the canonical target_traces knob + derive-scale hook (#34).

Acceptance (#34):
  * Lender's operator surface is the canonical ``generation.target_traces``; no bespoke
    ``generation.volume.scale`` operator knob remains (internal only);
  * the derivation hook does derive-scale (``target_traces -> volume.scale``), with the
    golden suite / experiments / queue left unscaled (that last invariant lives in the
    generator + is proven byte-for-byte by the determinism golden gate).

The manifest half (knob shape) is an authoring-time check (needs ``inject_target_traces``
behind the ``[authoring]`` extra / ``jsonschema``); the hook half is pure runtime.
"""

from __future__ import annotations

import importlib.util

import pytest
import yaml

from synth.config import (
    Config,
    TRACES_PER_UNIT_SCALE,
    derive_scale_derivation,
    load_config,
    resolve_target_traces,
)

REPO_ROOT = __import__("synth.state", fromlist=["REPO_ROOT"]).REPO_ROOT
MANIFEST = REPO_ROOT / "usecase.yaml"
CONFIG = str(REPO_ROOT / "config" / "demo.yaml")

# The bounds Lender declares for the canonical knob (must match usecase.yaml verbatim).
LENDER_MIN, LENDER_MAX, LENDER_DEFAULT = 1000, 15000, 5000
LENDER_TITLE = "Target traces"
LENDER_DESCRIPTION = (
    "Total number of backdated traces to generate — the single, uniform volume knob for "
    "this demo. ~5000 is Cloud-free-tier safe; ~10000 is the full self-hosted story. Traces "
    "are session-DERIVED, so the kit maps this to its internal volume scaler (Lender: "
    "derive-scale) — an advisory dial, not an exact count; the certification suite, "
    "experiment runs, and review queue are fixed-size and never scaled."
)

_authoring = importlib.util.find_spec("jsonschema") is not None


# --- the hook (runtime) --------------------------------------------------------------
def test_derive_scale_divides_by_the_reference_yield():
    assert derive_scale_derivation(TRACES_PER_UNIT_SCALE, {}) == {"volume.scale": 1.0}
    assert derive_scale_derivation(150, {"anything": 1}) == {"volume.scale": 150 / TRACES_PER_UNIT_SCALE}


def test_hook_satisfies_the_lib_derivation_contract():
    from langfuse_synth_core.derivation import DerivationHook

    hook: DerivationHook = derive_scale_derivation
    assert hook(5000, {}) == {"volume.scale": 5000 / TRACES_PER_UNIT_SCALE}


def test_set_target_traces_derives_scale_at_load():
    cfg = load_config(CONFIG, ["generation.target_traces=5055"])
    assert cfg.generation.target_traces == 5055
    assert cfg.generation.volume.scale == 5055 / TRACES_PER_UNIT_SCALE


def test_unset_target_traces_keeps_the_config_scale():
    cfg = load_config(CONFIG)
    assert cfg.generation.target_traces is None
    assert cfg.generation.volume.scale == 1.0  # config/demo.yaml shipped scale, untouched


def test_resolve_is_idempotent_and_returns_cfg():
    cfg = Config.model_validate(yaml.safe_load(
        (REPO_ROOT / "config" / "demo.yaml").read_text()))
    cfg.generation.target_traces = 3000
    assert resolve_target_traces(cfg) is cfg
    assert cfg.generation.volume.scale == 3000 / TRACES_PER_UNIT_SCALE
    resolve_target_traces(cfg)  # again — stable (target_traces unchanged)
    assert cfg.generation.volume.scale == 3000 / TRACES_PER_UNIT_SCALE


# --- the manifest operator surface ---------------------------------------------------
def _config_schema() -> dict:
    return yaml.safe_load(MANIFEST.read_text())["config_schema"]["properties"]


def test_manifest_exposes_only_the_canonical_volume_knob():
    props = _config_schema()
    assert "generation.target_traces" in props
    # The bespoke operator knob is GONE from the operator surface (internal only now).
    assert "generation.volume.scale" not in props


def test_canonical_knob_has_the_full_required_shape():
    knob = _config_schema()["generation.target_traces"]
    assert knob["type"] == "integer"
    assert (knob["minimum"], knob["maximum"], knob["default"]) == (
        LENDER_MIN, LENDER_MAX, LENDER_DEFAULT)
    assert knob["minimum"] <= knob["default"] <= knob["maximum"]
    for field in ("title", "description"):
        assert isinstance(knob[field], str) and knob[field]


@pytest.mark.skipif(not _authoring, reason="inject_target_traces lives behind [authoring]")
def test_manifest_knob_matches_the_sdk_injector_output():
    """The committed knob is exactly what the SDK one-liner would emit for Lender's bounds —
    so the manifest can't drift from the canonical shape."""
    from langfuse_synth_core.authoring import inject_target_traces
    from langfuse_synth_core.derivation import TARGET_TRACES_KEY

    expected = inject_target_traces(
        minimum=LENDER_MIN, maximum=LENDER_MAX, default=LENDER_DEFAULT,
        title=LENDER_TITLE, description=LENDER_DESCRIPTION,
    )["properties"][TARGET_TRACES_KEY]
    assert _config_schema()["generation.target_traces"] == expected
