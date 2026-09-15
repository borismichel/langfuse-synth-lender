"""Issue #250: exercise provisioning and the workbench over the stable HTTP contract.

The fake is the external Langfuse service, not a provisioning helper. Its shapes are
from Langfuse's public OpenAPI (2026-09-15); obsolete routes deliberately return 404.
"""
from copy import deepcopy
from urllib.parse import urlsplit

import pytest
import requests
from typer.testing import CliRunner

from synth.cli import app
from synth.config import load_config
from synth.workbench.catalog import fetch_catalog

EVALUATORS = "/api/public/v2/evaluators"
RULES = "/api/public/v2/evaluation-rules"


class Response:
    def __init__(self, data, status=200):
        self.data, self.status_code = deepcopy(data), status
        self.text = str(data)
        self.headers = {}

    def json(self):
        return deepcopy(self.data)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(self.text, response=self)


class LangfuseAPI:
    def __init__(self):
        self.evaluators = []
        self.rules = []
        self.calls = []
        self.failures = {}
        self.page_size = 2
        self.datasets = [{"id": "ds-cert", "name": "certification-suite"}]

    def request(self, method, url, *, params=None, json=None, **kw):
        path = urlsplit(url).path
        self.calls.append((method, path, deepcopy(json), deepcopy(params)))
        failure = self.failures.get((method, path))
        if failure:
            return Response({"message": "service failure"}, failure)
        collection = (self.evaluators if path.startswith(EVALUATORS) else
                      self.rules if path.startswith(RULES) else None)
        if collection is not None:
            root = EVALUATORS if path.startswith(EVALUATORS) else RULES
            resource_id = path[len(root):].lstrip("/")
            if method == "GET":
                if resource_id:
                    return Response(next(r for r in collection if r["id"] == resource_id))
                offset = int((params or {}).get("cursor", "offset:0").split(":")[1])
                end = offset + self.page_size
                meta = {"cursor": f"offset:{end}"} if end < len(collection) else {}
                return Response({"data": collection[offset:end], "meta": meta})
            if root == RULES:
                assert not set(json) - {"name", "enabled", "sampling", "filter", "evaluatorAssignments"}
                for a in json.get("evaluatorAssignments", []):
                    assert set(a) <= {"evaluatorId", "variableMapping"}
                    assert any(e["id"] == a["evaluatorId"] for e in self.evaluators)
            else:
                assert json["type"] in {"code", "llm_as_judge"}
                if json["type"] == "llm_as_judge":
                    assert "reasoning" not in json["outputDefinition"]
                    assert "score" not in json["outputDefinition"]
                    assert "version" not in json["outputDefinition"]
            if method == "POST":
                row = {"id": f"{'e' if root == EVALUATORS else 'r'}-{len(collection)+1}", **deepcopy(json)}
                if root == EVALUATORS:
                    row.update(version=1, evaluationRuleAssignments=[], status="active",
                               pausedReason=None, pausedMessage=None)
                    if row["type"] == "llm_as_judge":
                        if isinstance(row["prompt"], str):
                            row["prompt"] = [{"role": "user", "content": row["prompt"]}]
                        row.update(variables=["input", "output"], status="paused",
                                   pausedReason="DEFAULT_MODEL_MISSING",
                                   pausedMessage="Configure a project default evaluation model.")
                        row.setdefault("modelConfig", None)
                        row.setdefault("variableMapping", None)
                collection.append(row)
                return Response(row, 201)
            assert method == "PATCH" and resource_id
            row = next(r for r in collection if r["id"] == resource_id)
            row.update(deepcopy(json))
            if root == EVALUATORS:
                row["version"] += 1
            return Response(row)
        if method == "GET":
            if path == "/api/public/v2/datasets":
                page = (params or {}).get("page", 1)
                return Response({"data": self.datasets[page-1:page],
                                 "meta": {"totalPages": len(self.datasets)}})
            if path in {"/api/public/v2/prompts", "/api/public/score-configs", "/api/public/dataset-items"}:
                return Response({"data": [], "meta": {"totalPages": 1}})
            if path == "/api/public/unstable/evaluators":
                return Response({}, 404)
        pytest.fail(f"Unexpected API call: {method} {path}")


