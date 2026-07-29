"""The Companion Adapter shell-swap, from the kit's side (Spec G · G5, #144).

The UI goldens prove the swap moved no presenter-visible byte. This module proves the other
half: that the *shell underneath* is actually the adapter — that invocation, bind, health,
secret intake, the Langfuse clients and the LLM client come from it rather than from wiring
this kit still owns, and that the two Surfaces the adapter has to carry here (the copilot's
per-submission model selector, and the certification workbench's per-run release model) both
resolve **through** it.

Everything runs against a fake adapter and fake SDKs: no network, no real key, no billable
call. The fake is duck-typed, which is the point — the seam is a Protocol, so a kit-side
double satisfying the same shape is exactly what the real shell is substitutable for.
"""
from __future__ import annotations

import pytest

from synth.config import load_config


# ---------------------------------------------------------------------------
# Fakes: an adapter-shaped double, recording what the Surface asked it for
# ---------------------------------------------------------------------------
class _FakeLLM:
    def __init__(self, model: str):
        self.provider, self.model = "anthropic", model

    def complete(self, *, system, messages, temperature, max_tokens):
        from types import SimpleNamespace

        return SimpleNamespace(
            text='{"answer_type":"factual","answer":"Operating profit was a loss of '
                 'EUR 2,431 thousand.","figures":{"operating_profit_eur":-2431000},'
                 '"ratios":{},"citations":["F-3"],"basis":"parenthesised negative"}',
            input_tokens=311, output_tokens=57)


class _FakePrompt:
    version = 7

    def compile(self, **kw):
        return [{"role": "system", "content": "you are an analyst copilot"},
                {"role": "user", "content": kw.get("question", "")}]


class _FakeLangfuse:
    def __init__(self):
        self.dataset_items, self.flushed = [], 0

    def get_prompt(self, *a, **kw):
        return _FakePrompt()

    def create_dataset_item(self, **kw):
        from types import SimpleNamespace

        self.dataset_items.append(kw)
        return SimpleNamespace(id="item-fake-0001")

    def flush(self):
        self.flushed += 1


class _FakeIngestor:
    def __init__(self):
        self.events, self.flushed = [], 0

    def add(self, ev):
        self.events.append(ev)

    def extend(self, evs):
        self.events.extend(evs)

    def flush(self):
        self.flushed += 1


class _FakeAdapter:
    """Adapter-shaped: the six responsibilities the Surface consumes, and a record of every
    ask — including which model each ``llm()`` call named."""

    def __init__(self):
        self.lf = _FakeLangfuse()
        self.ing = _FakeIngestor()
        self.models_asked: list[str | None] = []

    def langfuse(self):
        return self.lf

    def llm(self, model=None):
        self.models_asked.append(model)
        return _FakeLLM(model or "adapter-default-model")

    def ingestor(self, **kw):
        return self.ing


@pytest.fixture()
def cfg():
    return load_config("config/demo.yaml")


@pytest.fixture()
def adapter():
    return _FakeAdapter()


@pytest.fixture()
def no_project_probe(monkeypatch):
    """Stub the demo-project guardrail: it is a raw REST call against a live instance, and
    every test here is about client provenance, not about that probe."""
    monkeypatch.setattr("synth.live.submit.assert_demo_project",
                        lambda base_url, hint: ("proj-fake", "demo"))


# ---------------------------------------------------------------------------
# The kit no longer owns a shell
# ---------------------------------------------------------------------------
def test_kit_no_longer_ships_its_own_llm_module():
    """``src/synth/llm.py`` was byte-identical to the core resolution module; provider
    routing now lives in — and is tested by — core, so the duplicate is gone for good."""
    with pytest.raises(ModuleNotFoundError):
        import synth.llm  # noqa: F401


def test_declared_live_secrets_match_the_manifest():
    """What the CLI hands the adapter as ``requires_secrets`` is what the portal actually
    injects, per ``usecase.yaml``'s live component — the adapter reads exactly these."""
    import yaml

    from synth.cli import LIVE_SECRETS

    manifest = yaml.safe_load(open("usecase.yaml"))
    component = manifest["live_components"][0]
    assert component["requires_secrets"] == LIVE_SECRETS


# ---------------------------------------------------------------------------
# Copilot: the model selector resolves through the adapter
# ---------------------------------------------------------------------------
def test_live_submission_takes_every_client_from_the_adapter(cfg, adapter, no_project_probe):
    from synth.live.prefabs import build_prefabs
    from synth.live.submit import submit

    question = build_prefabs(cfg.generation.seed)[0].question
    res = submit(cfg, question, "claude-sonnet-4-6", adapter=adapter, log=lambda m: None)

    # the model the analyst SELECTED is what the adapter was asked to resolve
    assert adapter.models_asked == ["claude-sonnet-4-6"]
    assert res["model"] == "claude-sonnet-4-6"
    # the trace went out through the adapter's write client, and was flushed
    assert adapter.ing.events and adapter.ing.flushed == 1
    assert res["trace_id"] and res["prompt_version"] == 7


