"""Managed-judge management via the unstable evaluator API.

The workbench can create the two scenario judges (groundedness_cert /
policy_compliance) programmatically and scope them to the suites' experiment runs via
evaluation rules — removing the manual "create the judge in the UI" step where the
server supports it. The surface is marked *unstable* by Langfuse, so every call here
degrades gracefully: on 404 (older self-hosted) or validation errors, the UI falls
back to the copy-paste instructions that already live in the runbook, and shows the
server's structured error verbatim.
"""
from __future__ import annotations

import os

import requests

from ..config import Config
from ..script import _COMPLIANCE_JUDGE, _GROUNDEDNESS_JUDGE

JUDGE_TEMPLATES = {
    "groundedness_cert": {
        "prompt": _GROUNDEDNESS_JUDGE,
        "dataType": "NUMERIC",
        "reasoning": "One sentence naming any unsupported claim or contradicted figure.",
        "score": "0.0–1.0: the fraction of claims supported by the cited extract lines.",
    },
    "policy_compliance": {
        "prompt": _COMPLIANCE_JUDGE,
        "dataType": "BOOLEAN",
        "reasoning": "One sentence explaining the conduct verdict.",
        "score": "true if the answer complies with all conduct rules, else false.",
    },
}


def _auth():
    return (os.environ.get("LANGFUSE_PUBLIC_KEY", ""), os.environ.get("LANGFUSE_SECRET_KEY", ""))


def list_judges(base: str) -> tuple[list[dict], bool]:
    """Returns (evaluators, api_available)."""
    try:
        resp = requests.get(f"{base.rstrip('/')}/api/public/unstable/evaluators",
                            auth=_auth(), timeout=12)
        if resp.status_code == 404:
            return [], False
        resp.raise_for_status()
        return resp.json().get("data", []), True
    except requests.RequestException:
        return [], False


def ensure_judge(cfg: Config, name: str) -> tuple[dict | None, str]:
    """Create (or reuse) one of the scenario judges. Returns (evaluator, error)."""
    base = cfg.target.base_url
    tpl = JUDGE_TEMPLATES.get(name)
    if tpl is None:
        return None, f"unknown judge template {name!r}"
    existing, available = list_judges(base)
    if not available:
        return None, ("unstable evaluator API not available on this server — create the "
                      "judge in the UI (prompt + mappings are in DEMO_SCRIPT.md beat 4)")
    match = next((e for e in existing if e.get("name") == name), None)
    if match:
        return match, ""
    body = {
        "name": name,
        "prompt": tpl["prompt"],
        "outputDefinition": {
            "dataType": tpl["dataType"],
            "reasoning": {"description": tpl["reasoning"]},
            "score": {"description": tpl["score"]},
        },
        "modelConfig": {"provider": "anthropic", "model": cfg.certification.judge_model},
    }
    resp = requests.post(f"{base.rstrip('/')}/api/public/unstable/evaluators",
                         json=body, auth=_auth(), timeout=20)
    if resp.status_code in (200, 201):
        return resp.json(), ""
    return None, f"{resp.status_code}: {resp.text[:400]}"


def ensure_rule(cfg: Config, judge: dict, dataset_ids: list[str]) -> tuple[dict | None, str]:
    """Create an evaluation rule scoping ``judge`` to experiment runs on the given
    datasets. Variable mapping follows the judge templates: {{input}} ← item input,
    {{output}} ← trace output. Server-side validation errors are surfaced verbatim
    (the unstable API returns structured recovery guidance)."""
    base = cfg.target.base_url
    name = f"wb-{judge.get('name')}-experiments"
    variables = judge.get("variables") or ["input", "output"]
    mapping = []
    for var in variables:
        if var == "input":
            mapping.append({"variable": "input", "source": "experiment_item_input"})
        elif var == "output":
            mapping.append({"variable": "output", "source": "trace_output"})
        elif var == "expected_output":
            mapping.append({"variable": "expected_output", "source": "expected_output"})
        else:
            mapping.append({"variable": var, "source": "trace_input"})
    body = {
        "name": name,
        "target": "experiment",
        "enabled": True,
        "evaluator": {"name": judge.get("name"), "scope": judge.get("scope", "project")},
        "sampling": 1,
        "filter": [{"column": "datasetId", "operator": "any of", "value": dataset_ids,
                    "type": "stringOptions"}],
        "mapping": mapping,
    }
    resp = requests.post(f"{base.rstrip('/')}/api/public/unstable/evaluation-rules",
                         json=body, auth=_auth(), timeout=20)
    if resp.status_code in (200, 201):
        return resp.json(), ""
    if resp.status_code == 409:
        return {"name": name}, ""  # already exists — fine
    return None, f"{resp.status_code}: {resp.text[:400]}"
