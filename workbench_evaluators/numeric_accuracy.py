"""Deterministic certification assertion: extracted figures exact (EUR ints), ratios
within tolerance, answer behaviour correct. Wraps synth.grading so a workbench run
and `synth certify` can never disagree."""
from langfuse import Evaluation

from synth.grading import grade

NAME = "numeric_accuracy"
REQUIREMENT_IDS = ["MRM-ACC-1", "MRM-ACC-2", "MRM-ACC-3", "MRM-ACC-4"]


def evaluator(*, input, output, expected_output, metadata=None, **kwargs):
    ok, detail = grade(expected_output, output)["figure_accuracy"]
    return Evaluation(name=NAME, value="pass" if ok else "fail", comment=detail)
