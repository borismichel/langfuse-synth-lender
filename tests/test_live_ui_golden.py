"""Byte-identical UI gate for the Companion Adapter shell-swap (Spec G · G5, #144).

The migration re-plumbs the live surface's *shell* onto ``langfuse-synth-core``'s Companion
Adapter (invocation/bind/health/secret-intake + ready Langfuse/LLM clients) and must not move
a single presenter-visible byte (story 13, 31 — "re-plumb the shell, never bend the Surface").
These committed goldens are that fence.

This kit is the deliberate stress case (#25 D8): its Surface is ~4.6× EV's, and the
**certification workbench** — a router mounted under ``/workbench`` with its own redirects,
plain-text responses, and raw REST reads — is exactly the divergent Surface the boundary must
carry without absorbing scenario knowledge. So the gate covers the whole workbench, not a
token page: overview, designer, specs (list + detail), runs (list + detail), compare,
coverage, the promote wizard, and the evidence pack, alongside the copilot index and the
``/dossier``. Each is captured bare *and* ``LIVE_BASE_PATH``-prefixed, since the portal
serves live deployments behind ``/live/{id}`` (LAN-357).

Discipline mirrors the kit's spool golden (``tests/golden/lender_spool.ndjson``): a committed
byte oracle plus a render-here-and-compare check. Every page renders deterministically with
**no live Langfuse read** — the catalog is pinned to ``offline_catalog`` (the deterministic
plan's view, as ``test_workbench.py`` already does), the state is a fixed ``RunState``, and
the runs/specs are fixtures written into a tmp results dir. To re-bless after an *intended*
surface change, delete ``tests/golden/ui/`` and re-run: the fixtures are regenerated below.
"""
from __future__ import annotations

from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / "golden" / "ui"

# Fixed, non-secret connection identity so every "open in Langfuse" deep link renders to a
# stable string. The project id is what ``Links.from_cfg`` reads out of the run state.
STATE_BASE_URL = "https://demo.langfuse.example"
STATE_PROJECT_ID = "clproj0000demo0001"

RUN_ID = "wb-golden-000001"
RUN_ID_B = "wb-golden-000002"

# Every presenter-visible route, as {name: path}. Rendered in both serving modes.
ROUTES = {
    "index": "/",
    "dossier": "/dossier",
    "wb_overview": "/workbench/",
    "wb_designer": "/workbench/designer",
    "wb_specs": "/workbench/specs",
    "wb_spec_detail": "/workbench/specs/golden-cert-v1",
    "wb_runs": "/workbench/runs",
    "wb_run_detail": f"/workbench/runs/{RUN_ID}",
    "wb_compare": f"/workbench/compare?a={RUN_ID}&b={RUN_ID_B}",
    "wb_coverage": "/workbench/coverage",
    "wb_evidence": f"/workbench/evidence/{RUN_ID}",
    "wb_promote": "/workbench/promote",
}


def _seed_state() -> None:
    """Persist a RunState so ``/dossier`` renders the full dossier (not the "no dossier yet"
    placeholder) and the workbench deep links resolve. Values are fixed, not seeded, so the
    oracle is independent of the generator's volume knobs."""
    from synth.state import RunState

    RunState(
        base_url=STATE_BASE_URL,
        project_name="demo",
        run_date="2026-06-09",
        prompt_name="analyst-copilot",
        prompt_versions={"latest": 7, "production": 7},
        incumbent_model="claude-sonnet-4-5",
        candidate_a_model="claude-sonnet-4-6",
        candidate_b_model="claude-haiku-4-5",
        judge_model="claude-sonnet-4-6",
        baseline_run_date="2026-06-02",
        candidate_run_date="2026-06-06",
        suites={"certification_suite": {
            "name": "certification-suite", "items": 72,
            "gates": {"numeric_lookup": 0.98, "out_of_scope": 1.0},
            "runs": {
                "cert-baseline": {"model": "claude-sonnet-4-5", "date": "2026-06-02",
                                  "verdict": "baseline", "pass_rates": {"numeric_lookup": 0.86}},
                "cert-candidate-a": {"model": "claude-sonnet-4-6", "date": "2026-06-06",
                                     "verdict": "pass", "pass_rates": {"numeric_lookup": 0.99}},
                "cert-candidate-b": {"model": "claude-haiku-4-5", "date": "2026-06-06",
                                     "verdict": "fail", "pass_rates": {"numeric_lookup": 0.74}},
            }}},
        queue={"name": "certification-intake", "id": "q-golden", "completed": 18, "pending": 3},
        golden=[{"key": "numeric", "title": "Sign-flipped operating profit",
                 "trace_id": "a" * 32}],
        flagged_pending=[{"borrower": "Nordwind Logistik GmbH", "case_id": "CASE-4471",
                          "incumbent_figure_eur": 2431000, "correct_figure_eur": -2431000}],
        project_id=STATE_PROJECT_ID,
    ).save()


