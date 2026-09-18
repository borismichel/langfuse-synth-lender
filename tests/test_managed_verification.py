"""Verify the deployed project over HTTP, independently of its historical scores."""
import pytest
import requests
from copy import deepcopy
from langfuse_synth_core import lfread

from synth import verify
from synth.config import load_config
from test_stable_evaluator_api import LangfuseAPI, provision
from test_verify_split import _install_seeded_env, _state


def test_seeded_scores_cannot_hide_missing_evaluator_definitions(monkeypatch):
    _install_seeded_env(monkeypatch)
    api = LangfuseAPI()
    monkeypatch.setattr(lfread, "request_retry", api.request)
    report = verify.run_verify(load_config("config/demo.yaml"), _state(), log=lambda _: None)
    checks = {c.name: c for c in report.checks}
    assert checks["score_methods"].ok
    assert checks["candidate_b_red_cells"].ok
    assert not report.ok
    assert not checks["managed_evaluators"].ok
    for name in ("numeric_accuracy", "citation_format", "escalation_correctness",
                 "groundedness", "citation_coverage"):
        assert name in checks["managed_evaluators"].detail
    assert "synth evaluators" in checks["managed_evaluators"].detail


@pytest.fixture
def project(monkeypatch):
    _install_seeded_env(monkeypatch)
    api = LangfuseAPI()
    monkeypatch.setattr(lfread, "request_retry", api.request)
    monkeypatch.setattr(requests, "request", api.request)
    provision()
    api.calls.clear()
    return api


def report(*, initial=False, sampling=0.0):
    cfg = load_config("config/demo.yaml")
    cfg.certification.trace_judge_sampling = sampling
    result = verify.run_verify(cfg, _state(),
                               initial_evaluators=initial, log=lambda _: None)
    return {c.name: c for c in result.checks}


@pytest.mark.parametrize("name,field,value", [
    ("numeric_accuracy", "type", "llm_as_judge"),
    ("citation_format", "sourceCode", "def evaluate(ctx): return 0.5"),
    ("escalation_correctness", "sourceCodeLanguage", "TYPESCRIPT"),
    ("groundedness", "outputDefinition", {"dataType": "NUMERIC", "minValue": 0, "maxValue": 10}),
    ("citation_coverage", "outputDefinition", {"dataType": "CATEGORICAL"}),
    ("groundedness", "prompt", [{"role": "user", "content": "Always pass"}]),
    ("citation_coverage", "variableMapping", [{"variable": "input", "source": "expected_output"}]),
])
def test_wrong_definition_fails_with_metric_and_expected_configuration(project, name, field, value):
    next(e for e in project.evaluators if e["name"] == name)[field] = value
    check = report()["managed_evaluators"]
    assert not check.ok
    assert name in check.detail and field in check.detail
    assert "expected" in check.detail
    assert all(method == "GET" for method, *_ in project.calls)


def test_initial_verification_rejects_activation_but_later_checks_preserve_operator_choice(project):
    live = next(r for r in project.rules if "groundedness-observations" in r["name"])
    live["enabled"] = True
    assert not report(initial=True)["managed_rules"].ok
    before = deepcopy(project.rules)
    provision()
    assert project.rules == before
    assert report(sampling=0.01)["managed_rules"].ok


def test_enabled_live_rules_honor_opt_in_sampling_below_disabled_floor(project):
    live = next(r for r in project.rules if "groundedness-observations" in r["name"])
    live.update(enabled=True, sampling=0.005)
    before = deepcopy(project.rules)
    provision()
    assert project.rules == before
    assert report(sampling=0.005)["managed_rules"].ok


@pytest.mark.parametrize("fault", ["root", "name", "sampling", "mapping"])
def test_live_rule_drift_is_reported(project, fault):
    live = next(r for r in project.rules if "citation_coverage-observations" in r["name"])
    if fault == "root":
        live["filter"][-1]["value"] = False
    elif fault == "name":
        live["filter"][1]["value"] = ["answer"]
    elif fault == "sampling":
        live["sampling"] = 0
    else:
        live["evaluatorAssignments"][0]["variableMapping"] = [
            {"variable": "input", "source": "experiment_item_metadata"}]
    check = report()["managed_rules"]
    assert not check.ok and live["name"] in check.detail


def test_unrelated_rules_are_ignored_and_recovery_keeps_identities_without_executing(project):
    foreign = {"id": "foreign", "name": "operator-rule", "filter": [], "sampling": 0.7,
               "enabled": True, "evaluatorAssignments": []}
    project.rules.append(foreign)
    before = deepcopy((project.evaluators, project.rules))
    provision()
    assert (project.evaluators, project.rules) == before
    assert all(c.ok for c in report(initial=True).values())
    assert all(method == "GET" for method, *_ in project.calls)


