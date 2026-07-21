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
from ..llm import API_KEY_ENV, resolve_model, resolve_provider
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


def _request(method: str, url: str, **kw) -> tuple[requests.Response | None, str]:
    """Best-effort HTTP for the unstable / newer-only surfaces. These endpoints are a
    **self-hosted gap**: on older self-hosted (v3) they may 404, or the host may time
    out / reset the connection. Every caller degrades gracefully, so a transport-level
    failure must return ``(None, msg)`` rather than raise — a missing capability can
    never abort the seed. (HTTP error *statuses* are returned to the caller as a normal
    response so it can branch on 404/422/etc.)"""
    kw.setdefault("timeout", 20)
    try:
        return requests.request(method, url, **kw), ""
    except requests.RequestException as exc:
        return None, f"request failed (self-hosted gap or transient): {exc}"


# Deterministic CODE evaluators (unstable API, type="code") — no LLM connection needed.
# Each is self-contained Python implementing evaluate(ctx) -> EvaluationResult, mirroring
# synth.grading so a UI-run evaluator and the seed agree. The runtime injects Score and
# EvaluationResult; ctx.observation.output is the copilot answer, ctx.experiment.
# item_expected_output is the dataset item's expected answer.
# Shared, self-contained dict coercion prepended to every code evaluator. ``output`` /
# ``item_expected_output`` arrive as a dict (our run_experiment task) OR as a string —
# a UI Prompt Experiment yields the model's raw TEXT/JSON-string, so calling ``.get()``
# on it raises ``'str' object has no attribute 'get'`` (the crash seen on the live deck).
# ``_d`` parses JSON strings, unwraps a chat-message ``{"role","content"}`` wrapper, and
# falls back to ``{}`` for free text — so the evaluator scores gracefully instead of
# crashing. Standard library only (the runtime allows no third-party deps).
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


def ensure_code_evaluator(cfg: Config, name: str, source: str) -> tuple[dict | None, str]:
    """Create (or reuse) a deterministic code evaluator. No LLM connection required.

    Update-aware: if an evaluator of this name exists but its ``sourceCode`` differs from
    ``source``, POST again — the unstable API creates the NEXT version and auto-migrates
    existing evaluation rules to it. So re-running ``synth evaluators`` ships code fixes;
    identical source is a no-op (no version churn)."""
    base = cfg.target.base_url
    existing, available = list_judges(base)
    if not available:
        return None, "unstable evaluator API not available"
    desired = source.strip() + "\n"
    match = next((e for e in existing if e.get("name") == name), None)
    if match:
        current = match.get("sourceCode") or ""
        if not current:  # the list endpoint omits sourceCode — fetch the detail
            det, _e = _request("GET", f"{base.rstrip('/')}/api/public/unstable/evaluators/{match.get('id')}",
                               auth=_auth())
            if det is not None and det.status_code == 200:
                current = det.json().get("sourceCode") or ""
        if current.strip() == desired.strip():
            return match, ""  # unchanged — no new version
        # else fall through to POST a new version (existing rules auto-migrate to it)
    body = {"name": name, "type": "code", "sourceCode": desired,
            "sourceCodeLanguage": "PYTHON"}
    resp, err = _request("POST", f"{base.rstrip('/')}/api/public/unstable/evaluators",
                         json=body, auth=_auth())
    if resp is None:
        return None, err
    if resp.status_code in (200, 201):
        return resp.json(), ""
    return None, f"{resp.status_code}: {resp.text[:300]}"


# Real API-key prefixes per provider — guards against a ``.env`` placeholder being
# upserted (which would create/CLOBBER the project's LLM connection with an invalid
# secret: preflight then 401s on the judges).
_KEY_PREFIX = {"anthropic": "sk-ant-", "openai": "sk-"}


def _looks_like_real_key(provider: str, key: str) -> bool:
    """A real key starts with the provider prefix and is well over 40 chars."""
    return key.startswith(_KEY_PREFIX.get(provider, "sk-")) and len(key) > 40


def ensure_llm_connection(cfg: Config) -> tuple[bool, str]:
    """Upsert an LLM connection so the managed judges have a model to run on. Uses the
    selected provider's key from env (``LLM_PROVIDER``; default Anthropic). Returns
    ``(ok, message)``. Without a *real* key, the judges can't be created — the caller
    skips, but any connection already configured in the project is left untouched."""
    base = cfg.target.base_url
    provider = resolve_provider()
    env_var = API_KEY_ENV[provider]
    key = os.environ.get(env_var, "")
    if not key:
        return False, f"no {env_var} in env — add an LLM connection in project settings"
    if not _looks_like_real_key(provider, key):
        return False, (f"{env_var} looks like a placeholder — NOT upserting (would "
                       "clobber a real connection). Paste a real key in .env or add the "
                       "connection in project settings, then re-run `synth evaluators`")
    body = {"provider": provider, "adapter": provider, "secretKey": key,
            "withDefaultModels": True}
    resp, err = _request("PUT", f"{base.rstrip('/')}/api/public/llm-connections",
                         json=body, auth=_auth())
    if resp is None:
        return False, err
    if resp.status_code in (200, 201):
        return True, f"{provider} LLM connection upserted"
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