def test_model_selector_default_is_the_incumbent(cfg, adapter, no_project_probe):
    """No explicit selection (the copilot's default option) still resolves the incumbent
    through the adapter — the selector's default is scenario knowledge, kept kit-side."""
    from synth.live.prefabs import build_prefabs
    from synth.live.submit import submit

    question = build_prefabs(cfg.generation.seed)[0].question
    submit(cfg, question, None, adapter=adapter, log=lambda m: None)
    assert adapter.models_asked == [cfg.certification.incumbent_model]


def test_each_candidate_model_resolves_exactly_as_named(cfg, adapter):
    """The invariant the whole certification story turns on: with no deployment pin, an
    explicitly named candidate is honoured exactly, so incumbent-vs-candidate stays a real
    comparison. Provider routing moved to core in this swap; this pins that the kit's
    multi-candidate lever still survives the trip through the adapter."""
    cert = cfg.certification
    for model in (cert.incumbent_model, cert.candidate_a_model, cert.candidate_b_model):
        assert adapter.llm(model).model == model
    assert adapter.models_asked == [cert.incumbent_model, cert.candidate_a_model,
                                    cert.candidate_b_model]


def test_flagging_an_answer_writes_through_the_adapter(cfg, adapter, monkeypatch):
    from synth.live.submit import thumbs_down

    monkeypatch.setattr("synth.live.submit.assert_demo_project",
                        lambda base_url, hint: ("proj-fake", "demo"))
    res = thumbs_down(cfg, "a" * 32, "the filing prints (2,431)", adapter=adapter,
                      log=lambda m: None)
    assert adapter.ing.flushed == 1
    assert len(adapter.ing.events) == 1
    assert res["comment"] == "the filing prints (2,431)"


def test_headless_submit_path_still_builds_its_own_clients(cfg, monkeypatch, no_project_probe):
    """`synth submit` has no adapter, so the clients come off the core resolution module and
    the env exactly as before the swap — the swap must not have made the CLI depend on a
    shell it never runs inside."""
    from synth.live.prefabs import build_prefabs
    from synth.live.submit import submit

    asked = {}

    def fake_get_llm(model=None):
        asked["model"] = model
        return _FakeLLM(model)

    monkeypatch.setattr("langfuse_synth_core.companion.llm.get_llm", fake_get_llm)
    monkeypatch.setattr("langfuse_synth_core.lfclient.get_langfuse",
                        lambda c: _FakeLangfuse())
    monkeypatch.setattr("langfuse_synth_core.seed.ingest.Ingestor.from_env",
                        classmethod(lambda cls, base, **kw: _FakeIngestor()))

    question = build_prefabs(cfg.generation.seed)[0].question
    submit(cfg, question, "claude-sonnet-4-6", log=lambda m: None)
    assert asked["model"] == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Workbench: the divergent Surface plugs in, with nothing pushed back
# ---------------------------------------------------------------------------
def test_workbench_run_resolves_the_release_model_through_the_adapter(cfg, adapter, monkeypatch,
                                                                     tmp_path):
    """The workbench's release model is a *second* per-request model — chosen in a saved spec
    rather than a dropdown — and it resolves through the same adapter seam. This is the ring's
    load-bearing case: the workbench needed one argument on `llm()`, not a concession from
    the boundary."""
    from synth.workbench import runner as runner_mod
    from synth.workbench.specs import ExperimentSpec, Gates, Release, Target

    cfg.workbench.results_dir = str(tmp_path / ".workbench")
    spec = ExperimentSpec(
        name="wiring", release=Release(model="claude-haiku-4-5", prompt_name="analyst-copilot",
                                       prompt_version=7),
        targets=[Target(dataset_name="certification-suite")],
        evaluators=[], gates=Gates(threshold=0.98))

    # Stop after the clients are resolved: get_dataset raising ends the run, and the runner
    # records the error rather than killing the thread — which is what we want here.
    monkeypatch.setattr(_FakeLangfuse, "get_dataset",
                        lambda self, name: (_ for _ in ()).throw(RuntimeError("stop here")),
                        raising=False)
    run_id = "wb-wiring-000001"
    monkeypatch.setitem(runner_mod.RUNS, run_id,
                        {"state": "running", "progress": 0, "total": 0, "message": "starting"})
    runner_mod._execute(cfg, spec, run_id, adapter)

    assert adapter.models_asked == ["claude-haiku-4-5"]


