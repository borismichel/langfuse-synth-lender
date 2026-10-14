"""Rehearsal verifies actual Spool and real SDK exports through the HTTP read boundary."""
import json
from datetime import datetime, timezone

import pytest
from langfuse_synth_core import read
from synth import verify as verifier
from synth.config import load_config
from synth.seed.generator import build_plan
from synth.seed.run import run_seed
from synth.seed.datasets import create_suite
from synth.seed.cert_runs import seed_experiment_runs
from test_verify_split import _Resp, _install_seeded_env
from test_experiment_evidence import service as service


def decoded(value):
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value


def observation(sid, tid, name, attrs):
    metadata = {key.removeprefix("langfuse.observation.metadata."): decoded(value)
                for key, value in attrs.items() if key.startswith("langfuse.observation.metadata.")}
    # Observed Cloud v4.59 read shape: object fields use dotted metadata keys;
    # input/output remain raw JSON strings, including on experiment item roots.
    question = metadata.pop("analyst_question", None)
    if isinstance(question, dict):
        metadata.update({f"analyst_question.{key}": value for key, value in question.items()})
    return {"id": sid, "traceId": tid, "name": name,
            "type": attrs.get("langfuse.observation.type", "span").upper(),
            "input": attrs.get("langfuse.observation.input"),
            "output": attrs.get("langfuse.observation.output"),
            "metadata": metadata,
            "tags": attrs.get("langfuse.trace.tags", []),
            "promptName": attrs.get("langfuse.observation.prompt.name"),
            "promptVersion": attrs.get("langfuse.observation.prompt.version"),
            "costDetails": decoded(attrs.get("langfuse.observation.cost_details"))}


@pytest.mark.parametrize("history", [14, 30])
def test_rehearsal_passes_full_verifier_on_generated_evidence(service, monkeypatch, tmp_path, history):
    _install_seeded_env(monkeypatch)  # unchanged managed-evaluator provisioning fixture
    cfg = load_config("config/cloud-demo.yaml" if history == 14 else "config/demo.yaml", ["generation.target_traces=1000",
                      f"generation.window_days={history}", "generation.as_of_date=2026-10-16"])
    cfg.target.host = "http://localhost:3000"
    spool = tmp_path / "events.ndjson"
    state = run_seed(cfg, dry_run=True, persist=False, do_import=False, spool_path=spool, log=lambda _: None)
    plan = build_plan(cfg, datetime(2026, 10, 16, 12, tzinfo=timezone.utc))
    create_suite(service.lf, cfg, plan.cert)
    assert seed_experiment_runs(cfg, service.lf, plan.cert, log=lambda _: None) == 3
    service.lf.flush()
    observations, scores = [], []
    for raw in spool.read_text().splitlines():
        row = json.loads(raw)
        if "spanId" in row:
            attrs = {a["key"]: next(iter(a["value"].values())) for a in row["attributes"]}
            for key, value in attrs.items():
                if isinstance(value, dict) and "values" in value:
                    attrs[key] = [next(iter(x.values())) for x in value["values"]]
            observations.append(observation(row["spanId"], row["traceId"], row["name"], attrs))
        elif row.get("type") == "score-create":
            scores.append(row["body"])
    for span in service.exporter.get_finished_spans():
        attrs = span.attributes
        observations.append(observation(f"{span.context.span_id:016x}", f"{span.context.trace_id:032x}",
                                        span.name, attrs))
    scores.extend(service.scores)
    runs = {link["datasetRunId"]: link["runName"] for link in service.links}

    def handler(method, url, *, params=None, **kwargs):
        params = params or {}
        path = url.removeprefix(cfg.target.host)
        if path == "/api/public/v2/observations":
            return _Resp(200, {"data": [o for o in observations if o["traceId"] == params.get("traceId")], "meta": {}})
        if path == "/api/public/v3/scores":
            rows = [s for s in scores if all(not params.get(p) or s.get(p) == params[p]
                    for p in ("name", "traceId")) and (not params.get("experimentId") or s.get("datasetRunId") == params["experimentId"])]
            return _Resp(200, {"data": [{**s, "subject": {"kind": "experiment" if s.get("datasetRunId") else "trace",
                "id": s.get("datasetRunId") or s.get("traceId")}} for s in rows], "meta": {}})
        if path == "/api/public/datasets/certification-suite":
            return _Resp(200, {"id": "ds-cert", "name": "certification-suite"})
        if path == "/api/public/experiments":
            return _Resp(200, {"data": [{"id": run_id, "name": name, "datasetId": "ds-cert"} for run_id, name in runs.items()], "meta": {}})
        if path == "/api/public/experiment-items":
            return _Resp(200, {"data": [{"id": link["observationId"], "experimentItemId": link["datasetItemId"],
                "traceId": link["traceId"], "experimentId": link["datasetRunId"]}
                for link in service.links if link["datasetRunId"] == params["experimentId"]], "meta": {}})
        if path == "/api/public/dataset-items":
            return _Resp(200, {"data": list(service.items.values()), "meta": {"totalPages": 1}})
        if path == "/api/public/annotation-queues":
            return _Resp(200, {"data": [{"id": "queue", "name": state.queue["name"]}]})
        if path == "/api/public/annotation-queues/queue/items":
            return _Resp(200, {"data": [{"status": status, "traceId": tid} for status, ids in [
                ("COMPLETED", plan.queue_completed_ids), ("PENDING", plan.queue_pending_ids)] for tid in ids]})
        raise AssertionError(f"Unexpected read {path}")

    monkeypatch.setattr(read, "request_retry", handler)
    monkeypatch.setattr(verifier, "get_json", lambda base, path, params=None, **_: handler("GET", base+path, params=params).json())
    report = verifier.run_verify(cfg, state, log=lambda _: None)
    assert report.ok, [(c.name, c.detail) for c in report.checks if not c.ok]
