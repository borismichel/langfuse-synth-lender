"""Workbench evaluator scaffold — the designer prefills new-evaluator code from this.

Contract (Langfuse v4 run_experiment evaluator):
- keyword-only signature: input, output, expected_output, metadata, **kwargs
- return a langfuse Evaluation (value "pass"/"fail" for categorical, float for numeric)
"""
from langfuse import Evaluation

NAME = "my_check"
REQUIREMENT_IDS = []            # e.g. ["MRM-ACC-1"] — links into the requirements register


def evaluator(*, input, output, expected_output, metadata=None, **kwargs):
    # `output` is the task's returned dict; `expected_output` the dataset item's.
    ok = bool(output)
    return Evaluation(name=NAME, value="pass" if ok else "fail",
                      comment="describe exactly why this passed or failed")
