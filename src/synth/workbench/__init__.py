"""Meridian — Validation Workbench (the branded governance layer on Langfuse).

A customer-facing tool for MRM / compliance / engineering to *design* certification
and validation experiments: pull prompts, datasets, and evaluators from Langfuse;
compose an auditable experiment spec (release × suites × evaluators × gates); add
deterministic evaluators as code scaffolded to the Langfuse SDK contract; trigger the
run; and filter results — with everything fed back into Langfuse as the system of
record. Served by `synth playground` under ``/workbench``.
"""
