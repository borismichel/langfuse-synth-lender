"""Prefix-aware internal paths for the live playground.

When the portal serves a deployment behind a path prefix (``/live/{id}/…``) it injects
``LIVE_BASE_PATH=/live/{id}`` into the live container. Routes stay mounted at ``/`` — the
portal strips the prefix before forwarding, and direct health probes hit ``/`` unchanged.
This helper is used ONLY when *rendering* an internal href / form-action / redirect so the
generated link resolves behind the prefix; it never changes where routes are mounted.

Default is ``""`` (bare serving): output is byte-identical to before. The env is
portal-agnostic, like ``SYNTH_OUT_DIR`` / ``SYNTH_STATE_DIR``. External URLs (Langfuse deep
links, fonts) must never go through this.
"""
from __future__ import annotations

import os


def base_path() -> str:
    """The live-serving path prefix (e.g. ``/live/abc``), resolved at call time so a
    container ``ENV`` or a shell export both work. Empty by default; a trailing slash is
    stripped so :func:`local` never emits a double slash."""
    return os.environ.get("LIVE_BASE_PATH", "").rstrip("/")


def local(path: str) -> str:
    """Prefix an internal absolute ``path`` (must start with ``/``) with :func:`base_path`.

    Bare (prefix unset) → ``path`` returned unchanged, so rendered output stays
    byte-identical to today. Prefixed → ``/live/abc`` + ``path`` (``local("/")`` →
    ``/live/abc/``).
    """
    prefix = base_path()
    return f"{prefix}{path}" if prefix else path
