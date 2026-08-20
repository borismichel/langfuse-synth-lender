"""`verify` reads through the seam, and asserts the same things it always did.

Two acceptance criteria meet here (portal #211):

  * *"every assertion each verify makes today is still made after the remap"* — so the same
    canned seeded environment is served **twice**, once as a deprecated-API Langfuse and
    once as a v4 one, and the report must come out identical. That is a stronger claim than
    the pre-remap version of this file made: it proved the assertions survived a refactor,
    and now it proves they survive a change of API generation;
  * *"Lender's dataset-run reads go through the Experiments API"* — the v4 arm below serves
    `/api/public/experiments` and `/api/public/experiment-items` and **404s** the
    `/datasets/{name}/runs` endpoints the deprecated arm answers. A verify still reaching
    for a dataset run the old way fails there rather than quietly passing.

The read path is faked at the transport (`read.request_retry`), not at the assertions, so
normalisation — the v3 score shape, the `subject` object, cursor pagination, v4's
raw-JSON-string `input` — runs for real.
"""

from __future__ import annotations

import json

import pytest

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


def _install_seeded_env(monkeypatch, *, generation: str, healthy_queue: bool = True) -> None:
    """Serve the canned seeded project as `generation` would — and only as it would.

    Every endpoint the other generation would have used answers a 404, so a read that
    silently stayed on a deprecated endpoint fails rather than passing on a fallback.
    """
    legacy = generation == read.LEGACY

    def scores_for(name, *, experiment_id=None, trace_id=None):
        """The canned score rows, in whichever shape the generation answers."""
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

        out = []
        for i, (n, dt, value, comment) in enumerate(rows):
            if legacy:
                numeric = value if dt == "NUMERIC" else 0
                out.append({"id": f"s{i}", "name": n, "dataType": dt, "value": numeric,
                            "stringValue": None if dt == "NUMERIC" else value,
                            "comment": comment, "traceId": subject["id"],
                            "datasetRunId": experiment_id})
            else:
                out.append({"id": f"s{i}", "name": n, "dataType": dt, "value": value,
                            "comment": comment, "subject": subject})
        return out

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

        # -- the deprecated arm -------------------------------------------
        if path == "/api/public/traces":          # the generation probe
            return _Resp(200, {"data": [], "meta": {"totalPages": 1}}) if legacy else _Resp(404, {})
        if path.startswith("/api/public/traces/"):
            if not legacy:
                return _Resp(404, {})
            tid = path.rsplit("/", 1)[-1]
            if tid in RUN_ITEM_TRACES or tid in GOLDEN_TRACES or tid == "fp":
                body = {"id": tid, "tags": ["golden"] if tid in GOLDEN_TRACES else [],
                        "observations": [_answer_observation(tid, raw_io=False)],
                        "scores": ([{"id": "h1", "name": "groundedness", "dataType": "NUMERIC",
                                     "value": 1.0,
                                     "comment": "human annotation: correct value is EUR 1,234"}]
                                   if tid == "gn" else [])}
                return _Resp(200, body)
            return _Resp(404, {})
        if path == f"/api/public/datasets/{SUITE}/runs":
            if not legacy:
                return _Resp(404, {})
            return _Resp(200, {"data": [{"name": n, "id": i} for n, i, _ in RUNS],
                               "meta": {"totalPages": 1}})
        if path.startswith(f"/api/public/datasets/{SUITE}/runs/"):
            if not legacy:
                return _Resp(404, {})
            return _Resp(200, {"datasetRunItems": [{"id": f"i{k}", "traceId": t}
                                                   for k, t in enumerate(RUN_ITEM_TRACES)]})
        if path == "/api/public/v2/scores":
            if not legacy:
                return _Resp(404, {})
            return _Resp(200, {"data": scores_for(params.get("name"),
                                                  experiment_id=params.get("datasetRunId"),
                                                  trace_id=params.get("traceId")),
                               "meta": {"totalPages": 1}})

        # -- the v4 arm ----------------------------------------------------
        if path == "/api/public/v2/observations":
            if legacy:
                return _Resp(404, {})
            tid = params.get("traceId")
            rows = ([_answer_observation(tid, raw_io=True)]
                    if tid in RUN_ITEM_TRACES + GOLDEN_TRACES + ("fp",) else [])
            for row in rows:
                row["tags"] = ["golden"] if tid in GOLDEN_TRACES else []
            return _Resp(200, {"data": rows, "meta": {}})
        if path == "/api/public/v3/scores":
            if legacy:
                return _Resp(404, {})
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
            if legacy:
                return _Resp(404, {})
            assert params.get("datasetId") == DATASET_ID
            return _Resp(200, {"data": [{"id": i, "name": n, "datasetId": DATASET_ID}
                                        for n, i, _ in RUNS], "meta": {}})
        if path == "/api/public/experiment-items":
            if legacy:
                return _Resp(404, {})
            return _Resp(200, {"data": [{"id": f"i{k}", "experimentId": params.get("experimentId"),
                                         "traceId": t}
                                        for k, t in enumerate(RUN_ITEM_TRACES)], "meta": {}})

        raise AssertionError(f"unexpected read: {path!r} (generation={generation})")

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


@pytest.mark.parametrize("generation", [read.LEGACY, read.V4])
def test_healthy_seeded_env_passes_every_assertion(monkeypatch, generation):
    _install_seeded_env(monkeypatch, generation=generation)
    checks = _run()
    assert set(checks) == ALL_CHECKS
    assert all(checks.values()), f"unexpected failures: {[k for k, v in checks.items() if not v]}"


@pytest.mark.parametrize("generation", [read.LEGACY, read.V4])
def test_flipping_the_queue_signal_flips_only_that_assertion(monkeypatch, generation):
    # A queue with no PENDING items → the review-queue assertion (and only it) must fail,
    # proving verify runs the REAL assertion, not an always-pass stub.
    _install_seeded_env(monkeypatch, generation=generation, healthy_queue=False)
    checks = _run()
    assert checks["review_queue"] is False
    for name in ALL_CHECKS - {"review_queue"}:
        assert checks[name] is True, f"{name} regressed — the remap changed an assertion"


def test_the_report_is_identical_on_both_generations(monkeypatch):
    """The point of the seam, stated as a test: the same project, read through either API,
    yields the same verdict — so a target's cutover cannot change what `verify` says."""
    _install_seeded_env(monkeypatch, generation=read.LEGACY)
    legacy = _run()
    _install_seeded_env(monkeypatch, generation=read.V4)
    assert _run() == legacy


def test_the_dataset_runs_are_read_through_the_experiments_api(monkeypatch):
    """The v4 arm 404s `/datasets/{name}/runs` outright, so `seeded_runs` and
    `run_prompt_link` passing there is the acceptance criterion, observed."""
    _install_seeded_env(monkeypatch, generation=read.V4)
    checks = _run()
    assert checks["seeded_runs"] and checks["run_prompt_link"] and checks["run_level_scores"]


def test_verify_recognises_a_v4_host_and_says_so(monkeypatch):
    """Nothing configures the generation — detection probes for it, and the log names what
    answered, which is the first thing to know when a passing check starts failing."""
    _install_seeded_env(monkeypatch, generation=read.V4)
    lines: list[str] = []
    V.run_verify(load_config("config/demo.yaml"), _state(), log=lines.append)
    assert any("v4 read APIs" in line for line in lines), lines
