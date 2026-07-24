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
import os
from dataclasses import dataclass, field

import requests

from ..config import Config
from .catalog import Catalog


def _auth():
    return (os.environ.get("LANGFUSE_PUBLIC_KEY", ""), os.environ.get("LANGFUSE_SECRET_KEY", ""))


def _get(base: str, path: str, params: dict | None = None) -> dict:
    resp = requests.get(f"{base.rstrip('/')}{path}", params=params or {}, auth=_auth(), timeout=15)
    resp.raise_for_status()
    return resp.json()


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
    except requests.RequestException as exc:
        return [], f"queue lookup failed: {exc}"

    already = {it.get("sourceTraceId")
               for ds in catalog.datasets for it in ds.items
               if it.get("sourceTraceId")}
    out = []
    for qi in items:
        tid = qi.get("objectId")
        if not tid or tid in already or qi.get("objectType") != "TRACE":
            continue
        out.append(_hydrate(cfg, tid, qi.get("status", "")))
    return out, ""


def _hydrate(cfg: Config, trace_id: str, status: str) -> Candidate:
    base = cfg.target.base_url
    cand = Candidate(trace_id=trace_id, status=status)
    try:
        trace = _get(base, f"/api/public/traces/{trace_id}")
        cand.question = trace.get("input") or {}
        cand.produced = trace.get("output") or {}
        cand.borrower = (trace.get("metadata") or {}).get("borrower", "")
        cand.case_id = (trace.get("metadata") or {}).get("case_id", "")
        for s in trace.get("scores") or []:
            if s.get("comment") and (s.get("name") == "analyst_feedback"
                                     or "human annotation" in s.get("comment", "")):
                cand.reviewer_comments.append(s["comment"])
        # the deterministic conventions produce the corrected ground truth for our
        # templated questions — prefill, reviewer confirms/edits
        try:
            from ..agent import answer_deterministic

            cand.suggested_expected = answer_deterministic(cand.question).model_dump()
        except Exception:  # noqa: BLE001 — free-form trace: reviewer types it
            cand.suggested_expected = {}
    except requests.RequestException:
        pass
    return cand


def promote(cfg: Config, *, trace_id: str, dataset_name: str, slice_name: str,
            expected_output_json: str, requirement_ids: list[str]) -> tuple[str, str]:
    """Create the dataset item. Returns (item_id, error)."""
    try:
        expected = json.loads(expected_output_json)
    except json.JSONDecodeError as exc:
        return "", f"expected output is not valid JSON: {exc}"
    base = cfg.target.base_url
    try:
        trace = _get(base, f"/api/public/traces/{trace_id}")
    except requests.RequestException as exc:
        return "", f"trace lookup failed: {exc}"

    from langfuse_synth_core.lfclient import get_langfuse

    lf = get_langfuse(cfg)
    item = lf.create_dataset_item(
        dataset_name=dataset_name,
        input=trace.get("input") or {},
        expected_output=expected,
        metadata={"slice": slice_name, "curated": True, "promoted_via": "workbench",
                  "requirement_ids": requirement_ids},
        source_trace_id=trace_id,
    )
    lf.flush()
    return getattr(item, "id", "") or "(created)", ""
