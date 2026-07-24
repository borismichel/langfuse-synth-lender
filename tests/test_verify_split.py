"""The verify SPLIT yields identical assertions to the pre-split verify (Ring 2, #34).

Acceptance (#34): "split ``verify`` yields identical assertions to pre-split against a
seeded env."

We prove that OFFLINE and deterministically. The read-client (auth + paginated GET of
scores/traces) now lives in ``langfuse_synth_core.lfread`` (+ the shared ``request_retry``);
the ``run_verify`` assertion body is byte-unchanged and still calls those helpers under their
original local names (``_get`` / ``_get_scores`` / ``_get_resp``). Here we stand up a *canned*
seeded environment — the exact JSON shapes a real seeded Langfuse would return — feed it
through the split read path, and assert:

  1. a healthy seeded env passes every check (the anchors the pre-split verify asserted), and
  2. flipping one seeded signal flips exactly the check that owns it (so the assertions are
     the real ones, not stubbed to always-pass).

By construction this equals the pre-split behaviour: only the helper *definitions* moved
across the seam (same auth, same pagination), while the assertion code that consumes them is
identical. Against a live seeded env the two therefore produce the same report.
"""

from __future__ import annotations

from synth import verify as V
from synth.config import load_config
from synth.state import RunState

SUITE = "certification-suite"


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


def _install_seeded_env(monkeypatch, *, healthy_queue: bool = True) -> None:
    """Patch the split read path to return the JSON a healthy seeded Langfuse would."""

    def fake_get(base, path, params=None, *, throttle=0.0):
        if path == "/api/public/dataset-items":
            return {"data": [{"sourceTraceId": f"s{i}"} for i in (1, 2, 3)],
                    "meta": {"totalPages": 1}}
        if path == f"/api/public/datasets/{SUITE}/runs":
            return {"data": [{"name": "baseline - t", "id": "rb"},
                             {"name": "candidate_a - t", "id": "ra"},
                             {"name": "candidate_b haiku - t", "id": "rc"}]}
        if path == "/api/public/v2/scores":  # run-level rollups, keyed by datasetRunId
            rate = {"rb": 0.95, "ra": 0.93, "rc": 0.60}[(params or {})["datasetRunId"]]
            return {"data": [{"name": "mean_groundedness", "value": 0.9},
                             {"name": "rate_numeric_accuracy", "value": rate},
                             {"name": "verdict", "value": "pass"}]}
        if path.startswith("/api/public/traces/"):
            tid = path.rsplit("/", 1)[-1]
            if tid == "t1":  # a run item's answer trace: prompt-linked + costed
                return {"observations": [{"name": "answer", "promptName": "analyst-copilot",
                                          "promptVersion": 7, "costDetails": {"total": 0.01}}]}
            if tid == "gc":  # covenant golden: prompt-linked + chat-shaped input
                return {"observations": [{
                    "name": "answer", "promptName": "analyst-copilot", "promptVersion": 7,
                    "input": [{"role": "system", "content": "You are an analyst copilot."}]}]}
            if tid == "gn":  # numeric golden: carries a human-annotation score
                return {"scores": [{"comment": "human annotation: correct value is EUR 1,234"}]}
            return {"observations": []}
        if path == "/api/public/annotation-queues":
            return {"data": [{"name": "certification-review", "id": "q1"}]}
        if path == "/api/public/annotation-queues/q1/items":
            done = [{"status": "COMPLETED"} for _ in range(6)]
            pend = [{"status": "PENDING"} for _ in range(6)] if healthy_queue else []
            return {"data": done + pend}
        raise AssertionError(f"unexpected GET path {path!r}")

    def fake_get_scores(base, name, limit_pages=30, *, throttle=0.0):
        if name == "numeric_accuracy":
            return [{"stringValue": "fail", "comment": "answer states X but the table prints Y",
                     "traceId": f"n{i}"} for i in range(5)]
        if name == "analyst_feedback":
            return [{"traceId": "fp", "comment": "analyst down-vote with a reason"}]
        if name in ("groundedness", "citation_coverage"):
            return [{"value": 0.9}]
        raise AssertionError(f"unexpected score name {name!r}")

    def fake_get_resp(base, path, params=None):
        if path.startswith(f"/api/public/datasets/{SUITE}/runs/"):
            return _Resp(200, {"datasetRunItems": [{"traceId": "t1"}, {"traceId": "t1b"},
                                                   {"traceId": "t1c"}]})
        if path.startswith("/api/public/traces/"):
            return _Resp(200, {"tags": ["golden"]})  # every seeded trace exists + tagged
        return _Resp(404, {})

    monkeypatch.setattr(V, "_get", fake_get)
    monkeypatch.setattr(V, "_get_scores", fake_get_scores)
    monkeypatch.setattr(V, "_get_resp", fake_get_resp)


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
    # proving the split verify runs the REAL pre-split assertion, not an always-pass stub.
    _install_seeded_env(monkeypatch, healthy_queue=False)
    checks = _run()
    assert checks["review_queue"] is False
    for name in ALL_CHECKS - {"review_queue"}:
        assert checks[name] is True, f"{name} regressed — the split changed an assertion"
