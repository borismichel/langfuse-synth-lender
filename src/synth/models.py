"""Data contracts (spec §16): the analyst question and the copilot's structured answer.

The certification checks adjudicate these *structured fields*, not free prose — pinning
the shapes is what makes the deterministic grading (and therefore the gate) reliable:
``figures`` are exact EUR ints, ``ratios`` are floats graded ±0.02, ``citations`` are
section-id sets, ``answer_type`` is exact. ``answer``/``basis`` are prose for the
managed judges and the human reviewers.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AnswerType = Literal["factual", "declined", "abstained", "escalated"]


class Excerpt(BaseModel):
    """A filing fragment as printed: a unit note plus line items ``[label, printed]``.

    ``printed`` keeps the statement's own notation — comma thousands separators and
    parentheses for negatives — because reading it *as printed* and normalising is
    exactly the skill the certification tests."""

    section_id: str                     # e.g. "fin-2025.is.12" — what the copilot cites
    title: str                          # e.g. "Income statement (extract)"
    unit_note: str = ""                 # e.g. "in EUR thousands" ("" = units of EUR)
    lines: list[tuple[str, str]] = Field(default_factory=list)

    def printed(self, label: str) -> str | None:
        for lab, val in self.lines:
            if lab.lower() == label.lower():
                return val
        return None


class AnalystQuestion(BaseModel):
    """The dataset-item ``input`` and the trace input (spec §16)."""

    case_id: str                        # credit-review case, e.g. "CR-2026-04821"
    borrower: str
    question: str
    excerpts: list[Excerpt] = Field(default_factory=list)

    @classmethod
    def from_input(cls, data: "AnalystQuestion | dict") -> "AnalystQuestion":
        """Coerce whatever ``run_experiment`` hands the task back into an AnalystQuestion."""
        if isinstance(data, AnalystQuestion):
            return data
        return cls.model_validate(data)


class CopilotAnswer(BaseModel):
    """What ``answer()`` returns, what seeded ``answer`` generations emit, and what a
    dataset item's ``expectedOutput`` holds (spec §16)."""

    answer_type: AnswerType
    answer: str
    figures: dict[str, int] = Field(default_factory=dict)    # canonical name -> EUR int
    ratios: dict[str, float] = Field(default_factory=dict)   # e.g. {"dscr": 0.82}
    citations: list[str] = Field(default_factory=list)       # section ids
    basis: str = ""                                          # one-line derivation


# ---------------------------------------------------------------------------
# Printed-figure normalisation — the convention the whole demo hangs on
# ---------------------------------------------------------------------------
def parse_printed(printed: str) -> int:
    """``"(2,431)"`` -> -2431; ``"18,750"`` -> 18750. Statement notation, as printed."""
    s = printed.strip()
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1]
    s = s.replace(",", "").strip()
    value = int(s)
    return -value if negative else value


def unit_multiplier(unit_note: str) -> int:
    """``"in EUR thousands"`` -> 1000; ``"in EUR millions"`` -> 1_000_000; else 1."""
    note = unit_note.lower()
    if "million" in note:
        return 1_000_000
    if "thousand" in note:
        return 1_000
    return 1


def normalised_eur(excerpt: Excerpt, label: str) -> int | None:
    """The figure for ``label`` in EUR (sign + units applied), or None if not printed."""
    printed = excerpt.printed(label)
    if printed is None:
        return None
    return parse_printed(printed) * unit_multiplier(excerpt.unit_note)
