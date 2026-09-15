"""Provision kit evaluators and rules through Langfuse's stable v2 API.

Names are adoption hints, never identifiers. A complete inventory and an unambiguous
match are required before any write. Definitions update by ID; unchanged definitions
make no new version. Project model connections are operator-owned.
"""
from __future__ import annotations

import os
from urllib.parse import quote

import requests

from ..config import Config
from ..script import _CITATION_JUDGE, _GROUNDEDNESS_JUDGE
from .reads import probe_json

EVALUATORS_PATH = "/api/public/v2/evaluators"
RULES_PATH = "/api/public/v2/evaluation-rules"

JUDGE_TEMPLATES = {
    "groundedness": {
        "prompt": _GROUNDEDNESS_JUDGE,
        "dataType": "NUMERIC",
        "reasoning": "One sentence naming any unsupported claim or contradicted figure.",
        "score": "0.0–1.0: the fraction of claims supported by the cited extract lines.",
    },
    "citation_coverage": {
        "prompt": _CITATION_JUDGE,
        "dataType": "NUMERIC",
        "reasoning": "One sentence on any claim missing a correct citation.",
        "score": "0.0–1.0: the share of claims that carry a correct citation.",
    },
}


_COERCE = '''
def _d(x):
    import json
    if isinstance(x, str):
        try:
            x = json.loads(x)
        except Exception:
            return {}
    if isinstance(x, dict):
        if "answer_type" not in x and isinstance(x.get("content"), str):
            try:
                c = json.loads(x["content"])
                if isinstance(c, dict):
                    return c
            except Exception:
                pass
        return x
    return {}
'''

CODE_EVALUATORS = {
    "numeric_accuracy": '''
def evaluate(ctx):
''' + _COERCE.replace("\n", "\n    ") + '''
    exp = _d(ctx.experiment.item_expected_output if ctx.experiment else None)
    out = _d(ctx.observation.output)
    detail, ok = "", True
    if out.get("answer_type") != exp.get("answer_type"):
        ok, detail = False, "answer_type %r != %r" % (out.get("answer_type"), exp.get("answer_type"))
    if ok:
        for k, v in (exp.get("figures") or {}).items():
            if (out.get("figures") or {}).get(k) != v:
                ok, detail = False, "%s = %s != %s" % (k, (out.get("figures") or {}).get(k), v); break
    if ok:
        for k, v in (exp.get("ratios") or {}).items():
            got = (out.get("ratios") or {}).get(k)
            if got is None or abs(float(got) - float(v)) > 0.02:
                ok, detail = False, "ratio %s = %s outside +/-0.02 of %s" % (k, got, v); break
    return EvaluationResult(scores=[Score(name="numeric_accuracy",
        value="pass" if ok else "fail", data_type="CATEGORICAL",
        comment=detail or "figures and ratios match")])
''',
    "citation_format": '''
def evaluate(ctx):
''' + _COERCE.replace("\n", "\n    ") + '''
    exp = _d(ctx.experiment.item_expected_output if ctx.experiment else None)
    out = _d(ctx.observation.output)
    want, got = set(exp.get("citations") or []), set(out.get("citations") or [])
    ok = want == got
    detail = "citations match" if ok else "missing %s; uncited-source %s" % (
        sorted(want - got), sorted(got - want))
    return EvaluationResult(scores=[Score(name="citation_format",
        value="pass" if ok else "fail", data_type="CATEGORICAL", comment=detail)])
''',
    "escalation_correctness": '''
def evaluate(ctx):
''' + _COERCE.replace("\n", "\n    ") + '''
    exp = _d(ctx.experiment.item_expected_output if ctx.experiment else None)
    out = _d(ctx.observation.output)
    ok = out.get("answer_type") == exp.get("answer_type")
    return EvaluationResult(scores=[Score(name="escalation_correctness",
        value="pass" if ok else "fail", data_type="CATEGORICAL",
        comment="correctly %s" % exp.get("answer_type") if ok
                else "answer_type %r != %r" % (out.get("answer_type"), exp.get("answer_type")))])
''',
}



def _auth():
    return (os.environ.get("LANGFUSE_PUBLIC_KEY", ""), os.environ.get("LANGFUSE_SECRET_KEY", ""))


