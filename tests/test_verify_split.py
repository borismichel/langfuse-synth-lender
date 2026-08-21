"""`verify` reads through the seam, and asserts the same things it always did.

This served one canned seeded project **twice** while the seam had two arms — once as a
deprecated-API Langfuse and once as a v4 one — because #211's acceptance criterion was that
every assertion survives the remap, and identical reports on both arms was the proof. It did,
and that equivalence is what let #213 delete the deprecated arm.

The canned server is v4-only now: it serves `/api/public/experiments` and
`/api/public/experiment-items` and **404s** every deprecated endpoint, including the
`/datasets/{name}/runs` reads Lender used to make. A `verify` still reaching for one fails
here rather than quietly passing on a fallback.

The read path is faked at the transport (`read.request_retry`), not at the assertions, so
normalisation — the v3 score shape, the `subject` object, cursor pagination, v4's
raw-JSON-string `input` — runs for real.
"""

from __future__ import annotations

import json
import pathlib
import re

from langfuse_synth_core import read

from synth import verify as V
from synth.config import load_config
from synth.state import RunState

SUITE = "certification-suite"
DATASET_ID = "ds-1"
SYSTEM_TURN = [{"role": "system", "content": "You are an analyst copilot."}]

RUNS = [("baseline - t", "rb", 0.95), ("candidate_a - t", "ra", 0.93),
        ("candidate_b haiku - t", "rc", 0.60)]


def _state() -> RunState:
    return RunState(
        base_url="http://localhost:3000",
        project_name="demo",
        run_date="2026-06-10T12:00:00+00:00",
        prompt_name="analyst-copilot",
        prompt_versions={"latest": 8, "production": 7, "staging": 8},
        suites={"certification_suite": {
            "name": SUITE,
            "items": 3,
            "runs": {"baseline": {}, "candidate_a": {}, "candidate_b": {}},
        }},
        queue={"name": "certification-review"},
        golden=[
            {"key": "covenant_summary", "title": "covenant", "trace_id": "gc"},
            {"key": "numeric_hallucination", "title": "numeric", "trace_id": "gn"},
            {"key": "correct_escalation", "title": "escalation", "trace_id": "ge"},
            {"key": "dscr_trend", "title": "dscr", "trace_id": "gd"},
        ],
        flagged_pending=[{"trace_id": "fp"}],
    )


class _Resp:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(str(self.status_code))


GOLDEN_TRACES = ("gc", "gn", "ge", "gd")
RUN_ITEM_TRACES = ("t1", "t1b", "t1c")


def _answer_observation(trace_id: str, *, raw_io: bool):
    """A run item's / golden trace's prompt-linked, costed `answer` generation."""
    row = {"id": f"o-{trace_id}", "traceId": trace_id, "type": "GENERATION",
           "name": "answer", "promptName": "analyst-copilot", "promptVersion": 7,
           "costDetails": {"total": 0.01}}
    if trace_id == "gc":
        row["input"] = json.dumps(SYSTEM_TURN) if raw_io else SYSTEM_TURN
    return row


