"""Ready-made analyst questions for the playground (plus the model selector).

The first two prefabs are the demo's golden patterns — parenthesised negatives and
units-in-thousands — so the room can ask the exact question production got wrong and
watch the candidate get it right.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..content import flagged_cases
from ..models import AnalystQuestion
from langfuse_synth_core.rng import Rng


@dataclass(frozen=True)
class Prefab:
    key: str
    label: str
    note: str
    question: AnalystQuestion


def build_prefabs(seed: int) -> list[Prefab]:
    rng = Rng(seed)
    cases = flagged_cases(rng)
    from ..content import build_question
    from ..filings import case_id

    q_cov = build_question(rng, "prefab_cov", "Adler Maschinenbau AG",
                           case_id(rng, "prefab_cov"), 2025, "covenant")
    q_ua = build_question(rng, "prefab_ua", "Gotland Timber AB",
                          case_id(rng, "prefab_ua"), 2025, "unanswerable")
    q_adv = build_question(rng, "prefab_adv", "Castello Beverage Group",
                           case_id(rng, "prefab_adv"), 2025, "advice")
    q_esc = build_question(rng, "prefab_esc", "Ligurian Shipping SpA",
                           case_id(rng, "prefab_esc"), 2025, "escalation")

    return [
        Prefab("paren", "Parenthesised negative — the flagged pattern",
               "Net result printed (2,431) in EUR thousands: is it read as a loss?",
               cases[0].question),
        Prefab("units", "Units in thousands — the flagged pattern",
               "Borrowings printed 18,750 under an 'in EUR thousands' header.",
               cases[1].question),
        Prefab("covenant", "DSCR covenant check",
               "Ratio computed from two extracts; 1.20x covenant verdict.",
               q_cov),
        Prefab("unanswerable", "Not in the extracts — must abstain",
               "The order backlog isn't in the supplied extracts.",
               q_ua),
        Prefab("advice", "Credit-advice probe — must decline",
               "Asking for a lending recommendation; the copilot supports, not decides.",
               q_adv),
        Prefab("escalation", "Conflicting sources — must escalate",
               "Filing conflicts with internal case evidence: human-in-the-loop, not adjudication.",
               q_esc),
    ]


def prefabs_by_key(seed: int) -> dict[str, Prefab]:
    return {p.key: p for p in build_prefabs(seed)}
