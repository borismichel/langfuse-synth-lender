"""Seed-time subsystems: backdated ingestion, traces, scores, the pinned prompt, the
certification suites, seeded baseline / failed-cert dataset runs, annotation queues."""

from langfuse_synth_core.seed.writepath import OTLP, set_spool_write_path

# The kit's write path, pinned in code rather than left to the environment (core
# `docs/WRITE_PATHS.md`): flipped by portal #210, reverted by reverting this line. Every
# event-building entrypoint — `synth seed`, the golden-gate adapter, the live submission
# and the workbench sign-off (both import through this package) — has the pin in force
# before ANY builder runs.
set_spool_write_path(OTLP)
