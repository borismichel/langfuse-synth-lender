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
from ..script import _CITATION_JUDGE, _GROUNDEDNESS_JUDGE

# The two LLM-as-judge evaluators, named to match the score configs the rest of the
# kit uses (so judge scores co-filter with everything else on the scores surface).
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


def _auth():
    return (os.environ.get("LANGFUSE_PUBLIC_KEY", ""), os.environ.get("LANGFUSE_SECRET_KEY", ""))


# Deterministic CODE evaluators (unstable API, type="code") — no LLM connection needed.
# Each is self-contained Python implementing evaluate(ctx) -> EvaluationResult, mirroring
# synth.grading so a UI-run evaluator and the seed agree. The runtime injects Score and
# EvaluationResult; ctx.observation.output is the copilot answer, ctx.experiment.
# item_expected_output is the dataset item's expected answer.
CODE_EVALUATORS = {
    "numeric_accuracy": '''
def evaluate(ctx):
    exp = (ctx.experiment.item_expected_output if ctx.experiment else None) or {}
    out = ctx.observation.output or {}
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
    exp = (ctx.experiment.item_expected_output if ctx.experiment else None) or {}
    out = ctx.observation.output or {}
    want, got = set(exp.get("citations") or []), set(out.get("citations") or [])
    ok = want == got
    detail = "citations match" if ok else "missing %s; uncited-source %s" % (
        sorted(want - got), sorted(got - want))
    return EvaluationResult(scores=[Score(name="citation_format",
        value="pass" if ok else "fail", data_type="CATEGORICAL", comment=detail)])
''',
    "escalation_correctness": '''
def evaluate(ctx):
    exp = (ctx.experiment.item_expected_output if ctx.experiment else None) or {}
    out = ctx.observation.output or {}
    ok = out.get("answer_type") == exp.get("answer_type")
    return EvaluationResult(scores=[Score(name="escalation_correctness",
        value="pass" if ok else "fail", data_type="CATEGORICAL",
        comment="correctly %s" % exp.get("answer_type") if ok
                else "answer_type %r != %r" % (out.get("answer_type"), exp.get("answer_type")))])
''',
}


def ensure_code_evaluator(cfg: Config, name: str, source: str) -> tuple[dict | None, str]:
    """Create (or reuse) a deterministic code evaluator. No LLM connection required."""
    base = cfg.target.base_url
    existing, available = list_judges(base)
    if not available:
        return None, "unstable evaluator API not available"
    match = next((e for e in existing if e.get("name") == name), None)
    if match:
        return match, ""
    body = {"name": name, "type": "code", "sourceCode": source.strip() + "\n",
            "sourceCodeLanguage": "PYTHON"}
    resp = requests.post(f"{base.rstrip('/')}/api/public/unstable/evaluators",
                         json=body, auth=_auth(), timeout=20)
    if resp.status_code in (200, 201):
        return resp.json(), ""
    return None, f"{resp.status_code}: {resp.text[:300]}"


def ensure_llm_connection(cfg: Config) -> tuple[bool, str]:
    """Upsert an LLM connection so the managed judges have a model to run on. Uses
    ``ANTHROPIC_API_KEY`` from env (matches the judge_model provider). Returns
    ``(ok, message)``. Without a key, the judges can't be created — the caller skips."""
    base = cfg.target.base_url
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return False, "no ANTHROPIC_API_KEY in env — add an LLM connection in project settings"
    body = {"provider": "anthropic", "adapter": "anthropic", "secretKey": key,
            "withDefaultModels": True}
    resp = requests.put(f"{base.rstrip('/')}/api/public/llm-connections",
                        json=body, auth=_auth(), timeout=20)
    if resp.status_code in (200, 201):
        return True, "anthropic LLM connection upserted"
    if resp.status_code == 404:
        return False, "llm-connections API not available on this server"
    return False, f"{resp.status_code}: {resp.text[:200]}"


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


def _judge_provider(base: str) -> str:
    """The ``modelConfig.provider`` must match an existing LLM connection's ``provider``
    value EXACTLY, including casing — the UI registers Anthropic as ``"Anthropic"``, so
    sending ``"anthropic"`` yields a 422 "No valid LLM model found". Read the connection
    list and return the provider whose adapter is anthropic (fallback ``"Anthropic"``)."""
    try:
        conns = requests.get(f"{base.rstrip('/')}/api/public/llm-connections",
                             params={"limit": 50}, auth=_auth(), timeout=12).json().get("data", [])
    except requests.RequestException:
        conns = []
    for c in conns:
        if c.get("adapter") == "anthropic" and c.get("provider"):
            return c["provider"]
    return "Anthropic"


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
        "modelConfig": {"provider": _judge_provider(base), "model": cfg.certification.judge_model},
    }
    resp = requests.post(f"{base.rstrip('/')}/api/public/unstable/evaluators",
                         json=body, auth=_auth(), timeout=20)
    if resp.status_code in (200, 201):
        return resp.json(), ""
    return None, f"{resp.status_code}: {resp.text[:400]}"


def ensure_rule(cfg: Config, judge: dict, dataset_ids: list[str]) -> tuple[dict | None, str]:
    """Create an evaluation rule scoping ``judge`` to experiment runs on the given
    datasets (``target=experiment``, filtered by ``datasetId``).

    Body shape verified against the OpenAPI spec / live Cloud API:
    - ``evaluator`` must carry ``{name, scope, type}`` — ``type`` is ``code`` or
      ``llm_as_judge`` (omitting it is the 400 ``invalid_body`` we hit before);
    - **code** evaluators take NO ``mapping`` — they read ``ctx`` directly and the
      server auto-fills the variable mapping (confirmed: the response echoes a
      server-generated mapping). Sending a guessed mapping is what was rejected;
    - **llm_as_judge** evaluators need a ``mapping`` whose ``source`` is one of the
      bare enum values {input, output, metadata, expected_output,
      experiment_item_metadata}. Our two judges use only ``{{input}}``/``{{output}}``.

    Server-side validation errors are surfaced verbatim (the unstable API returns
    structured recovery guidance)."""
    base = cfg.target.base_url
    etype = judge.get("type") or "llm_as_judge"
    name = f"wb-{judge.get('name')}-experiments"
    body = {
        "name": name,
        "target": "experiment",
        "enabled": True,
        "evaluator": {"name": judge.get("name"),
                      "scope": judge.get("scope", "project"),
                      "type": etype},
        "sampling": 1,
        "filter": [{"column": "datasetId", "operator": "any of", "value": dataset_ids,
                    "type": "stringOptions"}],
    }
    if etype != "code":
        # Map each declared prompt variable to a valid experiment source. Our judges
        # use input/output only; expected_output/experiment_item_metadata are also
        # valid for target=experiment if a future judge declares them.
        _src = {
            "input": "input",
            "output": "output",
            "metadata": "metadata",
            "expected_output": "expected_output",
            "experimentItemExpectedOutput": "expected_output",
            "experimentItemMetadata": "experiment_item_metadata",
        }
        variables = judge.get("variables") or ["input", "output"]
        body["mapping"] = [{"variable": var, "source": _src.get(var, "input")}
                           for var in variables]
    resp = requests.post(f"{base.rstrip('/')}/api/public/unstable/evaluation-rules",
                         json=body, auth=_auth(), timeout=20)
    if resp.status_code in (200, 201):
        return resp.json(), ""
    if resp.status_code == 409:
        return {"name": name}, ""  # already exists — fine
    return None, f"{resp.status_code}: {resp.text[:400]}"