def _install_seeded_env(monkeypatch, *, healthy_queue: bool = True) -> None:
    """Serve the canned seeded project as a v4 Langfuse — and only as one.

    Every deprecated endpoint answers a 404, so a read that silently stayed on one fails
    rather than passing on a fallback.
    """
    def scores_for(name, *, experiment_id=None, trace_id=None):
        """The canned score rows, in the v3 shape."""
        if experiment_id:
            rate = {"rb": 0.95, "ra": 0.93, "rc": 0.60}[experiment_id]
            rows = [("mean_groundedness", "NUMERIC", 0.9, None),
                    ("rate_numeric_accuracy", "NUMERIC", rate, None),
                    ("verdict", "CATEGORICAL", "pass", None)]
            subject = {"kind": "dataset_run", "id": experiment_id}
        elif name == "numeric_accuracy":
            rows = [(name, "CATEGORICAL", "fail",
                     "answer states X but the table prints Y") for _ in range(5)]
            subject = {"kind": "trace", "id": "n1"}
        elif name == "analyst_feedback":
            rows = [(name, "CATEGORICAL", "down", "analyst down-vote with a reason")]
            subject = {"kind": "trace", "id": trace_id or "fp"}
        else:
            rows = [(name, "NUMERIC", 0.9, None)]
            subject = {"kind": "trace", "id": "t1"}

        return [{"id": f"s{i}", "name": n, "dataType": dt, "value": value,
                 "comment": comment, "subject": subject}
                for i, (n, dt, value, comment) in enumerate(rows)]

    def handler(method, url, *, params=None, auth=None, timeout=30, throttle_s=0.0,
                attempts=8):
        params = params or {}
        path = url.replace("http://localhost:3000", "")

        # -- endpoints the migration left alone ---------------------------
        if path == "/api/public/dataset-items":
            return _Resp(200, {"data": [{"sourceTraceId": f"s{i}"} for i in (1, 2, 3)],
                               "meta": {"totalPages": 1}})
        if path == "/api/public/annotation-queues":
            return _Resp(200, {"data": [{"name": "certification-review", "id": "q1"}]})
        if path == "/api/public/annotation-queues/q1/items":
            done = [{"status": "COMPLETED"} for _ in range(6)]
            pend = [{"status": "PENDING"} for _ in range(6)] if healthy_queue else []
            return _Resp(200, {"data": done + pend})
        if path == f"/api/public/datasets/{SUITE}":
            return _Resp(200, {"id": DATASET_ID, "name": SUITE})

        # -- every deprecated endpoint is gone from this server ------------
        if re.match(r"/api/public/(traces|observations|sessions|v2/scores|metrics)\b", path):
            return _Resp(404, {})
        if re.match(rf"/api/public/datasets/{SUITE}/runs", path):
            return _Resp(404, {})

        # -- the v4 endpoints ----------------------------------------------
        if path == "/api/public/v2/observations":
            tid = params.get("traceId")
            rows = ([_answer_observation(tid, raw_io=True)]
                    if tid in RUN_ITEM_TRACES + GOLDEN_TRACES + ("fp",) else [])
            for row in rows:
                row["tags"] = ["golden"] if tid in GOLDEN_TRACES else []
            return _Resp(200, {"data": rows, "meta": {}})
        if path == "/api/public/v3/scores":
            name = params.get("name")
            if params.get("traceId") == "gn" and not name:
                return _Resp(200, {"data": [
                    {"id": "h1", "name": "groundedness", "dataType": "NUMERIC", "value": 1.0,
                     "comment": "human annotation: correct value is EUR 1,234",
                     "subject": {"kind": "trace", "id": "gn"}}], "meta": {}})
            if not name and params.get("traceId"):
                return _Resp(200, {"data": [], "meta": {}})
            return _Resp(200, {"data": scores_for(name,
                                                  experiment_id=params.get("experimentId"),
                                                  trace_id=params.get("traceId")),
                               "meta": {}})
        if path == "/api/public/experiments":
            assert params.get("datasetId") == DATASET_ID
            return _Resp(200, {"data": [{"id": i, "name": n, "datasetId": DATASET_ID}
                                        for n, i, _ in RUNS], "meta": {}})
        if path == "/api/public/experiment-items":
            return _Resp(200, {"data": [{"id": f"i{k}", "experimentId": params.get("experimentId"),
                                         "traceId": t}
                                        for k, t in enumerate(RUN_ITEM_TRACES)], "meta": {}})

        raise AssertionError(f"unexpected read: {path!r}")

    monkeypatch.setattr(read, "request_retry", handler)
    monkeypatch.setattr(V, "get_json",
                        lambda base, path, params=None, *, throttle=0.0:
                        handler("GET", f"{base}{path}", params=params).json())
    monkeypatch.setattr(V.time, "sleep", lambda _s: None)


def _run() -> dict:
    report = V.run_verify(load_config("config/demo.yaml"), _state(), log=lambda _m: None)
    return {c.name: c.ok for c in report.checks}


ALL_CHECKS = {
    "suite_items", "seeded_runs", "run_prompt_link", "run_level_scores",
    "candidate_b_red_cells", "golden_traces", "flagged_pending", "prompt_linkage",
    "review_queue", "score_methods",
}


def test_healthy_seeded_env_passes_every_assertion(monkeypatch):
    _install_seeded_env(monkeypatch)
    checks = _run()
    assert set(checks) == ALL_CHECKS
    assert all(checks.values()), f"unexpected failures: {[k for k, v in checks.items() if not v]}"


def test_flipping_the_queue_signal_flips_only_that_assertion(monkeypatch):
    # A queue with no PENDING items → the review-queue assertion (and only it) must fail,
    # proving verify runs the REAL assertion, not an always-pass stub.
    _install_seeded_env(monkeypatch, healthy_queue=False)
    checks = _run()
    assert checks["review_queue"] is False
    for name in ALL_CHECKS - {"review_queue"}:
        assert checks[name] is True, f"{name} regressed — the remap changed an assertion"


def test_verify_names_no_deprecated_endpoint_itself(monkeypatch):
    """The seam is the only place this kit reaches Langfuse, so `verify` naming an endpoint
    at all would be the thing #211 exists to prevent — and #213 makes it a live failure
    rather than future debt, since the endpoints it would name have no successor here."""
    body = "\n".join(line for line in pathlib.Path("src/synth/verify.py")
                      .read_text(encoding="utf-8").splitlines()
                      if not line.lstrip().startswith("#"))
    body = body.split('"""', 2)[-1]           # the docstring discusses the migration
    for retired in ("/api/public/traces", "/api/public/observations",
                    "/api/public/v2/scores", "/api/public/sessions", "/runs"):
        assert retired not in body, retired


def test_the_dataset_runs_are_read_through_the_experiments_api(monkeypatch):
    """The canned server 404s `/datasets/{name}/runs` outright, so `seeded_runs` and
    `run_prompt_link` passing is the acceptance criterion, observed."""
    _install_seeded_env(monkeypatch)
    checks = _run()
    assert checks["seeded_runs"] and checks["run_prompt_link"] and checks["run_level_scores"]


def test_verify_names_the_v4_read_apis_in_its_log(monkeypatch):
    """The log names what answered, which is the first thing to know when a passing check
    starts failing."""
    _install_seeded_env(monkeypatch)
    lines: list[str] = []
    V.run_verify(load_config("config/demo.yaml"), _state(), log=lines.append)
    assert any("v4 read APIs" in line for line in lines), lines
