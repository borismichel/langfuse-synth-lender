# Filing evidence in certification experiments

Implements [Demo Depot #251](https://github.com/borismichel/langfuse-demo-depot/issues/251).

## Representation

The hosted dataset is unchanged: readable user messages in `input`, the complete
structured question in `metadata.analyst_question`, and the original expected output,
IDs, provenance, scenario/slice and requirement mappings.

`synth.experiments.run_experiment` makes an execution copy of the hosted dataset,
omitting only `analyst_question` from the metadata the SDK propagates. The task writes
that complete question as ordinary metadata on the current `experiment-item-task`
observation. This is the observation ID the SDK links to the experiment item and
selects for judging. Its readable input is unchanged. Task callbacks receive the
original dataset item. Dataset identity and an already-loaded dataset version survive
the copy; selecting a workbench slice does not turn the run into a local experiment.

Seed, CLI certification and workbench runs use this boundary, including runs against
previously created datasets. Certification and the default workbench task reconstruct
the question from dataset metadata, with support for older structured inputs. The
workbench task uses the current `llm` client argument.

Both managed judges map `input` and `output` on the linked observation through their
stable evaluator definitions. Experiment rules select only experiment item roots and
inherit those mappings. Explicit operator mappings using `experiment_item_metadata`
are reported for review because that propagated copy omits the full question. They
are not silently accepted, rewritten or retired: the operator can select ordinary
observation `metadata` or full `input`. This preserves the stable-API reconciliation
and operator-ownership rules landed in #250.

No filing text is shortened, SDK validation is unchanged, and warnings are not muted.
The distinction follows the supported [Langfuse metadata API](https://langfuse.com/docs/observability/features/metadata).

## Reproduction and regression checks

Use Python 3.12 (the runtime image's Python version):

```sh
python -m venv .venv
.venv/bin/pip install -e '.[dev,playground]' 'langfuse==4.14.4'
.venv/bin/pytest -q tests/test_experiment_evidence.py tests/test_verify_split.py tests/test_evaluator_cutover.py
```

SDK **4.14.4** is the released-image version identified in #251's deployment evidence.
The reproduction executes real `DatasetClient.run_experiment` calls. Only HTTP
transport boundaries are replaced: dataset/link/score requests use a fake service,
and the real OTLP exporter delivers protobuf bytes to a local test receiver. No
project credentials, model calls or network service are needed. This verifies the
SDK's exported payload, not storage or evaluator execution in a live Langfuse server.

The unmodified execution path reproduces the filing-copy drop, retains a 200-character
metadata value, and drops a 201-character one. The fixed path checks all **216 items
across three runs**, with no dropped-attribute warnings. Each received linked
observation is compared against its source fixture: readable input, structured
question (including section IDs, unit notes, signs and figures), expected output,
answer, dataset/item IDs, labels and requirement mappings.

All **1,080 per-item scores and grading explanations** are compared to a SHA-256
fingerprint captured before the fix at commit `25e44b3` on SDK 4.14.4:

`26e3dc8e0c300263bf0acd6aae48e6c2f664089b04907cb61b2d7cb40de43fa7`

The narrative verdicts remain `baseline`, `pass`, `fail`. CI runs the same suite
with SDK 4.14.4 and the SDK resolved by a fresh install of the runtime dependency
range. It must continue to reproduce the old failure as well as pass the fixed path.

### Local validation, 2026-09-15

- SDK 4.14.4: full suite **196 passed** (one unrelated Starlette deprecation warning).
- SDK 4.15.3 (fresh runtime resolution): **39 focused checks passed** across experiment
  evidence, readback verification and evaluator mappings.
- Typechecking: the new experiment boundary, certification task, workbench runner
  and default task pass. Three errors in existing seed aggregate, verifier provenance
  and judge-name code also reproduce on the unchanged base; this patch adds none.
- Contract checks: all blocking checks pass. The existing health-path and companion
  app-layout migration findings remain advisory.

## Post-deployment verification

After the kit release is deployed and seeded, run `synth verify`. Its separate
`run_filing_evidence` check reads every matched seeded experiment item's **linked
observation**, comparing input and structured question to the stored dataset item.
Missing or changed evidence fails even if scores and a complete answer generation
are present. Regression tests cover missing input/metadata/linked observation and
changed section IDs, units and signs. Score availability remains a separate check.

This change does not publish an image, deploy the kit, or run a live seed. Those
checks belong to the kit release/verification ticket referenced by #251.
