"""Keep generated filing fixtures outside SDK propagation limits.

Dataset storage retains the full structured question. Only the in-memory execution
copy omits it from item metadata, which the SDK automatically propagates (200 chars
per flattened value). The task restores it as ordinary observation metadata on the
SDK's current experiment-item-task observation, before calling the kit task.
"""
from __future__ import annotations

from copy import copy
from typing import Any, Callable

from langfuse import Langfuse
from langfuse._client.datasets import DatasetClient
from langfuse.api import DatasetItem

from .models import AnalystQuestion


def question_from_item(item: DatasetItem) -> AnalystQuestion:
    """Read both hosted chat-shaped items and older structured-input items."""
    metadata = item.metadata or {}
    return AnalystQuestion.from_input(metadata.get("analyst_question") or item.input)


def run_experiment(lf: Langfuse, dataset: DatasetClient, *, task: Callable[..., Any],
                   items: list[DatasetItem] | None = None, **kwargs: Any):
    """Execute a hosted dataset (or selected slice) without propagating filing text.

    Keep SDK DatasetItems, their identities and the DatasetClient's version, so these
    remain hosted experiments. Neither the caller's dataset nor its items are mutated.
    Task callbacks still receive the original input, expected output and metadata.
    """
    originals = {item.id: item for item in (dataset.items if items is None else items)}
    execution = copy(dataset)
    execution.items = [item.model_copy(update={"metadata": {
        key: value for key, value in (item.metadata or {}).items()
        if key != "analyst_question"
    }}) for item in originals.values()]

    def with_evidence(*, item, **task_kwargs):
        original = originals[item.id]
        question = (original.metadata or {}).get("analyst_question")
        if question is not None:
            lf.update_current_span(metadata={"analyst_question": question})
        return task(item=original, **task_kwargs)

    return execution.run_experiment(task=with_evidence, **kwargs)
