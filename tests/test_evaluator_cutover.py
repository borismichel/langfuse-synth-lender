"""The v4 evaluator cutover: observation-scoped successors, and what retires the legacy rule.

These tests assert the *story* the rules tell Langfuse — which object triggers a judge, which
one observation it lands on, and which variables it can see there — not the HTTP shape that
carried it. The unstable evaluator API is stubbed at the ``requests`` boundary, because the
thing under test is the rule body this kit builds, not Langfuse's acceptance of it (portal
#212; the standing risk is recorded in ``workbench/judges.py``).
"""
from __future__ import annotations

import pytest

from synth.config import load_config
from synth.workbench import cutover, judges


@pytest.fixture()
def cfg():
    return load_config("config/demo.yaml")


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = str(self._payload)

    def json(self):
        return self._payload


@pytest.fixture()
def api(monkeypatch):
    """Record every write to the unstable evaluator API and answer it 200."""
    calls: list[tuple[str, str, dict]] = []

    def _request(method, url, **kw):
        calls.append((method, url, kw.get("json") or {}))
        return _Resp(200, {"id": "r-new", **(kw.get("json") or {})}), ""

    monkeypatch.setattr(judges, "_request", _request)
    return calls


# ---------------------------------------------------------------------------
# The successor's shape
# ---------------------------------------------------------------------------
def test_live_rule_targets_exactly_one_observation_per_trace(cfg, api):
    """A v4 observation evaluator scores every observation it matches, so the live rule must
    select ONE per trace. Both the seeded pool and a live turn name the planning generation
    ``copilot-turn`` too — the old ``type = GENERATION`` filter matched it AND the answer
    generation, judging the planner's tool-call JSON and double-scoring the trace."""
    judge = {"name": "groundedness", "type": "llm_as_judge", "variables": ["input", "output"]}
    rule, err = judges.ensure_rule(cfg, judge, ["ds-1"], target="observation")
    assert not err
    body = api[-1][2]

    assert body["target"] == "observation"
    assert body["name"] == "wb-groundedness-observations"
    cols = {f["column"]: f for f in body["filter"]}
    assert cols["isRootObservation"] == {"type": "boolean", "column": "isRootObservation",
                                         "operator": "=", "value": True}
    assert cols["traceName"]["value"] == "copilot-turn"
    assert "type" not in cols                       # the GENERATION filter is gone
    assert rule is not None


def test_live_rule_maps_every_variable_onto_the_target_observation(cfg, api):
    """An observation evaluator cannot read siblings or children. Both judge variables must
    resolve on the root observation itself — which is where the seed and the live emitter
    both put the question and the answer."""
    judge = {"name": "citation_coverage", "type": "llm_as_judge",
             "variables": ["input", "output"]}
    judges.ensure_rule(cfg, judge, [], target="observation")
    body = api[-1][2]
    assert body["mapping"] == [{"variable": "input", "source": "input"},
                               {"variable": "output", "source": "output"}]
    assert all(m["source"] in cutover.OBSERVATION_SOURCES for m in body["mapping"])


def test_successors_are_created_disabled(cfg, api):
    """AC: the successor ships disabled, is validated on newly ingested data, and is enabled
    only after its scores are compared with the legacy rule's."""
    judge = {"name": "groundedness", "type": "llm_as_judge", "variables": ["input", "output"]}
    judges.ensure_rule(cfg, judge, [], target="observation", enabled=False)
    assert api[-1][2]["enabled"] is False


def test_code_evaluator_rules_stay_experiment_scoped(cfg, api):
    """The code evaluators compare against ``expected_output``, a source only
    ``target=experiment`` exposes — so ``experiment`` IS their v4 successor target, and they
    carry no mapping (Langfuse stores the fixed code runtime mapping)."""
    ev = {"name": "numeric_accuracy", "type": "code"}
    judges.ensure_rule(cfg, ev, ["ds-1"])
    body = api[-1][2]
    assert body["target"] == "experiment"
    assert "mapping" not in body
    assert body["filter"][0]["column"] == "datasetId"


