"""Managed-judge management via the unstable evaluator API.

The workbench can create the two scenario judges (groundedness_cert /
policy_compliance) programmatically and scope them to the suites' experiment runs via
evaluation rules — removing the manual "create the judge in the UI" step where the
server supports it.

**Standing risk: this whole module talks to an API Langfuse marks *unstable*.**
``/api/public/unstable/evaluators`` and ``/api/public/unstable/evaluation-rules`` are the
only programmatic way to provision managed evaluators, and they are also the only surface
that still *reads back* the legacy ``trace`` and ``dataset`` rule targets a project may
carry from before v4 — so the migration in :mod:`synth.workbench.cutover` cannot be done
without them. Unstable means the request and response shapes may change without a major
version, and this kit's evaluator provisioning is the surface that breaks when they do.
The risk is accepted, not designed away, and it is contained the same way the
self-hosted-gap risk is: every call degrades to a logged note plus the UI instructions in
the Presenter Runbook, and a failure here can never abort a `seed`. Expect a change here
rather than treating one as an outage; re-read the current schema before touching a body
in this module.

Every call therefore degrades gracefully: on 404 (older self-hosted) or validation errors,
the UI falls back to the copy-paste instructions that already live in the runbook, and
shows the server's structured error verbatim.
"""
from __future__ import annotations

import os

import requests

