# MRM Lending-Copilot Certification

A **Demo Depot cartridge**: this repo is a complete Demo Package that the depot
deploys **as-is** from its catalog, landing the Run-Triad — the Spool, the
Presenter Runbook, and a live Companion. An operator picks it in the portal, points it at a demo Langfuse project
(Cloud or self-hosted), and gets a fully seeded, presentable environment. Nothing
on this page needs installing to *run* the demo; everything developer-facing lives
at the bottom, under
[Development and running outside the depot](#development-and-running-outside-the-depot).

**The story in one line:**

> production runs → `certification-suite` curated from annotated traces → three seeded experiment runs (baseline passes · candidate A passes better/cheaper · **candidate B fails the numeric-accuracy gate**) → all five score-method types on one surface.

## The business case & story arc
 
**The business.** A commercial lender's credit analysts answer questions over financial filings all day: "summarise the covenant package", "what's the DSCR trend across the last three filings", "does this 10-K breach the leverage ratio". An analyst copilot does the first pass. It searches filings, fetches documents, extracts tables, and synthesises a cited answer. Because that answer feeds credit decisions, it falls under Model Risk Management (MRM). Every change to the model, prompt, or parameters must be *certified* before it ships, and the certification has to leave an audit trail a regulator can read.
 
**The tension.** A cheaper model is on the table. Does it hold the line on the one thing that can't slip: getting the numbers right against the printed tables? That's what this dataset answers on screen.
 
**The arc** (the five-row demo walks it end to end):
 
1. **Production reality.** ~10–12k real traces show the copilot working at scale: planner generation → tool calls → cited synthesis, with the occasional tool error, escalation, and hallucination caught in the wild.
2. **Ground truth.** Analysts and reviewers annotate those traces; the best become a 72-item `certification-suite`, the human-validated yardstick.
3. **The contest.** Three experiment runs race on that suite against the *live production prompt*: `baseline` (Sonnet 4.5) passes; candidate A (Sonnet 4.6) passes better and cheaper; candidate B (Haiku 4.5) is cheapest per token but **fails the numeric-accuracy gate (81.8% vs ≥95%)**. Every red cell names the figure that diverged.
4. **The catch.** Deterministic checks, LLM judges, and human reviewers all converge on the same verdict, so the governance gate stops candidate B before it ships.
5. **One evidence trail.** All five score-method types, the same vocabulary across production traces and certification runs, on one surface a model-risk officer signs off.

**This kit tells the certification story:** a commercial lender's analyst
copilot over financial filings, certified for any change (model, prompt,
parameters) through an automated pipeline — production traces → human-validated
ground truth → comparative experiment runs → one evidence trail.

## What's in the package (the Run-Triad)

Deploying this kit lands everything it takes to present the demo:

- **The Spool** — ~10–12k backdated traces in ~1,150 sessions, the 72-item
  `certification-suite`, three seeded experiment runs, the
  `certification-review` annotation queue, five golden traces, and all five
  score-method types, written into your Langfuse project over OTLP.
  Byte-deterministic and model-free; the full inventory is under
  [What the seeded data contains](#what-the-seeded-data-contains-full-preset).
- **The Presenter Runbook** — `DEMO_SCRIPT.md` (five checklist rows, each two
  clicks deep) plus `DEMO_MAP.md` (checklist row → exact UI path → which golden
  trace/object to open), generated at seed time and filled with *this run's
  real* ids. The portal renders the runbook on the deployment page; it is the
  talk track.
- **The Companion** — the live analyst copilot, its `/dossier` (the rendered
  validation memo), and the alpha `/workbench` governance surface. Started on
  demand from the portal; see [The Companion, played live](#the-companion-played-live).

## Deploying it from the depot

1. Pick **MRM Lending-Copilot Certification** in the portal catalog and click
   **Deploy this demo**. Connect a Langfuse demo project — the kit refuses any
   project whose name doesn't contain `demo`, and the check runs before any job
   starts, so a customer's production project is never at risk.
2. The pipeline pauses with the exact billable-units estimate for your OK
   before anything is written, then runs this kit's own Recipe — materializing
   the deterministic Spool and replaying it into your project — and finishes
   with the kit's own `verify`, proving every demo anchor landed. On hosts
   exposing the evaluator API (Cloud, current self-hosted), seeding also
   populates the project's **Evaluators** page (three code evaluators + two
   LLM judges, scoped to the suite); the judges bind to the project's
   default evaluation model, configured once in Langfuse project settings (definitions can be saved without it).
3. Present from the **Presenter Runbook** on the deployment page and the seeded
   Langfuse project.
4. The Companion is the encore: it is never running by default — start it from
   the deployment page when you want to hand the room the wheel. It needs an
   LLM key (provider chosen at deploy time) for live copilot answers. The
   `/workbench` route carries an **alpha** chip in the portal — preview it
   yourself before showing it live.
5. Teardown is project-level: to run the demo fresh, point a new deployment at
   a fresh Langfuse project and re-seed.

## The Companion, played live

The live analyst copilot lets the audience ask their own filing questions and
watch the answer land in Langfuse as a fresh, fully-instrumented trace —
planner, tool calls, cited synthesis — next to the seeded history, scored by
the same judges. `/dossier` renders the certification evidence as a validation
memo a model-risk officer would sign.

The **Validation Workbench** (`/workbench`) is the branded governance layer on
the same APIs: spec designer, evaluator code
injection, runs/results/compare, requirement coverage, promote-from-queue,
sign-off + evidence packs. *(The workbench is a work-in-progress, alpha-stage
surface — the seeded certification story is the supported path.)*

The workbench's **Temperature** field is recorded in the release tuple and on the
sign-off, but it is not sent to the model: the Anthropic SDK (1.0.0) removed the
sampling parameters and the companion layer no longer forwards them. Certification
runs are reproducible through the pinned prompt and model, not the sampler.

## What the seeded data contains (full preset)

- **~10–12k traces in ~1,150 sessions** over 30 days — sessions/day driven (~50
  weekdays, ~5 weekend), log-normal turns (median ~7, p95 ~22, tail to 30), Berlin
  business hours with lunch dip and Friday-afternoon decline, 48 named analysts
  (Zipf-like; ~12% of traces from German-named analysts whose sessions are FULLY
  German — language never mixes within a user or chat), 1–3% tool errors **with retry spans**, a handful of
  failed generations, a nightly covenant-monitor batch line (ambience).
- **Per-turn structure:** the root `copilot-turn` is a **planner generation** (reads the
  prompt + question and decides which tools to call — an extended-thinking pass with
  `reasoning` tokens; it envelopes the turn like Vercel's `ai.streamText`), parenting →
  `filings_search` → `document_fetch` → `table_extract` (per filing on trend questions) →
  optional `covenant_db_lookup` / `internal_ratings_lookup` → the nested `answer`
  generation (the synthesis — real tokens/cost, linked to the **exact prompt version live
  at its timestamp**) → `escalated_to_human` event where applicable. Real spend lives on
  `answer`, so the trace aggregates the two genuine calls (plan + synthesis) without
  double-counting. Metadata: release/git_sha, prompt_version, `filing-type:` / `desk:` /
  `language:` tags.
- **Prompt `analyst-copilot`:** 8 versions with commit-message history; `production` =
  v7, `staging` = v8; a mid-window v5→v6 transition and a v7 "fix" — with an optional,
  subtle groundedness dip in the v6 era (flag: `ambience.quality_dip`).
- **`certification-suite`** (72 items, one hosted dataset) tagged by scenario —
  summary 14 · numeric_lookup 22 · trend 10 · covenant 14 · out_of_scope 12 — curated
  items carry `sourceTraceId`; per-scenario gates in config.
- **Three seeded experiment runs** on that suite (procedurally scored): each run item
  emits a prompt-linked `answer` generation — so every run **references the production
  `analyst-copilot` prompt** and carries a real token/cost column (same vocabulary as the
  production traces). `baseline-claude-sonnet-4-5` passes; `cert-claude-sonnet-4-6` passes
  with better groundedness (~0.94 vs ~0.91) at **lower cost** (tighter outputs, now a real
  number); `cert-claude-haiku-4-5` is cheapest per token but **fails numeric_lookup
  (81.8% vs ≥95%)** — every red cell's comment states the exact figure that diverged from
  the printed table.
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

## Delivery model: a cartridge, not a standalone app

Per the delivery-model decision (2026-07-29): **a kit is a cartridge that goes
into the depot** — the primary delivery method is as-is through the portal,
which owns deployment, seeding, artifacts, and the Companion's lifecycle. A
standalone-run story exists (everything below runs from a clone), but the
decision on how kits run individually *outside* the depot is explicitly
deferred — this repo references that open question without answering it.

---

## Development and running outside the depot

Everything from here down is for **kit development** — the `synth` CLI, local
seeding, tests, and release plumbing. None of it is needed to deploy or present
the demo through the depot. (Running a kit standalone this way works today, but
it is the kit-dev loop, not a supported delivery method — see the
[delivery model](#delivery-model-a-cartridge-not-a-standalone-app) note above.)

### Quick start

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

### Commands

```
synth plan | seed | import-spool | verify        # the deterministic seed pipeline
synth probe                                      # backdated-ingestion check (run FIRST on Cloud)
synth certify --model <id> [--gate|--offline]    # live certification run (real model calls)
synth evaluators                                 # populate code evaluators + LLM judges, scope to the suite
synth enrich                                     # optional ~50-call archetype layer (prose variety)
synth memo | script                              # CERT_MEMO.md · DEMO_SCRIPT.md + DEMO_MAP.md + DEMO_WALKTHROUGH.html
synth submit | playground                        # live copilot + /dossier + /workbench
```

`synth playground` serves the same app the depot starts as the Companion — the
copilot, `/dossier`, and the alpha `/workbench`.

### Architecture notes

Backdated **raw OTLP** (`/api/public/otel/v1/traces`, `x-langfuse-ingestion-version: 4`)
— the Langfuse SDK stamps wall clock and can't backfill, and a Spool is weeks of
backdated history. Every observation is a span and a trace is its minted root; scores
stay `score-create` envelopes on `/api/public/ingestion`, which is the supported v4 path
for them. Two-phase seeding (NDJSON spool → chunked import) so a wedged upload can't lose
the generated data — but the import is **not resumable**: OTLP appends where the old batch
transport upserted, so a second run over the same spool is refused rather than allowed to
double the volume (core `docs/WRITE_PATHS.md`). Deterministic BLAKE2b ids; **seeded
experiment runs** via the SDK `run_experiment` path + backdated caseload; annotation queue
via the public queues API.

**Evaluators (`synth evaluators`, also seed step 5b).** Provisioning and the
workbench use the supported `/api/public/v2/evaluators` and
`/api/public/v2/evaluation-rules` APIs. All five definitions can be saved without
an LLM connection:

- `numeric_accuracy`, `citation_format`, `escalation_correctness`: categorical
  pass/fail Python checks mirroring `synth.grading`.
- `groundedness`, `citation_coverage`: numeric 0–1 judges with the MRM rubrics.
  New judges use the **project default evaluation model**. Without it, Langfuse
  saves the definitions paused; the command and workbench display the reason.
  Configure the model in Langfuse project settings to make them runnable.
  Provisioning never creates or overwrites an LLM connection. Existing explicit
  evaluator model selections are retained when a definition changes.

**Reruns and ownership.** Lists are read completely using cursor pagination.
A unique matching evaluator can be adopted, including a manually created one.
Changes use its stable ID; unchanged definitions create no new versions. Duplicate
names are reported for operator resolution, with no arbitrary selection. Run one
provisioning command per project at a time: the API has no unique-name or create
idempotency constraint. A failed create is not blindly retried; the next run
inventories the project before resuming.

Certification rules select the configured dataset's v2 ID **and**
`isExperimentItemRootSpan = true`, so code checks receive experiment expected
output. They default to sampling 1.0. Live judge rules select `traceName` and
observation `name` equal to `copilot-turn`, plus `isRootObservation = true`.
They retain `certification.trace_judge_sampling` and start **disabled**. Existing
operator activation, sampling and unrelated assignments are preserved.
When configured sampling is zero, disabled live rules retain the kit's 1% floor;
`--enable-live` still requires an explicitly positive configured sampling rate.
New certification judge rules are disabled when their model is missing. Once a
rule exists, ordinary provisioning never activates it, even after a model appears.

**Readback verification and repair.** `synth verify` independently checks the five
managed definitions and all seven kit rules. It reads each stable evaluator and
rule ID, validates the code/rubrics, categorical pass/fail or numeric 0–1 scales,
assignments, experiment context, dataset/root filters and configured sampling.
Unrelated project rules are outside the completeness check. Intentional operator
sampling changes must also be reflected in the config supplied to verification.
Saved judges paused for a missing model remain complete demo configuration; the
report names their pause reason and explicitly says they are not runnable.

The portal runs `synth verify --initial-evaluators` for new deployments, enforcing
disabled live rules and disabled missing-model judge rules before reporting Ready.
Later manual verification omits that flag to respect operator activation choices.
Missing or misconfigured definitions/rules fail even when all historical scores
exist. Stable API absence is reported explicitly as unsupported on self-hosted
targets; Cloud API, authentication, rate-limit and server failures fail verification.

The **Spool contains observations and scores, not evaluator definitions or rules**.
To repair only managed configuration, use the deployment's original config and
Langfuse project credentials:

```sh
synth evaluators --config config/cloud-demo.yaml
synth verify --config config/cloud-demo.yaml
```

This does not regenerate or import the Spool, rewrite historical scores, configure
model credentials, run evaluations or backfill. Review reported ownership conflicts
and deliberate sampling changes in Langfuse; repair preserves existing activation
and sampling choices. Existing demo projects are not modified by upgrading this kit
or the registry. Never replay the Spool to repair evaluators.

**Legacy rules.** Stable rules use `wb-<metric>-experiments-v2` and
`wb-<metric>-observations-v2`. Recognised kit predecessors (`-experiments`,
`-observations`, `-traces`) are retained until the replacement's configuration and
stable evaluator assignment have been read back and validated. Activation and
sampling carry over from an unambiguous predecessor. Retirement only sets
`enabled=false`; unrelated or shared rules are left alone. Named-child legacy
mappings need operator review. No rules, evaluators or scores are deleted.
The retained configuration supports audit/rollback planning; legacy trace/dataset
rules cannot be re-enabled through the stable API.

`synth evaluators --enable-live` remains the deliberate activation step for new
live rules, after score comparison on new traffic. Ordinary provisioning performs
no trial evaluation, backfill, Spool generation or Spool import. Authentication,
rate limiting, temporary failures and an unavailable stable API produce distinct
messages. An unavailable endpoint calls for checking the URL, proxy and server
version; it is not assumed to mean an older self-hosted deployment.

API contracts checked against the [official Langfuse API reference](https://api.reference.langfuse.com/)
and [OpenAPI source](https://github.com/langfuse/langfuse/blob/main/web/public/generated/api/openapi.yml)
on 2026-09-15. The existing core pin is v4.1.1; contract tests ran with Python SDK
4.15.3. This repair does not change seeded metric values or dataset history.

Evaluation rules are **live-ingestion only — they never backfill**, so scoping a rule
(experiment or observation) fires **zero** evaluations on the already-seeded, backdated
data; it only arms *future* runs/traffic (e.g. a live `synth certify` or a playground
turn). The seeded `groundedness`/`citation_coverage` **scores** on the historical
traces and runs are deterministic (same score vocabulary), so the judges show up as
governed objects with matching history and no live judge runs. **Ordering invariant:**
judges + rules are created *after* the experiment runs are seeded and flushed, so a rule
can never judge the seeded data.

**Experiment runs (cloud-vs-v3):** the baseline/A/B runs are created via the SDK `run_experiment` path (deterministic, no model calls), NOT the legacy REST `dataset-run-items` endpoint — on Langfuse ≥ v3.185 (incl. Cloud) the Experiments tab only surfaces `run_experiment`-created runs (REST runs exist via API but render an empty comparison grid; older self-hosted v3.179 showed them).

**Two score levels, deliberately distinct.**
- **Per-item** scores: `run_experiment`'s `evaluators=` attach the five names
  (`numeric_accuracy`, `citation_format`, `escalation_correctness`, `groundedness`,
  `citation_coverage`) to each run item. These are the comparison grid's per-item columns
  — and the code-evaluator/judge rules fill them when triggered (rules don't backfill the
  seeded runs, so kick them off from the run view; a freshly-triggered run fills
  automatically).
- **Per-run** (Experiment-Level) rollups: `run_experiment`'s `run_evaluators=` attach
  aggregates to the full dataset run, shown in the **Experiment-Level Scores** column.
  Named with `mean_` / `rate_` prefixes — `mean_groundedness`, `mean_citation_coverage`,
  `rate_numeric_accuracy`, `rate_citation_format`, `rate_escalation_correctness`,
  `verdict` — so they read clearly as rollups, truncate unambiguously, and **never clash**
  with the per-item score names. Computed from `item_results`, so they can't disagree with
  the cells.

(Aside: on the current "Faster Langfuse experience (preview)" the per-item *aggregate
column picker* can surface only a subset of the `run_experiment` `evaluators=` scores
until the rules are triggered — the run-level column always shows the deltas.)

Known cosmetics (say it before they ask): prompt-version *creation* timestamps can't
be backdated (era linkage on generations carries the story); seeded scores show source
`API`; queue items show seed-time creation dates.

### Guardrails & teardown

The seeder refuses to run unless the project name contains `target.project_hint`.
**Re-seeding is not a reset**: OTLP appends rather than upserting, so a second seed over the
same project tells the whole story twice, and `synth import-spool` refuses a second run over
the same spool. **Teardown is project-level** (fresh project + re-seed; deterministic ids
regenerate identically).

### Tests

```bash
pip install -e '.[dev,playground]' && pytest -q
# determinism + spool golden, truth table, run verdicts, session shape, v2 trace structure,
# artifacts, workbench — plus the two gates the Companion Adapter swap added: the 22
# presenter-visible UI goldens (tests/golden/ui/) and the adapter wiring suite.
# Install BOTH extras: [dev] brings the golden gate's authoring deps and [playground] the
# web-server deps, and without them those gates SKIP rather than run. The suite is
# network-free — it is exactly what CI runs (.github/workflows/ci.yml).
```

### Image releases

Pushing a `vX.Y.Z` tag triggers `.github/workflows/publish.yml`, which builds this
kit's image, pushes it to `ghcr.io/borismichel/langfuse-synth-lender`, and cosign-signs
it keylessly (Spec E · E7, #102). See
[`langfuse-synth-core`'s `docs/CI_SIGNING.md`](https://github.com/borismichel/langfuse-synth-core/blob/main/docs/CI_SIGNING.md)
for the full contract — image naming, cadence, runner, and the signing-identity policy
the portal's verification gate checks against.