def _judge_provider(base: str, provider: str) -> str:
    """The ``modelConfig.provider`` must match an existing LLM connection's ``provider``
    value EXACTLY, including casing — the UI registers Anthropic as ``"Anthropic"``, so
    sending ``"anthropic"`` yields a 422 "No valid LLM model found". Read the connection
    list and return the provider whose adapter matches ``provider`` (fallback: the
    provider id capitalised, e.g. ``"Anthropic"`` / ``"Openai"``)."""
    try:
        conns = requests.get(f"{base.rstrip('/')}/api/public/llm-connections",
                             params={"limit": 50}, auth=_auth(), timeout=12).json().get("data", [])
    except requests.RequestException:
        conns = []
    for c in conns:
        if c.get("adapter") == provider and c.get("provider"):
            return c["provider"]
    return provider.capitalize()


def _judge_model_config(base: str, cfg: Config) -> dict:
    """The managed judge's provider + model for the selected LLM provider.

    Anthropic (the default) keeps ``cfg.certification.judge_model`` exactly, so existing
    deployments are unchanged; any other provider resolves its own judge model
    (``LLM_MODEL`` if set, else the provider default)."""
    provider = resolve_provider()
    model = cfg.certification.judge_model if provider == "anthropic" else resolve_model(provider)
    return {"provider": _judge_provider(base, provider), "model": model}


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
        "modelConfig": _judge_model_config(base, cfg),
    }
    resp, err = _request("POST", f"{base.rstrip('/')}/api/public/unstable/evaluators",
                         json=body, auth=_auth())
    if resp is None:
        return None, err
    if resp.status_code in (200, 201):
        return resp.json(), ""
    return None, f"{resp.status_code}: {resp.text[:400]}"


def ensure_rule(cfg: Config, judge: dict, dataset_ids: list[str], *,
                target: str = "experiment", sampling: float = 1.0,
                enabled: bool = True) -> tuple[dict | None, str]:
    """Create an evaluation rule scoping ``judge`` to either certification
    ``experiment`` runs (filtered by ``datasetId``) or live ``observation`` traffic
    (filtered to copilot generations) — the SAME evaluator, two surfaces.

    Body shape verified against the OpenAPI spec / live Cloud API:
    - ``evaluator`` must carry ``{name, scope, type}`` — ``type`` is ``code`` or
      ``llm_as_judge`` (omitting it is the 400 ``invalid_body`` we hit before);
    - **code** evaluators take NO ``mapping`` — they read ``ctx`` directly and the
      server auto-fills the variable mapping. They are also **experiment-only**: they
      compare against ``expected_output``, which the API only allows for
      ``target=experiment``; callers must not point them at ``observation``;
    - **llm_as_judge** evaluators need a ``mapping`` whose ``source`` is a bare enum
      value. For ``experiment``: {input, output, metadata, expected_output,
      experiment_item_metadata}; for ``observation``: {input, output, metadata}. Our
      two judges use only ``{{input}}``/``{{output}}`` — valid on both targets.

    ``sampling`` is the fraction of matching objects to evaluate (1.0 for experiments;
    a low rate for live traces). ``enabled=False`` creates the rule deactivated (no
    preflight, zero triggers) — used for trace monitoring that ships paused.

    Server-side validation errors are surfaced verbatim (the unstable API returns
    structured recovery guidance)."""
    base = cfg.target.base_url
    etype = judge.get("type") or "llm_as_judge"
    if target == "experiment":
        name = f"wb-{judge.get('name')}-experiments"
        rule_filter = [{"column": "datasetId", "operator": "any of",
                        "value": dataset_ids, "type": "stringOptions"}]
    else:
        # Live monitoring: judge the copilot's answer generations as they ingest.
        name = f"wb-{judge.get('name')}-traces"
        rule_filter = [{"column": "type", "operator": "any of",
                        "value": ["GENERATION"], "type": "stringOptions"}]
    body = {
        "name": name,
        "target": target,
        "enabled": enabled,
        "evaluator": {"name": judge.get("name"),
                      "scope": judge.get("scope", "project"),
                      "type": etype},
        "sampling": sampling,
        "filter": rule_filter,
    }
    if etype != "code":
        # Map each declared prompt variable to a valid source for this target. Our
        # judges use input/output only — valid on both observation and experiment.
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
    resp, err = _request("POST", f"{base.rstrip('/')}/api/public/unstable/evaluation-rules",
                         json=body, auth=_auth())
    if resp is None:
        return None, err
    if resp.status_code in (200, 201):
        return resp.json(), ""
    if resp.status_code == 409:
        return {"name": name}, ""  # already exists — fine
    return None, f"{resp.status_code}: {resp.text[:400]}"
