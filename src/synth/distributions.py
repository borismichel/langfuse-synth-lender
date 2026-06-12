"""Realistic distributions (spec §5): log-normal latency, model-appropriate tokens.

All sampling routes through the seeded ``Rng`` so it is reproducible. Latency is
returned in milliseconds; the caller turns step latencies into nested start/end
timestamps (latency = endTime - startTime, per spec §2).

Token counts are anchored to the *visible* generation I/O (the chat messages a viewer
sees when they open the observation), so usage survives a close look: input = rendered
messages + unseen per-role overhead (tool schemas, policy context); output tracks the
rendered completion, with the Opus planner billing its gap as ``reasoning`` tokens.
"""
from __future__ import annotations

import json

from .pricing import ROLE_PROFILES
from .rng import Rng

CACHE_HIT_RATE = 0.82  # warm prompt-cache hit rate on the stable prefix (after warmup)


def cache_split(rng: Rng, role: str, input_tokens: int) -> tuple[int, int, int]:
    """Split total input into ``(regular_input, cache_read, cache_creation)``.

    The stable system/policy/tools prefix is read from cache on a warm hit (~82%) and
    written on a miss; the variable remainder (application + history) is always fresh."""
    prefix_med = ROLE_PROFILES[role].get("cache_prefix", 0)
    if prefix_med <= 0 or input_tokens <= 1:
        return input_tokens, 0, 0
    # ≥15% of the input is always per-request (user message, history) — never cached
    prefix = min(max(1, int(rng.lognormal(prefix_med, 0.15))), int(input_tokens * 0.85))
    variable = input_tokens - prefix
    if rng.chance(CACHE_HIT_RATE):
        return variable, prefix, 0   # warm hit: prefix served from cache (~0.1x)
    return variable, 0, prefix       # miss: prefix written to cache (~1.25x)


def text_tokens(content) -> int:
    """~4 chars/token estimate over rendered content (chat messages, str, or JSON-able)."""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):  # chat messages
        text = "".join(str(m.get("content", "")) for m in content)
    else:
        text = json.dumps(content)
    return max(1, len(text) // 4)


def sample_tokens(rng: Rng, role: str, *, visible_input: int = 0, visible_output: int = 0,
                  context_tokens: int = 0) -> tuple[int, int, int]:
    """Returns ``(input, output, reasoning)`` token counts for one call.

    Input = the rendered messages (``visible_input``) + sampled unseen overhead (tool
    schemas, policy context — sized per role) + ``context_tokens`` (multi-turn history,
    upstream reasoning). Output hugs the rendered completion (small jitter — the 4-chars
    heuristic is itself approximate); roles with a ``reasoning_med`` budget (the Opus
    planner) bill the rest as separate reasoning tokens, so the visible text and the
    usage numbers never contradict each other."""
    p = ROLE_PROFILES[role]
    overhead = max(0, int(rng.lognormal(p["overhead_med"], p["in_sig"])))
    inp = max(1, visible_input + overhead + max(0, int(context_tokens)))
    if visible_output:
        out = max(1, int(visible_output * rng.lognormal(1.0, 0.04)))
    else:
        out = max(1, int(rng.lognormal(p["out_med"], p["out_sig"])))
    reasoning = 0
    if p.get("reasoning_med"):
        reasoning = max(0, int(rng.lognormal(p["reasoning_med"], p["out_sig"])))
    return inp, out, reasoning


def sample_latency_ms(rng: Rng, role: str, slow_factor: float = 1.0) -> int:
    """Per-step latency, log-normal with a long tail. ``slow_factor`` injects degradation."""
    p = ROLE_PROFILES[role]
    base = rng.lognormal(p["lat_med_ms"], p["lat_sig"]) * slow_factor
    # occasional heavy tail outlier
    if rng.chance(0.02):
        base *= rng.uniform(3, 8)
    return max(1, int(base))


def tool_latency_ms(rng: Rng, median: float, sigma: float = 0.4, slow_factor: float = 1.0) -> int:
    base = rng.lognormal(median, sigma) * slow_factor
    if rng.chance(0.015):
        base *= rng.uniform(3, 6)
    return max(1, int(base))
