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

from langfuse_synth_core.lfread import get_json
from langfuse_synth_core.target import TargetProfile

from ..config import Config
from .catalog import Catalog

if TYPE_CHECKING:
    from langfuse_synth_core.companion import CompanionAdapter


# Annotation queues were never deprecated, so the read seam does not model them and the
# library's read primitive is the right one — with `attempts=1`, because this renders inside
# a live workbench request and every caller degrades on the error. The *trace* reads below
# go through the seam instead: under v4 there is no trace row to GET (portal #211).
def _get(base: str, path: str, params: dict | None = None) -> dict:
    return get_json(base, path, params, attempts=1)


def _reader(cfg: Config):
    """A read-seam reader for this target, asking once — same reason as `_get` above."""
    return TargetProfile.detect(cfg.target.base_url).reader(attempts=1)


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
    except Exception as exc:  # noqa: BLE001 — an unreachable instance is a message, not a crash
        return [], f"queue lookup failed: {exc}"

    already = {it.get("sourceTraceId")
               for ds in catalog.datasets for it in ds.items
               if it.get("sourceTraceId")}
    out = []
    reader = _reader(cfg)          # one reader, so the generation is probed once
    for qi in items:
        tid = qi.get("objectId")
        if not tid or tid in already or qi.get("objectType") != "TRACE":
            continue
        out.append(_hydrate(cfg, tid, qi.get("status", ""), reader))
    return out, ""


def _hydrate(cfg: Config, trace_id: str, status: str, reader=None) -> Candidate:
    cand = Candidate(trace_id=trace_id, status=status)
    try:
        trace = (reader or _reader(cfg)).trace(trace_id)
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
    except Exception:  # noqa: BLE001 — an unreadable trace prefills nothing; the reviewer types it
        pass
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
        trace = _reader(cfg).trace(trace_id, with_scores=False)
    except Exception as exc:  # noqa: BLE001 — an unreachable instance is a message, not a crash
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
