"""Langfuse SDK client construction (prompts, datasets, experiment, query-back).

The ingestion path uses raw HTTP (``seed/ingest.py``); the SDK is used only for the
separate API surfaces. We honour the spec's ``LANGFUSE_BASE_URL`` env name and bridge
it to the SDK's ``host`` / ``LANGFUSE_HOST``.
"""
from __future__ import annotations

import os

from .config import Config


def get_langfuse(cfg: Config):
    from langfuse import Langfuse

    base = cfg.target.base_url
    os.environ.setdefault("LANGFUSE_HOST", base)
    return Langfuse(
        host=base,
        public_key=os.environ.get("LANGFUSE_PUBLIC_KEY"),
        secret_key=os.environ.get("LANGFUSE_SECRET_KEY"),
    )
