"""Single source of truth for **target-specific behaviour** (Cloud vs self-hosted).

The kit is cloned per scenario, and each clone is pointed at a different Langfuse
(Cloud, or a self-hosted instance) via ``LANGFUSE_BASE_URL``. Two facts about the
target change what the seeder does — keep both decisions HERE so a clone never grows
its own scattered ``"cloud.langfuse.com" in url`` checks:

1. **Is it Langfuse Cloud?** (URL-derived.) Cloud rate-limits the per-object REST
   writes (dataset-run / annotation-queue items), so we space them out; self-hosted has
   no such limit. This is purely a function of the host, so it lives on ``TargetProfile``.

2. **Does it expose the unstable evaluator API?** (capability-probed, NOT URL-derived —
   see ``workbench.judges.list_judges``.) Cloud and *newer* self-hosted (≥ the unstable
   evaluator release) have it; older self-hosted (e.g. v3.179) does not. We probe rather
   than match the URL because a newer self-hosted host should still take the API path.
   When absent, evaluator/judge/rule creation degrades to logged UI instructions.

Best practice (and the path this kit is tuned for) is **Langfuse Cloud**. See
``CONFIGURATIONS.md`` for the full Cloud-vs-self-hosted matrix and the homogeneous score
model that both paths produce.
"""
from __future__ import annotations

from dataclasses import dataclass

# Both EU (cloud.langfuse.com) and US (us.cloud.langfuse.com) contain this substring.
CLOUD_HOST_MARKER = "cloud.langfuse.com"

# Per-request spacing on the one-at-a-time REST writes, Cloud only (it rate-limits them).
CLOUD_POST_THROTTLE_S = 0.35


@dataclass(frozen=True)
class TargetProfile:
    """URL-derived target facts. Build once with :meth:`detect` and pass it around."""

    base_url: str
    is_cloud: bool
    post_throttle_s: float

    @classmethod
    def detect(cls, base_url: str) -> "TargetProfile":
        url = (base_url or "").rstrip("/")
        is_cloud = CLOUD_HOST_MARKER in url
        return cls(base_url=url, is_cloud=is_cloud,
                   post_throttle_s=CLOUD_POST_THROTTLE_S if is_cloud else 0.0)

    @property
    def label(self) -> str:
        return "Langfuse Cloud" if self.is_cloud else "self-hosted Langfuse"


def post_throttle_seconds(base_url: str) -> float:
    """Convenience: per-object REST write spacing for this target (0 off-Cloud)."""
    return TargetProfile.detect(base_url).post_throttle_s
