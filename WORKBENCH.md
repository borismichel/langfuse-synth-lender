# WORKBENCH.md — Meridian Validation Workbench

The branded governance layer on top of Langfuse: MRM / compliance / engineering design
certification and validation experiments themselves — pulling **prompts, datasets, and
evaluators from Langfuse**, composing an auditable **experiment spec**, injecting
**deterministic evaluators as code** (scaffolded to the Langfuse SDK contract),
triggering runs, and filtering the results — with every run fed back into Langfuse as
the system of record.

Served by the playground: `synth playground` → **http://127.0.0.1:8000/workbench**.
No extra setup; it reuses the kit's `.env`. With Langfuse unreachable it falls back to
an **offline catalog** derived from the deterministic plan (designing works; running
needs the instance).

## The flow (what to demo)

1. **Overview** — the catalog Langfuse provides: suites with slice counts, the
   evaluator code registry (with SHA fingerprints), recent runs, requirement coverage.
   Switch the acting role here (Builder / Validator / Approver).
2. **Designer** — compose a spec: the **release** (model = the lever; prompt pinned by
   version from prompt management), **targets** (suites, optionally narrowed to
   slices), **evaluators** (deterministic checks by name; managed judges created and
   scoped via API where the server supports it), **gates** (thresholds + the
   production_flagged override), optional **dataset freeze**. Specs are versioned and
   **SHA-256-hashed** — acceptance criteria as governed data.
3. **Inject an evaluator** — the new-evaluator form is prefilled with the SDK-contract
   scaffold. Acceptance pipeline: compile → AST signature check → smoke-run against a
   sample suite item. Accepted code lands in `workbench_evaluators/` and its SHA rides
   in every run's metadata.
4. **Run** — background execution through the same single agent function the seeder
   and `synth certify` use; deterministic checks ride as code evaluators; managed
   judges fire on the new Dataset Runs automatically. Progress is live.
5. **Results** — filterable by suite / slice / verdict / failing evaluator; red rows
   carry the exact failing arithmetic; every row deep-links to its Langfuse trace.
   Gate banner per suite. **Compare** any two runs per item (improved / regressed).
6. **Coverage** — the requirements register (`workbench_requirements.yaml`) ×
   suite items × evaluators × latest outcome. Two requirements are deliberately
   uncovered (adversarial robustness; the fairness-of-decisioning boundary) — the
   matrix finds gaps, it doesn't rubber-stamp.
7. **Promote** — completed `ground-truth-intake` reviews that aren't yet suite items,
   prefilled with the reviewer's corrected ground truth → one click into the suite
   with `sourceTraceId` provenance, slice, and requirement links.
8. **Sign-off + evidence** — only an **Approver** signs off; the approval is recorded
   in the run, as a `reviewer_verdict` score, and as a COMPLETED item in the
   `certification-signoff` queue. The **evidence pack** (spec hash, code SHAs, slice
   tables, gate verdicts, failures with reasons, Langfuse run links, sign-off) is
   downloadable only after sign-off. `synth memo` includes a Workbench section.

## What was missing for MRM / compliance / engineering — and where it went

| Gap | Why it matters | Disposition |
|---|---|---|
| Requirement traceability + coverage | Auditor's first question: *which requirements does the suite test, and what's untested?* | **Built** — register + coverage matrix |
| Ground-truth promotion workflow | Annotation queues capture judgment; datasets need it as expected output — the seam was manual | **Built** — promote wizard |
| Suite freezing / versioning | Certify against a frozen suite, or the evidence is unstable | **Built** — datasetVersion pin per run |
| Acceptance criteria as governed data | Thresholds need an author, a rationale, and a history | **Built** — hashed, versioned specs with notes + role |
| Independence / 4-eyes | The builder must not approve their own gate | **Built** (demo-grade) — role switcher + Approver-only sign-off |
| Evidence pack | The validation file regulators actually read | **Built** — per-run export, sign-off-gated |
| Evaluator code governance | A verdict must be traceable to the exact check logic | **Built** — committed files, SHA fingerprints in run metadata |
| Evaluator management | Evaluators configured by hand drift from the record | **Built** — 3 code evaluators (`type=code`, no LLM connection) + 2 LLM judges (`groundedness`, `citation_coverage`) created via the (unstable) evaluator API and scoped to **experiments** (rule `target=experiment`, sampling 1.0). The judges are *also* scoped to **live traces** (`target=observation`, low sampling, default PAUSED) — same evaluator, certification + monitoring on one surface. Code evaluators stay experiment-only (need `expected_output`). UI fallback |
| Statistical sufficiency per slice | 4 items in a slice ≠ evidence; needs min-n / confidence intervals on pass rates | **Roadmap** |
| Production-coverage drift | Does the suite mix still match live traffic? (question kinds vs. slices) | **Roadmap** |
| Scheduled recertification / CI hooks | Periodic + event-triggered re-runs (`synth certify --gate` covers CI today) | **Roadmap** |
| Model inventory / registry view | Which release is certified, since when, by whom — across systems | **Roadmap** |
| Failure triage loop | A failed run item should auto-open an annotation-queue review | **Roadmap** |
| Real authn/authz · sandboxed evaluator execution | The demo runs injected code in-process with cookie roles — fine on stage, not in production | **Roadmap** (say it before they ask) |

## Honesty notes (for the presenter)

- The evaluator/judge **unstable API** is marked unstable by Langfuse; the workbench
  surfaces server validation errors verbatim and falls back to UI instructions. Two
  gotchas it handles: code evaluators take **no variable mapping** (the server fills it
  from `ctx`), and a judge's `modelConfig.provider` must match the LLM connection's
  **exact casing** (`"Anthropic"`, not `"anthropic"`).
- Evaluation **rules never backfill** — they score only live ingestion after creation.
  Judges are therefore created *after* the experiment runs are seeded, so the seeded
  data shows deterministic judge scores and **no live judge runs are triggered**; the
  rule only arms future runs.
- Injected code executes **in-process** after compile/AST/smoke acceptance — a demo
  pattern. Production needs sandboxing and code review.
- Roles are a **cookie switcher**, not auth. The point being demonstrated is the
  *recorded* separation of duties, not access control.
- Langfuse stays the system of record: every workbench run is a real Dataset Run with
  scores; the local `.workbench/` store is a structured index for filtering/compare.
