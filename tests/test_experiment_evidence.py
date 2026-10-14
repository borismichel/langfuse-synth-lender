"""Real SDK experiments, with only Langfuse's HTTP service/export boundary replaced.

Run on the released image's SDK with ``pip install langfuse==5.0.1``. No model or
project credentials are used. The dataset API and exported observations are inspected
independently of scores: a scored item does not prove its judge received the evidence.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest
import requests
from langfuse import Langfuse
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from langfuse_synth_core.rng import Rng
from synth.config import load_config
from synth.content import user_turn
from synth.seed.certification import build
from synth.seed.cert_runs import seed_experiment_runs
from synth.seed.datasets import create_suite

STAMP = "2026-09-15T12:00:00+00:00"
INPUT = "langfuse.observation.input"
OUTPUT = "langfuse.observation.output"
QUESTION = "langfuse.observation.metadata.analyst_question"
PROPAGATED = "langfuse.experiment.item.metadata"


class ObservationReceiver(requests.Session):
    """Receive the real OTLP exporter's protobuf at the HTTP transport boundary."""

    def __init__(self, links):
        super().__init__()
        self.spans = []
        self.links = links

    def request(self, method, url, data=None, **kwargs):
        # Older exporters call Session.post(), which delegates here; newer transport
        # versions call request() directly. Intercept both before any network access.
        assert method.upper() == "POST"
        assert url == "http://langfuse.test/api/public/otel/v1/traces"
        payload = ExportTraceServiceRequest.FromString(data)

        def value(v):
            kind = v.WhichOneof("value")
            return ([value(x) for x in v.array_value.values] if kind == "array_value"
                    else getattr(v, kind))

        for resource in payload.resource_spans:
            for scope in resource.scope_spans:
                for span in scope.spans:
                    self.spans.append(SimpleNamespace(
                        name=span.name, parent_id=int.from_bytes(span.parent_span_id, "big"),
                        context=SimpleNamespace(span_id=int.from_bytes(span.span_id, "big"),
                                                trace_id=int.from_bytes(span.trace_id, "big")),
                        attributes={a.key: value(a.value) for a in span.attributes}))
                    attrs = self.spans[-1].attributes
                    # v5 records hosted experiment membership on OTLP task observations.
                    # Project those exported facts into the service's experiment-item read model.
                    if span.name == "experiment-item-task":
                        self.links.append({
                            "datasetItemId": attrs["langfuse.experiment.item.id"],
                            "observationId": span.span_id.hex(), "traceId": span.trace_id.hex(),
                            "runName": attrs["langfuse.experiment.name"],
                            "datasetRunId": attrs["langfuse.experiment.id"],
                            "datasetVersion": attrs.get("langfuse.experiment.item.version"),
                        })
        response = requests.Response()
        response.status_code = 200
        response._content = b""
        return response

    def get_finished_spans(self):
        return self.spans


@pytest.fixture
def plan(request):
    preset = getattr(request, "param", "presentation")
    cfg = load_config("config/demo.yaml" if preset == "presentation" else "config/cloud-demo.yaml",
                      overrides=[] if preset == "presentation" else [
                          "generation.target_traces=1000",
                          f"generation.window_days={14 if preset == 'rehearsal14' else 30}"])
    return cfg, build(cfg, Rng(cfg.generation.seed), datetime.fromisoformat(STAMP))


