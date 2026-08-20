"""Target-specific behaviour — the shared core's, re-exported under the kit's own name.

Two facts about the Langfuse this clone is pointed at change what the kit does: **is it
Cloud?** (URL-derived — Cloud rate-limits the per-object REST reads and writes, so they are
spaced out and lean on the Retry-After-aware backoff in ``langfuse_synth_core.http``) and
**which read API generation does it serve?** (probed — Cloud goes v4-only on 2026-11-16 and
a self-hosted host cuts over whenever its operator upgrades it, so a host name answers
nothing).

Both used to live here, and a byte-identical copy lived in the EV kit. The verify read-seam
cutover (portal #211) moved them into :mod:`langfuse_synth_core.target` beside the read seam
that does the probing — one implementation, so the two kits cannot drift and a third
inherits it. This module is the kit's name for it; every call site is unchanged.

The kit's *other* capability question — **does this host expose the unstable evaluator
API?** — is not here and never was: it is probed at its own call site
(``workbench.judges.list_judges``) because a newer self-hosted host should take the API path
whatever its URL says, and when it is absent the workbench degrades to logged UI
instructions.
"""
from __future__ import annotations

from langfuse_synth_core.target import (
    CLOUD_HOST_MARKER,
    CLOUD_POST_THROTTLE_S,
    TargetProfile,
    post_throttle_seconds,
)

__all__ = ["CLOUD_HOST_MARKER", "CLOUD_POST_THROTTLE_S", "TargetProfile",
           "post_throttle_seconds"]
