"""Where this kit's Langfuse and LLM clients come from (Spec G · G5, #144).

Since the Companion Adapter shell-swap there are two callers of every client, and exactly
one rule for both:

* **The live Surface** — the analyst copilot, the ``/dossier``, and the certification
  workbench — runs inside the adapter, so it hands one in and the clients come from *it*.
  The adapter owns secret intake and provider resolution (D4/D6): the Surface receives ready
  clients and never sees a raw key, a sentinel, or a source marker.
* **The headless CLI** — ``synth submit``, ``synth certify``, ``synth seed``, ``synth
  enrich`` — has no adapter, so the clients are built directly off the core resolution
  module and the env, byte-for-byte as before the swap.

Both branches resolve through the same core code (``companion.llm`` for the provider,
``lfclient``/``seed.ingest`` for Langfuse), so the two paths cannot drift apart. Keeping the
fork here rather than repeating it at each call site means the rule is stated once, and a
call site reads as "give me a client" rather than as plumbing.

Note what deliberately does NOT route through here: the workbench's raw REST reads
(``workbench/catalog.py``, ``workbench/promote.py``) and the seed path's LLM-connection
upsert (``workbench/judges.py``). Those are Surface/seed concerns that sit *beside* the
adapter — the catalog's paginated reads carry their own graceful-degradation contract (the
"offline catalog" banner), and the connection upsert must POST a real provider key to
Langfuse, which is a seeding act, not a Surface one. The adapter offers ``read_json`` for
kits that want the read seam; pushing this kit's scenario-shaped reads through it would move
scenario knowledge toward the boundary, which is exactly what G5 is meant to prevent.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .config import Config

if TYPE_CHECKING:
    from langfuse_synth_core.companion import CompanionAdapter


def langfuse(cfg: Config, *, adapter: "CompanionAdapter | None" = None) -> Any:
    """The Langfuse SDK client (prompts, datasets, experiments)."""
    if adapter is not None:
        return adapter.langfuse()
    from langfuse_synth_core.lfclient import get_langfuse

    return get_langfuse(cfg)


def llm(model: str | None = None, *, adapter: "CompanionAdapter | None" = None) -> Any:
    """The ready LLM client for ``model``.

    ``model`` is this kit's per-submission choice — the copilot's model selector, or a
    workbench spec's release model. The adapter resolves *which provider and which key*; the
    model named here is the caller default, and a deployment-pinned ``LLM_MODEL`` still
    outranks it (the amendment G5 pushed into the adapter rather than reaching around it)."""
    if adapter is not None:
        return adapter.llm(model)
    from langfuse_synth_core.companion.llm import get_llm

    return get_llm(model)


def ingestor(cfg: Config, *, adapter: "CompanionAdapter | None" = None, **kw: Any) -> Any:
    """The backdated-batch write client used to emit live traces and scores."""
    if adapter is not None:
        return adapter.ingestor(**kw)
    from langfuse_synth_core.seed.ingest import Ingestor

    return Ingestor.from_env(cfg.target.base_url, **kw)