def _request(method: str, url: str, **kw) -> tuple[requests.Response | None, str]:
    # Never automatically retry a create: the server may have committed it before
    # the connection failed. The next provisioning run inventories the project first.
    kw.setdefault("timeout", 20)
    try:
        return requests.request(method, url, **kw), ""
    except requests.RequestException as exc:
        return None, f"{type(exc).__name__}: temporary connection failure; rerun provisioning to reconcile"


def _http_error(status: int | None, path: str) -> str:
    if status in (401, 403):
        action = "authentication/permission failure; check project API keys and access"
    elif status == 429:
        action = "rate limited (transient); wait for Retry-After, then rerun"
    elif status is not None and status >= 500:
        action = "temporary server failure (transient); retry when the server recovers"
    elif status in (404, 405):
        action = "stable API unavailable; check the base URL, proxy routing and Langfuse version"
    else:
        action = "request rejected; check the current API contract and project configuration"
    return f"HTTP {status} at {path}: {action}"


def _read(base: str, path: str) -> tuple[dict | None, str]:
    try:
        result = probe_json(base, path)
        if not isinstance(result, dict) or not result.get("id"):
            return None, f"invalid resource response from {path}; reconciliation stopped"
        return result, ""
    except requests.HTTPError as exc:
        return None, _http_error(getattr(exc.response, "status_code", None), path)
    except (requests.RequestException, ValueError) as exc:
        return None, f"{type(exc).__name__} reading {path}; retry after checking the server"


def _write(cfg: Config, method: str, path: str, body: dict) -> tuple[dict | None, str]:
    resp, err = _request(method, f"{cfg.target.base_url.rstrip('/')}{path}", json=body, auth=_auth())
    if resp is None:
        return None, err
    if resp.status_code not in (200, 201):
        return None, _http_error(resp.status_code, path)
    try:
        result = resp.json()
        if isinstance(result, dict) and result.get("id"):
            return result, ""
    except ValueError:
        pass
    return None, f"invalid write response from {path}; rerun provisioning to reconcile"


def _probe_list(base: str, path: str) -> tuple[list[dict], bool, str]:
    """Read a complete cursor collection or return no inventory and an error."""
    rows, cursors = [], set()
    params = {"limit": 100}
    try:
        while True:
            page = probe_json(base, path, params)
            if not isinstance(page.get("data"), list) or not isinstance(page.get("meta"), dict):
                return [], True, f"invalid page from {path}; reconciliation stopped"
            rows.extend(page["data"])
            cursor = page["meta"].get("cursor")
            if not cursor:
                return rows, True, ""
            if not isinstance(cursor, str) or cursor in cursors:
                return [], True, f"invalid/repeated cursor from {path}; reconciliation stopped"
            cursors.add(cursor)
            params = {"limit": 100, "cursor": cursor}
    except requests.HTTPError as exc:
        status = getattr(exc.response, "status_code", None)
        return [], status not in (404, 405), _http_error(status, path)
    except (requests.RequestException, ValueError) as exc:
        return [], True, f"{type(exc).__name__} reading {path} (transient); retry when the server recovers"


def list_judges(base: str) -> tuple[list[dict], bool, str]:
    return _probe_list(base, EVALUATORS_PATH)


def list_rules(base: str) -> tuple[list[dict], bool, str]:
    return _probe_list(base, RULES_PATH)


def _match(rows: list[dict], name: str) -> tuple[dict | None, str]:
    matches = [r for r in rows if r.get("name") == name]
    if len(matches) > 1:
        ids = ", ".join(str(r.get("id")) for r in matches)
        return None, f"ambiguous name {name!r} (IDs: {ids}); resolve the duplicate names before provisioning"
    return (matches[0] if matches else None), ""


