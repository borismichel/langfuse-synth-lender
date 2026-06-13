# Langfuse Demo Data Synthesiser — MRM Lending-Copilot Certification (Spec v2)

Seed a Langfuse project (Cloud free tier or self-hosted) with realistic telemetry and
**pre-built certification objects** for the MRM lending-copilot scenario: a commercial
lender's **analyst copilot over financial filings**, certified for any change (model,
prompt, parameters) through an automated pipeline — production traces → human-validated
ground truth → comparative experiment runs → one evidence trail.

> production runs → `certification-suite` curated from annotated traces → three seeded
> experiment runs (baseline passes · candidate A passes better/cheaper · **candidate B
> fails the numeric-accuracy gate**) → all five score-method types on one surface.

The spec of record is [`langfuse-demo-synth-spec-v2.md`](langfuse-demo-synth-spec-v2.md)
(v1 retained as design history). The companion `ev` kit tells the prompt-loop story;
this kit tells the certification story.

---

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env                     # LANGFUSE_BASE_URL + keys (Cloud or self-hosted)

# Cloud free tier? Check current event caps (https://langfuse.com/pricing), then:
synth probe  --config config/cloud-demo.yaml   # ONE backdated trace; fails loudly if
                                               # the host drops historical timestamps
synth plan   --config config/cloud-demo.yaml   # prints the exact event count (adjust
                                               # generation.volume.scale to ≤80% of cap)

# Seed (NO model calls — fully deterministic):
synth seed   --config config/demo.yaml         # full preset, self-hosted scale
synth seed   --config config/cloud-demo.yaml   # cloud preset (14d window, scale 0.5)
synth verify --config config/demo.yaml         # assert every demo anchor via the API
```

`synth seed` writes **`DEMO_SCRIPT.md`** (the runbook: five checklist rows, each two
clicks deep) and **`DEMO_MAP.md`** (checklist row → exact UI path → which golden
trace/object to open) — filled with this run's real ids.

## What gets created (full preset)

- **~10–12k traces in ~1,150 sessions** over 30 days — sessions/day driven (~50
  weekdays, ~5 weekend), log-normal turns (median ~7, p95 ~22, tail to 30), Berlin
  business hours with lunch dip and Friday-afternoon decline, 48 named analysts
  (Zipf-like; ~12% of traces from German-named analysts whose sessions are FULLY
  German — language never mixes within a user or chat), 1–3% tool errors **with retry spans**, a handful of
  failed generations, a nightly covenant-monitor batch line (ambience).
- **Per-turn structure:** `copilot-turn` root → `filings_search` → `document_fetch` →
  `table_extract` (per filing on trend questions) → optional `covenant_db_lookup` /
  `internal_ratings_lookup` → ONE generation linked to the **exact prompt version live
  at its timestamp** → `escalated_to_human` event where applicable. Metadata:
  release/git_sha, prompt_version, `filing-type:` / `desk:` / `language:` tags.
- **Prompt `analyst-copilot`:** 8 versions with commit-message history; `production` =
  v7, `staging` = v8; a mid-window v5→v6 transition and a v7 "fix" — with an optional,
  subtle groundedness dip in the v6 era (flag: `ambience.quality_dip`).
- **`certification-suite`** (72 items, one hosted dataset) tagged by scenario —
  summary 14 · numeric_lookup 22 · trend 10 · covenant 14 · out_of_scope 12 — curated
  items carry `sourceTraceId`; per-scenario gates in config.
- **Three seeded experiment runs** on that suite (backdated, procedurally scored):
  `baseline-claude-sonnet-4-5` passes; `cert-claude-sonnet-4-6` passes with better
  groundedness (~0.94 vs ~0.91) at lower cost; `cert-claude-haiku-4-5` **fails
  numeric_lookup (81.8% vs ≥95%)** — every red cell's comment states the exact figure
  that diverged from the printed table.
- **`certification-review` queue** — 16 completed (human `reviewer_verdict` + judge
  scores side by side, ~88% agreement with visible disagreements) and 14 pending,
  including a fresh flagged thumbs-down awaiting promotion (the live beat).
- **Five golden traces** (tag `golden`): covenant risk summary · numeric hallucination
  caught (deterministic + judge + human all flag it) · correct escalation · DSCR trend
  (per-filing table extraction) · citation gap (fluent answer, citation_coverage 0.32).
- **All five score-method types** on one surface, one vocabulary across traces and
  runs: `numeric_accuracy` / `citation_format` / `escalation_correctness`
  (deterministic), `groundedness` / `citation_coverage` (judge), `analyst_feedback`
  (user), `reviewer_verdict` (human).

## Commands

```
synth plan | seed | import-spool | verify        # the deterministic seed pipeline
synth probe                                      # backdated-ingestion check (run FIRST on Cloud)
synth certify --model <id> [--gate|--offline]    # live certification run (real model calls)
synth enrich                                     # optional ~50-call archetype layer (prose variety)
synth memo | script                              # CERT_MEMO.md · DEMO_SCRIPT.md + DEMO_MAP.md
synth submit | playground                        # live copilot + /dossier + /workbench
```

The **Validation Workbench** (`synth playground` → `/workbench`, see
[`WORKBENCH.md`](WORKBENCH.md)) is the branded governance layer on the same APIs:
spec designer, evaluator code injection, runs/results/compare, requirement coverage,
promote-from-queue, sign-off + evidence packs. [`CHECKLIST.md`](CHECKLIST.md) maps
requirements → features → demo beats.

## Architecture notes

Backdated **batch ingestion** (`/api/public/ingestion`, ingestion-version-4 header) —
the OTel SDK can't backfill; two-phase recoverable seeding (NDJSON spool → chunked
import; resume with `synth import-spool`); deterministic BLAKE2b ids (re-seeding
upserts); **seeded dataset runs** via `dataset-run-items` + backdated `createdAt`;
annotation queue via the public queues API. Managed judges (groundedness,
citation_coverage) are created once — UI, or the workbench's unstable-API path; their
prompts and the claimed judge model (`certification.judge_model`) are in the runbook.

**Experiment runs (cloud-vs-v3):** the baseline/A/B runs are created via the SDK `run_experiment` path (deterministic, no model calls), NOT the legacy REST `dataset-run-items` endpoint — on Langfuse ≥ v3.185 (incl. Cloud) the Experiments tab only surfaces `run_experiment`-created runs (REST runs exist via API but render an empty comparison grid; older self-hosted v3.179 showed them). On Cloud the seed also populates the managed LLM judges (groundedness, citation_coverage) via the unstable evaluator API when an LLM connection is configured.

Known cosmetics (say it before they ask): prompt-version *creation* timestamps can't
be backdated (era linkage on generations carries the story); seeded scores show source
`API`; queue items show seed-time creation dates.

## Guardrails & teardown

The seeder refuses to run unless the project name contains `target.project_hint`.
Re-seeding upserts traces/scores; dataset-run-items would duplicate — **teardown is
project-level** (fresh project + re-seed; deterministic ids regenerate identically).

## Tests

```bash
pip install pytest && pytest -q   # 51 tests: determinism, truth table, run verdicts,
                                  # session shape, v2 trace structure, artifacts, workbench
```
