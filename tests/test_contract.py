from datetime import datetime, timezone
from types import SimpleNamespace

from typer.testing import CliRunner

from synth.cli import app
from synth.config import load_config
from synth.memo import render_memo
from synth.script import render_script
from synth.seed.run import run_seed


RUN_DATE = datetime(2026, 6, 12, 12, 0, 0, tzinfo=timezone.utc)


def _dry_run_state(tmp_path):
    cfg = load_config(
        "config/demo.yaml",
        overrides=["generation.volume.scale=0.05", "generation.window_days=14"],
    )
    state = run_seed(
        cfg,
        dry_run=True,
        persist=False,
        run_date=RUN_DATE,
        spool_path=tmp_path / "spool.ndjson",
        log=lambda m: None,
    )
    return cfg, state


def test_load_config_applies_yaml_coerced_dotted_overrides():
    cfg = load_config(
        "config/cloud-demo.yaml",
        overrides=["generation.volume.scale=0.3", "generation.window_days=14"],
    )

    assert cfg.generation.volume.scale == 0.3
    assert cfg.generation.window_days == 14


def test_plan_accepts_repeatable_set_options(monkeypatch):
    import synth.seed.run as seed_run

    def fake_run_seed(cfg, **kwargs):
        return SimpleNamespace(
            summary={
                "preset_scale": cfg.generation.volume.scale,
                "window_days": cfg.generation.window_days,
            }
        )

    monkeypatch.setattr(seed_run, "run_seed", fake_run_seed)
    result = CliRunner().invoke(
        app,
        [
            "plan",
            "-c",
            "config/cloud-demo.yaml",
            "--set",
            "generation.volume.scale=0.3",
            "--set",
            "generation.window_days=14",
        ],
    )

    assert result.exit_code == 0
    assert '"preset_scale": 0.3' in result.output
    assert '"window_days": 14' in result.output


def test_artifacts_honor_synth_out_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNTH_OUT_DIR", str(tmp_path / "out"))
    cfg, state = _dry_run_state(tmp_path)

    render_script(cfg, state)
    render_memo(cfg, state)

    out_dir = tmp_path / "out"
    assert (out_dir / "DEMO_SCRIPT.md").exists()
    assert (out_dir / "DEMO_MAP.md").exists()
    assert (out_dir / "DEMO_WALKTHROUGH.html").exists()
    assert (out_dir / "CERT_MEMO.md").exists()
