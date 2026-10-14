# Destination-first setup — Depot #328

Source candidate v0.6.5, core v4.3.0, SDK4.17.0. Source work is ready for independent review; immutable kit release and signed image publication remain with the integration coordinator. No live Langfuse target was seeded and no provider call was made.

## Contracts and behaviour

Setup v1 declares inclusive UTC history through generation.window_days (14/30). Cloud defaults remain target5000/history14; self-hosted target5000/history30. The target is advisory: the existing deterministic sessions/day → turns algorithm remains in charge of realized volume. Presentation keeps effective operator defaults. Quick rehearsal requests1000, within existing public bounds, and retains all72 certification items, three72-item runs, five golden traces, the reserved unpromoted case and16completed/14pending review items. Custom uses the same bounded schema; no hidden knobs are exposed.

The historical window starts one day later and includes the entire selected as-of day. The existing hour/week weighting and prompt rollout offsets remain intact. Last-second observation graphs are moved earlier by their overflow plus one microsecond, with exact durations, IDs and parent relationships retained. Live generation omits this historical bound. SCHEDULE.json records actual observation start/end extrema and the requested schedule; the same values persist in RunState.summary. It is generated only alongside persisted seed artifacts; plan and non-persisting tests do not emit a receipt.

## Offline evidence

Tests at existing generation, Spool, SDK transport and verifier boundaries pass (256 tests). The Wednesday14October → Friday16October2026 example is literal, with emitted latest-day activity. Month/year/leap boundaries cover Jan1, Feb29 and Mar1. Tests inspect child/end times, not merely the stored anchor. A forced last-microsecond trace verifies whole-graph correction without duration changes.

[Measured generation](setup-generation-328.json) uses actual model-free run_seed on both host bases:

| Mode | History | Requested target | Actual traces | Sessions | Actual emitted UTC range |
| --- | --- | --- | --- | --- | --- |
| Quick rehearsal |14|1000|483|50|Oct3 01:10–Oct16 14:05:25.271|
| Presentation |14|5000|2185|251|Oct3 01:10–Oct16 16:36:41.046|
| Quick rehearsal |30|1000|1119|113|Sep17 01:10–Oct16 17:59:28.120|
| Presentation |30|5000|5113|563|Sep17 01:10–Oct16 18:40:07.179|

The CLI plan was executed for both histories. Counts intentionally differ from requested targets. For rehearsal14 and30, test_setup_rehearsal_readback.py supplies the real Spool, actual dataset API writes, real SDK experiment exports/scores and generated queue memberships to the existing HTTP read seam. All14run_verify checks pass. Managed-evaluator configuration uses the unchanged existing HTTP fixture; this proves verifier behaviour and generated evidence, not a live service. test_experiment_evidence additionally executes all216SDK experiment items for Presentation and both rehearsals and retains the exact1080item-score hash and baseline/pass/fail outcomes.

The released core validator passes. CI-equivalent conformance --advisory passes with exactly the two pre-existing Companion migration advisories: health_path `/` and absent target-shape synth.companion factory. Strict synth-authoring check therefore reports local_ready:false despite passed validation/tests. This slice does not claim that migration debt is resolved. Live readback, published image signature and registry consumption remain separate release/integration evidence.

## Intentional golden change

The inclusive calendar correction changes the pinned150-target golden chronologically. Both versions contain252traceIDs,1621spans and531scores, with no added/removed span IDs.1439span start/end timestamps move.448completion-start attributes move. Calendar changes cross existing prompt rollout boundaries:247propagated prompt-version/release/git-sha attributes and78prompt-linked generation versions/inputs change; their78usage/cost attributes follow the existing prompt text/token accounting. These are derived chronology effects, not changed prompts, model prices, sampling policy or certification outcomes. Fresh-process repeatability passed before freezing through the released authoring CLI; the complete golden and scenario suites pass afterwards.

## Product references and preserved boundaries

Linked author skill Langfuse dependency was read at `/Users/bmichel/.agents/skills/author-langfuse-demo-kit/dependencies/langfuse/SKILL.md`. Dependency entrypoint SHA256: `e8908ea1c924374ba7bc5e82b220709d107db6a3450a0c4d72228a91a2a7534a`. Current [Sessions documentation](https://langfuse.com/docs/observability/features/sessions) confirms grouping multiple traces under a session; this change preserves existing session identities and observation transport. No new observation/evaluator design or SDK migration is included.

Portal evidence lives in Depot `design/demo-wizard-324/328/verification.md`. Existing release admission is retained for this registered kit update; normal reviewed immutable release, signed image and registry pin checks remain pending with the coordinator. No production deployment, project deletion, provider spend or new infrastructure is part of this evidence.