def _seed_spec(cfg):
    """One saved spec, so the specs list and the spec-detail page render real content.
    ``ExperimentSpec`` carries no timestamp, so its canonical JSON and hash are stable."""
    from synth.workbench.specs import ExperimentSpec, Gates, Release, Target, save_spec

    return save_spec(cfg, ExperimentSpec(
        name="golden cert", release=Release(model="claude-sonnet-4-6",
                                            prompt_name="analyst-copilot", prompt_version=7),
        targets=[Target(dataset_name="certification-suite", slices=["numeric_lookup"])],
        evaluators=["numeric_accuracy", "citation_format"],
        gates=Gates(threshold=0.98, slice_overrides={"out_of_scope": 1.0}),
        created_by="builder", notes="blessed fixture for the byte-identical gate"))


def _row(item_id: str, passed: bool, slice_name: str = "numeric_lookup"):
    return {"dataset": "certification-suite", "item_id": item_id, "slice": slice_name,
            "passed": passed,
            "scores": {"numeric_accuracy": {"value": "pass" if passed else "fail",
                                            "comment": "" if passed else "sign flipped"}},
            "trace_id": item_id,
            "trace_url": f"{STATE_BASE_URL}/project/{STATE_PROJECT_ID}/traces/{item_id}",
            "detail": "" if passed else "sign flipped"}


def _seed_runs(cfg, spec) -> None:
    """Two finished runs: the detail/evidence subject and a second one so the run-detail page
    offers the compare form and ``/workbench/compare`` has both sides."""
    from synth.workbench.results import WorkbenchRun, gate_verdicts, save_run

    rows_a = [_row("item0001", True), _row("item0002", False),
              _row("item0003", True, "out_of_scope")]
    rows_b = [_row("item0001", True), _row("item0002", True),
              _row("item0003", False, "out_of_scope")]
    for run_id, rows, model in ((RUN_ID, rows_a, "claude-sonnet-4-6"),
                                (RUN_ID_B, rows_b, "claude-sonnet-4-5")):
        save_run(cfg, WorkbenchRun(
            run_id=run_id, spec_ref=spec.ref, spec_hash=spec.spec_hash, spec=spec.model_dump(),
            release={"model": model, "prompt_name": "analyst-copilot", "prompt_version": 7,
                     "temperature": 0.0},
            evaluator_shas={"numeric_accuracy": "n" * 64, "citation_format": "c" * 64},
            started="2026-06-09T09:00:00+00:00", finished="2026-06-09T09:04:00+00:00",
            state="done", rows=rows, gates=gate_verdicts(rows, spec.model_dump()),
            langfuse_runs=[{"dataset": "certification-suite", "run_name": f"{spec.ref}-{model}",
                            "runs_url": f"{STATE_BASE_URL}/project/{STATE_PROJECT_ID}"
                                        f"/datasets/ds-golden/runs"}]))


def _client(monkeypatch, tmp_path, base_path: str | None):
    """Build a TestClient over the live app in the given serving mode, with every live read
    pinned to its deterministic offline equivalent.

    ``base_path`` None → bare serving (env unset); a string → that ``LIVE_BASE_PATH``."""
    from fastapi.testclient import TestClient

    import synth.workbench.views as views_mod
    from synth.config import load_config
    from synth.live.app import create_app
    from synth.workbench.catalog import offline_catalog

    monkeypatch.setenv("SYNTH_STATE_DIR", str(tmp_path))
    _seed_state()

    if base_path is None:
        monkeypatch.delenv("LIVE_BASE_PATH", raising=False)
    else:
        monkeypatch.setenv("LIVE_BASE_PATH", base_path)

    cfg = load_config("config/demo.yaml")
    cfg.workbench.results_dir = str(tmp_path / ".workbench")
    # The catalog is the workbench's only live Langfuse read; offline_catalog is the
    # deterministic plan's view of the same suites (test_workbench.py uses it the same way).
    monkeypatch.setattr(views_mod, "fetch_catalog", lambda c, with_items=True: offline_catalog(c))
    views_mod._CATALOG_CACHE.clear()

    spec = _seed_spec(cfg)
    assert spec.ref == "golden-cert-v1", spec.ref  # the ROUTES entry above pins this
    _seed_runs(cfg, spec)
    return TestClient(create_app(cfg))


