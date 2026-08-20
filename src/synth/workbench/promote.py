"""Promote-from-queue wizard — closes the annotation seam.

The flow MRM actually needs: a reviewed production trace (completed item in the
``ground-truth-intake`` queue) becomes a suite item in one step, carrying the
reviewer's corrected ground truth — never the production model's wrong answer. The
wizard lists completed intake items whose trace is not yet any dataset item's
``sourceTraceId``, prefills the form from the trace (input, the reviewer's comment,
and a deterministic suggested expected output), and creates the dataset item with
slice + requirement ids.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import requests

from langfuse_synth_core.read import ReadError

from ..config import Config
from .catalog import Catalog
from .reads import probe_json as _get
from .reads import probe_reader

if TYPE_CHECKING:
    from langfuse_synth_core.companion import CompanionAdapter

# The annotation-queue reads go through `reads.probe_json` (the migration left those
# endpoints alone); the *trace* reads go through the read seam, because under v4 there is no
# trace row to GET (portal #211). Both ask once — this renders inside a live request and
# every caller here degrades on the error rather than failing the page.
_READ_FAILED = (ReadError, requests.RequestException)


@dataclass
class Candidate:
    trace_id: str
    status: str
    question: dict = field(default_factory=dict)        # the trace input
    produced: dict = field(default_factory=dict)        # what production answered (NOT ground truth)
    suggested_expected: dict = field(default_factory=dict)
    reviewer_comments: list[str] = field(default_factory=list)
    borrower: str = ""
    case_id: str = ""


def list_candidates(cfg: Config, catalog: Catalog) -> tuple[list[Candidate], str]:
    """Completed review-queue items whose trace is not yet in the suite."""
    base = cfg.target.base_url
    qname = cfg.certification.queue.name
    try:
        queues = _get(base, "/api/public/annotation-queues", {"limit": 100}).get("data", [])
        queue = next((q for q in queues if q.get("name") == qname), None)
        if queue is None:
            return [], f"queue {qname!r} not found"
        items = _get(base, f"/api/public/annotation-queues/{queue['id']}/items",
                     {"limit": 100}).get("data", [])
    except _READ_FAILED as exc:
        return [], f"queue lookup failed: {exc}"

    already = {it.get("sourceTraceId")
               for ds in catalog.datasets for it in ds.items
               if it.get("sourceTraceId")}
    out = []
    reader = probe_reader(cfg.target.base_url)   # one reader: the generation is probed once
    for qi in items:
        tid = qi.get("objectId")
        if not tid or tid in already or qi.get("objectType") != "TRACE":
            continue
        out.append(_hydrate(cfg, tid, qi.get("status", ""), reader))
    return out, ""


def _hydrate(cfg: Config, trace_id: str, status: str, reader=None) -> Candidate:
    cand = Candidate(trace_id=trace_id, status=status)
    try:
        trace = (reader or probe_reader(cfg.target.base_url)).trace(trace_id)
        if trace is None:
            return cand
        cand.question = trace.input or {}
        cand.produced = trace.output or {}
        cand.borrower = (trace.metadata or {}).get("borrower", "")
        cand.case_id = (trace.metadata or {}).get("case_id", "")
        for s in trace.scores:
            if s.comment and (s.name == "analyst_feedback"
                              or "human annotation" in s.comment):
                cand.reviewer_comments.append(s.comment)
        # the deterministic conventions produce the corrected ground truth for our
        # templated questions — prefill, reviewer confirms/edits
        try:
            from ..agent import answer_deterministic

            cand.suggested_expected = answer_deterministic(cand.question).model_dump()
        except Exception:  # noqa: BLE001 — free-form trace: reviewer types it
            cand.suggested_expected = {}
    except _READ_FAILED:
        pass          # an unreadable trace prefills nothing; the reviewer types it
    return cand


def promote(cfg: Config, *, trace_id: str, dataset_name: str, slice_name: str,
            expected_output_json: str, requirement_ids: list[str],
            adapter: "CompanionAdapter | None" = None) -> tuple[str, str]:
    """Create the dataset item. Returns (item_id, error). The SDK client comes from the
    Companion Adapter when the live Surface hands one in (Spec G · G5, #144); the trace
    lookup below goes through the read seam, which answers the same row whichever API
    generation the target serves (portal #211)."""
    try:
        expected = json.loads(expected_output_json)
    except json.JSONDecodeError as exc:
        return "", f"expected output is not valid JSON: {exc}"
    try:
        trace = probe_reader(cfg.target.base_url).trace(trace_id, with_scores=False)
    except _READ_FAILED as exc:
        return "", f"trace lookup failed: {exc}"
    if trace is None:
        return "", f"trace {trace_id} not found"

    from ..clients import langfuse as get_langfuse

    lf = get_langfuse(cfg, adapter=adapter)
    item = lf.create_dataset_item(
        dataset_name=dataset_name,
        input=trace.input or {},
        expected_output=expected,
        metadata={"slice": slice_name, "curated": True, "promoted_via": "workbench",
                  "requirement_ids": requirement_ids},
        source_trace_id=trace_id,
    )
    lf.flush()
    return getattr(item, "id", "") or "(created)", ""
