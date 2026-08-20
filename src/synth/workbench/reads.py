"""How the workbench reads Langfuse: once, and ready to be told "not here".

Every read on this surface renders a page. The catalog falls back to the deterministic
offline view when the instance is unreachable, the judge and evaluator endpoints are a
**self-hosted gap** that legitimately answers `404`, and the promote wizard prefills a form
it can perfectly well leave blank. All of them degrade on the answer — so the answer has to
arrive.

That is the whole reason this module exists. The shared read client retries eight times
with exponential backoff, which is right for a `verify` sweep riding out Cloud's 429s and
wrong here: three minutes of waiting before showing the offline catalog is worse resilience
than showing it at once (portal #211). One attempt, said once, in the place the workbench
reads through.

The split below is the read seam's: entities Langfuse remapped for v4 (traces, observations,
scores, experiments) go through :func:`probe_reader`; the endpoints the migration left
alone — prompts, datasets, dataset items, score configs, annotation queues, the unstable
evaluator surface — go through :func:`probe_json`.
"""
from __future__ import annotations

from langfuse_synth_core.lfread import get_json
from langfuse_synth_core.read import LangfuseReader
from langfuse_synth_core.target import TargetProfile

#: One shot. See the module docstring for why this is not the shared client's default.
PROBE_ATTEMPTS = 1


def probe_json(base: str, path: str, params: dict | None = None) -> dict:
    """GET one of the endpoints the read seam does not model. Raises on a bad status."""
    return get_json(base, path, params, attempts=PROBE_ATTEMPTS)


def probe_reader(base_url: str) -> LangfuseReader:
    """A read-seam reader for this target, resolving its generation on first use."""
    return TargetProfile.detect(base_url).reader(attempts=PROBE_ATTEMPTS)
