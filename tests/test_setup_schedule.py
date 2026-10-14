"""Operator schedule and preset declarations agree with the emitted historical Spool."""
from datetime import date, datetime, timezone
import json

import pytest
import yaml

from synth.config import load_config
from synth.seed.run import run_seed
from synth.state import REPO_ROOT


def _timestamps(path):
    stamps = []
    for line in path.read_text().splitlines():
        row = json.loads(line)
        if "spanId" in row:
            stamps.extend(datetime.fromtimestamp(int(row[key]) / 1e9, timezone.utc)
                          for key in ("startTimeUnixNano", "endTimeUnixNano"))
    return stamps


def test_friday_history_includes_friday_and_starts_on_saturday(tmp_path):
    cfg = load_config(str(REPO_ROOT / "config/cloud-demo.yaml"), [
        "generation.target_traces=1000", "generation.as_of_date=2026-10-16"])
    spool = tmp_path / "events.ndjson"
    run_seed(cfg, dry_run=True, persist=False, do_import=False, spool_path=spool, log=lambda _: None)
    stamps = _timestamps(spool)
    assert stamps
    assert min(stamps).date() == date(2026, 10, 3)
    assert max(stamps).date() == date(2026, 10, 16)


def test_last_second_observation_graph_keeps_durations_inside_as_of_day():
    from synth.seed.generator import build_plan
    from synth.seed.traces import build_trace_events

    cfg = load_config(str(REPO_ROOT / "config/cloud-demo.yaml"), ["generation.target_traces=1000"])
    plan = build_plan(cfg, datetime(2026, 10, 16, 12, tzinfo=timezone.utc))
    spec = plan.golden_specs[0]
    spec.timestamp = datetime(2026, 10, 16, 23, 59, 59, 999999, tzinfo=timezone.utc)
    unbounded = build_trace_events(plan.rng, cfg, spec)
    end = datetime(2026, 10, 17, tzinfo=timezone.utc)
    bounded = build_trace_events(plan.rng, cfg, spec, end_before=end)
    old_spans = [x for x in unbounded if "spanId" in x]
    new_spans = [x for x in bounded if "spanId" in x]
    assert max(int(x["endTimeUnixNano"]) for x in new_spans) < int(end.timestamp() * 1e9)
    assert [(x["spanId"], int(x["endTimeUnixNano"]) - int(x["startTimeUnixNano"])) for x in old_spans] == [
        (x["spanId"], int(x["endTimeUnixNano"]) - int(x["startTimeUnixNano"])) for x in new_spans]


def test_manifest_declares_effective_host_defaults_and_rehearsal():
    from langfuse_synth_core.authoring.validate import validate_doc, load_schema
    from jsonschema import Draft7Validator
    doc = yaml.safe_load((REPO_ROOT / "usecase.yaml").read_text())
    assert validate_doc(doc, Draft7Validator(load_schema())) == []
    setup = doc["setup"]
    assert setup["schedule"] == {"timezone": "UTC", "end": "inclusive", "history": {"field": "generation.window_days"}}
    assert setup["host_defaults"]["self_hosted"]["generation.window_days"] == 30
    assert setup["host_defaults"]["cloud_eu"]["generation.window_days"] == 14
    assert setup["presets"]["quick_rehearsal"]["values"] == {"generation.target_traces": 1000}


@pytest.mark.parametrize(("as_of", "days", "start"), [
    ("2026-10-16", 30, "2026-09-17"),
    ("2027-01-01", 14, "2026-12-19"),
    ("2028-03-01", 14, "2028-02-17"),
    ("2028-02-29", 30, "2028-01-31"),
])
def test_emitted_history_calendar_boundaries(tmp_path, as_of, days, start):
    cfg = load_config(str(REPO_ROOT / "config/cloud-demo.yaml"), [
        "generation.target_traces=1000", f"generation.as_of_date={as_of}",
        f"generation.window_days={days}"])
    spool = tmp_path / "events.ndjson"
    state = run_seed(cfg, dry_run=True, persist=False, do_import=False, spool_path=spool, log=lambda _: None)
    stamps = _timestamps(spool)
    assert min(stamps).date().isoformat() == start
    assert max(stamps).date().isoformat() == as_of
    assert state.summary["generated_range"]["start"][:10] == start
    assert state.summary["generated_range"]["end"][:10] == as_of


def test_saved_schedule_artifact_measures_observations(tmp_path, monkeypatch):
    import synth.seed.run as runner
    monkeypatch.setenv("SYNTH_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SYNTH_OUT_DIR", str(tmp_path / "out"))
    monkeypatch.setattr(runner, "FIXTURES_DIR", tmp_path / "fixtures")
    cfg = load_config(str(REPO_ROOT / "config/cloud-demo.yaml"), [
        "generation.target_traces=1000", "generation.as_of_date=2026-10-16"])
    spool = tmp_path / "events.ndjson"
    state = run_seed(cfg, dry_run=True, persist=True, do_import=False, spool_path=spool, log=lambda _: None)
    receipt = json.loads((tmp_path / "out/SCHEDULE.json").read_text())
    assert receipt == {"version": 1, "requested": {"as_of_date": "2026-10-16", "history_days": 14,
                      "timezone": "UTC", "end": "inclusive"}, "generated_range": state.summary["generated_range"]}
    stamps = _timestamps(spool)
    assert abs((datetime.fromisoformat(receipt["generated_range"]["end"]) - max(stamps)).total_seconds()) < .001


@pytest.mark.parametrize("history", [14, 30])
@pytest.mark.parametrize("target", [1000, 5000])
def test_presets_keep_certification_decisions_and_review_cohorts(history, target):
    from synth.seed.generator import build_plan
    from synth.seed.cert_runs import run_gate_verdict
    cfg = load_config(str(REPO_ROOT / "config/cloud-demo.yaml"), [
        f"generation.target_traces={target}", f"generation.window_days={history}"])
    plan = build_plan(cfg, datetime(2026, 10, 16, 12, tzinfo=timezone.utc))
    assert len(plan.cert.suite) == 72
    assert {run.key: run_gate_verdict(cfg, run)[0] for run in plan.cert.runs} == {
        "baseline": True, "candidate_a": True, "candidate_b": False}
    assert [len(run.items) for run in plan.cert.runs] == [72, 72, 72]
    assert len(plan.cert.flagged_pending_trace_ids) == 1
    assert set(plan.cert.flagged_pending_trace_ids).isdisjoint({item.source_trace_id for item in plan.cert.suite})
    assert (len(plan.queue_completed_ids), len(plan.queue_pending_ids)) == (16, 14)
    assert len(plan.golden_specs) == 5
    assert plan.summary["total_traces"] != target  # advisory target, not exact output
