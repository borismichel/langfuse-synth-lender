"""The generated artefacts (runbook, DEMO_MAP, dossier, fixtures) must render from
run state without template errors and carry the real anchors (spec v2 §9)."""
import json
from datetime import datetime, timezone
from pathlib import Path

from synth.config import load_config
from synth.memo import render_memo
from synth.script import MAP_OUT, render_script
from synth.seed.run import run_seed

RUN_DATE = datetime(2026, 6, 12, 12, 0, 0, tzinfo=timezone.utc)


def _state(tmp_path):
    cfg = load_config("config/demo.yaml")
    cfg.generation.volume.scale = 0.05
    state = run_seed(cfg, dry_run=True, persist=False, run_date=RUN_DATE,
                     spool_path=tmp_path / "spool.ndjson", log=lambda m: None)
    return cfg, state


def test_demo_script_and_map_render_with_anchors(tmp_path, monkeypatch):
    import synth.script as script_mod

    cfg, state = _state(tmp_path)
    monkeypatch.setattr(script_mod, "MAP_OUT", tmp_path / "DEMO_MAP.md")
    out = render_script(cfg, state, out_path=tmp_path / "DEMO_SCRIPT.md")
    text = out.read_text()
    assert "built-in method" not in text
    assert "certification-suite" in text
    assert "baseline-claude-sonnet-4-5" in text
    assert "cert-claude-haiku-4-5" in text           # candidate B
    assert "fails numeric accuracy" in text
    assert state.golden_by_key("numeric_hallucination")["trace_id"] in text
    map_text = (tmp_path / "DEMO_MAP.md").read_text()
    assert "built-in method" not in map_text
    for g in state.golden:
        assert g["trace_id"] in map_text             # every golden trace mapped
    assert "numeric_accuracy · groundedness · citation_coverage" in map_text


def test_cert_memo_renders_v2(tmp_path):
    cfg, state = _state(tmp_path)
    out = render_memo(cfg, state, out_path=tmp_path / "CERT_MEMO.md")
    text = out.read_text()
    assert "built-in method" not in text
    assert "72 items" in text
    assert "REJECTED" in text and "Recommend certification" in text
    assert "claude-sonnet-4-6" in text and "claude-haiku-4-5" in text
    assert "81.8%" in text                            # candidate B's numeric_lookup rate


def test_dossier_page_renders(tmp_path, monkeypatch):
    cfg, state = _state(tmp_path)
    state_file = tmp_path / "state.json"
    state.save(str(state_file))
    import synth.state as state_mod

    monkeypatch.setattr(state_mod.RunState, "exists", staticmethod(lambda path=None: True))
    monkeypatch.setattr(state_mod.RunState, "load",
                        classmethod(lambda cls, path=None: cls(
                            **{k: v for k, v in json.loads(state_file.read_text()).items()
                               if k in cls.__dataclass_fields__})))
    from synth.live.dashboard import render_dossier

    html = render_dossier(cfg)
    assert "certification dossier" in html
    assert "REJECTED" in html
    assert "Recommendation" in html


def test_fixtures_written(tmp_path, monkeypatch):
    import synth.seed.run as run_mod

    monkeypatch.setattr(run_mod, "FIXTURES_DIR", tmp_path / "fixtures")
    cfg = load_config("config/demo.yaml")
    cfg.generation.volume.scale = 0.05
    run_seed(cfg, dry_run=True, persist=True, run_date=RUN_DATE,
             spool_path=tmp_path / "spool.ndjson", log=lambda m: None)
    rows = json.loads((tmp_path / "fixtures" / "golden_cases.json").read_text())
    kinds = {r["kind"] for r in rows}
    assert kinds == {"golden_trace", "run_red_cell"}
    golden = [r for r in rows if r["kind"] == "golden_trace"]
    assert len(golden) == 5
    nh = next(r for r in golden if r["key"] == "numeric_hallucination")
    assert nh["expected"]["figures"]["net_result_eur"] == -2_431_000
    assert nh["answer"]["figures"]["net_result_eur"] == 2_431_000
    red = [r for r in rows if r["kind"] == "run_red_cell"]
    assert sum(1 for r in red if r["run_errors"].get("candidate_b")) == 4
