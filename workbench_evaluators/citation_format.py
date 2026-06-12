"""Deterministic certification assertion: cited section ids match the expected set."""
from langfuse import Evaluation

from synth.grading import grade

NAME = "citation_format"
REQUIREMENT_IDS = ["MRM-GRD-1"]


def evaluator(*, input, output, expected_output, metadata=None, **kwargs):
    ok, detail = grade(expected_output, output)["citation_accuracy"]
    return Evaluation(name=NAME, value="pass" if ok else "fail", comment=detail)