def test_workbench_router_is_handed_the_adapter(cfg, adapter, monkeypatch, tmp_path):
    """The mounted router receives the adapter from ``create_app`` and forwards it to the
    action that triggers a run — the workbench plugs in *through* the shell, not around it."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    import synth.workbench.views as views_mod
    from synth.live.app import create_app
    from synth.workbench.catalog import offline_catalog
    from synth.workbench.specs import ExperimentSpec, Gates, Release, Target, save_spec

    cfg.workbench.results_dir = str(tmp_path / ".workbench")
    monkeypatch.setattr(views_mod, "fetch_catalog", lambda c, with_items=True: offline_catalog(c))
    views_mod._CATALOG_CACHE.clear()
    spec = save_spec(cfg, ExperimentSpec(
        name="routed", release=Release(model="claude-sonnet-4-6"),
        targets=[Target(dataset_name="certification-suite")], evaluators=[],
        gates=Gates(threshold=0.98)))

    seen = {}
    monkeypatch.setattr(views_mod.runner_mod, "start_run",
                        lambda c, s, *, adapter=None: (seen.setdefault("adapter", adapter),
                                                       "wb-routed-000001")[1:] + ("",))

    client = TestClient(create_app(cfg, adapter))
    resp = client.post("/workbench/runs", data={"spec_ref": spec.ref}, follow_redirects=False)
    assert resp.status_code == 303
    assert seen["adapter"] is adapter


def test_promote_writes_the_dataset_item_through_the_adapter(cfg, adapter, monkeypatch,
                                                             tmp_path):
    """The promote wizard's SDK write was threaded through the adapter in #144 but sat
    unreachable behind the route's pre-existing crash (depot issue #155). With that fixed,
    this proves the newly-live path end to end: POST /workbench/promote resolves its client
    via ``adapter.langfuse()``, the dataset item carries the trace provenance, and the write
    is flushed — the trace *lookup* stays on the module's own REST reader by design."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    import synth.workbench.promote as promote_mod
    import synth.workbench.views as views_mod
    from synth.live.app import create_app
    from synth.workbench.catalog import offline_catalog

    cfg.workbench.results_dir = str(tmp_path / ".workbench")
    monkeypatch.setattr(views_mod, "fetch_catalog", lambda c, with_items=True: offline_catalog(c))
    views_mod._CATALOG_CACHE.clear()
    monkeypatch.setattr(promote_mod, "_get",
                        lambda base, path, params=None: {"input": {"question": "op profit?"}})

    client = TestClient(create_app(cfg, adapter))
    resp = client.post("/workbench/promote", data={
        "trace_id": "a" * 32, "dataset_name": "certification-suite",
        "slice_name": "production_flagged", "requirement_ids": "MRM-ACC-1, MRM-ACC-2",
        "expected_output_json": '{"figures": {"operating_profit_eur": -2431000}}',
    }, follow_redirects=False)

    assert resp.status_code == 303
    from urllib.parse import unquote

    assert "ok=item item-fake-0001 in certification-suite" in unquote(resp.headers["location"])
    item = adapter.lf.dataset_items[0]
    assert item["source_trace_id"] == "a" * 32
    assert item["input"] == {"question": "op profit?"}
    assert item["expected_output"] == {"figures": {"operating_profit_eur": -2431000}}
    assert item["metadata"]["requirement_ids"] == ["MRM-ACC-1", "MRM-ACC-2"]
    assert adapter.lf.flushed == 1


# ---------------------------------------------------------------------------
# The full inherit path: what `synth playground` actually starts
# ---------------------------------------------------------------------------
def test_playground_verb_runs_the_full_inherit_path(monkeypatch):
    """The CLI builds a real ``CompanionAdapter`` from the manifest's values and hands it the
    Surface factory — so bind, the readiness health route, and graceful shutdown are all
    inherited rather than re-implemented here (this kit's ``uvicorn.run`` call is gone)."""
    pytest.importorskip("fastapi")
    from langfuse_synth_core.companion import CompanionAdapter

    from synth import cli

    served = {}
    monkeypatch.setattr(CompanionAdapter, "serve",
                        lambda self, app, *, host, port, **kw: served.update(
                            app=app, host=host, port=port, adapter=self))

    cli.playground(config="config/demo.yaml", host="0.0.0.0", port=8080)

    adapter = served["adapter"]
    assert (served["host"], served["port"]) == ("0.0.0.0", 8080)
    assert list(adapter.requires_secrets) == cli.LIVE_SECRETS
    assert adapter.health_path == CompanionAdapter.DEFAULT_HEALTH_PATH
    # the adapter's own default is the incumbent; per-submission choices ride llm(model)
    assert adapter.llm_model_default == load_config("config/demo.yaml").certification.incumbent_model
    # the Surface it serves is this kit's app, built WITH the adapter
    assert served["app"].title.startswith("Meridian")


def test_health_route_carries_the_readiness_report(monkeypatch):
    """The adapter's health route is mounted alongside the kit's own routes without colliding
    with them — the manifest keeps the in-scene index ``/`` as the portal's liveness poll,
    while ``/healthz`` carries the readiness body the adapter-lands smoke (#143) reads."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from langfuse_synth_core.companion import CompanionAdapter

    from synth.live.app import create_app

    cfg = load_config("config/demo.yaml")
    real = CompanionAdapter(cfg, requires_secrets=["LANGFUSE_PUBLIC_KEY"])
    app = create_app(cfg, real)
    real.mount_health(app)

    client = TestClient(app)
    body = client.get("/healthz").json()
    assert set(body) == {"ready", "langfuse_write_ok", "llm_bound", "detail"}
    # secret-free: names and presence only, never a value
    assert body["detail"]["secrets_present"] == {"LANGFUSE_PUBLIC_KEY": False}
    # the kit's own routes are untouched by the mount
    assert client.get("/").status_code == 200
