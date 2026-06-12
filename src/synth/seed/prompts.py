"""The ``analyst-copilot`` prompt with real version history (spec v2 §5).

~8 versions over the window, labels ``production`` (the stable release, version N-1)
and ``staging`` (the experimental newest, version N). Backdated generations link to
the **exact version that was live at their timestamp** — including one mid-window
transition (v5 → v6) and a "fix" release (v6 → v7) that the optional quality-dip
ambience hangs off.

All versions share the same conventions core (the deterministic agent mirrors it
byte-for-byte); they differ in version-specific guidance lines — enough that the
version diff view shows believable prompt-engineering history. Cosmetic limitation:
prompt-version *creation timestamps* cannot be backdated via the API, so the history
shows as created at seed time; the era linkage on generations is what carries the
story.

Idempotent: registration tops the prompt up to N versions and re-asserts the labels;
a re-seed creates nothing new.
"""
from __future__ import annotations

import os
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[3]
PROMPTS_DIR = REPO_ROOT / "prompts"

# Per-version guidance line + commit message — believable prompt-engineering history.
# v6 is the mid-window refactor the quality dip hangs off; v7 is the fix; v8 staging.
VERSION_HISTORY: list[tuple[str, str]] = [
    ("Answer analyst questions strictly from the supplied filing extracts.",
     "initial production prompt"),
    ("Always include the section_id of every extract used.",
     "require citations on every claim"),
    ("Report figures in full EUR after applying the unit note.",
     "unit normalisation made explicit"),
    ("Decline credit recommendations; the copilot supports analysis, not decisions.",
     "conduct rules tightened (compliance review)"),
    ("Compute ratios only from extracted figures; flag DSCR < 1.20x as a covenant breach.",
     "ratio + covenant handling"),
    ("Prefer concise answers; lead with the figure, then the derivation.",
     "response-style refactor (shorter answers)"),
    ("Restored: verify every figure against the cited extract line before answering.",
     "fix: re-add verification step dropped in the style refactor"),
    ("Experimental: when multiple filings disagree, show both values and flag the delta.",
     "staging: multi-filing disagreement handling"),
]


@lru_cache(maxsize=None)
def _base_text() -> str:
    return (PROMPTS_DIR / "analyst_copilot.txt").read_text().rstrip() + "\n"


@lru_cache(maxsize=None)
def prompt_text(version: int = 7) -> str:
    """The full system-prompt text for a version — base conventions plus the
    version's guidance line. The same bytes registered in prompt management and
    displayed in seeded ``answer`` inputs."""
    idx = max(1, min(version, len(VERSION_HISTORY))) - 1
    guidance, _ = VERSION_HISTORY[idx]
    return _base_text() + f"\nVERSION GUIDANCE (v{idx + 1}): {guidance}\n"


def commit_message(version: int) -> str:
    idx = max(1, min(version, len(VERSION_HISTORY))) - 1
    return VERSION_HISTORY[idx][1]


def version_for_timestamp(cfg, run_date: datetime, ts: datetime) -> int:
    """Which analyst-copilot version was live in production at ``ts`` (the era map):
    v(N-3) until the mid-window transition, v(N-2) until the fix, v(N-1) after."""
    from ..timegen import day_anchor

    cert = cfg.certification
    prod = cert.production_version              # e.g. 7
    t_transition = day_anchor(run_date, cert.prompt_transition_day_offset)
    t_fix = day_anchor(run_date, cert.prompt_fix_day_offset)
    if ts < t_transition:
        return max(1, prod - 2)                 # v5 — the long-stable predecessor
    if ts < t_fix:
        return max(1, prod - 1)                 # v6 — the refactor (quality-dip era)
    return prod                                  # v7 — the fix, current production


def _chat_prompt(system_text: str) -> list[dict]:
    return [
        {"role": "system", "content": system_text},
        {"role": "user", "content": "{{question}}"},
    ]


def set_version_labels(base_url: str, name: str, version: int, labels: list[str]) -> None:
    """(Re)assert labels on a specific prompt version (PATCH /versions/{version})."""
    pub = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    sec = os.environ.get("LANGFUSE_SECRET_KEY", "")
    resp = requests.patch(
        f"{base_url.rstrip('/')}/api/public/v2/prompts/{name}/versions/{version}",
        json={"newLabels": labels}, auth=(pub, sec), timeout=15)
    resp.raise_for_status()


def register_prompts(lf, cfg) -> dict:
    """Ensure versions 1..N exist (create only the missing tail — idempotent on
    re-seed), then assert ``production`` -> v(N-1) and ``staging`` -> v(N).
    Returns {"latest": N, "production": N-1, "staging": N}."""
    name = cfg.certification.prompt_name
    n = cfg.certification.n_prompt_versions

    latest = 0
    try:
        existing = lf.get_prompt(name, label="latest", type="chat", cache_ttl_seconds=0)
        latest = int(getattr(existing, "version", 0) or 0)
    except Exception:  # noqa: BLE001 — prompt doesn't exist yet
        latest = 0

    for v in range(latest + 1, n + 1):
        lf.create_prompt(name=name, type="chat", prompt=_chat_prompt(prompt_text(v)),
                         labels=[], commit_message=commit_message(v))
    latest = max(latest, n)

    prod_v = cfg.certification.production_version
    stage_v = cfg.certification.staging_version
    base_url = cfg.target.base_url
    for version, labels in ((prod_v, ["production"]), (stage_v, ["staging"])):
        try:
            set_version_labels(base_url, name, version, labels)
        except Exception:  # noqa: BLE001 — non-fatal; set in the UI
            pass
    return {"latest": latest, "production": prod_v, "staging": stage_v}
