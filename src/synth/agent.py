"""The single agent function — the only lever is the **model** (spec §7).

``answer(question, model) -> CopilotAnswer`` is the shared spine: seeding, the seeded
certification runs, and the live ``synth certify`` button all route through it, so they
can never drift apart.

Two execution paths, identical contract:

- **Deterministic (seed path, default).** A faithful mirror of the pinned production
  prompt's conventions (spec §17): read printed figures, apply parentheses-negative and
  the unit note, cite section ids, abstain when the extracts don't contain the answer,
  decline advice/PII/speculation. No model call — a 15k-trace seed is free and
  byte-reproducible. An explicit ``error_mode`` injects the documented failure patterns
  (sign / units / wrong_year / miscite / overreach / advice) used for the incumbent's
  flagged cases and the seeded baseline / failed-cert runs — so every red cell in the
  comparison view is exactly the arithmetic this module encodes.

- **Live (demo path).** ``answer(q, model, live=True, lf=..., llm=...)`` fetches the
  pinned ``production`` prompt from Langfuse, calls the *requested model* at
  temperature 0, and parses the structured CopilotAnswer. This is the certification
  task `synth certify --model X` runs — same code for every candidate, model swapped
  by one argument.
"""
from __future__ import annotations

import json
import re

from .filings import CANONICAL
from .models import (
    AnalystQuestion,
    CopilotAnswer,
    Excerpt,
    parse_printed,
    unit_multiplier,
)

ERROR_MODES = ("sign", "units", "wrong_year", "miscite", "overreach", "advice")

_ESCALATION_PAT = re.compile(r"how should i proceed|conflicts? with", re.I)
_ADVICE_PAT = re.compile(r"should we|would you recommend|can we approve|extend the", re.I)
_PII_PAT = re.compile(r"home address|date of birth|salary of|personal", re.I)
_SPECULATION_PAT = re.compile(r"do you expect|will .*(grow|improve|recover)|forecast|next year", re.I)
_DSCR_PAT = re.compile(r"coverage ratio|dscr", re.I)
_LEVERAGE_PAT = re.compile(r"net leverage", re.I)
_SUMMARY_PAT = re.compile(r"^summari[sz]e", re.I)
_FYE_PAT = re.compile(r"ended 31 march (\d{4})", re.I)
_TREND_PAT = re.compile(r"develop(?:ed)? over the last|across the last (?:three|two)", re.I)


# ---------------------------------------------------------------------------
# Deterministic policy — a faithful mirror of prompts/analyst_copilot.txt
# ---------------------------------------------------------------------------
def _fy(q: AnalystQuestion) -> int:
    if q.excerpts:
        m = re.search(r"fin-(\d{4})", q.excerpts[0].section_id)
        if m:
            return int(m.group(1))
    return 2025


def _read(exc: Excerpt, label: str, *, sign_blind: bool, unit_blind: bool) -> int | None:
    printed = exc.printed(label)
    if printed is None:
        return None
    v = parse_printed(printed)
    if sign_blind:
        v = abs(v)
    return v * (1 if unit_blind else unit_multiplier(exc.unit_note))


def _find(q: AnalystQuestion, label: str, *, sign_blind: bool = False,
          unit_blind: bool = False, exclude: Excerpt | None = None) -> tuple[int, Excerpt] | None:
    for exc in q.excerpts:
        if exclude is not None and exc.section_id == exclude.section_id:
            continue
        v = _read(exc, label, sign_blind=sign_blind, unit_blind=unit_blind)
        if v is not None:
            return v, exc
    return None


def _question_label(q: AnalystQuestion) -> str | None:
    """Which printed line the question asks about (templated questions, fixed labels)."""
    text = q.question.lower()
    for label in sorted(CANONICAL, key=len, reverse=True):
        if label.lower() in text:
            return label
    return None


def _select_year_excerpts(q: AnalystQuestion, wrong_year: bool) -> list[Excerpt]:
    """Fiscal-year discipline: if the question names a year-end, only the matching
    excerpt may be read. ``wrong_year`` mirrors the failure of grabbing the other one."""
    m = _FYE_PAT.search(q.question)
    if not m or len(q.excerpts) < 2:
        return q.excerpts
    year = m.group(1)
    matching = [e for e in q.excerpts if f"31 March {year}" in e.title]
    others = [e for e in q.excerpts if f"31 March {year}" not in e.title]
    if not matching:
        return q.excerpts
    return (others or matching) if wrong_year else matching


def _eur(v: int) -> str:
    return f"EUR {v:,}"


