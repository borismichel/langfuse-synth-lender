"""Cost = token counts x per-model pricing (spec §5). Pricing lives in config (auditable).

We emit ``usageDetails`` (map str->int tokens) and ``costDetails`` (map str->float EUR)
on each generation, so Langfuse's cost view is driven by the same numbers a reviewer
can read in ``config/demo.yaml``.
"""
from __future__ import annotations

from .config import Config, Model

# Anthropic prompt-caching price multipliers on the base input rate (spec §5):
# a cache *read* is ~0.1x, a cache *write* (creation) ~1.25x.
CACHE_READ_MULT = 0.1
CACHE_WRITE_MULT = 1.25


def cost_details(model: Model, input_tokens: int, output_tokens: int,
                 cache_read: int = 0, cache_creation: int = 0,
                 reasoning: int = 0) -> dict[str, float]:
    inp = input_tokens / 1000.0 * model.input_per_1k
    cr = cache_read / 1000.0 * model.input_per_1k * CACHE_READ_MULT
    cc = cache_creation / 1000.0 * model.input_per_1k * CACHE_WRITE_MULT
    out = output_tokens / 1000.0 * model.output_per_1k
    rs = reasoning / 1000.0 * model.output_per_1k  # thinking bills at the output rate
    details = {"input": round(inp, 6), "output": round(out, 6)}
    if cache_read:
        details["cache_read_input_tokens"] = round(cr, 6)
    if cache_creation:
        details["cache_creation_input_tokens"] = round(cc, 6)
    if reasoning:
        details["reasoning"] = round(rs, 6)
    details["total"] = round(inp + cr + cc + out + rs, 6)
    return details


def usage_details(input_tokens: int, output_tokens: int,
                  cache_read: int = 0, cache_creation: int = 0,
                  reasoning: int = 0) -> dict[str, int]:
    details = {"input": input_tokens, "output": output_tokens}
    if cache_read:
        details["cache_read_input_tokens"] = cache_read
    if cache_creation:
        details["cache_creation_input_tokens"] = cache_creation
    if reasoning:
        details["reasoning"] = reasoning
    details["total"] = input_tokens + cache_read + cache_creation + output_tokens + reasoning
    return details


# Per-role token profiles and latency medians. Input tokens are anchored to the call's
# *visible* chat messages; ``overhead_med`` is the median size of the unseen remainder
# (tool schemas, policy docs, scaffolding) a real production agent carries on top —
# sized so totals land where a production call would (plan ~3.2k, work ~2.4k, light
# ~700). Two things are layered on top in traces.py: multi-turn history (input grows
# with turn index) and Opus reasoning impact (the planner's full output — visible plan
# + ``reasoning_med`` thinking tokens — is fed into the decision step's input). Opus
# reasons (large, slow); Haiku is small and fast — so call-count and spend views
# disagree (§5). ``cache_prefix`` = median size of the stable, cacheable prompt prefix
# (system prompt + policy + tool schemas) carved out of the input — read from cache on
# a warm hit. ``out_med``/``out_sig`` are the fallback when no visible output is given.
ROLE_PROFILES = {
    "plan":  {"overhead_med": 3100, "in_sig": 0.35, "out_med": 1600, "out_sig": 0.5,  "reasoning_med": 1500, "lat_med_ms": 5200, "lat_sig": 0.5,  "cache_prefix": 2100},
    "work":  {"overhead_med": 2000, "in_sig": 0.3,  "out_med": 230,  "out_sig": 0.35, "lat_med_ms": 1500, "lat_sig": 0.45, "cache_prefix": 1800},
    "light": {"overhead_med": 600,  "in_sig": 0.3,  "out_med": 160,  "out_sig": 0.4,  "lat_med_ms": 600,  "lat_sig": 0.4,  "cache_prefix": 400},
}


def model_for_role(cfg: Config, role: str) -> Model:
    return cfg.model_by_role(role)
