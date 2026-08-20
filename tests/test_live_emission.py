"""The playground emits through the live-emission seam, and still looks like the pool.

Two things have to hold at once after the cutover (portal #211):

  * a live turn is written by the **live seam** — wall clock, the Langfuse SDK, no Spool
    envelope, nothing byte-compared against a golden. The playground used to build the
    seeder's backdated span tree and push it through the `Ingestor`, which coupled a
    surface with no timestamp to supply to machinery whose whole purpose is supplying one;
  * the trace still **renders as the seeded pool does** — same tree, same names, same
    observation types — because the demo's move is a live answer landing at the top of the
    same timeline, on the incumbent model and then the candidate.

So the shape assertion is written against the seeded builder's own output for a live-kind
turn rather than a hand-copied list: if the two writers drift, this fails.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from langfuse_synth_core.live.emit import LiveEmitter
from langfuse_synth_core.rng import Rng

from synth.agent import answer_deterministic
from synth.config import load_config
from synth.content import flagged_cases
from synth.live.trace import TRACE_NAME, emit_live_turn


class _FakeSpan:
    def __init__(self, recorder, kw, parent=None):
        self.recorder = recorder
        self.kw = dict(kw)
        self.parent = parent
        self.trace_id = "trace-live"
        self.id = f"obs-{len(recorder.spans)}"
        recorder.spans.append(self)

    def update(self, **kw):
        self.kw.update(kw)
        return self

    @contextmanager
    def start_as_current_observation(self, **kw):
        yield _FakeSpan(self.recorder, kw, parent=self)


class _FakeClient:
    def __init__(self):
        self.spans: list[_FakeSpan] = []
        self.scores: list[dict] = []
        self.flushes = 0

    @contextmanager
    def start_as_current_observation(self, **kw):
        yield _FakeSpan(self, kw, parent=None)

    def create_score(self, **kw):
        self.scores.append(kw)

    def flush(self):
        self.flushes += 1


@contextmanager
def _no_propagate(**_attrs):
    yield


@pytest.fixture
def client():
    return _FakeClient()


@pytest.fixture
def emitter(client):
    return LiveEmitter("http://localhost:3000", public_key="pk", secret_key="sk",
                       client=client, propagate=_no_propagate)


def _turn(cfg):
    """One non-escalating question and its deterministic answer."""
    case = next(c for c in flagged_cases(Rng(cfg.generation.seed))
                if answer_deterministic(c.question).answer_type != "escalated")
    return case.question, answer_deterministic(case.question)


def _emit(emitter, cfg):
    question, answer = _turn(cfg)
    trace_id = emit_live_turn(
        emitter, cfg, question=question, answer=answer,
        answer_input=[{"role": "system", "content": "You are an analyst copilot."}],
        answer_usage=(2400, 320), answer_model=cfg.certification.incumbent_model,
        prompt=None, prompt_version=7, tags=["playground"])
    return question, answer, trace_id


def _seeded_shape(cfg, question, answer):
    """The (name, observation type) pairs the seeder writes for a live-kind turn.

    The Spool is OTLP spans since the write-path cutover (#210), so the type comes off the
    span attribute; the minted root is the trace and is named for it on both writers.
    """
    from synth.seed.traces import TraceSpec, build_trace_events

    spec = TraceSpec(trace_id=Rng(1).trace_id("shape", "1"),
                     timestamp=datetime(2026, 6, 4, tzinfo=timezone.utc),
                     question=question, answer=answer, user_id="u", session_id=None,
                     environment="production", kind="live", question_kind="live",
                     model_override=cfg.certification.incumbent_model, prompt_version=7)
    spans = build_trace_events(Rng(cfg.generation.seed), cfg, spec, 7,
                               answer_usage=(2400, 320), answer_latency_ms=900,
                               answer_input=[{"role": "system", "content": "x"}])

    def obs_type(span):
        for attr in span["attributes"]:
            if attr["key"] == "langfuse.observation.type":
                return attr["value"]["stringValue"].upper()
        return "SPAN"

    # The seeder emits the minted root first and the planner second, both named for the
    # trace; drop only the first so the planner stays in the comparison.
    return [(s["name"], obs_type(s)) for s in spans][1:]


def test_the_live_turn_carries_the_same_tree_the_seeder_writes(emitter, client):
    cfg = load_config("config/demo.yaml")
    question, answer, _ = _emit(emitter, cfg)

    live = [(s.kw.get("name"), str(s.kw.get("as_type", "span")).upper())
            for s in client.spans][1:]
    assert live == _seeded_shape(cfg, question, answer), (
        "the live writer and the seeder drifted — a live answer would no longer render like "
        "the pool it lands in front of")


def test_the_tools_nest_under_the_planning_pass(emitter, client):
    """The planner envelopes the turn and the tools hang off it, as in the pool. Flattening
    would list the right names and render the wrong trace."""
    _emit(emitter, load_config("config/demo.yaml"))
    root, planner = client.spans[0], client.spans[1]
    by_name = {s.kw.get("name"): s for s in client.spans[2:]}

    assert planner.parent is root
    for name in ("filings_search", "document_fetch", "answer"):
        assert by_name[name].parent is planner, name


def test_the_answer_generation_carries_the_real_usage_and_the_selected_model(emitter, client):
    cfg = load_config("config/demo.yaml")
    _, answer, _ = _emit(emitter, cfg)
    gen = next(s for s in client.spans if s.kw.get("name") == "answer")

    assert gen.kw["model"] == cfg.certification.incumbent_model
    assert gen.kw["usage_details"]["input"] == 2400
    assert gen.kw["usage_details"]["output"] == 320
    assert gen.kw["cost_details"]["total"] > 0
    assert gen.kw["output"] == answer.model_dump()


def test_the_overall_io_lands_on_the_root_observation(emitter, client):
    """Under v4 there is no trace body: the trace's input and output live on its root."""
    question, answer, trace_id = _emit(emitter, load_config("config/demo.yaml"))
    root = client.spans[0]

    assert root.kw["name"] == TRACE_NAME
    assert root.kw["input"] == question.model_dump()
    assert root.kw["output"] == answer.model_dump()
    assert trace_id == "trace-live"


def test_an_escalating_answer_still_emits_the_escalation_event(emitter, client):
    cfg = load_config("config/demo.yaml")
    question, answer = _turn(cfg)
    escalated = answer.model_copy(update={"answer_type": "escalated"})
    emit_live_turn(emitter, cfg, question=question, answer=escalated,
                   answer_input=[{"role": "system", "content": "x"}],
                   answer_usage=(10, 10), answer_model=cfg.certification.incumbent_model)

    event = next(s for s in client.spans if s.kw.get("name") == "escalated_to_human")
    assert str(event.kw.get("as_type")).upper() == "EVENT"
    assert event.kw["output"] == {"queued": True}


def test_the_turn_is_delivered_before_the_surface_answers(emitter, client):
    _emit(emitter, load_config("config/demo.yaml"))
    assert client.flushes == 1


def test_the_playground_never_reaches_for_the_spool():
    """The determinism line, kit-side: a live surface that imported the Spool's builders or
    its ingestor would be back on the backdating path the seam exists to leave."""
    import pathlib

    for module in ("live/trace.py", "live/submit.py", "workbench/signoff.py"):
        source = pathlib.Path("src/synth").joinpath(module).read_text()
        body = source.split('"""', 2)[-1]          # the docstrings discuss the line
        for forbidden in ("Ingestor", "build_trace_events", "seed.events", "score_event("):
            assert forbidden not in body, f"{module} reaches for the Spool: {forbidden}"
