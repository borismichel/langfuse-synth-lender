"""Golden-path invariants (spec v2): the certification story must hold structurally."""
from datetime import datetime, timezone

from synth.config import load_config
from synth.grading import item_passes
from synth.seed.cert_runs import run_gate_verdict, run_pass_rates
from synth.seed.generator import build_plan
from synth.seed.traces import build_trace_events

RUN_DATE = datetime(2026, 6, 12, 12, 0, 0, tzinfo=timezone.utc)


def _plan(scale=0.06):
    cfg = load_config("config/demo.yaml")
    cfg.generation.volume.scale = scale
    return cfg, build_plan(cfg, RUN_DATE)


def test_suite_counts_match_config():
    cfg, plan = _plan()
    for scenario, c in cfg.certification.dataset.scenarios.items():
        have = len(plan.cert.items_by_scenario(scenario))
        assert have == c.n_items, scenario
    assert len(plan.cert.suite) == cfg.certification.dataset.n_items == 72


def test_suite_is_self_consistent():
    _, plan = _plan()
    for it in plan.cert.suite:
        ok, detail = item_passes(it.scenario, it.expected, it.expected)
        assert ok, f"{it.scenario}: {detail}"


def test_three_runs_with_the_designed_verdicts():
    """Baseline passes, candidate A passes, candidate B fails numeric accuracy —
    the comparison screen shows a real decision (spec v2 §5)."""
    cfg, plan = _plan()
    runs = {r.key: r for r in plan.cert.runs}
    assert set(runs) == {"baseline", "candidate_a", "candidate_b"}

    ok_base, detail_base = run_gate_verdict(cfg, runs["baseline"])
    assert ok_base, detail_base                       # passes (with one harmless slip)
    base_rates = run_pass_rates(runs["baseline"])
    assert 0.95 <= base_rates["numeric_lookup"] < 1.0  # the slip is visible, not fatal

    ok_a, _ = run_gate_verdict(cfg, runs["candidate_a"])
    assert ok_a
    assert all(v == 1.0 for v in run_pass_rates(runs["candidate_a"]).values())
    assert runs["candidate_a"].groundedness_mu > runs["baseline"].groundedness_mu
    assert runs["candidate_a"].token_factor < 1.0      # lower cost

    ok_b, detail_b = run_gate_verdict(cfg, runs["candidate_b"])
    assert not ok_b
    nb = detail_b["numeric_lookup"]
    assert not nb["ok"] and nb["rate"] < nb["gate"]    # fails exactly the numeric gate
    others = {k: d for k, d in detail_b.items() if k != "numeric_lookup"}
    assert all(d["ok"] for d in others.values())       # ... and ONLY that gate


def test_red_cells_are_reproducible_arithmetic():
    _, plan = _plan()
    run_b = next(r for r in plan.cert.runs if r.key == "candidate_b")
    fails = [ri for ri in run_b.items
             if not item_passes(ri.item.scenario, ri.item.expected, ri.got)[0]]
    assert len(fails) == 4
    for ri in fails:
        assert ri.item.run_errors.get("candidate_b") in ("sign", "units")
        assert ri.got != ri.item.expected


def test_golden_traces_exist_and_carry_their_stories():
    _, plan = _plan()
    golden = {g.key: g for g in plan.cert.golden}
    assert set(golden) == {"covenant_summary", "numeric_hallucination",
                           "correct_escalation", "dscr_trend", "citation_gap"}
    assert golden["numeric_hallucination"].answer != golden["numeric_hallucination"].expected
    assert golden["correct_escalation"].answer.answer_type == "escalated"
    assert len(golden["dscr_trend"].answer.ratios) == 3            # one DSCR per fiscal year
    assert golden["citation_gap"].answer.citations == []
    assert golden["citation_gap"].answer.answer_type == "factual"  # fluent — that's the trap
    golden_specs = {s.trace_id for s in plan.golden_specs}
    assert all(g.trace_id in golden_specs for g in plan.cert.golden)
    for s in plan.golden_specs:
        assert "golden" in s.tags


