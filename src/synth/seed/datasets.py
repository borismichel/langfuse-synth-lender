"""Pre-seed the hosted certification suite (spec v2 §5) — ONE dataset, every item
tagged by scenario type and, where curated from production, carrying a
``source_trace_id`` (ground truth from real traffic, checklist row 2).
"""
from __future__ import annotations

from ..config import Config
from ..content import user_turn
from .certification import Certification, requirement_ids_for


def create_suite(lf, cfg: Config, cert: Certification) -> dict:
    name = cfg.certification.dataset.name
    lf.create_dataset(
        name=name,
        description=(
            "Certification suite for the analyst copilot, curated from annotated "
            "production traces. Scenario types: summary, numeric_lookup, trend, "
            "covenant, out_of_scope. Graded by deterministic assertions "
            "(numeric_accuracy, citation_format, escalation_correctness) plus the "
            "managed judges (groundedness, citation_coverage); per-scenario gates "
            "in the runner config."),
        metadata={"scenario": "mrm-lending-copilot-certification", "seeded": True},
    )
    created = 0
    for it in cert.suite:
        # input = the analyst's turn as a readable chat message (question + retrieved
        # extracts) — what the model saw — NOT the bare AnalystQuestion object. The
        # structured question is preserved in metadata (`analyst_question`) so the
        # deterministic task/grader can still reconstruct figures/excerpts. The system
        # prompt is the thing under test, applied per-run, so it stays OUT of the item.
        lf.create_dataset_item(
            dataset_name=name,
            id=it.item_id,
            input=[{"role": "user", "content": user_turn(it.question)}],
            expected_output=it.expected.model_dump(),
            metadata={"scenario": it.scenario, "slice": it.scenario, "curated": it.curated,
                      "requirement_ids": requirement_ids_for(it.scenario),
                      "analyst_question": it.question.model_dump()},
            source_trace_id=it.source_trace_id,
        )
        created += 1
    return {"name": name, "items_created": created,
            "curated": sum(1 for it in cert.suite if it.curated)}
