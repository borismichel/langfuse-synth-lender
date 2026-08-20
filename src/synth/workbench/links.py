"""Deep links into the Langfuse UI — every workbench asset points at its system-of-
record counterpart (dataset, dataset runs, item, trace, prompt, annotation queue,
judge deployments).

URLs are project-scoped (``{base}/project/{projectId}/…``); the project id comes from
``.synth_state.json`` (captured by ``synth seed``'s guardrail). When it's unknown
(dry-run state, never seeded), every helper returns ``""`` and the views render no
link — never a broken one.

**A deep link is a delivery surface, so the route set is asserted, not assumed** (portal
#212). v4 reorganised the Langfuse UI, and a link that 404s does it in front of a customer
rather than in CI. :data:`ROUTES` is the set of route templates this kit is allowed to
build, each one checked against the v4 app's own routing; ``tests/test_deep_links.py``
fails any helper that leaves it. Two of them were wrong before this was written:

* ``datasets/{id}/runs`` has no page — dataset runs are **experiments** under v4 and live
  at ``datasets/{id}/experiments``;
* ``datasets/{id}`` is an alias that redirects to the Items tab, so a link meaning "the
  dataset" says ``items`` and means it.
"""
from __future__ import annotations

from ..config import Config
from ..state import RunState

#: Every project-scoped route this kit may link to, as templates with ``{}`` placeholders.
#: Sorted by the UI section they belong to.
ROUTES = frozenset({
    "",                                 # the project home
    "traces",
    "traces/{}",
    "sessions",
    "scores",
    "datasets",
    "datasets/{}/items",                # the dataset (its Items tab; the bare id redirects)
    "datasets/{}/experiments",          # runs — renamed to experiments under v4
    "datasets/{}/items/{}",
    "prompts",
    "prompts/{}",
    "annotation-queues",
    "annotation-queues/{}",
    "evals",
})


class Links:
    def __init__(self, base_url: str, project_id: str):
        self.base = base_url.rstrip("/")
        self.pid = project_id

    @classmethod
    def from_cfg(cls, cfg: Config) -> "Links":
        pid = ""
        if RunState.exists():
            try:
                pid = RunState.load().project_id or ""
            except Exception:  # noqa: BLE001
                pid = ""
        return cls(cfg.target.base_url, pid)

    @staticmethod
    def upgrade(url: str) -> str:
        """Rewrite a URL built by a pre-v4 version of this kit onto its v4 route.

        Workbench run records store the runs URL they were given at run time, and the
        workbench renders that stored value rather than rebuilding it (it has to: the
        catalog may be offline). A record written before the cutover therefore carries
        ``datasets/{id}/runs``, which has no page under v4 — so stored links are repaired
        on the way out instead of being handed to a presenter as they were saved."""
        if not url:
            return url
        base, sep, tail = url.partition("/datasets/")
        if not sep or not tail.endswith("/runs"):
            return url
        return f"{base}/datasets/{tail[:-len('/runs')]}/experiments"

    # -- helpers ------------------------------------------------------------
    def _p(self, suffix: str) -> str:
        if not self.pid:
            return ""
        return f"{self.base}/project/{self.pid}/{suffix.lstrip('/')}"

    def project(self) -> str:
        return self._p("")

    def datasets(self) -> str:
        return self._p("datasets")

    def dataset(self, dataset_id: str) -> str:
        """The dataset, on its Items tab — the bare ``datasets/{id}`` is an alias that
        redirects there, and a link is clearer when it names the page it lands on."""
        return self._p(f"datasets/{dataset_id}/items") if dataset_id else self.datasets()

    def dataset_runs(self, dataset_id: str) -> str:
        """The dataset's runs. Under v4 a dataset run is an **experiment** and the runs
        list lives on the Experiments tab; ``datasets/{id}/runs`` has no page at all (only
        ``runs/{runId}``), so the old link 404'd."""
        return self._p(f"datasets/{dataset_id}/experiments") if dataset_id else self.datasets()

    def dataset_item(self, dataset_id: str, item_id: str) -> str:
        if dataset_id and item_id:
            return self._p(f"datasets/{dataset_id}/items/{item_id}")
        return self.dataset(dataset_id)

    def trace(self, trace_id: str) -> str:
        return self._p(f"traces/{trace_id}") if trace_id else ""

    def prompt(self, name: str) -> str:
        return self._p(f"prompts/{name}") if name else self._p("prompts")

    def queues(self) -> str:
        return self._p("annotation-queues")

    def queue(self, queue_id: str) -> str:
        return self._p(f"annotation-queues/{queue_id}") if queue_id else self.queues()

    def evals(self) -> str:
        """Judge / evaluation-rule deployments live under the project's evals section."""
        return self._p("evals")
