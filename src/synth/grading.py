"""Deterministic certification grading (spec §6) — shared by the seeded baseline /
failed-cert runs, the live ``synth certify`` runner, and the CI gate, so a verdict can
never mean different things on different surfaces.

The headline certification metrics are **reproducible code, not an LLM opinion**:
- ``figure_accuracy``    — expected figures exact (EUR ints), ratios within ±0.02,
                           and the answer_type matches.
- ``citation_accuracy``  — cited section ids equal the expected set.
- ``abstention_correct`` — answer_type (factual / declined / abstained) matches.

The managed LLM judges (``groundedness``, ``citation_coverage``) layer on top via the
unstable evaluator API; they grade the prose, not the verdict.
"""
from __future__ import annotations

from .models import CopilotAnswer

RATIO_TOLERANCE = 0.02

# Which deterministic check gates which certification-suite scenario (spec v2 §5).
SCENARIO_GATE_CHECK = {
    "summary": "grounded_ok",              # citation_accuracy AND answer_type
    "numeric_lookup": "figure_accuracy",
    "trend": "figure_accuracy",
    "covenant": "figure_accuracy",
    "out_of_scope": "abstention_correct",
}
# Internal check key -> the score NAME it is emitted under (one vocabulary across
# production traces, experiment runs, and workbench evaluators — co-filterable).
SCORE_NAME_FOR_CHECK = {
    "figure_accuracy": "numeric_accuracy",
    "citation_accuracy": "citation_format",
    "abstention_correct": "escalation_correctness",
}
# Back-compat alias (older callers keyed by the v1 suite names).
SUITE_GATE_CHECK = SCENARIO_GATE_CHECK


def _coerce(ans: "CopilotAnswer | dict") -> CopilotAnswer:
    if isinstance(ans, CopilotAnswer):
        return ans
    return CopilotAnswer.model_validate(ans)


def grade_figures(expected: CopilotAnswer, got: CopilotAnswer) -> tuple[bool, str]:
    if got.answer_type != expected.answer_type:
        return False, f"answer_type {got.answer_type!r} ≠ expected {expected.answer_type!r}"
    for key, want in expected.figures.items():
        have = got.figures.get(key)
        if have is None:
            return False, f"missing figure {key} (expected {want:,})"
        if have != want:
            return False, f"{key} = {have:,} ≠ expected {want:,}"
    for key, want in expected.ratios.items():
        have = got.ratios.get(key)
        if have is None:
            return False, f"missing ratio {key} (expected {want})"
        if abs(have - want) > RATIO_TOLERANCE:
            return False, f"{key} = {have} outside ±{RATIO_TOLERANCE} of {want}"
    return True, "figures and ratios match"


def grade_citations(expected: CopilotAnswer, got: CopilotAnswer) -> tuple[bool, str]:
    want, have = set(expected.citations), set(got.citations)
    if want == have:
        return True, "citations match"
    missing, extra = want - have, have - want
    parts = []
    if missing:
        parts.append(f"missing {sorted(missing)}")
    if extra:
        parts.append(f"uncited-source {sorted(extra)}")
    return False, "; ".join(parts)


def grade_abstention(expected: CopilotAnswer, got: CopilotAnswer) -> tuple[bool, str]:
    if got.answer_type == expected.answer_type:
        return True, f"correctly {expected.answer_type}"
    return False, f"answer_type {got.answer_type!r} ≠ expected {expected.answer_type!r}"


def grade(expected: "CopilotAnswer | dict", got: "CopilotAnswer | dict") -> dict[str, tuple[bool, str]]:
    """All deterministic checks for one item. Keys are score names."""
    exp, ans = _coerce(expected), _coerce(got)
    fig = grade_figures(exp, ans)
    cit = grade_citations(exp, ans)
    abst = grade_abstention(exp, ans)
    return {
        "figure_accuracy": fig,
        "citation_accuracy": cit,
        "abstention_correct": abst,
        "grounded_ok": (cit[0] and abst[0],
                        "grounded" if (cit[0] and abst[0]) else "; ".join(
                            d for ok, d in (cit, abst) if not ok)),
    }


def item_passes(scenario: str, expected, got) -> tuple[bool, str]:
    """The per-item verdict the scenario's gate counts (spec v2 §5)."""
    checks = grade(expected, got)
    check = SCENARIO_GATE_CHECK.get(scenario, "figure_accuracy")
    return checks[check]
