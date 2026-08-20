"""`synth verify` — read the data back through the seam and assert the demo's anchors
(spec v2 acceptance criteria).

Asserts:
- the certification-suite exists with the configured item count; curated items carry
  ``sourceTraceId`` links,
- all three seeded runs exist as **experiments** (dataset runs) and carry their full item
  count; ``numeric_accuracy = fail`` scores with reasons exist (candidate B's red cells are
  real),
- each run item carries a prompt-linked ``answer`` generation (references the production
  prompt) with a real token/cost column,
- run-level aggregate scores exist and candidate B has the lowest ``rate_numeric_accuracy``,
- the golden traces exist and are tagged ``golden``,
- the pending flagged trace exists, carries the analyst's down-vote + comment, and is
  NOT in the suite,
- the ``answer`` generation links to the prompt version live at the trace's timestamp,
  with the chat-shaped input (also catches the re-seed merge trap),
- the review queue exists with completed AND pending items (alive, not finished),
- all five score-method names are present on the scores surface.

**Every Langfuse read here goes through the read seam** (``langfuse_synth_core.read``),
which owns the endpoints and answers the same normalised rows whichever API generation the
target serves (portal #211). That matters most for the dataset runs: under v4 a run is an
**experiment**, listed by dataset id through ``/api/public/experiments`` and its items
through ``/api/public/experiment-items``, and this file no longer knows that — it asks
``reader.experiments(...)`` and gets the same rows on either generation.

The two endpoints the seam does not model, because they were never deprecated — dataset
items and annotation queues — are read with ``lfread.get_json``, which still carries the
shared auth and the Retry-After-aware backoff.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from langfuse_synth_core.lfread import get_json

from .config import Config
from .state import RunState
from .target import TargetProfile


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class VerifyReport:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str) -> None:
        self.checks.append(Check(name, ok, detail))

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)


def _system_prompt_of(observation) -> str:
    """The system turn of a chat-shaped generation input, or ``""`` when it is not one.

    The seam decodes v4's raw-JSON-string `input` back into the messages the deprecated API
    returned parsed, so this reads the same on either generation."""
    inp = observation.input
    if isinstance(inp, list) and inp and isinstance(inp[0], dict) and inp[0].get("role") == "system":
        return str(inp[0].get("content", ""))
    return ""


def _costed(observation) -> bool:
    """Whether a generation carries a real spend column. The seam rolls legacy's
    ``calculatedTotalCost`` and v4's ``totalCost`` onto one field, and the ingested
    breakdown stays available for a target that reports only that."""
    return bool(observation.total_cost or (observation.cost_details or {}).get("total"))


def run_verify(cfg: Config, state: RunState, *, log=print) -> VerifyReport:
    # `try_resolve`, not `resolved`: bad keys or a wrong host must come back as failed
    # checks with the reason on each line, which is what this report is for — not as a
    # traceback in place of it. Unresolved, each read below probes again inside its own
    # check and fails there (portal #211).
    profile, unreadable = TargetProfile.detect(cfg.target.base_url).try_resolve()
    base = profile.base_url
    reader = profile.reader()
    throttle = profile.post_throttle_s
    log(f"· verifying against {profile.label} ({base})"
        + (f" — cannot read it: {unreadable}" if unreadable else ""))
    report = VerifyReport()
    suite = state.suite

    # -- suite: item count + provenance --------------------------------------
    item_sources: set[str] = set()
    try:
        items: list[dict] = []
        page = 1
        while page <= 5:
            data = get_json(base, "/api/public/dataset-items",
                            {"datasetName": suite["name"], "limit": 100, "page": page},
                            throttle=throttle)
            rows = data.get("data", [])
            items.extend(rows)
            if not rows or page >= data.get("meta", {}).get("totalPages", page):
                break
            page += 1
        item_sources = {it.get("sourceTraceId") for it in items if it.get("sourceTraceId")}
        ok = len(items) == suite["items"]
        report.add("suite_items", ok,
                   f"{suite['name']}: {len(items)} items (expected {suite['items']}); "
                   f"{len(item_sources)} curated links")
    except Exception as exc:  # noqa: BLE001
        report.add("suite_items", False, f"error: {exc}")

    # -- the three seeded runs: exist AND have linked item results -------------
    # Guards the cloud-vs-v3 discrepancy: the Experiments tab on newer Langfuse
    # (≥ v3.185, incl. Cloud) only surfaces runs created via run_experiment, not the
    # legacy REST dataset-run-items path. We can't query the UI view, but an empty
    # run (0 items) is the API-visible symptom of a run that won't render — so we
    # assert every run carries items and a scored sample trace.
    experiments = []
    try:
        # The SDK run_experiment appends a " - <timestamp>" suffix to each run name, so
        # match expected run names by PREFIX. (The runs list is also eventually consistent
        # on Cloud — retry until the 3 runs appear.)
        expected = sorted((suite.get("runs") or {}).keys())
        for attempt in range(8):
            experiments = reader.experiments(dataset_name=suite["name"])
            actual_names = [e.name for e in experiments]
            if all(any(a.startswith(name) for a in actual_names) for name in expected):
                break
            time.sleep(8)
        missing, matched = [], []
        for name in expected:
            hit = next((e for e in experiments if e.name.startswith(name)), None)
            (matched.append(hit) if hit else missing.append(name))
        # Each run must carry the FULL item count (== suite size). An empty run won't
        # surface in the Experiments tab; a short run (e.g. 71/72) means a run item was
        # dropped — exactly the symptom of a transient ingest blip during run_experiment.
        want_items = suite.get("items")
        short = []  # (run, count) for runs missing items
        for experiment in matched:
            n = len(reader.experiment_items(experiment))
            if want_items and n != want_items:
                short.append((experiment.name[:28], n))
        ok = not missing and not short
        report.add("seeded_runs", ok,
                   f"runs on {suite['name']} (prefix-matched, SDK adds a timestamp suffix): "
                   f"{len(matched)}/{len(expected)} present @ {want_items} items each; "
                   f"missing {missing or 'none'}"
                   + (f"; SHORT {short}" if short else ""))
    except Exception as exc:  # noqa: BLE001
        report.add("seeded_runs", False, f"error: {exc}")

    # -- run items reference the production prompt + carry cost ----------------------
    # Each run item emits a prompt-linked ``answer`` generation, so the runs reference
    # analyst-copilot and carry a real token/cost column (parity with the production
    # traces). Spot-check one run item's trace for the link + cost.
    try:
        experiment = experiments[0] if experiments else None
        linked = costed = False
        detail = "no runs found"
        if experiment is not None:
            run_items = reader.experiment_items(experiment)
            tid = next((i.trace_id for i in run_items if i.trace_id), None)
            detail = f"run {experiment.name[:28]!r}: no item trace"
            if tid:
                trace = reader.trace(tid, with_scores=False)
                for o in (trace.observations if trace else []):
                    if o.name != "answer":
                        continue
                    if o.prompt_name == state.prompt_name and o.prompt_version:
                        linked = True
                    if _costed(o):
                        costed = True
                detail = (f"run {experiment.name[:28]!r} item trace {tid[:12]}… "
                          f"prompt-linked={linked}, cost={costed}")
        report.add("run_prompt_link", linked and costed, detail)
    except Exception as exc:  # noqa: BLE001
        report.add("run_prompt_link", False, f"error: {exc}")

    # -- run-level (Experiment-Level) aggregate scores -----------------------------
    # Per-run rollups attached to the dataset run (mean_/rate_ prefixed, clash-free with
    # the item scores). Assert each run carries them AND candidate B (haiku) has the
    # lowest rate_numeric_accuracy — the rejection delta must be real at the rollup.
    try:
        by_rate = {}
        present_ok = True
        for experiment in experiments:
            scores = reader.scores(experiment_id=experiment.id, limit_pages=1)
            names = {s.name for s in scores}
            if not {"mean_groundedness", "rate_numeric_accuracy", "verdict"} <= names:
                present_ok = False
            for s in scores:
                # A rollup that came back without a number is a rollup that is not there:
                # keeping it would put `None` into the comparison below and crash the whole
                # check, where the honest answer is "this run has no rate_numeric_accuracy".
                if s.name == "rate_numeric_accuracy" and s.numeric_value is not None:
                    by_rate[experiment.name] = s.numeric_value
        worst = min(by_rate.items(), key=lambda kv: kv[1]) if by_rate else ("", None)
        delta_ok = "haiku" in worst[0]
        report.add("run_level_scores", present_ok and delta_ok and len(by_rate) >= 3,
                   f"{len(by_rate)} runs carry run-level aggregates; lowest rate_numeric_accuracy "
                   f"= {worst[1]} on {worst[0][:28]!r} (candidate B expected)")
    except Exception as exc:  # noqa: BLE001
        report.add("run_level_scores", False, f"error: {exc}")

    try:
        na = reader.scores(name="numeric_accuracy")
        # A categorical `fail` and the numeric 0 both mean the same red cell; the seam
        # keeps them apart (a label has no numeric value), so ask for either.
        fails = [s for s in na if s.string_value == "fail" or s.numeric_value == 0]
        with_reason = sum(1 for s in fails if (s.comment or "").strip())
        ok = len(fails) >= 4 and with_reason >= 4
        report.add("candidate_b_red_cells", ok,
                   f"{len(fails)} numeric_accuracy fails ({with_reason} with reasons) — "
                   "candidate B's rejection is evidenced")
    except Exception as exc:  # noqa: BLE001
        report.add("candidate_b_red_cells", False, f"error: {exc}")

    # -- golden traces ----------------------------------------------------------
    try:
        found = 0
        for g in state.golden:
            trace = reader.trace(g["trace_id"], with_scores=False)
            if trace is not None and "golden" in (trace.tags or []):
                found += 1
        ok = found == len(state.golden) and found >= 4
        report.add("golden_traces", ok, f"{found}/{len(state.golden)} golden traces tagged & present")
    except Exception as exc:  # noqa: BLE001
        report.add("golden_traces", False, f"error: {exc}")

    # -- pending flagged case (reserved) ------------------------------------------
    try:
        fb = state.flagged_pending[0] if state.flagged_pending else {}
        tid = fb.get("trace_id")
        ok = False
        detail = "no flagged_pending in state"
        if tid:
            exists = reader.trace(tid, with_scores=False) is not None
            downs = reader.scores(name="analyst_feedback", trace_id=tid)
            has_down = any((s.comment or "").strip() for s in downs)
            leaked = tid in item_sources
            ok = exists and has_down and not leaked
            detail = (f"trace exists={exists}, down-vote+comment={has_down}, "
                      f"leaked into suite={leaked}")
        report.add("flagged_pending", ok, detail)
    except Exception as exc:  # noqa: BLE001
        report.add("flagged_pending", False, f"error: {exc}")

    # -- prompt-era linkage + chat input on a golden trace --------------------------
    try:
        tid = state.golden_by_key("covenant_summary").get("trace_id")
        linked = chat_ok = False
        detail = "no golden trace in state"
        if tid:
            trace = reader.trace(tid, with_scores=False)
            for o in (trace.observations if trace else []):
                if o.name != "answer":
                    continue
                if o.prompt_name == state.prompt_name and o.prompt_version:
                    linked = True
                if "analyst copilot" in _system_prompt_of(o):
                    chat_ok = True
            detail = f"trace {tid[:12]}… prompt-linked={linked}, chat-shaped input={chat_ok}"
        report.add("prompt_linkage", linked and chat_ok,
                   detail + ("" if chat_ok else " (stale-merge? use a fresh project)"))
    except Exception as exc:  # noqa: BLE001
        report.add("prompt_linkage", False, f"error: {exc}")

    # -- review queue alive -----------------------------------------------------------
    try:
        queues = get_json(base, "/api/public/annotation-queues", {"limit": 100},
                          throttle=throttle).get("data", [])
        q = next((x for x in queues if x.get("name") == state.queue.get("name")), None)
        ok = False
        detail = f"queue {state.queue.get('name')!r} not found"
        if q:
            items = get_json(base, f"/api/public/annotation-queues/{q['id']}/items",
                             {"limit": 100}, throttle=throttle).get("data", [])
            n_done = sum(1 for i in items if i.get("status") == "COMPLETED")
            n_pend = sum(1 for i in items if i.get("status") == "PENDING")
            ok = n_done >= 5 and n_pend >= 5
            detail = f"{n_done} completed, {n_pend} pending (alive ✓)" if ok else \
                     f"{n_done} completed, {n_pend} pending — queue must have both"
        report.add("review_queue", ok, detail)
    except Exception as exc:  # noqa: BLE001
        report.add("review_queue", False, f"error: {exc}")

    # -- all five score-method names present ---------------------------------------------
    try:
        present = {}
        for name in ("numeric_accuracy", "groundedness", "citation_coverage",
                     "analyst_feedback"):
            present[name] = len(reader.scores(name=name, limit_pages=1))
        # human annotations live on queue-completed traces (old timestamps — a global
        # newest-first scan misses them); check a trace known to carry them
        tid = state.golden_by_key("numeric_hallucination").get("trace_id")
        human = 0
        if tid:
            trace = reader.trace(tid)
            human = sum(1 for s in (trace.scores if trace else [])
                        if "human annotation" in (s.comment or ""))
        present["human_annotation(on golden trace)"] = human
        ok = all(v > 0 for v in present.values())
        report.add("score_methods", ok, f"score counts: {present}")
    except Exception as exc:  # noqa: BLE001
        report.add("score_methods", False, f"error: {exc}")

    for c in report.checks:
        log(f"  [{'PASS' if c.ok else 'FAIL'}] {c.name}: {c.detail}")
    return report
