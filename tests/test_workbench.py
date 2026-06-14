"""Validation Workbench: code-injection acceptance, specs, results, coverage, pages."""
import json
from datetime import datetime, timezone

import pytest

from synth.config import load_config
from synth.workbench import registry
from synth.workbench.catalog import offline_catalog
from synth.workbench.requirements import coverage_matrix, load_register
from synth.workbench.results import (
    WorkbenchRun,
    aggregates,
    compare,
    filter_rows,
    gate_verdicts,
    list_runs,
    save_run,
)
from synth.workbench.specs import ExperimentSpec, Gates, Release, Target, load_spec, save_spec


@pytest.fixture()
def cfg(tmp_path):
    c = load_config("config/demo.yaml")
    c.workbench.results_dir = str(tmp_path / ".workbench")
    return c


# ---------------------------------------------------------------------------
# Registry — the injected-code acceptance pipeline
# ---------------------------------------------------------------------------
def test_seeded_evaluators_discover_clean():
    evs = registry.discover_evaluators()
    names = {e.name for e in evs if not e.error}
    assert {"numeric_accuracy", "citation_format", "escalation_correctness"} <= names
    assert all(len(e.sha256) == 64 for e in evs)


def test_template_passes_pipeline(cfg):
    assert registry.validate_evaluator_code(registry.EVALUATOR_TEMPLATE) == []
    assert registry.smoke_run_evaluator(cfg, registry.EVALUATOR_TEMPLATE) is None


def test_bad_signature_rejected():
    errors = registry.validate_evaluator_code("def evaluator(input, output):\n    return 1\n")
    assert any("keyword-only" in e for e in errors)
    assert any("NAME" in e for e in errors)


def test_crashing_code_rejected_by_smoke_run(cfg):
    code = (
        "NAME = 'boom'\n"
        "def evaluator(*, input, output, expected_output, metadata=None, **kwargs):\n"
        "    raise RuntimeError('boom')\n")
    assert registry.validate_evaluator_code(code) == []
    assert "boom" in (registry.smoke_run_evaluator(cfg, code) or "")


def test_save_evaluator_roundtrip(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "EVALUATORS_DIR", tmp_path / "evals")
    code = registry.EVALUATOR_TEMPLATE.replace('"my_check"', '"tmp_check"')
    reg, errors = registry.save_evaluator(cfg, "tmp_check.py", code)
    assert errors == [] and reg is not None and reg.name == "tmp_check"
    again = registry.discover_evaluators()
    assert any(e.name == "tmp_check" for e in again)


# ---------------------------------------------------------------------------
# Specs — versioning + stable hash
# ---------------------------------------------------------------------------
def _spec():
    return ExperimentSpec(
        name="Q3 cert", release=Release(model="claude-sonnet-4-6", prompt_version=1),
        targets=[Target(dataset_name="certification-suite",
                        slices=["numeric_lookup"])],
        evaluators=["numeric_accuracy"],
        gates=Gates(threshold=0.95, slice_overrides={"out_of_scope": 1.0}))


def test_spec_versioning_and_hash(cfg):
    a = save_spec(cfg, _spec())
    b = save_spec(cfg, _spec())
    assert (a.version, b.version) == (1, 2)
    assert a.slug == b.slug == "q3-cert"
    loaded = load_spec(cfg, a.ref)
    assert loaded.spec_hash == a.spec_hash          # stable across round-trip
    assert a.spec_hash != b.spec_hash               # version is part of identity


# ---------------------------------------------------------------------------
# Results — filtering, aggregation, gates, compare
# ---------------------------------------------------------------------------
def _row(item_id, passed, slice_name="numeric_lookup", ds="certification-suite"):
    return {"dataset": ds, "item_id": item_id, "slice": slice_name, "passed": passed,
            "scores": {"numeric_accuracy": {"value": "pass" if passed else "fail",
                                            "comment": "" if passed else "sign flipped"}},
            "trace_id": "", "trace_url": "", "detail": "" if passed else "sign flipped"}


