"""The runbook-executability rule (issue #182, the Lender half of issue #181's rule).

Every step a presenter performs in the Presenter Runbook must be reachable from the
delivered surfaces — the Langfuse UI or the Companion's routes. `synth` commands may
appear only in a clearly-marked developer-mode section. Provenance lines ("generated
by ...") are descriptive facts about the pipeline, not presenter beats, and are exempt
(issue #181's lint deliberately ignores them).

These assertions run against the *rendered* artefacts, so they hold for whatever the
templates produce from real run state.
"""
import re
from datetime import datetime, timezone

from synth.config import load_config
from synth.script import WALKTHROUGH_OUT, render_script
from synth.seed.run import run_seed

RUN_DATE = datetime(2026, 6, 12, 12, 0, 0, tzinfo=timezone.utc)

DEV_MODE_MARKER = "Developer mode"
# Descriptive provenance, e.g. "Generated from run state by `synth script`".
PROVENANCE = re.compile(r"[Gg]enerated (by|from|at)")
# Issue #181's lint heuristic: a fenced block whose first line is a `synth` command.
FENCED_SYNTH = re.compile(r"^```[^\n]*\n\s*synth ", re.MULTILINE)


def _render(tmp_path, monkeypatch):
    import synth.script as script_mod

    cfg = load_config("config/demo.yaml")
    cfg.generation.volume.scale = 0.05
    state = run_seed(cfg, dry_run=True, persist=False, run_date=RUN_DATE,
                     spool_path=tmp_path / "spool.ndjson", log=lambda m: None)
    monkeypatch.setattr(script_mod, "MAP_OUT", tmp_path / "DEMO_MAP.md")
    monkeypatch.setattr(script_mod, "WALKTHROUGH_OUT", tmp_path / "DEMO_WALKTHROUGH.html")
    script = render_script(cfg, state, out_path=tmp_path / "DEMO_SCRIPT.md").read_text()
    return {
        "DEMO_SCRIPT.md": script,
        "DEMO_MAP.md": (tmp_path / "DEMO_MAP.md").read_text(),
        "DEMO_WALKTHROUGH.html": (tmp_path / "DEMO_WALKTHROUGH.html").read_text(),
    }


def _presenter_region(text: str) -> str:
    """Everything a presenter reads: above the developer-mode section, provenance dropped."""
    head = text.split(DEV_MODE_MARKER)[0]
    return "\n".join(line for line in head.splitlines() if not PROVENANCE.search(line))


def test_presenter_beats_reference_no_synth_command(tmp_path, monkeypatch):
    for name, text in _render(tmp_path, monkeypatch).items():
        presenter = _presenter_region(text)
        assert "synth " not in presenter, f"{name}: presenter beat references a synth command"


def test_synth_commands_live_in_a_marked_developer_mode_section(tmp_path, monkeypatch):
    arts = _render(tmp_path, monkeypatch)
    script = arts["DEMO_SCRIPT.md"]
    assert DEV_MODE_MARKER in script
    dev = script.split(DEV_MODE_MARKER, 1)[1]
    # Relocated, not deleted — the developer path still documents the pipeline verbs.
    for verb in ("synth seed", "synth verify", "synth probe", "synth certify"):
        assert verb in dev, f"{verb} missing from the developer-mode section"
    # #181's lint shape: no fenced synth block sits outside the marked section.
    for name, text in arts.items():
        head = text.split(DEV_MODE_MARKER)[0]
        assert not FENCED_SYNTH.search(head), f"{name}: fenced synth block outside developer mode"


def test_workbench_is_an_optional_escalation_via_the_live_asset_link(tmp_path, monkeypatch):
    arts = _render(tmp_path, monkeypatch)
    for name in ("DEMO_SCRIPT.md", "DEMO_WALKTHROUGH.html"):
        presenter = _presenter_region(arts[name])
        assert "/workbench" in presenter, f"{name}: workbench not offered"
        assert "optional" in presenter.lower(), f"{name}: workbench not framed as optional"
        # The live-cert beat points at the workbench designer, not a CLI verb.
        assert "designer" in presenter.lower(), f"{name}: no workbench designer → run beat"
    # The map's workbench row is reached from the deployment, not a shell.
    assert "/workbench" in _presenter_region(arts["DEMO_MAP.md"])


def test_prep_and_teardown_are_depot_native(tmp_path, monkeypatch):
    presenter = _presenter_region(_render(tmp_path, monkeypatch)["DEMO_SCRIPT.md"])
    assert ".env" not in presenter          # no shell-era prep
    assert "deploy again" in presenter.lower()


def test_anchors_stay_run_state_generated(tmp_path, monkeypatch):
    import synth.script as script_mod

    cfg = load_config("config/demo.yaml")
    cfg.generation.volume.scale = 0.05
    state = run_seed(cfg, dry_run=True, persist=False, run_date=RUN_DATE,
                     spool_path=tmp_path / "spool.ndjson", log=lambda m: None)
    monkeypatch.setattr(script_mod, "MAP_OUT", tmp_path / "DEMO_MAP.md")
    monkeypatch.setattr(script_mod, "WALKTHROUGH_OUT", tmp_path / "DEMO_WALKTHROUGH.html")
    render_script(cfg, state, out_path=tmp_path / "DEMO_SCRIPT.md")
    walkthrough = (tmp_path / "DEMO_WALKTHROUGH.html").read_text()
    for g in state.golden:
        assert g["trace_id"] in (tmp_path / "DEMO_MAP.md").read_text()
    assert state.golden_by_key("numeric_hallucination")["trace_id"] in walkthrough
    assert state.suite["name"] in walkthrough
    # Provenance retained (descriptive, exempt from the rule).
    assert PROVENANCE.search(walkthrough)
    assert PROVENANCE.search((tmp_path / "DEMO_SCRIPT.md").read_text())
    assert WALKTHROUGH_OUT.name == "DEMO_WALKTHROUGH.html"