@pytest.fixture
def api(monkeypatch):
    from langfuse_synth_core import lfread
    api = LangfuseAPI()
    monkeypatch.setattr(lfread, "request_retry", api.request)
    monkeypatch.setattr(requests, "request", api.request)
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "LLM_PROVIDER", "LLM_MODEL"):
        monkeypatch.delenv(key, raising=False)
    return api


def provision():
    result = CliRunner().invoke(app, ["evaluators", "--config", "config/demo.yaml"])
    assert result.exit_code == 0, result.output or str(result.exception)
    return result.output


def test_provisioning_creates_all_five_without_a_model_and_workbench_reads_them(api):
    output = provision()
    cat = fetch_catalog(load_config("config/demo.yaml"), with_items=False)
    assert cat.judges_api and not cat.error
    assert {e["name"] for e in cat.judges} == {
        "numeric_accuracy", "citation_format", "escalation_correctness",
        "groundedness", "citation_coverage"}
    assert len(cat.rules) == 7
    assert "default evaluation model" in output
    for e in cat.judges:
        if e["type"] == "llm_as_judge":
            assert e["outputDefinition"]["minValue"] == 0
            assert e["outputDefinition"]["maxValue"] == 1
        else:
            assert 'data_type="CATEGORICAL"' in e["sourceCode"]
            assert '"pass" if ok else "fail"' in e["sourceCode"]
    for rule in cat.rules:
        cols = {f["column"]: f["value"] for f in rule["filter"]}
        if "datasetId" in cols:
            assert cols["datasetId"] == ["ds-cert"]
            assert cols["isExperimentItemRootSpan"] is True
        else:
            assert not rule["enabled"]
            assert cols["isRootObservation"] is True
            assert cols["name"] == ["copilot-turn"]
    assert all("unstable" not in path for _, path, _, _ in api.calls)


def test_reruns_reuse_ids_without_versions_or_writes_and_reconcile_definition_updates(api, monkeypatch):
    from synth.workbench import judges
    provision()
    identities = {e["name"]: e["id"] for e in api.evaluators}
    # Operator configuration and unrelated assignments must survive definition updates.
    groundedness = next(e for e in api.evaluators if e["name"] == "groundedness")
    groundedness["modelConfig"] = {"provider": "Operator connection", "model": "chosen-model"}
    live = next(r for r in api.rules if "groundedness-observations" in r["name"])
    live.update(enabled=True, sampling=0.2)
    custom_assignment = {"evaluatorId": "external", "variableMapping": [{"variable": "x", "source": "output"}]}
    live["evaluatorAssignments"].append(custom_assignment)
    before = deepcopy(api.rules)
    api.calls.clear()
    provision()
    assert all(method == "GET" for method, *_ in api.calls)
    assert all(e["version"] == 1 for e in api.evaluators)
    assert api.rules == before

    monkeypatch.setitem(judges.CODE_EVALUATORS, "numeric_accuracy", judges.CODE_EVALUATORS["numeric_accuracy"] + "\n# revised certification check\n")
    monkeypatch.setitem(judges.JUDGE_TEMPLATES, "groundedness", {
        **judges.JUDGE_TEMPLATES["groundedness"], "prompt": "Updated rubric: {{input}} {{output}}"})
    provision()
    assert {e["name"]: e["id"] for e in api.evaluators} == identities
    assert {e["name"] for e in api.evaluators if e["version"] == 2} == {"numeric_accuracy", "groundedness"}
    assert groundedness["modelConfig"] == {"provider": "Operator connection", "model": "chosen-model"}
    assert api.rules == before
    api.calls.clear()
    provision()
    assert all(method == "GET" for method, *_ in api.calls)