@pytest.fixture
def service():
    items, links, scores = {}, [], []
    dataset = {"id": "ds-cert", "name": "certification-suite", "projectId": "project",
               "metadata": {}, "createdAt": STAMP, "updatedAt": STAMP}

    def http(request):
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        if path == "/api/public/v2/datasets" and request.method == "POST":
            dataset.update(body)
            return httpx.Response(200, json=dataset)
        if path.startswith("/api/public/v2/datasets/"):
            return httpx.Response(200, json=dataset)
        if path == "/api/public/dataset-items":
            if request.method == "POST":
                row = {**body, "datasetId": dataset["id"], "status": "ACTIVE",
                       "createdAt": STAMP, "updatedAt": STAMP, "mediaReferences": []}
                items[row["id"]] = row
                return httpx.Response(200, json=row)
            return httpx.Response(200, json={"data": list(items.values()), "meta": {
                "page": 1, "limit": 100, "totalItems": len(items), "totalPages": 1}})
        if path == "/api/public/scores" and request.method == "POST":
            scores.extend(body)
            return httpx.Response(202, json={"message": "Accepted"})
        if path == "/api/public/projects":
            return httpx.Response(200, json={"data": [{"id": "project", "name": "test", "metadata": {},
                "organization": {"id": "org", "name": "test"}}]})
        if path.startswith("/api/public/v2/prompts/"):
            return httpx.Response(200, json={"name": "analyst-copilot", "version": 7,
                "type": "chat", "config": {}, "labels": ["production"], "tags": [],
                "prompt": [{"role": "system", "content": "Read the filing extracts."},
                           {"role": "user", "content": "{{question}}"}]})
        raise AssertionError(f"Unexpected SDK request: {request.method} {path}")

    receiver = ObservationReceiver(links)
    exporter = OTLPSpanExporter(endpoint="http://langfuse.test/api/public/otel/v1/traces",
                                session=receiver)
    client = Langfuse(public_key=f"pk-test-{uuid.uuid4()}", secret_key="sk-test",
                      base_url="http://langfuse.test", span_exporter=exporter, flush_interval=0.05,
                      httpx_client=httpx.Client(transport=httpx.MockTransport(http)))
    yield SimpleNamespace(lf=client, exporter=receiver, items=items, links=links, scores=scores)
    client.shutdown()


