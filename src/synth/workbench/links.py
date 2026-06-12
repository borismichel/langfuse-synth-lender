"""Deep links into the Langfuse UI — every workbench asset points at its system-of-
record counterpart (dataset, dataset runs, item, trace, prompt, annotation queue,
judge deployments).

URLs are project-scoped (``{base}/project/{projectId}/…``); the project id comes from
``.synth_state.json`` (captured by ``synth seed``'s guardrail). When it's unknown
(dry-run state, never seeded), every helper returns ``""`` and the views render no
link — never a broken one.
"""
from __future__ import annotations

from ..config import Config
from ..state import RunState


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
        return self._p(f"datasets/{dataset_id}") if dataset_id else self.datasets()

    def dataset_runs(self, dataset_id: str) -> str:
        return self._p(f"datasets/{dataset_id}/runs") if dataset_id else self.datasets()

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