def test_workbench_creates_judges_only_for_certification_and_explains_missing_model(api, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from synth.live.app import create_app
    from synth.workbench import views
    cfg = load_config("config/demo.yaml")
    cfg.workbench.results_dir = str(tmp_path)
    api.datasets = [{"id": "other", "name": "unrelated"}, *api.datasets]
    views._CATALOG_CACHE.clear()
    client = TestClient(create_app(cfg))
    response = client.post("/workbench/judges", data={"judge": "groundedness"}, follow_redirects=False)
    assert response.status_code == 303
    assert len(api.rules) == 1
    assert next(f["value"] for f in api.rules[0]["filter"] if f["column"] == "datasetId") == ["ds-cert"]
    response = client.get("/workbench/designer")
    assert "default evaluation model" in response.text
    assert "paused" in response.text
    assert "reconcile" in response.text.lower()
    client.post("/workbench/judges", data={"judge": "groundedness"}, follow_redirects=False)
    assert len(api.evaluators) == len(api.rules) == 1


def test_dataset_resolution_reads_every_page_and_never_scopes_unrelated_datasets(api):
    api.datasets = [{"id": str(i), "name": f"other-{i}"} for i in range(12)] + api.datasets
    provision()
    experiment_rules = [r for r in api.rules if "experiments" in r["name"]]
    assert len(experiment_rules) == 5
    assert all(next(f["value"] for f in r["filter"] if f["column"] == "datasetId") == ["ds-cert"]
               for r in experiment_rules)


@pytest.mark.parametrize("status, message", [(404, "stable API unavailable"), (401, "authentication"),
                                            (403, "permission"), (429, "rate limited"), (503, "temporary server")])
def test_api_failures_are_actionable_without_writes(api, status, message):
    api.failures[("GET", EVALUATORS)] = status
    output = provision()
    assert message in output
    assert not api.evaluators and not api.rules
    assert all(method == "GET" for method, *_ in api.calls)
    cat = fetch_catalog(load_config("config/demo.yaml"), with_items=False)
    assert message in cat.error
    assert cat.judges_api == (status != 404)


def test_ambiguous_names_are_reported_without_selection_or_destructive_reconciliation(api):
    provision()
    duplicate = deepcopy(api.evaluators[0])
    duplicate["id"] = "duplicate-id"
    api.evaluators.append(duplicate)
    before = deepcopy((api.evaluators, api.rules))
    api.calls.clear()
    output = provision()
    assert "ambiguous name" in output
    assert (api.evaluators, api.rules) == before
    assert all(method == "GET" for method, *_ in api.calls)


def test_partial_write_failure_is_reported_and_rerun_finishes_without_duplicates(api):
    api.failures[("POST", RULES)] = 503
    output = provision()
    assert "503" in output
    assert len(api.evaluators) == 5 and not api.rules
    api.failures.clear()
    provision()
    assert len(api.evaluators) == 5 and len(api.rules) == 7
    assert all(e["version"] == 1 for e in api.evaluators)


def test_adopts_a_manual_definition_and_leaves_unrelated_project_state(api):
    from synth.workbench.judges import CODE_EVALUATORS
    api.evaluators.append({"id": "manual-code", "name": "numeric_accuracy", "type": "code",
                           "sourceCode": CODE_EVALUATORS["numeric_accuracy"].strip() + "\n",
                           "sourceCodeLanguage": "PYTHON", "version": 4})
    api.evaluators.append({"id": "external", "name": "custom", "type": "code", "version": 8})
    unrelated = {"id": "external-rule", "name": "operator-rule", "filter": [],
                 "enabled": True, "sampling": 0.7,
                 "evaluatorAssignments": [{"evaluatorId": "manual-code", "variableMapping": None}]}
    api.rules.append(deepcopy(unrelated))
    provision()
    assert api.evaluators[0]["id"] == "manual-code" and api.evaluators[0]["version"] == 4
    assert api.evaluators[1] == {"id": "external", "name": "custom", "type": "code", "version": 8}
    assert api.rules[0] == unrelated


def test_a_failed_second_page_or_detail_read_never_authorizes_writes(api, monkeypatch):
    provision()
    original = api.request

    def fail_page(method, url, **kw):
        if urlsplit(url).path == EVALUATORS and (kw.get("params") or {}).get("cursor"):
            return Response({}, 429)
        return original(method, url, **kw)

    from langfuse_synth_core import lfread
    monkeypatch.setattr(lfread, "request_retry", fail_page)
    api.calls.clear()
    output = provision()
    assert "429" in output and "nothing provisioned or retired" in output
    assert all(method == "GET" for method, *_ in api.calls)
    monkeypatch.setattr(lfread, "request_retry", original)
    api.failures[("GET", EVALUATORS + "/e-1")] = 503
    output = provision()
    assert "503" in output
    assert all(method == "GET" for method, *_ in api.calls)


def test_ambiguous_rules_are_not_selected(api):
    provision()
    duplicate = deepcopy(api.rules[0])
    duplicate["id"] = "duplicate-rule"
    api.rules.append(duplicate)
    api.calls.clear()
    assert "ambiguous name" in provision()
    assert all(method == "GET" for method, *_ in api.calls)


def add_predecessor(api, *, enabled=True):
    """Current stable read shape for a legacy trace rule (no target field)."""
    ev = next(e for e in api.evaluators if e["name"] == "groundedness")
    old = {"id": "old-live-rule", "name": "wb-groundedness-traces", "enabled": enabled,
           "sampling": 0.12, "filter": [{"column": "type", "type": "stringOptions",
                                         "operator": "any of", "value": ["GENERATION"]}],
           "evaluatorAssignments": [{"evaluatorId": ev["id"], "variableMapping": [
               {"mappingType": "legacy", "variable": v, "langfuseObject": "trace",
                "objectName": None, "source": v} for v in ("input", "output")]}]}
    api.rules = [r for r in api.rules if "groundedness-observations" not in r["name"]]
    api.rules.append(old)
    return old


def test_migration_preserves_operator_activation_and_retires_only_after_validated_replacement(api):
    provision()
    old = add_predecessor(api)
    foreign = {**deepcopy(old), "id": "foreign-rule", "name": "operator-rule"}
    api.rules.append(foreign)
    api.calls.clear()
    provision()
    replacement = next(r for r in api.rules if "groundedness-observations" in r["name"])
    assert replacement["enabled"] and replacement["sampling"] == 0.12
    assert not old["enabled"]
    assert foreign["enabled"]
    reads = [(m, p) for m, p, *_ in api.calls]
    assert reads.index(("GET", RULES + "/" + replacement["id"])) < reads.index(("PATCH", RULES + "/old-live-rule"))
    assert all(m != "DELETE" for m, *_ in api.calls)
    before = deepcopy(api.rules)
    provision()
    assert api.rules == before


def test_failed_replacement_readback_keeps_predecessor_enabled(api, monkeypatch):
    provision()
    old = add_predecessor(api)
    original = api.request
    from langfuse_synth_core import lfread

    def fail_readback(method, url, **kw):
        if method == "GET" and urlsplit(url).path.startswith(RULES + "/"):
            return Response({}, 503)
        return original(method, url, **kw)

    monkeypatch.setattr(lfread, "request_retry", fail_readback)
    output = provision()
    assert "503" in output
    assert old["enabled"]


@pytest.mark.parametrize("key", ["placeholder", "sk-ant-" + "fake-test-key-" * 5])
def test_provisioning_never_writes_provider_connections_even_with_environment_keys(api, monkeypatch, key):
    monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    provision()
    assert len(api.evaluators) == 5
    assert not any("llm-connections" in path for _, path, *_ in api.calls)


def test_shared_rules_with_different_filters_are_not_reconfigured(api):
    provision()
    shared = next(r for r in api.rules if "groundedness-observations" in r["name"])
    shared["evaluatorAssignments"].append({"evaluatorId": "operator-evaluator", "variableMapping": None})
    shared["filter"] = []
    before = deepcopy(shared)
    output = provision()
    assert shared == before
    assert "shared rule" in output and "operator" in output


def test_foreign_assignments_cannot_hide_an_ambiguous_successor_during_retirement(api):
    provision()
    old = add_predecessor(api)
    provision()
    old["enabled"] = True
    successor = next(r for r in api.rules if "groundedness-observations" in r["name"])
    duplicate = {**deepcopy(successor), "id": "foreign-successor",
                 "evaluatorAssignments": [{"evaluatorId": "operator-evaluator", "variableMapping": None}]}
    api.rules.append(duplicate)
    output = provision()
    assert "ambiguous" in output
    assert old["enabled"]