def test_session_shape_is_lognormal_median_about_seven():
    cfg, plan = _plan(scale=0.25)
    lengths = sorted(len(v) for v in plan.sessions.values())
    lengths += [1] * (len({s.session_id for s in plan.ambient_specs}) - len(lengths))
    lengths.sort()
    median = lengths[len(lengths) // 2]
    assert 4 <= median <= 11                       # log-normal median ~7
    assert max(lengths) <= cfg.generation.volume.turns_max
    p95 = lengths[int(len(lengths) * 0.95)]
    assert 12 <= p95 <= 30                          # the long tail exists


def test_flagged_pending_is_reserved():
    cfg, plan = _plan()
    assert len(plan.cert.flagged_pending_trace_ids) == cfg.certification.n_flagged_reserved
    sources = {it.source_trace_id for it in plan.cert.suite if it.source_trace_id}
    assert set(plan.cert.flagged_pending_trace_ids).isdisjoint(sources)
    assert plan.cert.flagged_pending_trace_ids[0] in plan.queue_pending_ids


def test_golden_numeric_is_the_corrected_suite_item():
    _, plan = _plan()
    golden = next(g for g in plan.cert.golden if g.key == "numeric_hallucination")
    sourced = [it for it in plan.cert.suite if it.source_trace_id == golden.trace_id]
    assert len(sourced) == 1 and sourced[0].scenario == "numeric_lookup"
    assert golden.trace_id in plan.queue_completed_ids


def test_queue_is_alive():
    cfg, plan = _plan()
    assert len(plan.queue_completed_ids) == cfg.certification.queue.n_completed
    assert len(plan.queue_pending_ids) == cfg.certification.queue.n_pending
    assert set(plan.queue_completed_ids).isdisjoint(plan.queue_pending_ids)


def test_trace_events_v2_structure():
    cfg, plan = _plan()
    spec = next(s for s in plan.golden_specs if s.question_kind == "trend")
    events = build_trace_events(plan.rng, cfg, spec)
    names = [e["body"].get("name") for e in events]
    assert names[0] == "copilot-turn"                       # trace shell
    assert "filings_search" in names and "document_fetch" in names
    assert names.count("table_extract") == 3                 # one per filing (trend)
    answer = next(e for e in events if e["body"].get("name") == "answer")
    assert answer["body"]["promptName"] == cfg.certification.prompt_name
    assert answer["body"]["promptVersion"] == spec.prompt_version
    inp = answer["body"]["input"]
    assert inp[0]["role"] == "system" and "analyst copilot" in inp[0]["content"]
    trace = events[0]["body"]
    assert any(t.startswith("filing-type:") for t in trace["tags"])
    assert any(t.startswith("desk:") for t in trace["tags"])
    assert trace["metadata"]["git_sha"]
    for e in events:
        b = e["body"]
        if "startTime" in b and "endTime" in b and b.get("endTime"):
            assert b["startTime"] <= b["endTime"]


def test_escalation_event_emitted():
    cfg, plan = _plan()
    spec = next(s for s in plan.golden_specs if s.question_kind == "escalation")
    events = build_trace_events(plan.rng, cfg, spec)
    assert any(e["body"].get("name") == "escalated_to_human" for e in events)


def test_tool_errors_come_with_retry_spans():
    cfg, plan = _plan(scale=0.15)
    spec = next(s for s in plan.ambient_specs
                if s.error_step and s.error_step != "generation")
    events = build_trace_events(plan.rng, cfg, spec)
    errored = [e for e in events if e["body"].get("level") == "ERROR"]
    assert errored
    retries = [e for e in events
               if (e["body"].get("metadata") or {}).get("attempt") == 2]
    assert retries, "tool errors must be followed by a retry span"


def test_prompt_era_linkage():
    cfg, plan = _plan(scale=0.15)
    from synth.seed.prompts import version_for_timestamp

    prod = cfg.certification.production_version
    versions = {version_for_timestamp(cfg, RUN_DATE, s.timestamp)
                for s in plan.ambient_specs}
    assert versions == {prod - 2, prod - 1, prod}   # the three eras all appear
    for s in plan.ambient_specs[:200]:
        assert s.prompt_version == version_for_timestamp(cfg, RUN_DATE, s.timestamp)


def test_run_traces_carry_their_model_and_run_dates_backdated():
    cfg, plan = _plan()
    models = {s.model_override for s in plan.run_trace_specs}
    assert models == {cfg.certification.incumbent_model,
                      cfg.certification.candidate_a_model,
                      cfg.certification.candidate_b_model}
    for run in plan.cert.runs:
        for ri in run.items:
            assert ri.timestamp < RUN_DATE
