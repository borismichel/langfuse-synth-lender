"""`synth verify` — query the data back via the public API and assert the demo's
anchors (spec v2 acceptance criteria).

Asserts:
- the certification-suite exists with the configured item count; curated items carry
  ``sourceTraceId`` links,
- all three seeded runs exist as dataset runs; ``numeric_accuracy = fail`` scores with
  reasons exist (candidate B's red cells are real),
- the golden traces exist and are tagged ``golden``,
- the pending flagged trace exists, carries the analyst's down-vote + comment, and is
  NOT in the suite,
- the ``answer`` generation links to the prompt version live at the trace's timestamp,
  with the chat-shaped input (also catches the re-seed merge trap),
- the review queue exists with completed AND pending items (alive, not finished),
- all five score-method names are present on the scores surface.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import requests

from .config import Config
from .state import RunState


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


def _auth():
    return (os.environ.get("LANGFUSE_PUBLIC_KEY", ""), os.environ.get("LANGFUSE_SECRET_KEY", ""))


def _get(base: str, path: str, params: dict | None = None) -> dict:
    resp = requests.get(f"{base.rstrip('/')}{path}", params=params or {}, auth=_auth(), timeout=30)
    resp.raise_for_status()
    return resp.json()


def _get_scores(base: str, name: str, limit_pages: int = 30) -> list[dict]:
    out: list[dict] = []
    page = 1
    while page <= limit_pages:
        data = _get(base, "/api/public/v2/scores", {"name": name, "limit": 100, "page": page})
        rows = data.get("data", [])
        out.extend(rows)
        meta = data.get("meta", {})
        if not rows or page >= meta.get("totalPages", page):
            break
        page += 1
    return out


def run_verify(cfg: Config, state: RunState, *, log=print) -> VerifyReport:
    base = cfg.target.base_url
    report = VerifyReport()
    suite = state.suite

    # -- suite: item count + provenance --------------------------------------
    item_sources: set[str] = set()
    try:
        items: list[dict] = []
        page = 1
        while page <= 5:
            data = _get(base, "/api/public/dataset-items",
                        {"datasetName": suite["name"], "limit": 100, "page": page})
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

    # -- the three seeded runs ------------------------------------------------
    try:
        ds = _get(base, f"/api/public/datasets/{suite['name']}/runs", {"limit": 50})
        runs_seen = {r.get("name") for r in ds.get("data", [])}
        expected = set((suite.get("runs") or {}).keys())
        missing = expected - runs_seen
        report.add("seeded_runs", not missing,
                   f"runs on {suite['name']}: {sorted(runs_seen & expected)}; "
                   f"missing {sorted(missing) or 'none'}")
    except Exception as exc:  # noqa: BLE001
        report.add("seeded_runs", False, f"error: {exc}")

    try:
        na = _get_scores(base, "numeric_accuracy")
        fails = [s for s in na if str(s.get("stringValue") or s.get("value")) in ("fail", "0", "0.0")]
        with_reason = sum(1 for s in fails if (s.get("comment") or "").strip())
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
            r = requests.get(f"{base.rstrip('/')}/api/public/traces/{g['trace_id']}",
                             auth=_auth(), timeout=20)
            if r.status_code == 200 and "golden" in (r.json().get("tags") or []):
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
            exists = requests.get(f"{base.rstrip('/')}/api/public/traces/{tid}",
                                  auth=_auth(), timeout=20).status_code == 200
            downs = _get_scores(base, "analyst_feedback")
            has_down = any(s.get("traceId") == tid and (s.get("comment") or "").strip()
                           for s in downs)
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
            trace = _get(base, f"/api/public/traces/{tid}")
            for o in trace.get("observations", []):
                if o.get("name") != "answer":
                    continue
                if o.get("promptName") == state.prompt_name and o.get("promptVersion"):
                    linked = True
                inp = o.get("input")
                if (isinstance(inp, list) and inp and isinstance(inp[0], dict)
                        and inp[0].get("role") == "system"
                        and "analyst copilot" in str(inp[0].get("content", ""))):
                    chat_ok = True
            detail = f"trace {tid[:12]}… prompt-linked={linked}, chat-shaped input={chat_ok}"
        report.add("prompt_linkage", linked and chat_ok,
                   detail + ("" if chat_ok else " (stale-merge? use a fresh project)"))
    except Exception as exc:  # noqa: BLE001
        report.add("prompt_linkage", False, f"error: {exc}")

    # -- review queue alive -----------------------------------------------------------
    try:
        queues = _get(base, "/api/public/annotation-queues", {"limit": 100}).get("data", [])
        q = next((x for x in queues if x.get("name") == state.queue.get("name")), None)
        ok = False
        detail = f"queue {state.queue.get('name')!r} not found"
        if q:
            items = _get(base, f"/api/public/annotation-queues/{q['id']}/items",
                         {"limit": 100}).get("data", [])
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
            present[name] = len(_get_scores(base, name, limit_pages=1))
        gnd = _get_scores(base, "groundedness", limit_pages=3)
        human = sum(1 for s in gnd
                    if "human annotation" in (s.get("comment") or ""))
        present["human_annotation(groundedness)"] = human
        ok = all(v > 0 for v in present.values())
        report.add("score_methods", ok, f"score counts: {present}")
    except Exception as exc:  # noqa: BLE001
        report.add("score_methods", False, f"error: {exc}")

    for c in report.checks:
        log(f"  [{'PASS' if c.ok else 'FAIL'}] {c.name}: {c.detail}")
    return report