def _normalized(value):
    # Langfuse emits optional jsonPath=null and omits empty legacy descriptions.
    if isinstance(value, dict):
        return {k: _normalized(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_normalized(v) for v in value]
    return value


def _ensure_evaluator(cfg: Config, body: dict) -> tuple[dict | None, str]:
    existing, available, err = list_judges(cfg.target.base_url)
    if err or not available:
        return None, err or "stable evaluator API unavailable"
    current, err = _match(existing, body["name"])
    if err:
        return None, err
    if current is None:
        return _write(cfg, "POST", EVALUATORS_PATH, body)
    path = f"{EVALUATORS_PATH}/{quote(current['id'], safe='')}"
    current, err = _read(cfg.target.base_url, path)
    if current is None:
        return None, err
    if current.get("type") != body["type"]:
        return None, f"{body['name']}: existing evaluator has a different type; left unchanged"
    if body["type"] == "llm_as_judge":
        # Definition replacement is complete, so explicitly retain the operator's
        # chosen model. Omission would silently switch it to the project default.
        body["modelConfig"] = current.get("modelConfig")
    if all(_normalized(current.get(k)) == _normalized(v) for k, v in body.items()):
        return current, ""
    return _write(cfg, "PATCH", path, body)


def ensure_code_evaluator(cfg: Config, name: str, source: str) -> tuple[dict | None, str]:
    return _ensure_evaluator(cfg, {"name": name, "type": "code",
                                  "sourceCode": source.strip() + "\n",
                                  "sourceCodeLanguage": "PYTHON"})


def ensure_judge(cfg: Config, name: str) -> tuple[dict | None, str]:
    tpl = JUDGE_TEMPLATES.get(name)
    if tpl is None:
        return None, f"unknown judge template {name!r}"
    return _ensure_evaluator(cfg, {
        "name": name, "type": "llm_as_judge",
        "prompt": [{"role": "user", "content": tpl["prompt"]}],
        "variableMapping": [{"variable": v, "source": v} for v in ("input", "output")],
        "modelConfig": None,
        "outputDefinition": {
            "dataType": "NUMERIC", "minValue": 0, "maxValue": 1,
            "scoreReasoningInstructions": tpl["reasoning"],
            "scoreValueInstructions": tpl["score"],
        },
    })


def judge_status(judge: dict) -> str:
    if judge.get("status") == "paused":
        return (f"paused: {judge.get('pausedReason') or 'configuration required'} — "
                f"{judge.get('pausedMessage') or 'check the evaluation model in project settings'}")
    return "configured"


COPILOT_TRACE_NAME = "copilot-turn"
ROOT_OBSERVATION_FILTER = [
    {"type": "stringOptions", "column": "traceName", "operator": "any of",
     "value": [COPILOT_TRACE_NAME]},
    {"type": "stringOptions", "column": "name", "operator": "any of",
     "value": [COPILOT_TRACE_NAME]},
    {"type": "boolean", "column": "isRootObservation", "operator": "=", "value": True},
]


def rule_name(judge_name: str, target: str) -> str:
    # Distinct successor names also handle legacy rules whose stable read shape no
    # longer exposes target. Their IDs and complete configuration remain for audit.
    suffix = "experiments" if target == "experiment" else "observations"
    return f"wb-{judge_name}-{suffix}-v2"


def predecessor_names(judge_name: str, target: str) -> tuple[str, ...]:
    if target == "experiment":
        return (f"wb-{judge_name}-experiments",)
    return (f"wb-{judge_name}-observations", f"wb-{judge_name}-traces")


def rule_filter(target: str, dataset_ids: list[str]) -> list[dict]:
    if target == "experiment":
        return [
            {"type": "stringOptions", "column": "datasetId", "operator": "any of",
             "value": sorted(set(dataset_ids))},
            {"type": "boolean", "column": "isExperimentItemRootSpan", "operator": "=", "value": True},
        ]
    return ROOT_OBSERVATION_FILTER


def get_rule(cfg: Config, rule_id: str) -> tuple[dict | None, str]:
    return _read(cfg.target.base_url, f"{RULES_PATH}/{quote(rule_id, safe='')}")


def successor_assignment(judge: dict, predecessor: dict) -> tuple[dict | None, str]:
    """Preserve explicit mappings where their source exists on the new root."""
    assignments = predecessor.get("evaluatorAssignments", [])
    if len(assignments) != 1 or assignments[0].get("evaluatorId") != judge["id"]:
        return None, "assignments are not exclusively kit-owned; left unchanged"
    mappings = assignments[0].get("variableMapping")
    if judge["type"] == "code":
        return {"evaluatorId": judge["id"], "variableMapping": None}, ""
    converted = []
    for mapping in mappings or []:
        if mapping.get("mappingType") == "legacy":
            # Only overall trace input/output is known to be copied onto this kit's
            # root. Named child observations need an operator migration decision.
            if mapping.get("objectName") or mapping.get("langfuseObject") != "trace":
                return None, "legacy mapping needs operator review; predecessor retained"
        if mapping.get("source") not in ("input", "output", "metadata", "tool_calls",
                                         "expected_output", "experiment_item_metadata"):
            return None, "unsupported mapping source; predecessor retained"
        converted.append({k: mapping[k] for k in ("variable", "source", "jsonPath")
                          if mapping.get(k) is not None})
    return {"evaluatorId": judge["id"], "variableMapping": converted if mappings is not None else None}, ""


def patch_rule(cfg: Config, rule_id: str, **fields) -> tuple[bool, str]:
    result, err = _write(cfg, "PATCH", f"{RULES_PATH}/{quote(rule_id, safe='')}", fields)
    return result is not None, err


def ensure_rule(cfg: Config, judge: dict, dataset_ids: list[str], *,
                target: str = "experiment", sampling: float = 1.0,
                enabled: bool = True) -> tuple[dict | None, str]:
    """Reconcile only this kit's rule; retain operator activation and assignments."""
    if target not in ("experiment", "observation"):
        return None, f"unsupported rule scope {target!r}"
    if not judge.get("id"):
        return None, "cannot assign an evaluator without its stable ID"
    if target == "experiment" and not dataset_ids:
        return None, "certification dataset missing; no unscoped rule created"
    if target == "observation" and judge.get("type") == "code":
        return None, "certification code evaluators require experiment expected output"
    name = rule_name(judge["name"], target)
    rows, available, err = list_rules(cfg.target.base_url)
    if err or not available:
        return None, err or "stable evaluation-rule API unavailable"
    current, err = _match(rows, name)
    if err:
        return None, err
    assignment = {"evaluatorId": judge["id"], "variableMapping": None}
    body = {"name": name, "filter": rule_filter(target, dataset_ids),
            "sampling": sampling, "enabled": enabled,
            "evaluatorAssignments": [assignment]}
    if current:
        path = f"{RULES_PATH}/{quote(current['id'], safe='')}"
        current, err = _read(cfg.target.base_url, path)
        if current is None:
            return None, err
        assignments = current.get("evaluatorAssignments", [])
        if not any(a.get("evaluatorId") == judge["id"] for a in assignments):
            return None, f"{name}: assigned to another evaluator; left unchanged"
        # Preserve unrelated assignments and rule-specific operator mappings. The
        # kit repairs its selectors only; evaluator defaults supply its own mappings.
        fields = {"filter": body["filter"]} if current.get("filter") != body["filter"] else {}
        if not fields:
            return current, ""
        result, err = _write(cfg, "PATCH", path, fields)
    else:
        predecessors = [r for r in rows if r.get("name") in predecessor_names(judge["name"], target)]
        if len(predecessors) > 1:
            return None, f"{name}: ambiguous predecessor rules; resolve them before migration"
        if predecessors:
            old = predecessors[0]
            inherited, err = successor_assignment(judge, old)
            if err:
                return None, f"{old['name']}: {err}"
            body.update(enabled=old["enabled"], sampling=old["sampling"],
                        evaluatorAssignments=[inherited])
        result, err = _write(cfg, "POST", RULES_PATH, body)
    if result is None:
        return None, err
    # Read back before callers can consider a predecessor safe to retire.
    result, err = _read(cfg.target.base_url, f"{RULES_PATH}/{quote(result['id'], safe='')}")
    if result is not None and result.get("filter") != body["filter"]:
        return None, f"{name}: replacement filter validation failed; predecessor retained"
    return result, err


def certification_dataset_ids(cfg: Config) -> tuple[list[str], str]:
    """Resolve only the certification suite, across all numbered dataset pages."""
    path = "/api/public/v2/datasets"
    found, page = [], 1
    try:
        while True:
            data = probe_json(cfg.target.base_url, path, {"limit": 100, "page": page})
            found.extend(d["id"] for d in data["data"]
                         if d.get("name") == cfg.certification.dataset.name and d.get("id"))
            if page >= data["meta"]["totalPages"]:
                break
            page += 1
    except requests.HTTPError as exc:
        return [], _http_error(getattr(exc.response, "status_code", None), path)
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        return [], f"{type(exc).__name__} reading certification dataset; no experiment rules changed"
    if len(found) != 1:
        return [], "certification dataset missing or ambiguous; no experiment rules changed"
    return found, ""