@pytest.mark.parametrize("mode, base_path", [("bare", None), ("prefixed", "/live/x")])
@pytest.mark.parametrize("route", sorted(ROUTES))
def test_presenter_output_byte_identical(monkeypatch, tmp_path, mode, base_path, route):
    """Every presenter-visible route is byte-identical to its committed golden, before and
    after the shell-swap. Any diff fails the gate."""
    pytest.importorskip("fastapi")
    client = _client(monkeypatch, tmp_path, base_path)
    resp = client.get(ROUTES[route])
    assert resp.status_code == 200, f"{route} returned {resp.status_code}"
    golden = GOLDEN / f"{route}.{mode}.html"
    if not golden.exists():
        # Bless-on-missing, and FAIL: a fixture is never silently created into a green run.
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(resp.text)
        pytest.fail(f"blessed tests/golden/ui/{golden.name} — review the diff, commit it, "
                    f"and re-run to gate against it")
    assert resp.text == golden.read_text(), (
        f"{route} ({mode}) drifted from tests/golden/ui/{golden.name}; the shell-swap must "
        f"not move a presenter-visible byte (Spec G · G5 story 13/31)."
    )


@pytest.mark.parametrize("mode, base_path", [("bare", None), ("prefixed", "/live/x")])
def test_workbench_non_html_responses_hold(monkeypatch, tmp_path, mode, base_path):
    """The workbench's *non-HTML* Surface concerns — the 303 redirects it answers form posts
    with, and the plain-text evidence download — are presenter-visible too, and the swap must
    carry them unchanged. They are asserted here rather than as HTML goldens because their
    payload is a status + header, not a body."""
    pytest.importorskip("fastapi")
    client = _client(monkeypatch, tmp_path, base_path)
    prefix = base_path or ""

    # POST /workbench/role → 303 back to the (prefixed) overview, carrying the role cookie.
    resp = client.post("/workbench/role", data={"role": "approver"}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == f"{prefix}/workbench"
    assert resp.cookies["wb_role"] == "approver"

    # Evidence download is 4-eyes gated: plain text, 403, until an Approver has signed off.
    resp = client.get(f"/workbench/evidence/{RUN_ID}?download=1")
    assert resp.status_code == 403
    assert resp.headers["content-type"].startswith("text/plain")
    assert resp.text == ("evidence download requires sign-off (4-eyes) — preview without "
                         "?download=1")

    # An unknown run is a plain-text 404, not an HTML error page or a raw 500.
    resp = client.get("/workbench/evidence/wb-does-not-exist")
    assert resp.status_code == 404 and resp.text == "unknown run"


def test_goldens_capture_the_real_surface_not_an_empty_one():
    """Guard the fixtures themselves: each golden is the *populated* page (not a placeholder
    or an offline stub) — except promote, whose offline degradation notice *is* its
    deterministic surface — and the prefixed variants actually carry ``/live/x`` — so the
    byte-identical assertions above fence the real presenter surface."""
    def bare(name: str) -> str:
        return (GOLDEN / f"{name}.bare.html").read_text()

    def prefixed(name: str) -> str:
        return (GOLDEN / f"{name}.prefixed.html").read_text()

    # copilot index: the live form, the model selector, the two staff routes
    assert 'action="/ask"' in bare("index") and 'action="/live/x/ask"' in prefixed("index")
    assert "claude-sonnet-4-5 · incumbent" in bare("index")
    # dossier: the full certification record, not "No dossier yet"
    assert "No dossier yet" not in bare("dossier")
    assert "certification dossier" in bare("dossier")
    assert 'href="/live/x/workbench"' in prefixed("dossier")
    # workbench: real suites, specs, runs, gate verdicts, coverage gaps, evidence
    assert "72 items" in bare("wb_overview")
    assert "golden-cert-v1" in bare("wb_specs") and "no specs yet" not in bare("wb_specs")
    assert "certification-suite" in bare("wb_run_detail") and "FAIL" in bare("wb_run_detail")
    assert "regressed" in bare("wb_compare") or "improved" in bare("wb_compare")
    assert "UNCOVERED" in bare("wb_coverage")
    assert "UNSIGNED" in bare("wb_evidence")
    # promote (fixed by depot issue #155): the offline degradation notice, never a raw 500
    assert "offline — promotion needs the live instance" in bare("wb_promote")
    assert "Queue is clear" in bare("wb_promote")
    assert "/live/x/workbench/promote" in prefixed("wb_promote")
    assert "/live/x/workbench/runs" in prefixed("wb_run_detail")
    assert "/live/x/workbench/coverage" in prefixed("wb_overview")
