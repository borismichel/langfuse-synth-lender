"""The existing score-comparison gate for deliberate live-rule activation.

Provisioning, inventory and retirement are covered over HTTP by
``test_stable_evaluator_api.py`` using the stable API's actual response shape.
"""
from __future__ import annotations

import pytest
from synth.config import load_config
from synth.workbench import cutover, judges


@pytest.fixture
def cfg():
    return load_config("config/demo.yaml")


@pytest.fixture
def api(monkeypatch):
    calls = []
    class Response:
        status_code = 200
        def json(self):
            return {"id": "r2"}
    def request(method, url, **kw):
        calls.append((method, url, kw["json"]))
        return Response(), ""
    monkeypatch.setattr(judges, "_request", request)
    return calls


@pytest.fixture
def inventoried(monkeypatch):
    monkeypatch.setattr(cutover, "list_judges", lambda base: (
        [{"id": "e1", "name": "groundedness", "type": "llm_as_judge"}], True, ""))
    monkeypatch.setattr(cutover, "list_rules", lambda base: ([{
        "id": "r2", "name": "wb-groundedness-observations-v2", "enabled": False,
        "sampling": 0.0, "filter": judges.ROOT_OBSERVATION_FILTER,
        "evaluatorAssignments": [{"evaluatorId": "e1", "variableMapping": None}],
    }], True, ""))


class _FakeScore:
    def __init__(self, name, value, observation_id, trace_id):
        self.name, self.observation_id, self.trace_id = name, observation_id, trace_id
        self.numeric_value, self.string_value = value, None

    @property
    def value(self):
        return self.numeric_value if self.numeric_value is not None else self.string_value


class _FakeReader:
    """Answers the two reads the comparison needs: the judge's scores, and which of the
    observations they sit on is its trace's root."""

    def __init__(self, scores, roots):
        self._scores, self._roots = scores, roots

    def scores(self, *, name=None, **_kw):
        return [s for s in self._scores if name is None or s.name == name]

    def observations(self, *, trace_id=None, **_kw):
        return self._roots.get(trace_id, [])


class _FakeObs:
    def __init__(self, obs_id, is_root):
        self.id, self.is_root = obs_id, is_root


def _reader_with(pairs):
    """``pairs``: trace_id -> (root score value or None, legacy score value or None)."""
    scores, roots = [], {}
    for tid, (root_v, legacy_v) in pairs.items():
        obs = []
        if root_v is not None:
            scores.append(_FakeScore("groundedness", root_v, f"{tid}-root", tid))
            obs.append(_FakeObs(f"{tid}-root", True))
        if legacy_v is not None:
            scores.append(_FakeScore("groundedness", legacy_v, f"{tid}-gen", tid))
            obs.append(_FakeObs(f"{tid}-gen", False))
        roots[tid] = obs
    return _FakeReader(scores, roots)


def test_comparison_is_not_ready_until_the_successor_has_scored_new_data(cfg, monkeypatch):
    monkeypatch.setattr(cutover, "_reader", lambda cfg: _reader_with({"t1": (None, 0.9)}))
    cmp = cutover.compare(cfg, "groundedness")
    assert cmp.successor_scores == 0 and cmp.legacy_scores == 1
    assert not cmp.ready
    assert "no successor scores" in cmp.summary


def test_comparison_reports_agreement_where_both_rules_scored(cfg, monkeypatch):
    monkeypatch.setattr(cutover, "_reader", lambda cfg: _reader_with(
        {"t1": (0.90, 0.90), "t2": (0.40, 0.44), "t3": (0.95, 0.10), "t4": (0.80, None)}))
    cmp = cutover.compare(cfg, "groundedness", tolerance=0.05)
    assert (cmp.successor_scores, cmp.legacy_scores, cmp.compared) == (4, 3, 3)
    assert cmp.agreed == 2 and cmp.disagreed == [("t3", 0.95, 0.10)]
    assert cmp.ready                       # scored new data; a majority agrees


def test_comparison_is_ready_when_the_legacy_rule_never_ran(cfg, monkeypatch):
    """The shipped configs create the live rule paused, so a project can legitimately have
    no legacy baseline. That is reported, not silently treated as agreement."""
    monkeypatch.setattr(cutover, "_reader", lambda cfg: _reader_with({"t1": (0.9, None)}))
    cmp = cutover.compare(cfg, "groundedness")
    assert cmp.compared == 0 and cmp.ready
    assert "no legacy baseline" in cmp.summary


def test_enable_refuses_until_the_comparison_is_ready(cfg, inventoried, monkeypatch, api):
    monkeypatch.setattr(cutover, "_reader", lambda cfg: _reader_with({"t1": (None, 0.9)}))
    enabled, notes = cutover.enable_successors(cfg, sampling=0.05)
    assert enabled == [] and any("no successor scores" in n for n in notes)
    assert not api


def test_enable_turns_on_the_validated_successor_at_the_configured_sampling(
        cfg, inventoried, monkeypatch, api):
    monkeypatch.setattr(cutover, "_reader", lambda cfg: _reader_with({"t1": (0.9, 0.9)}))
    enabled, _notes = cutover.enable_successors(cfg, sampling=0.05)
    assert enabled == ["wb-groundedness-observations-v2"]
    assert api == [("PATCH", api[0][1], {"enabled": True, "sampling": 0.05})]
    assert api[0][1].endswith("/evaluation-rules/r2")
