"""The ``certification-review`` annotation queue (spec v2 §5) — one queue, alive.

A mix of COMPLETED items (human criteria scores present — the reviewer scores the
same configs certification uses — with judge scores alongside for the agreement story) and ~10–20 PENDING items (recent production traces
awaiting review — including the reserved flagged thumbs-down the live demo promotes
into the suite). The queue must look like a working process, not a finished one.
"""
from __future__ import annotations

import os
from typing import Callable

import requests

from ..config import Config


def _auth():
    return (os.environ.get("LANGFUSE_PUBLIC_KEY", ""), os.environ.get("LANGFUSE_SECRET_KEY", ""))


# Shared patient-retry helper + Cloud-only throttle (Cloud rate-limits per-object writes).
from ..target import post_throttle_seconds as throttle_seconds  # noqa: E402
from .cert_runs import _post_retry  # noqa: E402



def _get(base_url: str, path: str, params: dict | None = None) -> dict:
    resp = requests.get(f"{base_url.rstrip('/')}{path}", params=params or {},
                        auth=_auth(), timeout=20)
    resp.raise_for_status()
    return resp.json()


def score_config_ids(base_url: str, names: list[str]) -> list[str]:
    rows: list[dict] = []
    page = 1
    while page <= 10:
        data = _get(base_url, "/api/public/score-configs", {"limit": 100, "page": page})
        batch = data.get("data", [])
        rows.extend(batch)
        if not batch or page >= data.get("meta", {}).get("totalPages", page):
            break
        page += 1
    by_name = {r.get("name"): r.get("id") for r in rows}
    missing = [n for n in names if n not in by_name]
    if missing:
        raise RuntimeError(f"score configs not found (create them first): {missing}")
    return [by_name[n] for n in names]


def ensure_queue(base_url: str, name: str, description: str, config_ids: list[str]) -> str:
    data = _get(base_url, "/api/public/annotation-queues", {"limit": 100})
    for q in data.get("data", []):
        if q.get("name") == name:
            return q.get("id")
    resp = requests.post(f"{base_url.rstrip('/')}/api/public/annotation-queues",
                         json={"name": name, "description": description,
                               "scoreConfigIds": config_ids},
                         auth=_auth(), timeout=20)
    resp.raise_for_status()
    return resp.json().get("id")


def add_queue_item(base_url: str, queue_id: str, trace_id: str, status: str) -> None:
    import time

    resp = _post_retry(
        f"{base_url.rstrip('/')}/api/public/annotation-queues/{queue_id}/items",
        {"objectId": trace_id, "objectType": "TRACE", "status": status}, _auth())
    throttle = throttle_seconds(base_url)
    if throttle:
        time.sleep(throttle)
    resp.raise_for_status()


def seed_queue(cfg: Config, completed_trace_ids: list[str], pending_trace_ids: list[str],
               base_url: str, log: Callable[[str], None] = print) -> dict:
    """Create the certification-review queue with its completed history and pending
    backlog. Returns queue info for run state."""
    from .scores import REVIEW_QUEUE_CONFIGS

    qcfg = cfg.certification.queue
    ids = score_config_ids(base_url, REVIEW_QUEUE_CONFIGS)
    queue_id = ensure_queue(
        base_url, qcfg.name,
        "Ground-truth annotation feeding the certification suite: the reviewer scores "
        "the SAME criteria certification uses (groundedness, citation coverage, the "
        "deterministic scales) — those human scores are the ground truth the suite "
        "inherits.", ids)
    for tid in completed_trace_ids:
        add_queue_item(base_url, queue_id, tid, "COMPLETED")
    for tid in pending_trace_ids:
        add_queue_item(base_url, queue_id, tid, "PENDING")
    log(f"✓ annotation queue {qcfg.name!r}: {len(completed_trace_ids)} completed, "
        f"{len(pending_trace_ids)} pending (alive, not finished)")
    return {"name": qcfg.name, "id": queue_id,
            "completed": len(completed_trace_ids), "pending": len(pending_trace_ids)}