def test_no_rule_is_built_on_a_retired_target(cfg, api):
    """v4 accepts ``observation`` and ``experiment`` only; ``trace`` and ``dataset`` are the
    legacy targets the unstable API still *returns* but no longer accepts."""
    judge = {"name": "groundedness", "type": "llm_as_judge", "variables": ["input", "output"]}
    for target in ("observation", "experiment"):
        judges.ensure_rule(cfg, judge, ["ds-1"], target=target)
        assert api[-1][2]["target"] in cutover.LIVE_TARGETS


# ---------------------------------------------------------------------------
# Inventory and retirement
# ---------------------------------------------------------------------------
_RULES = [
    {"id": "r1", "name": "wb-groundedness-experiments", "target": "experiment",
     "enabled": True, "status": "active", "evaluator": {"name": "groundedness"}},
    {"id": "r2", "name": "wb-groundedness-observations", "target": "observation",
     "enabled": False, "status": "inactive", "evaluator": {"name": "groundedness"},
     "filter": [{"column": "isRootObservation", "operator": "=", "value": True}]},
    {"id": "r3", "name": "wb-groundedness-traces", "target": "observation",
     "enabled": True, "status": "active", "evaluator": {"name": "groundedness"},
     "filter": [{"column": "type", "operator": "any of", "value": ["GENERATION"]}]},
    {"id": "r4", "name": "hand-made-trace-rule", "target": "trace",
     "enabled": True, "status": "active", "evaluator": {"name": "groundedness"}},
    {"id": "r5", "name": "someone-elses-rule", "target": "observation",
     "enabled": True, "status": "active", "evaluator": {"name": "toxicity"},
     "filter": [{"column": "isRootObservation", "operator": "=", "value": True}]},
]


@pytest.fixture()
def inventoried(monkeypatch):
    monkeypatch.setattr(cutover, "list_rules", lambda base: (list(_RULES), True))


def test_inventory_separates_successors_from_their_legacy_predecessors(cfg, inventoried):
    inv = cutover.inventory(cfg)
    assert inv.api_available
    assert [r["id"] for r in inv.successors] == ["r1", "r2"]
    # r3: this kit's pre-v4 live rule (matched every GENERATION); r4: a retired target.
    assert [r["id"] for r in inv.legacy] == ["r3", "r4"]
    # r5 is not ours — an inventory that retires a rule it did not create is a foot-gun.
    assert "r5" not in [r["id"] for r in inv.legacy + inv.successors]


def test_retire_disables_the_legacy_rule_and_never_deletes_it(cfg, inventoried, api):
    """Rollback has to stay possible, so retirement is a PATCH to ``enabled=false``."""
    retired, notes = cutover.retire_legacy(cfg)
    assert not notes
    assert sorted(retired) == ["hand-made-trace-rule", "wb-groundedness-traces"]
    methods = {m for m, _u, _b in api}
    assert methods == {"PATCH"}
    assert "DELETE" not in methods
    for _m, url, body in api:
        assert body == {"enabled": False}
        assert url.endswith(("/evaluation-rules/r3", "/evaluation-rules/r4"))


def test_retire_is_a_no_op_without_the_unstable_api(cfg, monkeypatch, api):
    monkeypatch.setattr(cutover, "list_rules", lambda base: ([], False))
    retired, notes = cutover.retire_legacy(cfg)
    assert retired == [] and notes and not api


# ---------------------------------------------------------------------------
# Validation on newly ingested data
# ---------------------------------------------------------------------------
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
    assert enabled == ["wb-groundedness-observations"]
    assert api == [("PATCH", api[0][1], {"enabled": True, "sampling": 0.05})]
    assert api[0][1].endswith("/evaluation-rules/r2")