def test_sdk_v5_preserves_hosted_filing_metadata_as_one_json_value(service, plan, caplog):
    cfg, cert = plan
    cert.suite = cert.suite[:1]
    create_suite(service.lf, cfg, cert)
    stored = next(iter(service.items.values()))
    stored["metadata"].update(at_limit="x" * 200, over_limit="x" * 201)
    dataset = service.lf.get_dataset(cfg.certification.dataset.name)
    question = dataset.items[0].metadata["analyst_question"]
    assert len(json.dumps(question["excerpts"])) > 200
    with caplog.at_level(logging.WARNING, logger="langfuse"):
        result = dataset.run_experiment(name="reproduce-251", task=lambda **kw: "answer")
    assert len(result.item_results) == 1
    assert not [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    target_id = service.links[0]["observationId"]
    selected = next(s for s in service.exporter.get_finished_spans()
                    if f"{s.context.span_id:016x}" == target_id)
    metadata = json.loads(selected.attributes[PROPAGATED])
    assert metadata["analyst_question"] == question
    assert metadata["at_limit"] == "x" * 200
    assert metadata["over_limit"] == "x" * 201
    assert json.loads(selected.attributes[QUESTION]) == question


@pytest.mark.parametrize("plan", ["presentation", "rehearsal14", "rehearsal30"], indirect=True)
def test_seed_retains_complete_evidence_without_dropped_attributes(service, plan, caplog):
    cfg, cert = plan
    create_suite(service.lf, cfg, cert)
    with caplog.at_level(logging.WARNING, logger="langfuse"):
        assert seed_experiment_runs(cfg, service.lf, cert, log=lambda _: None) == 3
    assert not [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    spans = {f"{s.context.span_id:016x}": s for s in service.exporter.get_finished_spans()}
    assert len(service.links) == 216
    for link in service.links:
        item = next(it for it in cert.suite if it.item_id == link["datasetItemId"])
        selected = spans[link["observationId"]]
        assert json.loads(selected.attributes[INPUT]) == [
            {"role": "user", "content": user_turn(item.question)}]
        # Full structured comparison includes section IDs, notes, printed signs and figures.
        assert json.loads(selected.attributes[QUESTION]) == item.question.model_dump(mode="json")
        assert selected.attributes["langfuse.experiment.dataset.id"] == "ds-cert"
        assert selected.attributes["langfuse.experiment.item.id"] == item.item_id
        assert json.loads(selected.attributes["langfuse.experiment.item.expected_output"]) == (
            item.expected.model_dump(mode="json"))
        for key, value in service.items[item.item_id]["metadata"].items():
            if key == "analyst_question":
                continue
            actual = json.loads(selected.attributes[PROPAGATED])[key]
            assert actual == value
        run = next(r for r in cert.runs if link["runName"].startswith(r.run_name))
        expected = next(ri.got for ri in run.items if ri.item.item_id == item.item_id)
        assert json.loads(selected.attributes[OUTPUT]) == expected.model_dump(mode="json")

    # Captured from unmodified 25e44b3 on SDK 4.14.4, before the fix. Pin every
    # item score AND grading explanation, not only run averages or score counts.
    links = {link["traceId"]: (next(r.key for r in cert.runs
                                   if link["runName"].startswith(r.run_name)), link["datasetItemId"])
             for link in service.links}
    rows = [[*links[b["traceId"]], b["name"], b.get("value"), b.get("comment")]
            for b in service.scores if b.get("traceId") in links]
    rows.sort(key=lambda row: (row[0], row[1], row[2]))
    assert len(rows) == 1080
    assert hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest() == (
        "26e3dc8e0c300263bf0acd6aae48e6c2f664089b04907cb61b2d7cb40de43fa7")
    verdicts = [b["value"] for b in service.scores
                if b.get("datasetRunId") and b["name"] == "verdict"]
    assert verdicts == ["baseline", "pass", "fail"]


@pytest.mark.parametrize("legacy_input", [False, True])
def test_live_certification_reconstructs_hosted_question(service, plan, caplog, monkeypatch,
                                                       legacy_input):
    from synth import clients
    from synth.certify.run import certify

    cfg, cert = plan
    cert.suite = cert.suite[:1]
    source = cert.suite[0]
    create_suite(service.lf, cfg, cert)
    if legacy_input:
        service.items[source.item_id]["input"] = source.question.model_dump(mode="json")
        del service.items[source.item_id]["metadata"]["analyst_question"]
    received = []

    def complete(**kwargs):
        received.append(kwargs["messages"])
        return SimpleNamespace(text=source.expected.model_dump_json(),
                               input_tokens=300, output_tokens=100)

    monkeypatch.setattr(clients, "langfuse", lambda _: service.lf)
    monkeypatch.setattr(clients, "llm", lambda _: SimpleNamespace(model="test-model", complete=complete))
    with caplog.at_level(logging.WARNING, logger="langfuse"):
        result = certify(cfg, "test-model", log=lambda _: None)
    assert result.n_items == result.n_passed == 1
    assert received == [[{"role": "user", "content": user_turn(source.question)}]]
    assert not [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    selected = next(s for s in service.exporter.get_finished_spans()
                    if f"{s.context.span_id:016x}" == service.links[0]["observationId"])
    assert json.loads(selected.attributes[INPUT if legacy_input else QUESTION]) == (
        source.question.model_dump(mode="json"))


def test_workbench_slice_retains_identity_and_evidence(service, plan, caplog, tmp_path):
    import time
    from synth.workbench.runner import start_run, status
    from synth.workbench.specs import ExperimentSpec, Release, Target

    cfg, cert = plan
    source = cert.suite[0]
    cert.suite = [source, next(it for it in cert.suite if it.scenario != source.scenario)]
    create_suite(service.lf, cfg, cert)
    cfg.workbench.results_dir = str(tmp_path)
    received = []

    def complete(**kwargs):
        received.append(kwargs["messages"])
        return SimpleNamespace(text=source.expected.model_dump_json(),
                               input_tokens=300, output_tokens=100)

    adapter = SimpleNamespace(langfuse=lambda: service.lf,
        llm=lambda model: SimpleNamespace(model=model, complete=complete))
    spec = ExperimentSpec(name="evidence-251", release=Release(model="test-model"),
        targets=[Target(dataset_name=cfg.certification.dataset.name, slices=[source.scenario])],
        evaluators=["numeric_accuracy", "citation_format", "escalation_correctness"])
    with caplog.at_level(logging.WARNING, logger="langfuse"):
        run_id, error = start_run(cfg, spec, adapter=adapter)
        assert not error
        deadline = time.monotonic() + 15
        while status(run_id)["state"] == "running" and time.monotonic() < deadline:
            time.sleep(0.01)
    assert status(run_id)["state"] == "done", status(run_id)
    assert status(run_id)["progress"] == 1
    assert received == [[{"role": "user", "content": user_turn(source.question)}]]
    assert not [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert [link["datasetItemId"] for link in service.links] == [source.item_id]
    from synth.workbench.results import load_run
    saved = load_run(cfg, run_id)
    experiment_id = service.links[0]["datasetRunId"]
    assert saved.langfuse_runs[0]["runs_url"] == (
        f"http://langfuse.test/project/project/experiments/results?baseline={experiment_id}")
    selected = next(s for s in service.exporter.get_finished_spans()
                    if f"{s.context.span_id:016x}" == service.links[0]["observationId"])
    assert json.loads(selected.attributes[QUESTION]) == source.question.model_dump(mode="json")


def test_experiment_subset_preserves_dataset_version_and_original_task_data(service, plan):
    from synth.experiments import run_experiment

    cfg, cert = plan
    create_suite(service.lf, cfg, cert)
    dataset = service.lf.get_dataset(cfg.certification.dataset.name,
                                     version=datetime.fromisoformat(STAMP))
    before = [item.model_dump() for item in dataset.items]
    original = dataset.items[0]

    def task(*, item, **kwargs):
        assert item.model_dump() == before[0]
        return item.expected_output

    result = run_experiment(service.lf, dataset, name="frozen-subset", task=task, items=[original])
    assert len(result.item_results) == 1
    assert result.experiment_url == (
        f"http://langfuse.test/project/project/experiments/results?baseline={result.experiment_id}")
    assert service.links[0]["datasetItemId"] == original.id
    assert datetime.fromisoformat(service.links[0]["datasetVersion"]) == datetime.fromisoformat(STAMP)
    assert [item.model_dump() for item in dataset.items] == before


def test_live_runs_persist_with_read_only_seed_anchors(service, plan, monkeypatch, tmp_path):
    from synth import clients
    from synth.certify.run import certify
    from synth.live.dashboard import render_dossier
    from synth.memo import build_context
    from synth.state import RunState, state_path

    cfg, cert = plan
    source = cert.suite[0]
    cert.suite = [source]
    create_suite(service.lf, cfg, cert)
    cfg.workbench.results_dir = str(tmp_path / "workbench")
    spool = tmp_path / "spool"
    monkeypatch.setenv("SYNTH_STATE_DIR", str(spool))
    seed = RunState(base_url="http://langfuse.test", project_name="test", project_id="project",
                    run_date=STAMP, prompt_name="analyst-copilot", suites={
                        "certification_suite": {"name": "certification-suite", "runs": {
                            "seed-baseline": {"model": "incumbent", "verdict": "baseline"}}}})
    seed.save()
    from pathlib import Path
    anchors = Path(state_path())
    before = anchors.read_bytes()
    anchors.chmod(0o444)
    spool.chmod(0o555)
    monkeypatch.setattr(clients, "langfuse", lambda _: service.lf)
    monkeypatch.setattr(clients, "llm", lambda _: SimpleNamespace(model="test-model",
        complete=lambda **kw: SimpleNamespace(text=source.expected.model_dump_json(),
                                              input_tokens=300, output_tokens=100)))
    try:
        # The deployed Companion receives exactly this unwritable seed-state boundary.
        with pytest.raises(PermissionError):
            anchors.write_text("must not replace seed state")
        result = certify(cfg, "test-model", run_name="live-cert", log=lambda _: None)
        assert result.n_items == result.n_passed == 1
        assert anchors.read_bytes() == before
        assert list(RunState.load().suite["runs"]) == ["seed-baseline"]
        context = build_context(cfg, RunState.load())
        assert [r["run_name"] for r in context["live_runs"]] == ["live-cert"]
        assert "seed-baseline" in render_dossier(cfg) and "live-cert" in render_dossier(cfg)

        import time

        from synth.workbench.results import load_run
        from synth.workbench.runner import start_run, status
        from synth.workbench.specs import ExperimentSpec, Release, Target

        # A second, independent public surface records into the same live dossier.
        monkeypatch.setattr(clients, "langfuse", lambda _, **kw: service.lf)
        monkeypatch.setattr(clients, "llm", lambda model, **kw: SimpleNamespace(model=model,
            complete=lambda **kw: SimpleNamespace(text=source.expected.model_dump_json(),
                                                  input_tokens=300, output_tokens=100)))
        spec = ExperimentSpec(name="readonly-workbench", release=Release(model="test-model"),
            targets=[Target(dataset_name=cfg.certification.dataset.name)],
            evaluators=["numeric_accuracy", "citation_format", "escalation_correctness"])
        run_id, error = start_run(cfg, spec)
        assert not error
        deadline = time.monotonic() + 15
        while status(run_id)["state"] == "running" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert status(run_id)["state"] == "done", status(run_id)
        saved = load_run(cfg, run_id)
        while saved is None and time.monotonic() < deadline:
            time.sleep(0.01)
            saved = load_run(cfg, run_id)
        assert saved is not None and saved.state == "done"
        live_name = saved.langfuse_runs[0]["run_name"]
        assert {r["run_name"] for r in build_context(cfg, RunState.load())["live_runs"]} == {
            "live-cert", live_name}
        assert live_name in render_dossier(cfg)
        assert anchors.read_bytes() == before
        assert list(RunState.load().suite["runs"]) == ["seed-baseline"]

        # Reusing the local workbench directory for another project must not leak runs.
        another_project = RunState.load()
        another_project.project_id = "another-project"
        assert build_context(cfg, another_project)["live_runs"] == []
    finally:
        spool.chmod(0o755)
        anchors.chmod(0o644)


def test_live_escalation_exports_completed_event_output(service, caplog):
    from langfuse_synth_core.live.emit import LiveEmitter

    from synth.agent import answer_deterministic
    from synth.live.prefabs import prefabs_by_key
    from synth.live.trace import emit_live_turn

    cfg = load_config("config/demo.yaml")
    question = prefabs_by_key(cfg.generation.seed)["escalation"].question
    answer = answer_deterministic(question)
    assert answer.answer_type == "escalated"
    emitter = LiveEmitter("http://langfuse.test", public_key="pk-test", secret_key="sk-test",
                          client=service.lf)
    with caplog.at_level(logging.WARNING):
        trace_id, _ = emit_live_turn(emitter, cfg, question=question,
            answer_input=[{"role": "user", "content": user_turn(question)}],
            run_answer=lambda: (answer, 300, 100), answer_model=cfg.certification.candidate_a_model,
            prompt_version=7)
    event = next(s for s in service.exporter.get_finished_spans() if s.name == "escalated_to_human")
    assert event.attributes["langfuse.observation.type"] == "event"
    assert json.loads(event.attributes[INPUT]) == {"case_id": question.case_id}
    assert json.loads(event.attributes[OUTPUT]) == {"queued": True}
    assert json.loads(event.attributes["langfuse.observation.metadata.route"]) == "senior_credit_officer"
    assert json.loads(event.attributes["langfuse.observation.metadata.reason"]) == "conflicting sources"
    assert f"{event.context.trace_id:032x}" == trace_id
    planner = next(s for s in service.exporter.get_finished_spans()
                   if s.name == "copilot-turn" and s.parent_id)
    assert event.parent_id == planner.context.span_id
    assert not [r.message for r in caplog.records if r.levelno >= logging.WARNING]
