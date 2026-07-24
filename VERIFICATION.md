# VERIFICATION.md — pre-flight checks for the certification demo

Run these after `synth seed` and before going on stage. `synth verify` automates the
API-checkable parts; the UI checks take ~3 minutes.

## 1 · Automated

```bash
synth verify --config config/demo.yaml
```

Asserts, against the live project:
- **Suites** — all three datasets exist with the configured item counts; curated items
  carry `sourceTraceId` links.
- **Flagged reserved** — both flagged traces exist, are NOT in any suite, and carry an
  `analyst_feedback = down` score *with the analyst's comment*.
- **Baseline red cells** — `figure_accuracy = fail` scores (with reasons) exist on the
  seeded runs.
- **Seeded runs** — `baseline-<incumbent>` and `cert-2026-Q1-<failed>` exist as
  dataset runs.
- **Prompt pin** — the flagged trace's `answer` generation links to the pinned prompt
  version, and its input is the actual LLM turn (catches the re-seed merge trap:
  ingestion keeps first-seen values — use a fresh project after content changes).
- **Queues** — both annotation queues exist with their seeded completed history.

```bash
synth certify --config config/demo.yaml --offline   # no-model suite self-consistency
pytest -q                                           # determinism + truth table + invariants
```

## 2 · In the UI

| Check | Where | Expect |
|---|---|---|
| Dashboard alive | Home/dashboards | 30-day volume curve, cost by model (incumbent dominates spend, Haiku dominates count) |
| Case sessions | Sessions | multi-turn sessions; case-review IDs; per-session `case_review_rating` |
| Flagged trace A | `DEMO_SCRIPT.md` cast sheet → trace link | agent graph; `(2,431)` in `extract_table` output; thumbs-down with comment; prompt-linked `answer` |
| Filter beat | Traces, filter score `analyst_feedback = down` | exactly the flagged traces from the cast sheet (last week) |
| Suite slices | Datasets → cert-figure-extraction → items | `metadata.slice` populated; curated items link to source traces |
| Baseline run | cert-figure-extraction → Runs | `baseline-<incumbent>` dated last week; red `figure_accuracy` cells with reasons |
| Parachute | Runs | `cert-2026-Q1-<failed>` markedly red across suites |
| Queues | Annotation queues | `ground-truth-intake` + `certification-signoff` with completed history |
| Prompt pin | Prompts → filing_copilot | one version, label `production` |

## 3 · Live-path rehearsal (needs ANTHROPIC_API_KEY)

```bash
synth certify --config config/demo.yaml --model claude-sonnet-4-6 --gate   # expect: CERTIFIED
synth memo --config config/demo.yaml                                       # dossier renders with the live run
synth submit --prefab paren --model claude-sonnet-4-6                      # live trace, figures match ground truth
```

Rehearse `certify` at least once against the venue network. If the venue has no model
access, the break-glass path in `DEMO_SCRIPT.md` (seeded record + `--offline`) carries
the story.

## Known cosmetics (say it before they ask)

- Seeded scores show source `API` rather than `ANNOTATION`/`EVAL` — batch-ingested
  history; the live run's evaluator and judge scores carry their real sources.
- Queue *items* show their seed-time creation date (the API has no backdate for queue
  items); the underlying traces and reviewer scores are correctly backdated.
- Agent-graph observation types (AGENT/TOOL/RETRIEVER) render as spans with the type
  in metadata — native typed observations are OTel-endpoint-only (see ev kit notes).

## Offline determinism golden gate (author-time, pre-ingestion)

The checks above verify data *after* it lands in Langfuse. The golden gate verifies the
generator itself, **offline, before any ingestion** — the migration oracle for Spec A
(`langfuse-synth-core`, Step 0 · #30).

`tests/test_determinism.py::test_full_payload_golden_is_byte_identical` materializes the
**entire pre-ingestion Spool** (the NDJSON event stream — traces + observations + scores)
in a subprocess under `PYTHONHASHSEED=0` and a **deny-LLM egress block**, and asserts it is
byte-identical to the blessed snapshot at `tests/golden/lender_spool.ndjson`. So it proves,
in one shot, that generation is **deterministic** and **model-free at seed runtime**. Any
refactor that silently perturbs the pool, or that plants an LLM call in generation code,
fails here — loudly, before a single event is ingested.

```bash
pip install -e ".[dev]"        # pulls the git-pinned langfuse-synth-core[authoring] gate
pytest tests/test_determinism.py
```

**The oracle is pinned at `target_traces=150`.** Lender has no absolute count knob — total
traces are session-DERIVED — so `golden_seed.py` maps the canonical `target_traces` to the
native `volume.scale` (`scale = target_traces / 10111`, the measured yield at scale 1.0).
Coverage saturates at this pin: all five golden keys, both languages, every filing-type and
desk, the curated suite, flagged-pending, and the nightly batch are present, because the
certification suite / experiment runs / review queue are config-sized and stay **unscaled**.

> **Floor note.** Per-day session counts are rounded — `round(randint(lo,hi) * scale)` —
> so below scale ≈0.025 weekday counts round up to a ~1/weekday plateau instead of scaling
> to zero, and the realized trace count flattens (target_traces=150 → ~252 traces / ~4.1 MB).
> `target_traces` is an advisory volume dial for Lender, never an exact count — consistent
> with "total traces are DERIVED, not forced". The oracle cannot be made smaller than this
> plateau via the knob; that is inherent, and it does not weaken the byte-for-byte guarantee.

**Re-blessing** (only for a *deliberate* pool change — never hand-edit the snapshot):

```bash
synth-authoring freeze golden_seed:seed \
    --golden tests/golden/lender_spool.ndjson --target-traces 150 --search-path tests
```
