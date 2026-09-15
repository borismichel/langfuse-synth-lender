"""Read-only completeness checks for the kit's managed evaluation configuration."""
from __future__ import annotations

from ..config import Config
from ..target import TargetProfile
from .judges import (
    CODE_EVALUATORS, JUDGE_TEMPLATES, certification_dataset_ids, get_evaluator,
    get_rule, judge_status, list_judges, list_rules, rule_filter, rule_name,
)

RECOVERY = "Run `synth evaluators --config <same-config>` to repair evaluators only; do not replay the Spool."


def verify_managed_evaluators(cfg: Config, *, initial: bool = False) -> list[tuple[str, bool, str]]:
    rows, available, error = list_judges(cfg.target.base_url)
    if not available and not TargetProfile.detect(cfg.target.base_url).is_cloud:
        return [("managed_evaluators", True,
                 f"UNSUPPORTED self-hosted stable evaluator API: {error}; completeness not verified")]
    names = (*CODE_EVALUATORS, *JUDGE_TEMPLATES)
    definitions: dict[str, dict] = {}
    missing = [name for name in names if not any(e.get("name") == name for e in rows)]
    errors = [error] if error or not available else []
    if missing:
        errors.append("missing definitions: " + ", ".join(missing))
    for name in names:
        matches = [e for e in rows if e.get("name") == name]
        if len(matches) > 1:
            errors.append(f"{name}: ambiguous definitions; expected one stable evaluator ID")
        if len(matches) != 1:
            continue
        evaluator, error = get_evaluator(cfg, matches[0]["id"])
        if evaluator is None:
            errors.append(f"{name}: {error}")
            continue
        definitions[name] = evaluator
        expected: dict = {"id": matches[0]["id"], "name": name}
        if name in CODE_EVALUATORS:
            expected.update(type="code", sourceCodeLanguage="PYTHON",
                            sourceCode=CODE_EVALUATORS[name].strip() + "\n")
        else:
            template = JUDGE_TEMPLATES[name]
            expected.update(type="llm_as_judge",
                            prompt=[{"role": "user", "content": template["prompt"]}])
            mapping = [{k: v for k, v in m.items() if v is not None}
                       for m in evaluator.get("variableMapping") or []]
            if mapping != [{"variable": v, "source": v} for v in ("input", "output")]:
                errors.append(f"{name}: variableMapping expected full observation input and output")
            output = evaluator.get("outputDefinition") or {}
            for key, value in {"dataType": "NUMERIC", "minValue": 0, "maxValue": 1,
                               "scoreReasoningInstructions": template["reasoning"],
                               "scoreValueInstructions": template["score"]}.items():
                if output.get(key) != value:
                    errors.append(f"{name}: outputDefinition.{key} expected {value!r}")
        for key, value in expected.items():
            if evaluator.get(key) != value:
                detail = "kit's categorical pass/fail Python definition" if key == "sourceCode" else repr(value)
                errors.append(f"{name}: {key} expected {detail}")
    checks = [("managed_evaluators", not errors,
               "; ".join(errors) + ". " + RECOVERY if errors else
               "5/5 managed definitions read back: three categorical pass/fail code checks, two 0–1 judges")]
    rules, available, error = list_rules(cfg.target.base_url)
    rule_errors = [error or "stable rule API unavailable"] if error or not available else []
    dataset_ids, error = certification_dataset_ids(cfg)
    if error:
        rule_errors.append(error)
    verified = []
    for name in names:
        for target in (("experiment",) if name in CODE_EVALUATORS else ("experiment", "observation")):
            expected_name = rule_name(name, target)
            matches = [r for r in rules if r.get("name") == expected_name]
            if len(matches) != 1:
                rule_errors.append(f"{expected_name}: expected exactly one rule, found {len(matches)}")
                continue
            rule, error = get_rule(cfg, matches[0]["id"])
            if rule is None:
                rule_errors.append(f"{expected_name}: {error}")
                continue
            expected = {"name": expected_name, "id": matches[0]["id"],
                        "filter": rule_filter(target, dataset_ids),
                        "sampling": 1.0 if target == "experiment" else
                        max(cfg.certification.trace_judge_sampling, 0.01)}
            for key, value in expected.items():
                if rule.get(key) != value:
                    rule_errors.append(f"{expected_name}: {key} expected {value!r}, got {rule.get(key)!r}")
            evaluator = definitions.get(name)
            assignments = rule.get("evaluatorAssignments") or []
            if evaluator is None or len(assignments) != 1 or assignments[0].get("evaluatorId") != evaluator["id"]:
                rule_errors.append(f"{expected_name}: evaluatorAssignments expected only the stable ID for {name}")
            elif assignments[0].get("variableMapping") is not None:
                rule_errors.append(f"{expected_name}: evaluatorAssignments.variableMapping expected inherited defaults (null)")
            if initial:
                enabled = target == "experiment" and bool(evaluator and evaluator.get("status") == "active")
                if rule.get("enabled") is not enabled:
                    rule_errors.append(f"{expected_name}: initial enabled expected {enabled}")
            elif not isinstance(rule.get("enabled"), bool):
                rule_errors.append(f"{expected_name}: enabled expected a boolean operator activation choice")
            verified.append(f"{expected_name} ({rule['id']}, enabled={rule.get('enabled')})")
    checks.append(("managed_rules", not rule_errors,
                   "; ".join(rule_errors) + ". " + RECOVERY if rule_errors else
                   "7/7 kit rules read back with intended assignments, filters and sampling: " + "; ".join(verified)))
    paused = [f"{name}: {judge_status(e)} — not runnable" for name, e in definitions.items()
              if e.get("status") == "paused"]
    checks.append(("managed_runnability", True,
                   "; ".join(paused) if paused else "Saved evaluators report active; rule activation is operator-controlled."))
    return checks
