"""Backdated batch ingestion (spec §2) — the core architectural decision.

We build event objects directly and POST them to ``/api/public/ingestion`` with
explicit ``timestamp`` / ``startTime`` / ``endTime``, bypassing the high-level OTel
SDK (which pins everything to "now"). HTTP Basic auth with the project keys; the
``x-langfuse-ingestion-version: 4`` header makes the data visible in real time on
the v2 query/metrics endpoints (spec §12).

Idempotent on re-run: every object carries a deterministic id, so re-seeding upserts
within Langfuse's 30-day merge window rather than duplicating (spec §9, §11).

Two-phase by design (spec §2, hardened): generation **spools every event to an
NDJSON file on disk first**, then a separate pass **batch-imports** that file in
``chunk_size`` POSTs. Network never runs interleaved with generation, so a wedged
or slow upload can't lose the (expensive, deterministic) generated data — re-run
``import_spool`` against the same file to resume. Never one-request-per-event.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import requests


class IngestError(RuntimeError):
    pass


@dataclass
class Ingestor:
    base_url: str
    public_key: str
    secret_key: str
    chunk_size: int = 100
    ingestion_version: str = "4"
    dry_run: bool = False
    timeout: int = 30
    max_retries: int = 5
    spool_path: Path | None = None
    _events: list[dict] = field(default_factory=list)
    _spool_fh: object = field(default=None, repr=False)
    spooled: int = 0
    sent: int = 0

    @classmethod
    def from_env(cls, base_url: str, **kw) -> "Ingestor":
        pub = os.environ.get("LANGFUSE_PUBLIC_KEY")
        sec = os.environ.get("LANGFUSE_SECRET_KEY")
        if not (pub and sec) and not kw.get("dry_run"):
            raise IngestError(
                "LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY must be set (see .env.example)."
            )
        return cls(base_url=base_url.rstrip("/"), public_key=pub or "", secret_key=sec or "", **kw)

    # -- phase 1: accumulate / spool to disk ------------------------------
    def open_spool(self) -> None:
        """Begin streaming events to ``spool_path`` as NDJSON (one event per line).

        Writing straight to disk keeps memory flat across the full run and means the
        generated data survives a wedged or failed upload."""
        if self.spool_path is None:
            raise IngestError("open_spool: no spool_path set")
        self.spool_path.parent.mkdir(parents=True, exist_ok=True)
        self._spool_fh = self.spool_path.open("w", encoding="utf-8")
        self.spooled = 0

    def close_spool(self) -> None:
        if self._spool_fh is not None:
            self._spool_fh.flush()
            self._spool_fh.close()
            self._spool_fh = None

    def add(self, event: dict) -> None:
        if self._spool_fh is not None:
            self._spool_fh.write(json.dumps(event, separators=(",", ":")) + "\n")
            self.spooled += 1
        else:
            self._events.append(event)

    def extend(self, events) -> None:
        for event in events:
            self.add(event)

    @property
    def pending(self) -> int:
        return len(self._events)

    # -- phase 2: batch-import --------------------------------------------
    def import_spool(self, path: Path | None = None,
                     log: Callable[[str], None] = lambda _m: None) -> int:
        """Read a spooled NDJSON file and POST it in ``chunk_size`` batches.

        Re-runnable: idempotent ids mean a re-import upserts rather than duplicating,
        so this is the recovery path after an interrupted upload."""
        path = path or self.spool_path
        if path is None:
            raise IngestError("import_spool: no spool path")
        if not path.exists():
            raise IngestError(f"import_spool: spool file not found: {path}")
        chunk: list[dict] = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                chunk.append(json.loads(line))
                if len(chunk) >= self.chunk_size:
                    self._flush_chunk(chunk, log)
                    chunk = []
        if chunk:
            self._flush_chunk(chunk, log)
        return self.sent

    def _flush_chunk(self, chunk: list[dict], log: Callable[[str], None]) -> None:
        self._post_chunk(chunk)
        self.sent += len(chunk)
        log(f"  · imported {self.sent} events")

    # -- in-memory send (back-compat; the seed path uses spool/import) ----
    def flush(self) -> None:
        """Send all accumulated events in chunks; clears the buffer."""
        events, self._events = self._events, []
        for i in range(0, len(events), self.chunk_size):
            chunk = events[i : i + self.chunk_size]
            self._post_chunk(chunk)
            self.sent += len(chunk)

    def _post_chunk(self, chunk: list[dict]) -> None:
        if self.dry_run:
            return
        url = f"{self.base_url}/api/public/ingestion"
        headers = {"x-langfuse-ingestion-version": self.ingestion_version}
        backoff = 1.0
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = requests.post(
                    url,
                    json={"batch": chunk},
                    auth=(self.public_key, self.secret_key),
                    headers=headers,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                if attempt == self.max_retries:
                    raise IngestError(f"ingestion request failed: {exc}") from exc
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)
                continue

            if resp.status_code in (200, 201, 207):
                # 207 = partial success; surface per-event errors loudly.
                self._check_partial(resp)
                return
            if resp.status_code in (429, 500, 502, 503, 504):
                if attempt == self.max_retries:
                    raise IngestError(f"ingestion failed {resp.status_code}: {resp.text[:500]}")
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)
                continue
            raise IngestError(f"ingestion rejected {resp.status_code}: {resp.text[:500]}")

    @staticmethod
    def _check_partial(resp: requests.Response) -> None:
        try:
            body = resp.json()
        except ValueError:
            return
        errors = body.get("errors") or []
        if errors:
            sample = errors[:3]
            raise IngestError(f"{len(errors)} events rejected by ingestion; sample: {sample}")


# ---------------------------------------------------------------------------
# Project guardrail + score-config REST helpers (share the same auth)
# ---------------------------------------------------------------------------
def assert_demo_project(base_url: str, project_hint: str) -> tuple[str, str]:
    """Refuse to run unless the key's project name contains ``project_hint`` (spec §12).

    Returns ``(project_id, project_name)``. Loud failure if it doesn't match — this is
    the guardrail that stops a seed ever hitting a production project.
    """
    pub = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    sec = os.environ.get("LANGFUSE_SECRET_KEY", "")
    resp = requests.get(
        f"{base_url.rstrip('/')}/api/public/projects",
        auth=(pub, sec),
        timeout=15,
    )
    resp.raise_for_status()
    projects = resp.json().get("data", [])
    names = [p.get("name", "") for p in projects]
    matched = [p for p in projects if project_hint.lower() in p.get("name", "").lower()]
    if not matched:
        raise IngestError(
            f"GUARDRAIL: no project matching project_hint={project_hint!r} for these keys "
            f"(saw {names!r}). Point at a demo/sandbox project or fix project_hint. (spec §12)"
        )
    p = matched[0]
    return p.get("id", ""), p.get("name", "")


def ensure_score_config(base_url: str, body: dict) -> None:
    """Create a score config (POST /api/public/score-configs). Idempotent-ish: ignores 409."""
    pub = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    sec = os.environ.get("LANGFUSE_SECRET_KEY", "")
    resp = requests.post(
        f"{base_url.rstrip('/')}/api/public/score-configs",
        json=body,
        auth=(pub, sec),
        timeout=15,
    )
    if resp.status_code in (200, 201):
        return
    if resp.status_code == 409:
        return  # already exists
    # Some deployments 400 on duplicate name; treat as benign if the name already exists.
    if resp.status_code == 400 and "exist" in resp.text.lower():
        return
    raise IngestError(f"score-config create failed {resp.status_code}: {resp.text[:300]}")