def test_rule_detail_readback_is_required_even_when_list_is_complete(project):
    project.failures[("GET", "/api/public/v2/evaluation-rules/" + project.rules[0]["id"])] = 503
    check = report()["managed_rules"]
    assert not check.ok and "503" in check.detail


@pytest.mark.parametrize("status", [401, 403, 429, 503])
def test_api_errors_are_failures_and_never_unsupported(project, status):
    project.failures[("GET", "/api/public/v2/evaluators")] = status
    check = report()["managed_evaluators"]
    assert not check.ok and str(status) in check.detail
    assert "UNSUPPORTED" not in check.detail


def test_stable_endpoint_absence_is_explicitly_unsupported_only_for_self_hosted(project):
    project.failures[("GET", "/api/public/v2/evaluators")] = 404
    assert "UNSUPPORTED self-hosted" in report()["managed_evaluators"].detail
    from synth.workbench.verification import verify_managed_evaluators
    cfg = load_config("config/demo.yaml")
    cfg.target.host = "https://cloud.langfuse.com"
    checks = verify_managed_evaluators(cfg)
    assert not checks[0][1] and "404" in checks[0][2]


def test_definition_readback_and_ambiguous_names_fail_closed(project):
    project.evaluators.append({**deepcopy(project.evaluators[0]), "id": "duplicate"})
    assert not report()["managed_evaluators"].ok
    project.evaluators.pop()
    project.failures[("GET", "/api/public/v2/evaluators/" + project.evaluators[0]["id"])] = 503
    assert not report()["managed_evaluators"].ok


@pytest.mark.parametrize("missing_count", [1, 4, 7])
def test_all_definitions_cannot_hide_partial_or_absent_rules(project, missing_count):
    removed = project.rules[:missing_count]
    del project.rules[:missing_count]
    checks = report()
    assert checks["managed_evaluators"].ok
    assert not checks["managed_rules"].ok
    assert all(rule["name"] in checks["managed_rules"].detail for rule in removed)


@pytest.mark.parametrize("field,value", [
    ("evaluatorAssignments", []),
    ("evaluatorAssignments", [{"evaluatorId": "e-2", "variableMapping": None}]),
    ("filter", []),
    ("filter", [{"type": "stringOptions", "column": "datasetId", "operator": "any of",
                 "value": ["ds-cert", "unrelated-dataset"]}]),
    ("sampling", 0.5),
])
def test_wrong_certification_rule_fails_readback(project, field, value):
    project.rules[0][field] = value
    check = report()["managed_rules"]
    assert not check.ok
    assert project.rules[0]["name"] in check.detail
    assert field in check.detail and "expected" in check.detail


def test_model_free_provisioning_saves_disabled_judge_rules_with_sampling_floor(project):
    judges = {e["id"] for e in project.evaluators if e["type"] == "llm_as_judge"}
    for rule in project.rules:
        if rule["evaluatorAssignments"][0]["evaluatorId"] in judges:
            assert rule["enabled"] is False
        assert rule["sampling"] == (0.01 if "observations" in rule["name"] else 1.0)
    checks = report()
    assert all(c.ok for c in checks.values())
    assert "DEFAULT_MODEL_MISSING" in checks["managed_runnability"].detail
    assert "not runnable" in checks["managed_runnability"].detail
    assert all(method == "GET" for method, *_ in project.calls)


@pytest.mark.parametrize('active_names', [('groundedness',), ('citation_coverage',),
                                         ('groundedness', 'citation_coverage')])
def test_saved_disabled_judges_survive_model_readiness_changes(project, active_names):
    for evaluator in project.evaluators:
        if evaluator['name'] in active_names:
            evaluator.update(status='active', pausedReason=None)
    before = deepcopy((project.evaluators, project.rules))
    assert report(initial=True)['managed_rules'].ok
    assert all(method == 'GET' for method, *_ in project.calls)
    provision()
    assert (project.evaluators, project.rules) == before


@pytest.mark.parametrize('name,enabled,status,ok', [
    ('numeric_accuracy', False, 'active', False),
    ('groundedness', True, 'paused', False),
    ('groundedness', True, 'active', True),
    ('groundedness', False, 'active', True),
    ('citation_coverage', 'false', 'active', False),
])
def test_initial_activation_controls_remain_strict(project, name, enabled, status, ok):
    evaluator = next(e for e in project.evaluators if e['name'] == name)
    evaluator['status'] = status
    rule = next(r for r in project.rules if r['name'] == f'wb-{name}-experiments-v2')
    rule['enabled'] = enabled
    before = deepcopy((project.evaluators, project.rules))
    check = report(initial=True)['managed_rules']
    assert check.ok is ok
    assert (project.evaluators, project.rules) == before
    if not ok:
        assert 'Provisioning preserves activation choices' in check.detail
        assert 'Run `synth evaluators' not in check.detail