def _resolve(q: AnalystQuestion, *, sign_blind: bool = False, unit_blind: bool = False,
             wrong_year: bool = False) -> CopilotAnswer:
    text = q.question
    fy = _fy(q)

    # -- conduct rules first (escalate / decline before any extraction) ----
    if _ESCALATION_PAT.search(text):
        return CopilotAnswer(
            answer_type="escalated",
            answer="This needs human review: the analyst reports case evidence that "
                   "conflicts with the filed extracts, and adjudicating between sources "
                   "is credit judgment, not figure verification. Escalating to the "
                   "senior credit officer with both sources attached.",
            citations=[e.section_id for e in q.excerpts],
            basis="material conflict between sources — beyond verification scope")
    if _PII_PAT.search(text):
        return CopilotAnswer(
            answer_type="declined",
            answer="I can't help with personal data about individuals. I can only work "
                   "with the borrower's filed financial statements.",
            basis="personal-data request — out of scope under the copilot policy")
    if _ADVICE_PAT.search(text):
        return CopilotAnswer(
            answer_type="declined",
            answer="I can't make or recommend credit decisions. I can extract and verify "
                   "the figures so the credit committee can decide.",
            basis="credit-advice request — decisions rest with the analyst/committee")
    if _SPECULATION_PAT.search(text):
        return CopilotAnswer(
            answer_type="declined",
            answer="I can't forecast beyond the filings. The extracts cover historical "
                   "periods only; projections are outside my scope.",
            basis="speculation beyond the filed statements")

    # -- ratios (single-period; trend questions are handled below) -----------
    if _DSCR_PAT.search(text) and not _TREND_PAT.search(text):
        eb = _find(q, "EBITDA", sign_blind=sign_blind, unit_blind=unit_blind)
        ds = _find(q, "Scheduled debt service", sign_blind=sign_blind, unit_blind=unit_blind)
        if not eb or not ds:
            return _abstain(q)
        dscr = round(eb[0] / ds[0], 2)
        covenant = ""
        if "covenant" in text.lower():
            covenant = (" The 1.20x covenant is met." if dscr >= 1.2
                        else " The 1.20x covenant is NOT met (breach).")
        return CopilotAnswer(
            answer_type="factual",
            answer=f"{q.borrower}'s FY{fy} debt-service coverage ratio is {dscr:.2f}x "
                   f"(EBITDA {_eur(eb[0])} / scheduled debt service {_eur(ds[0])})."
                   f"{covenant}",
            figures={"ebitda_eur": eb[0], "debt_service_eur": ds[0]},
            ratios={"dscr": dscr},
            citations=sorted({eb[1].section_id, ds[1].section_id}),
            basis=f"DSCR = EBITDA / scheduled debt service = {eb[0]:,} / {ds[0]:,}")

    if _LEVERAGE_PAT.search(text):
        bo = _find(q, "Total borrowings", sign_blind=sign_blind, unit_blind=unit_blind)
        ca = _find(q, "Cash and cash equivalents", sign_blind=sign_blind, unit_blind=unit_blind)
        eb = _find(q, "EBITDA", sign_blind=sign_blind, unit_blind=unit_blind)
        if not bo or not ca or not eb:
            return _abstain(q)
        lev = round((bo[0] - ca[0]) / eb[0], 2)
        return CopilotAnswer(
            answer_type="factual",
            answer=f"{q.borrower}'s FY{fy} net leverage is {lev:.2f}x "
                   f"(net debt {_eur(bo[0] - ca[0])} / EBITDA {_eur(eb[0])}).",
            figures={"total_borrowings_eur": bo[0], "cash_eur": ca[0], "ebitda_eur": eb[0]},
            ratios={"net_leverage": lev},
            citations=sorted({bo[1].section_id, ca[1].section_id, eb[1].section_id}),
            basis=f"net leverage = (borrowings − cash) / EBITDA = ({bo[0]:,} − {ca[0]:,}) / {eb[0]:,}")

    # -- multi-period trend (spec v2 golden trace 4) --------------------------
    # ("Summarize … across the last three filings" is a summary, not a trend)
    if _TREND_PAT.search(text) and not _SUMMARY_PAT.search(text):
        per_fy: list[tuple[int, Excerpt]] = []
        for exc in q.excerpts:
            m = re.search(r"fin-(\d{4})", exc.section_id)
            if m:
                per_fy.append((int(m.group(1)), exc))
        per_fy.sort(key=lambda x: x[0])
        if not per_fy:
            return _abstain(q)
        if _DSCR_PAT.search(text):
            ratios: dict[str, float] = {}
            figures: dict[str, int] = {}
            cites = []
            for fy_n, exc in per_fy:
                eb = _read(exc, "EBITDA", sign_blind=sign_blind, unit_blind=unit_blind)
                ds = _read(exc, "Scheduled debt service", sign_blind=sign_blind, unit_blind=unit_blind)
                if eb is None or ds is None:
                    continue
                ratios[f"dscr_fy{fy_n}"] = round(eb / ds, 2)
                figures[f"ebitda_eur_fy{fy_n}"] = eb
                figures[f"debt_service_eur_fy{fy_n}"] = ds
                cites.append(exc.section_id)
            if not ratios:
                return _abstain(q)
            series = ", ".join(f"FY{k[-4:]}: {v:.2f}x" for k, v in sorted(ratios.items()))
            direction = ("improving" if list(ratios.values())[-1] >= list(ratios.values())[0]
                         else "deteriorating")
            return CopilotAnswer(
                answer_type="factual",
                answer=f"{q.borrower}'s debt-service coverage trend is {direction}: {series}. "
                       "Each figure is computed from the cited key-metrics extract for that year.",
                figures=figures, ratios=ratios, citations=cites,
                basis="DSCR per fiscal year = EBITDA / scheduled debt service, per cited extract")
        label = _question_label(q)
        if label:
            figures = {}
            cites = []
            for fy_n, exc in per_fy:
                v = _read(exc, label, sign_blind=sign_blind, unit_blind=unit_blind)
                if v is None:
                    continue
                figures[f"{CANONICAL[label]}_fy{fy_n}"] = v
                cites.append(exc.section_id)
            if not figures:
                return _abstain(q)
            series = ", ".join(f"FY{k[-4:]}: {_eur(v)}" for k, v in sorted(figures.items()))
            return CopilotAnswer(
                answer_type="factual",
                answer=f"{q.borrower}'s {label.lower()} over the period: {series}.",
                figures=figures, citations=cites,
                basis=f"{label} read per fiscal year from the cited extracts")
        return _abstain(q)

    # -- summary -------------------------------------------------------------
    if _SUMMARY_PAT.search(text):
        cites = [e.section_id for e in q.excerpts]
        rev = _find(q, "Revenue", sign_blind=sign_blind, unit_blind=unit_blind)
        net = _find(q, "Net result for the year", sign_blind=sign_blind, unit_blind=unit_blind)
        bits = []
        if rev:
            bits.append(f"revenue of {_eur(rev[0])}")
        if net:
            bits.append(f"a net {'loss' if net[0] < 0 else 'profit'} of {_eur(abs(net[0]))}")
        detail = "; ".join(bits) or "the extracted positions"
        return CopilotAnswer(
            answer_type="factual",
            answer=f"Key credit picture for {q.borrower} (FY{fy}): {detail}. "
                   "Figures are as filed in the cited extracts.",
            citations=cites,
            basis="summary restricted to the cited extracts")

    # -- single-figure lookup -------------------------------------------------
    label = _question_label(q)
    if label:
        for exc in _select_year_excerpts(q, wrong_year):
            v = _read(exc, label, sign_blind=sign_blind, unit_blind=unit_blind)
            if v is None:
                continue
            key = CANONICAL[label]
            qual = ""
            if key == "net_result_eur":
                qual = " (a loss)" if v < 0 else " (a profit)"
            printed = exc.printed(label)
            return CopilotAnswer(
                answer_type="factual",
                answer=f"{q.borrower}'s {label.lower()} per the cited extract was {_eur(v)}{qual}.",
                figures={key: v},
                citations=[exc.section_id],
                basis=f"printed as {printed} ({exc.unit_note or 'EUR'}) → {_eur(v)}")
    return _abstain(q)