from ..config import Config
from langfuse_synth_core.companion.llm import API_KEY_ENV, resolve_model, resolve_provider
from ..script import _CITATION_JUDGE, _GROUNDEDNESS_JUDGE
from .reads import probe_json

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
    """Best-effort HTTP for the unstable / newer-only surfaces.

    ``requests`` stays here for the **writes** — creating an evaluator, a rule, an LLM
    connection. Those are POSTs and PUTs against the unstable API, which core models on
    neither seam, so there is nothing to route them through. Every *read* in this module
    goes through ``lfread.get_json`` instead (portal #211). These endpoints are a
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
            try:
                current = probe_json(
                    base, f"/api/public/unstable/evaluators/{match.get('id')}"
                ).get("sourceCode") or ""
            except requests.RequestException:   # unreadable detail: treat as changed, POST
                current = ""
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
    """Returns (evaluators, api_available).

    A 404 is the capability answer — older self-hosted has no unstable evaluator API and the
    workbench degrades to logged UI instructions — and so is any transport failure, which is
    why every exception lands in the same place rather than propagating."""
    try:
        return probe_json(base, "/api/public/unstable/evaluators").get("data", []), True
    except requests.RequestException:   # 404, timeout, reset: all mean "no API here"
        return [], False


def _judge_provider(base: str, provider: str) -> str:
    """The ``modelConfig.provider`` must match an existing LLM connection's ``provider``
    value EXACTLY, including casing — the UI registers Anthropic as ``"Anthropic"``, so
    sending ``"anthropic"`` yields a 422 "No valid LLM model found". Read the connection
    list and return the provider whose adapter matches ``provider`` (fallback: the
    provider id capitalised, e.g. ``"Anthropic"`` / ``"Openai"``)."""
    try:
        conns = probe_json(base, "/api/public/llm-connections", {"limit": 50}).get("data", [])
    except requests.RequestException:   # no connections API here; fall back to capitalising
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


#: The trace this kit writes. The seeded pool and a live playground turn both name their
#: trace — and therefore its root observation — ``copilot-turn`` (``seed.traces`` /
#: ``live.trace``). Stated here rather than imported so this module stays free of the
#: emitters; the golden gate would catch a rename in either of them.
COPILOT_TRACE_NAME = "copilot-turn"

#: The live rule's selector under v4. It has to pick out **one** observation per trace: an
#: observation evaluator scores every observation it matches, and the root is the only one
#: carrying the analyst's question and the copilot's answer together. Both writers put them
#: there — ``seed.events.trace_event`` mints the root span with the overall input and
#: output, and ``live.emit`` opens the trace as that same root — which is what makes the
#: consolidation v4 demands already true here. ``isRootObservation`` matches logical
#: application roots; ``traceName`` keeps the rule off other traffic sharing the project.
#:
#: What this replaces was ``type any of [GENERATION]``, which under v4 matched the planning
#: generation *and* the answer generation of every turn: two scores per trace, one of them
#: grading the planner's tool-call JSON.
#: Filter *types* are per column and the API rejects a mismatch with
#: ``400 invalid_filter_value``, so these are taken from the unstable API's own
#: supported-columns table for ``target=observation`` and not from the migration guide's
#: prose: ``traceName`` is ``stringOptions`` (``any of`` / ``none of``, never a bare ``=``),
#: ``isRootObservation`` is ``boolean`` (``=`` / ``<>``).
ROOT_OBSERVATION_FILTER = [
    {"type": "stringOptions", "column": "traceName", "operator": "any of",
     "value": [COPILOT_TRACE_NAME]},
    {"type": "boolean", "column": "isRootObservation", "operator": "=", "value": True},
]


def rule_name(judge_name: str, target: str) -> str:
    """This kit's deployment name for a rule.

    The live rule is ``-observations``, deliberately not the pre-v4 ``-traces``: the old
    row is a *separate* rule that gets retired rather than overwritten, so rolling back is
    switching one rule off and another on (see :mod:`synth.workbench.cutover`)."""
    suffix = "experiments" if target == "experiment" else "observations"
    return f"wb-{judge_name}-{suffix}"


def list_rules(base: str) -> tuple[list[dict], bool]:
    """Returns ``(evaluation rules, api_available)`` — same capability answer as
    :func:`list_judges`, for the rule half of the surface."""
    try:
        return probe_json(base, "/api/public/unstable/evaluation-rules").get("data", []), True
    except requests.RequestException:   # 404, timeout, reset: all mean "no API here"
        return [], False


def patch_rule(cfg: Config, rule_id: str, **fields) -> tuple[bool, str]:
    """Update one evaluation rule in place. This is how a rule is turned off: the cutover
    **disables** its predecessor rather than deleting it, so the previous configuration
    stays in the project and rolling back is one more PATCH."""
    base = cfg.target.base_url
    resp, err = _request(
        "PATCH", f"{base.rstrip('/')}/api/public/unstable/evaluation-rules/{rule_id}",
        json=fields, auth=_auth())
    if resp is None:
        return False, err
    if resp.status_code in (200, 201, 204):
        return True, ""
    return False, f"{resp.status_code}: {resp.text[:300]}"


def ensure_rule(cfg: Config, judge: dict, dataset_ids: list[str], *,
                target: str = "experiment", sampling: float = 1.0,
                enabled: bool = True) -> tuple[dict | None, str]:
    """Create an evaluation rule scoping ``judge`` to either certification
    ``experiment`` runs (filtered by ``datasetId``) or live ``observation`` traffic
    (filtered to the copilot turn's root observation) — the SAME evaluator, two surfaces.
    Those two are v4's whole target vocabulary: ``trace`` and ``dataset`` are the legacy
    targets the unstable API still *returns* but no longer accepts.

    Body shape verified against the OpenAPI spec / live Cloud API:
    - ``evaluator`` must carry ``{name, scope, type}`` — ``type`` is ``code`` or
      ``llm_as_judge`` (omitting it is the 400 ``invalid_body`` we hit before);
    - **code** evaluators take NO ``mapping`` — they read ``ctx`` directly and the
      server auto-fills the variable mapping. They are also **experiment-only**: they
      compare against ``expected_output``, which the API only allows for
      ``target=experiment``. That is not a hole in the migration — ``experiment`` *is*
      the v4 successor of the legacy ``dataset`` target — but callers must not point a
      code evaluator at ``observation``, where the expected output it grades against
      does not exist;
    - **llm_as_judge** evaluators need a ``mapping`` whose ``source`` is a bare enum
      value. For ``experiment``: {input, output, metadata, tool_calls, expected_output,
      experiment_item_metadata}; for ``observation``: {input, output, metadata,
      tool_calls}. Our two judges use only ``{{input}}``/``{{output}}``, and under v4
      both have to resolve on the target observation *itself* — an observation
      evaluator cannot read siblings or children.

    ``sampling`` is the fraction of matching objects to evaluate (1.0 for experiments;
    a low rate for live traffic). ``enabled=False`` creates the rule deactivated (no
    preflight, zero triggers) — which is how every observation successor ships.

    Server-side validation errors are surfaced verbatim (the unstable API returns
    structured recovery guidance, including ``details.allowedValues`` for a filter
    column it rejects)."""
    base = cfg.target.base_url
    etype = judge.get("type") or "llm_as_judge"
    name = rule_name(judge.get("name"), target)
    if target == "experiment":
        rule_filter = [{"column": "datasetId", "operator": "any of",
                        "value": dataset_ids, "type": "stringOptions"}]
    else:
        rule_filter = [dict(f) for f in ROOT_OBSERVATION_FILTER]
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
        # The rule already exists. "Fine" is not good enough here: a project seeded by an
        # earlier version of this kit carries that version's filter, and leaving it is
        # exactly the silent-drift the v4 migration exists to end. The API's own recovery
        # guidance for a 409 is to PATCH the existing resource, so re-run the configuration
        # onto it. `enabled` is deliberately NOT sent — a rule an operator has already
        # validated and switched on must not be quietly switched off by a re-seed.
        existing, available = list_rules(base)
        match = next((r for r in existing if r.get("name") == name), None)
        if not available or match is None:
            return {"name": name}, ""     # can't read it back; the rule is there, leave it
        fields = {k: body[k] for k in ("target", "sampling", "filter") if k in body}
        if "mapping" in body:
            fields["mapping"] = body["mapping"]
        ok, perr = patch_rule(cfg, match["id"], **fields)
        return (match, "") if ok else (match, f"exists; update failed — {perr}")
    return None, f"{resp.status_code}: {resp.text[:400]}"