def _run(cfg, run_id, rows):
    spec = _spec().model_dump()
    run = WorkbenchRun(run_id=run_id, spec_ref="q3-cert-v1", spec_hash="x" * 64, spec=spec,
                       release={"model": "claude-sonnet-4-6"}, evaluator_shas={},
                       started=datetime.now(timezone.utc).isoformat(), state="done",
                       rows=rows, gates=gate_verdicts(rows, spec))
    save_run(cfg, run)
    return run


def test_filters_aggregates_and_gates(cfg):
    rows = [_row("i1", True), _row("i2", False), _row("i3", True, "covenant")]
    run = _run(cfg, "wb-test-aaa111", rows)
    assert len(filter_rows(run, verdict="fail")) == 1
    assert len(filter_rows(run, slice_name="covenant")) == 1
    assert len(filter_rows(run, evaluator="numeric_accuracy")) == 1  # rows where it failed
    aggs = aggregates(run)
    assert aggs["certification-suite"]["n"] == 3
    g = run.gates[0]
    assert not g["ok"] and abs(g["pass_rate"] - 2 / 3) < 1e-9     # 66% < 98% gate
    assert list_runs(cfg)[0].run_id == "wb-test-aaa111"


def test_gate_slice_override(cfg):
    rows = [_row(f"i{n}", True) for n in range(19)] + [_row("oos", False, "out_of_scope")]
    verdicts = gate_verdicts(rows, _spec().model_dump())
    g = verdicts[0]
    assert g["pass_rate"] == 0.95                                  # meets the base gate…
    assert not g["ok"]                                             # …but the out_of_scope override is 0/1
    assert g["slice_detail"]["out_of_scope"]["ok"] is False


def test_compare_alignment(cfg):
    a = _run(cfg, "wb-test-bbb222", [_row("i1", False), _row("i2", True)])
    b = _run(cfg, "wb-test-ccc333", [_row("i1", True), _row("i2", True)])
    rows = compare(a, b)
    assert {x["item_id"]: x["delta"] for x in rows} == {"i1": "improved", "i2": "="}


# ---------------------------------------------------------------------------
# Requirements & coverage
# ---------------------------------------------------------------------------
def test_register_loads_and_coverage_flags_gaps(cfg):
    register = load_register()
    ids = {r.id for r in register}
    assert {"MRM-ACC-1", "MRM-CON-4", "MRM-ROB-1", "MRM-FAIR-1"} <= ids
    cov = coverage_matrix(cfg, offline_catalog(cfg))
    by_id = {c.requirement.id: c for c in cov}
    assert by_id["MRM-ACC-1"].covered and by_id["MRM-ACC-1"].items
    assert "numeric_accuracy" in by_id["MRM-ACC-1"].evaluators
    assert by_id["MRM-CON-4"].covered                              # out_of_scope scenario
    assert not by_id["MRM-ROB-1"].covered                          # the deliberate gap
    assert not by_id["MRM-FAIR-1"].covered


def test_offline_catalog_matches_suites(cfg):
    cat = offline_catalog(cfg)
    assert not cat.online
    names = {d.name: d.n_items for d in cat.datasets}
    assert names == {"certification-suite": 72}
    suite = cat.dataset("certification-suite")
    assert suite.slices == {"summary": 14, "numeric_lookup": 22, "trend": 10,
                            "covenant": 14, "out_of_scope": 12}
    assert all("requirement_ids" in (it.get("metadata") or {}) for it in suite.items)