def _abstain(q: AnalystQuestion) -> CopilotAnswer:
    return CopilotAnswer(
        answer_type="abstained",
        answer="The supplied filing extracts do not contain that figure, so I can't "
               "answer without speculating. Request the relevant statement section.",
        basis="asked-for line item not present in the supplied extracts")


# ---------------------------------------------------------------------------
# Documented failure patterns (the red cells, spec §7) — applied deterministically
# ---------------------------------------------------------------------------
def _apply_post_error(q: AnalystQuestion, correct: CopilotAnswer, mode: str) -> CopilotAnswer:
    fy = _fy(q)
    if mode == "miscite":
        wrong = [f"fin-{fy}.mda.02"]  # a section that was never supplied
        return correct.model_copy(update={"citations": wrong,
                                          "basis": correct.basis + " [cited from memory]"})
    if mode == "overreach" and correct.answer_type == "abstained":
        # Fabricates a figure instead of abstaining — derived deterministically from the
        # first printed line so the wrong answer is stable across runs.
        first = q.excerpts[0] if q.excerpts else None
        seedv = abs(parse_printed(first.lines[0][1])) * unit_multiplier(first.unit_note) if first and first.lines else 9_000_000
        fab = (seedv // 3) // 1000 * 1000 or 1_000_000
        return CopilotAnswer(
            answer_type="factual",
            answer=f"{q.borrower}'s figure was approximately {_eur(fab)}.",
            figures={"order_backlog_eur": fab},
            citations=[first.section_id] if first else [],
            basis="estimated from comparable positions")  # the give-away
    if mode == "advice" and correct.answer_type == "declined":
        return CopilotAnswer(
            answer_type="factual",
            answer=f"Based on the filed figures, extending the facility to {q.borrower} "
                   "appears reasonable.",
            citations=[e.section_id for e in q.excerpts],
            basis="overall assessment of the cited extracts")  # the compliance breach
    return correct


def answer_deterministic(question: "AnalystQuestion | dict",
                         error_mode: str | None = None) -> CopilotAnswer:
    """The seed-path answer: correct under the pinned conventions, or with one
    documented failure pattern injected (``error_mode``)."""
    q = AnalystQuestion.from_input(question)
    if error_mode is not None and error_mode not in ERROR_MODES:
        raise ValueError(f"unknown error_mode {error_mode!r} (expected one of {ERROR_MODES})")
    resolved = _resolve(
        q,
        sign_blind=error_mode == "sign",
        unit_blind=error_mode == "units",
        wrong_year=error_mode == "wrong_year",
    )
    if error_mode in ("miscite", "overreach", "advice"):
        resolved = _apply_post_error(q, resolved, error_mode)
    return resolved


# ---------------------------------------------------------------------------
# Live path — pinned production prompt + the requested model (demo time)
# ---------------------------------------------------------------------------
def _answer_live(q: AnalystQuestion, model: str, *, lf, llm, prompt_name: str) -> CopilotAnswer:
    # cache_ttl_seconds=0: always pull the current `production` version, so the pinned
    # prompt version recorded on the run is exactly what ran.
    from .content import user_turn

    prompt = lf.get_prompt(prompt_name, label="production", type="chat", cache_ttl_seconds=0)
    # the user turn is the analyst's natural-language question with the retrieved
    # extracts attached (RAG-style) — not a JSON dump of the input object.
    turn = user_turn(q)
    messages = prompt.compile(question=turn)

    system = "\n\n".join(m["content"] for m in messages if m.get("role") == "system")
    turns = [m for m in messages if m.get("role") != "system"] or \
        [{"role": "user", "content": turn}]

    chat = [{"role": m.get("role"), "content": m.get("content")} for m in messages]
    with lf.start_as_current_observation(
        as_type="generation", name="answer", model=llm.model, input=chat,
        model_parameters={"temperature": 0, "max_tokens": 700}, prompt=prompt,
    ) as gen:
        result = llm.complete(system=system, messages=turns, temperature=0, max_tokens=700)
        ans = parse_answer(result.text)
        gen.update(output=ans.model_dump(),
                   usage_details={"input": result.input_tokens,
                                  "output": result.output_tokens})
    return ans


def parse_answer(text: str) -> CopilotAnswer:
    """Parse the model's JSON object into a CopilotAnswer, tolerating code fences/prose."""
    raw = _extract_json(text)
    data = json.loads(raw)
    data.setdefault("answer_type", "factual")
    data.setdefault("answer", "")
    figures = {k: int(round(float(v))) for k, v in (data.get("figures") or {}).items()}
    ratios = {k: float(v) for k, v in (data.get("ratios") or {}).items()}
    data["figures"], data["ratios"] = figures, ratios
    data.setdefault("citations", [])
    data.setdefault("basis", "")
    return CopilotAnswer.model_validate(data)


def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object found in model output: {text[:200]!r}")
    return text[start : end + 1]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def answer(
    question: "AnalystQuestion | dict",
    model: str,
    *,
    live: bool = False,
    lf=None,
    llm=None,
    prompt_name: str = "analyst-copilot",
    error_mode: str | None = None,
) -> CopilotAnswer:
    """Return a structured CopilotAnswer. ``model`` is the only lever.

    Default (seed path) is deterministic and model-free (``model`` is recorded, not
    called); ``error_mode`` injects a documented failure pattern. Pass ``live=True``
    with a Langfuse client (``lf``) and an :class:`~synth.llm.LLMClient` (``llm``) to
    run the real agent path used by ``synth certify``; the client owns the resolved
    provider and model.
    """
    q = AnalystQuestion.from_input(question)
    if live:
        if lf is None or llm is None:
            raise ValueError("live=True requires both lf (Langfuse) and llm (LLMClient) clients")
        return _answer_live(q, model, lf=lf, llm=llm, prompt_name=prompt_name)
    return answer_deterministic(q, error_mode=error_mode)
