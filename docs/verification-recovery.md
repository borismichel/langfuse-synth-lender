# Verification progress and safe activation (v0.6.4)

`synth verify --config <same-config> --initial-evaluators` reads configuration and
seeded evidence; it never enables a rule, configures a model, or executes an evaluation.

Verification prints each completed check immediately, announces each linked
filing-evidence read, and emits a heartbeat at most every 15 seconds while reads
or Retry-After backoff are pending. Output is flushed to the container log. The
completion line reports elapsed seconds; the read summary reports logical trace
reads and experiment-item list reads, separately from HTTP pagination/retries.
Cloud throttling and retry handling remain in the shared read seam. A per-run
cache removes repeated trace/item-list reads; every linked item retains its full
input, question, units, section and sign assertions.

Initial activation checks require enabled code experiment rules and disabled live
rules. An experiment judge may stay disabled even after its model becomes ready.
An enabled judge must report active. Definition completeness, model readiness and
rule activation are reported separately. A disabled judge is a safe operator
choice, not a missing definition. To correct an unsafe activation, explicitly
review the rule in Langfuse; repeat provisioning intentionally preserves saved
activation and cannot make that choice for you.

Missing/broken definitions and rules still fail and identify evaluator-only
provisioning as recovery. Do not reseed, replay the Spool, or enable evaluation
just to make a verifier green. Depot's supported `retry-verification` action can
run a saved verification command in this release without provisioning again;
its runbook records the image selection and original attempt.