# ---------------------------------------------------------------------------
# Pages render offline (TestClient over the mounted app)
# ---------------------------------------------------------------------------
def test_workbench_pages_render_offline(cfg, monkeypatch):
    from fastapi.testclient import TestClient

    import synth.workbench.views as views_mod
    from synth.live.app import create_app

    monkeypatch.setattr(views_mod, "fetch_catalog",
                        lambda c, with_items=True: offline_catalog(c))
    views_mod._CATALOG_CACHE.clear()
    client = TestClient(create_app(cfg))
    for path in ("/workbench/", "/workbench/designer", "/workbench/specs",
                 "/workbench/runs", "/workbench/coverage"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert "Validation" in resp.text or "workbench" in resp.text.lower(), path
    assert "UNCOVERED" in client.get("/workbench/coverage").text
    # spec → run-detail page for a stored run
    rows = [_row("i1", True)]
    _run(cfg, "wb-test-ddd444", rows)
    resp = client.get("/workbench/runs/wb-test-ddd444")
    assert resp.status_code == 200 and "Gate" in resp.text
    # evidence preview is open; download is sign-off-gated
    assert client.get("/workbench/evidence/wb-test-ddd444").status_code == 200
    assert client.get("/workbench/evidence/wb-test-ddd444?download=1").status_code == 403


def test_deep_links_are_project_scoped_or_absent():
    from synth.workbench.links import Links

    lf = Links("http://localhost:3000", "proj-123")
    assert lf.trace("abc") == "http://localhost:3000/project/proj-123/traces/abc"
    assert lf.dataset_runs("ds-1") == "http://localhost:3000/project/proj-123/datasets/ds-1/runs"
    assert lf.dataset_item("ds-1", "it-9").endswith("/datasets/ds-1/items/it-9")
    assert lf.prompt("analyst-copilot").endswith("/prompts/analyst-copilot")
    assert lf.queue("q-7").endswith("/annotation-queues/q-7")
    assert lf.evals().endswith("/project/proj-123/evals")
    # unknown project id -> no link, never a broken one
    none = Links("http://localhost:3000", "")
    assert none.trace("abc") == "" and none.datasets() == "" and none.evals() == ""


def test_evidence_pack_contents(cfg):
    run = _run(cfg, "wb-test-eee555", [_row("i1", True), _row("i2", False)])
    run.signoff = {"by": "j.weiss", "role": "approver", "note": "ok", "at": "2026-06-11T10:00:00"}
    from synth.workbench.signoff import evidence_markdown

    md = evidence_markdown(cfg, run)
    assert "x" * 12 in md.replace("`", "")          # spec hash appears
    assert "sign flipped" in md                      # failure reasons included
    assert "j.weiss" in md
    assert json.loads(md.split("```json")[1].split("```")[0])  # canonical spec embedded


def test_code_evaluators_survive_string_output():
    """Regression: a UI Prompt Experiment yields a TEXT/JSON-string output, so the
    code evaluators must coerce before .get() — never raise 'str' object has no
    attribute 'get'. Compile each source and run it across output shapes."""
    from dataclasses import dataclass
    from types import SimpleNamespace

    from synth.workbench.judges import CODE_EVALUATORS

    @dataclass
    class Score:
        name: str
        value: object
        data_type: str
        comment: str = None
        config_id: str = None
        metadata: dict = None

    @dataclass
    class EvaluationResult:
        scores: list

    expected = {"answer_type": "factual", "figures": {"revenue": 100},
                "ratios": {"dscr": 1.5}, "citations": ["F-1"]}
    structured_json = ('{"answer_type":"factual","figures":{"revenue":100},'
                       '"ratios":{"dscr":1.5},"citations":["F-1"]}')
    outputs = [
        expected,                                            # run_experiment dict
        structured_json,                                     # prompt-experiment JSON string
        {"role": "assistant", "content": structured_json},   # chat wrapper
        "The revenue was about 100 million.",                # free text
        None,                                                # missing
    ]
    for name, src in CODE_EVALUATORS.items():
        ns = {"Score": Score, "EvaluationResult": EvaluationResult}
        exec(compile(src, f"<{name}>", "exec"), ns)
        for out in outputs:
            ctx = SimpleNamespace(
                observation=SimpleNamespace(output=out),
                experiment=SimpleNamespace(item_expected_output=expected))
            res = ns["evaluate"](ctx)                         # must not raise
            assert res.scores and res.scores[0].name == name
            assert res.scores[0].value in ("pass", "fail")
        # the three structured shapes all pass; text/None fail gracefully
        def val(out):
            ctx = SimpleNamespace(observation=SimpleNamespace(output=out),
                                  experiment=SimpleNamespace(item_expected_output=expected))
            return ns["evaluate"](ctx).scores[0].value
        assert val(expected) == "pass"
        assert val(structured_json) == "pass"
        assert val("plain text") == "fail"
