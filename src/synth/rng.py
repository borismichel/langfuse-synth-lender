"""Single-seed determinism (spec §9).

One top-level ``seed`` drives every RNG and every generated ID. We never touch the
global ``random`` module or wall-clock entropy in the seed path, so the same
``(config, seed)`` reproduces a byte-identical project state — IDs included.

IDs use W3C Trace Context widths so they render like real OTel data:
- trace id   : 32 lowercase hex chars (16 bytes)
- observation: 16 lowercase hex chars (8 bytes)

They are derived deterministically from ``(seed, namespace, *keys)`` via BLAKE2b,
so re-running ``synth seed`` is idempotent: identical IDs upsert rather than
duplicate within Langfuse's 30-day merge window (spec §2, §11).
"""
from __future__ import annotations

import hashlib
import random
from typing import Iterable, Sequence


def _digest(seed: int, namespace: str, keys: Sequence[object]) -> bytes:
    h = hashlib.blake2b(digest_size=16)
    h.update(str(seed).encode())
    h.update(b"\x00")
    h.update(namespace.encode())
    for k in keys:
        h.update(b"\x00")
        h.update(str(k).encode())
    return h.digest()


class Rng:
    """A deterministic RNG plus deterministic ID minting, both keyed off one seed."""

    def __init__(self, seed: int):
        self.seed = seed
        self._rand = random.Random(seed)

    # -- substreams: derive an independent, reproducible RNG for a subsystem
    def sub(self, namespace: str, *keys: object) -> "Rng":
        derived = int.from_bytes(_digest(self.seed, namespace, keys), "big")
        return Rng(derived % (2**63))

    # -- plumb through the std-lib surface we use
    @property
    def random(self) -> random.Random:
        return self._rand

    def uniform(self, a: float, b: float) -> float:
        return self._rand.uniform(a, b)

    def randint(self, a: int, b: int) -> int:
        return self._rand.randint(a, b)

    def choice(self, seq: Sequence):
        return self._rand.choice(seq)

    def choices(self, population: Sequence, weights: Sequence[float], k: int = 1):
        return self._rand.choices(population, weights=weights, k=k)

    def shuffle(self, seq: list) -> None:
        self._rand.shuffle(seq)

    def gauss(self, mu: float, sigma: float) -> float:
        return self._rand.gauss(mu, sigma)

    def lognormal(self, median: float, sigma: float) -> float:
        """Log-normal with an explicit *median* (= exp(mu)); sigma is log-space spread."""
        import math

        return median * math.exp(self._rand.gauss(0.0, sigma))

    def chance(self, p: float) -> bool:
        return self._rand.random() < p

    # -- deterministic IDs ------------------------------------------------
    def trace_id(self, *keys: object) -> str:
        return _digest(self.seed, "trace", keys).hex()  # 32 hex chars

    def obs_id(self, *keys: object) -> str:
        return _digest(self.seed, "obs", keys).hex()[:16]  # 16 hex chars

    def score_id(self, *keys: object) -> str:
        return _digest(self.seed, "score", keys).hex()[:24]

    def item_id(self, *keys: object) -> str:
        return _digest(self.seed, "item", keys).hex()[:24]


def weighted_pick(rng: Rng, items: Iterable, weights: Iterable[float]):
    items = list(items)
    return rng.choices(items, list(weights), k=1)[0]
