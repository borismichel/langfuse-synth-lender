"""Deterministic certification assertion: factual / declined / abstained / escalated
behaviour exactly as the item's ground truth requires."""
from langfuse import Evaluation

from synth.grading import grade

NAME = "escalation_correctness"
REQUIREMENT_IDS = ["MRM-GRD-2", "MRM-CON-1", "MRM-CON-2", "MRM-CON-3", "MRM-CON-4", "MRM-CON-5"]


def evaluator(*, input, output, expected_output, metadata=None, **kwargs):
    ok, detail = grade(expected_output, output)["abstention_correct"]
    return Evaluation(name=NAME, value="pass" if ok else "fail", comment=detail)
