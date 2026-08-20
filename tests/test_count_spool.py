"""Spool-count exposure for the Lender kit (#35).

Two things this locks:

* the lib-side ``count_spool`` measures the **Step 0 golden** (``tests/golden/lender_spool.ndjson``)
  exactly, cross-checked against an independent recount of the snapshot's own bytes; and
* the kit exposes it to the portal the same way ``import-spool`` is exposed — a ``synth``
  console verb — printing the measured count as JSON with no new plumbing shape.

Note Lender maps ``target_traces`` to an internal ``volume.scale`` (derive-scale), so the
measured trace count is the *derived* volume, not the knob value — which is exactly why the
measured Spool count, not an advisory estimate, is what binds.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from langfuse_synth_core.seed.count import count_spool
from synth.cli import app

GOLDEN_PATH = Path(__file__).resolve().parent / "golden" / "lender_spool.ndjson"

# The Step 0 snapshot's own billable tallies, frozen alongside the byte-identical oracle
# (a deliberate re-bless of lender_spool.ndjson updates both). Lender maps target_traces=150
# to an internal volume.scale (derive-scale), so the measured trace count is the derived
# volume (252), not the knob value — which is exactly why the measured count is what binds.
# Since the OTLP cutover (portal #210) every observation rides an OTLP span — including
# one minted root per trace — the trace term is derived from distinct trace ids, and the
# billable `total` excludes it (a v4 trace is a view over its root, already inside
# `observations`; portal #220).
GOLDEN_TALLIES = {"traces": 252, "observations": 1621, "scores": 531, "total": 2152}

# Independent (logic duplicated here on purpose) recount, a different code path than
# the library's, so agreement is a real cross-check rather than a tautology.
def _independent_tally(path: Path) -> dict[str, int]:
    counts = {"traces": 0, "observations": 0, "scores": 0}
    trace_ids: set[str] = set()
    billable = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        entry = json.loads(line)
        if "spanId" in entry and "type" not in entry:  # an OTLP span line
            counts["observations"] += 1
            trace_ids.add(entry["traceId"])
            billable += 1
        elif entry["type"] == "score-create":
            counts["scores"] += 1
            billable += 1
    counts["traces"] = len(trace_ids)
    counts["total"] = billable
    return counts


def test_count_spool_matches_golden_step0_tallies():
    counts = count_spool(GOLDEN_PATH)
    # Anchor to the snapshot's own recorded tallies (the derived-volume trace count included).
    assert counts == GOLDEN_TALLIES
    # ...and cross-check that against an independent recount of the same bytes.
    assert counts == _independent_tally(GOLDEN_PATH)


def test_count_spool_cli_verb_prints_json(tmp_path: Path):
    """`synth count-spool <spool>` — exposed exactly like `synth import-spool`."""
    spool = tmp_path / "events.ndjson"
    spool.write_text(
        '{"traceId":"t","spanId":"a","name":"root"}\n'
        '{"traceId":"t","spanId":"b","name":"answer"}\n'
        '{"type":"score-create","id":"s"}\n'
        '{"type":"dataset-run-item-create","id":"r"}\n',  # excluded
        encoding="utf-8",
    )
    result = CliRunner().invoke(app, ["count-spool", str(spool)])
    assert result.exit_code == 0, result.output
    # The trace term is derived from distinct trace ids and is NOT billed: the root span it
    # names is already inside `observations` (portal #220).
    assert json.loads(result.output) == {
        "traces": 1, "observations": 2, "scores": 1, "total": 3,
    }
