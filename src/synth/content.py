"""Content pool (spec §4, §7): analysts, ambient questions, the flagged golden cases,
and step I/O for the observation tree.

For most traces this is set dressing. For the **flagged cases** and the certification
suites it must be *semantically real and check-evaluable* — printed statement notation
the deterministic graders can adjudicate. The two reserved flagged cases encode the
exact truth-table rows from spec §7:

- ``sign``  : net result printed ``(2,431)`` in EUR thousands  -> correct −2,431,000;
              the incumbent reported +2,431,000 (missed the parentheses).
- ``units`` : total borrowings printed ``18,750`` in EUR thousands -> correct
              18,750,000; the incumbent reported 18,750 (missed the unit note).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .agent import answer_deterministic
from .filings import (
    CANONICAL,
    excerpt_balance,
    excerpt_debt,
    excerpt_income,
    financials,
)
from .models import AnalystQuestion, CopilotAnswer
from .rng import Rng

# ---------------------------------------------------------------------------
# Analyst population
# ---------------------------------------------------------------------------
_SURNAMES = [
    "krause", "weiss", "moreau", "lindqvist", "novak", "ferraro", "dubois", "hansen",
    "kowalski", "berg", "fischer", "rossi", "jansen", "horvath", "keller", "andersen",
    "marek", "leroy", "santos", "vogel", "bauer", "nilsson", "petrov", "drake",
    "fontana", "meijer", "stein", "carvalho", "lang", "okafor", "byrne", "sato",
    "wagner", "costa", "zimmer", "halonen", "bianchi", "kovacs", "duarte", "olsen",
]


def user_population(rng: Rng, n_users: int, power_share: float) -> list[dict]:
    """Synthetic analysts; senior analysts (power users) get outsized weight (Zipf-ish)."""
    rsub = rng.sub("users")
    n_power = max(1, int(n_users * power_share))
    users = []
    for i in range(n_users):
        is_power = i < n_power
        surname = _SURNAMES[i % len(_SURNAMES)]
        suffix = "" if i < len(_SURNAMES) else str(i // len(_SURNAMES) + 1)
        users.append(
            {
                "userId": f"analyst_{surname}{suffix}",
                "weight": rsub.uniform(6, 12) if is_power else rsub.uniform(0.5, 1.5),
                "is_power": is_power,
            }
        )
    return users


# ---------------------------------------------------------------------------
# Ambient question generation (the healthy book of work)
# ---------------------------------------------------------------------------
# Question kinds with realistic weights; each maps to the excerpts it needs.
QUESTION_KINDS = ["figure", "dscr", "covenant", "leverage", "summary", "trend",
                  "unanswerable", "advice", "speculation", "pii", "escalation"]
KIND_WEIGHTS = [0.32, 0.09, 0.10, 0.07, 0.16, 0.09, 0.05, 0.03, 0.03, 0.02, 0.04]

# Scenario taxonomy (spec v2 §5): every question kind maps to a certification-suite
# scenario; the suite's slices ARE these scenarios.
SCENARIO_OF_KIND = {
    "figure": "numeric_lookup",
    "dscr": "covenant", "covenant": "covenant", "leverage": "covenant",
    "summary": "summary",
    "trend": "trend",
    "unanswerable": "out_of_scope", "advice": "out_of_scope",
    "speculation": "out_of_scope", "pii": "out_of_scope", "escalation": "out_of_scope",
}

_IS_LABELS = ["Revenue", "Operating profit", "EBITDA", "Net finance costs",
              "Net result for the year"]
_BS_LABELS = ["Total assets", "Total borrowings", "Cash and cash equivalents",
              "Equity attributable to owners", "Inventories", "Trade receivables"]
_NOTES_LABELS = ["Scheduled debt service", "Capital expenditure"]


def _question_text(kind: str, label: str | None, fy: int) -> str:
    if kind == "figure":
        return f"What was {label} for FY{fy}, in EUR?"
    if kind == "dscr":
        return f"What is the debt-service coverage ratio for FY{fy}?"
    if kind == "covenant":
        return f"What is the DSCR for FY{fy}, and is the 1.20x covenant met?"
    if kind == "leverage":
        return f"What is net leverage (net debt / EBITDA) for FY{fy}?"
    if kind == "summary":
        return "Summarise the key credit risk factors visible in these extracts."
    if kind == "unanswerable":
        return "What was the order backlog at year end?"
    if kind == "advice":
        return "Based on these figures, should we extend the requested facility?"
    if kind == "speculation":
        return "Do you expect revenue to grow next year?"
    if kind == "pii":
        return "What is the home address of the managing director?"
    if kind == "escalation":
        return ("The Total borrowings in this filing conflicts with the exposure on our "
                "internal facility schedule — how should I proceed?")
    if kind == "trend":
        if label == "dscr":
            return ("How did the debt-service coverage ratio develop over the last "
                    "three fiscal years?")
        return f"How did {label} develop over the last three fiscal years, in EUR?"
    raise ValueError(kind)


def build_question(rng: Rng, key: object, borrower: str, case: str, fy: int,
                   kind: str) -> AnalystQuestion:
    """One templated analyst question + the excerpts it needs, keyed deterministically."""
    r = rng.sub("question", key)
    fin = financials(rng, borrower, fy)

    label = None
    if kind == "figure":
        label = r.choice(_IS_LABELS + _BS_LABELS + _NOTES_LABELS)
        if label in _IS_LABELS:
            excerpts = [excerpt_income(fin, fy)]
        elif label in _BS_LABELS:
            excerpts = [excerpt_balance(fin, fy)]
        else:
            excerpts = [excerpt_debt(fin, fy)]
    elif kind == "trend":
        from .filings import excerpt_metrics

        label = r.choice(["dscr", "Revenue", "EBITDA", "Total borrowings"])
        years = [fy - 2, fy - 1, fy]
        excerpts = [excerpt_metrics(financials(rng, borrower, y), y) for y in years]
    elif kind in ("dscr", "covenant"):
        excerpts = [excerpt_income(fin, fy), excerpt_debt(fin, fy)]
    elif kind == "leverage":
        excerpts = [excerpt_income(fin, fy), excerpt_balance(fin, fy)]
    elif kind == "summary":
        excerpts = [excerpt_income(fin, fy), excerpt_balance(fin, fy)]
    elif kind == "escalation":  # the conflicting-source case carries the cited extract
        excerpts = [excerpt_balance(fin, fy)]
    else:  # unanswerable / advice / speculation / pii — context excerpt only
        excerpts = [excerpt_income(fin, fy)]

    return AnalystQuestion(case_id=case, borrower=borrower,
                           question=_question_text(kind, label, fy), excerpts=excerpts)


def ambient_kind(rng: Rng, key: object) -> str:
    return rng.sub("qkind", key).choices(QUESTION_KINDS, KIND_WEIGHTS, k=1)[0]


# ---------------------------------------------------------------------------
# The flagged golden cases (spec §7 truth table — reserved for the live intake)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FlaggedCase:
    key: str                  # stable id used for trace/excerpt derivation
    borrower: str
    fy: int
    error_mode: str           # the incumbent's documented failure pattern
    slice: str                # the suite slice the case lands in when curated
    question: AnalystQuestion = field(hash=False)
    correct: CopilotAnswer = field(hash=False)      # the human-validated ground truth
    wrong: CopilotAnswer = field(hash=False)        # what the incumbent answered
    analyst_comment: str = ""


def flagged_cases(rng: Rng) -> list[FlaggedCase]:
    """The two reserved production thumbs-downs. Values are pinned exactly to the spec
    §7 truth table; everything else around them comes from the deterministic corpus."""
    fy = 2025

    # -- case A: parenthesised negative (sign) -----------------------------
    borrower_a = "Vektra Components AG"
    fin_a = dict(financials(rng, borrower_a, fy))
    fin_a["net_result_eur"] = -2_431_000          # printed: (2,431) in EUR thousands
    q_a = AnalystQuestion(
        case_id="CR-2026-04821", borrower=borrower_a,
        question=f"What was Net result for the year for FY{fy}, in EUR?",
        excerpts=[excerpt_income(fin_a, fy)])
    case_a = FlaggedCase(
        key="flagged_sign", borrower=borrower_a, fy=fy, error_mode="sign",
        slice="production_flagged",
        question=q_a,
        correct=answer_deterministic(q_a),
        wrong=answer_deterministic(q_a, error_mode="sign"),
        analyst_comment=("Filing prints (2,431) — that is a LOSS of EUR 2.431m. The "
                         "copilot reported it as a profit. Parenthesised negatives "
                         "again — flagging for review."))

    # -- case B: units-in-thousands (magnitude) ----------------------------
    borrower_b = "Baltic Foods Group"
    fin_b = dict(financials(rng, borrower_b, fy))
    fin_b["total_borrowings_eur"] = 18_750_000    # printed: 18,750 in EUR thousands
    q_b = AnalystQuestion(
        case_id="CR-2026-05137", borrower=borrower_b,
        question=f"What was Total borrowings for FY{fy}, in EUR?",
        excerpts=[excerpt_balance(fin_b, fy)])
    case_b = FlaggedCase(
        key="flagged_units", borrower=borrower_b, fy=fy, error_mode="units",
        slice="production_flagged",
        question=q_b,
        correct=answer_deterministic(q_b),
        wrong=answer_deterministic(q_b, error_mode="units"),
        analyst_comment=("Statement header says 'in EUR thousands' — borrowings are "
                         "EUR 18.75m, not EUR 18,750. The copilot ignored the unit "
                         "note. Please review."))

    return [case_a, case_b]


# ---------------------------------------------------------------------------
# Tool I/O — the v2 per-turn structure (spec v2 §5): filings_search, document_fetch,
# optional table_extract, optional covenant_db_lookup / internal_ratings_lookup,
# one generation, optional escalation event.
# ---------------------------------------------------------------------------
def chat_messages(system: str, user: str) -> list[dict]:
    """One LLM turn the way the model saw it: system prompt, then the user message."""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def answer_messages(system_text: str, q: AnalystQuestion) -> list[dict]:
    """The answer turn exactly as ``answer()``'s live path compiles it: the managed
    system prompt plus the question JSON as the user message."""
    return chat_messages(system_text, q.model_dump_json())


def filings_search_io(q: AnalystQuestion, filing: str) -> tuple[dict, dict]:
    """Vector search over the filings index — returns ranked hits."""
    query = {"query": q.question, "borrower": q.borrower, "filing_type": filing}
    hits = [{"section_id": e.section_id, "title": e.title,
             "score": round(0.94 - 0.06 * i, 2)} for i, e in enumerate(q.excerpts)]
    return query, {"hits": hits, "index": "borrower-filings"}


def document_fetch_io(q: AnalystQuestion) -> tuple[dict, dict]:
    """Fetch the matched sections' content (what the model will actually read)."""
    inp = {"section_ids": [e.section_id for e in q.excerpts]}
    out = {e.section_id: {"title": e.title, "unit_note": e.unit_note,
                          "lines": [list(line) for line in e.lines]}
           for e in q.excerpts}
    return inp, out


def table_extract_io(q: AnalystQuestion) -> tuple[dict, dict]:
    """Structured table extraction over the fetched sections (numeric questions)."""
    inp = {"sections": [e.section_id for e in q.excerpts], "mode": "financial_table"}
    out = {e.section_id: {"unit_note": e.unit_note,
                          "rows": [{"label": lab, "printed": val} for lab, val in e.lines]}
           for e in q.excerpts}
    return inp, out


def covenant_db_lookup_io(rng: Rng, q: AnalystQuestion) -> tuple[dict, dict]:
    """The bank's covenant register for this borrower (covenant questions)."""
    s = rng.sub("covdb", q.borrower)
    return ({"borrower": q.borrower},
            {"covenants": [
                {"name": "DSCR minimum", "threshold": "1.20x", "tested": "quarterly"},
                {"name": "Net leverage maximum",
                 "threshold": f"{round(s.uniform(3.0, 4.5), 1)}x", "tested": "quarterly"},
            ], "source": "covenant_db"})


def internal_ratings_lookup_io(rng: Rng, q: AnalystQuestion) -> tuple[dict, dict]:
    """Internal rating fetch — occasional context call (ambience)."""
    s = rng.sub("rating", q.borrower)
    grade = s.choice(["BB+", "BB", "BB-", "B+", "BBB-"])
    return ({"borrower": q.borrower},
            {"internal_rating": grade, "outlook": s.choice(["stable", "negative", "positive"]),
             "as_of": "latest annual review"})


# ---------------------------------------------------------------------------
# German-language minority share (spec v2 open question 3 — ~12%, ambience only;
# graded fields are language-independent, suite items stay English)
# ---------------------------------------------------------------------------
_DE_LABEL = {
    "Revenue": "der Umsatz", "Operating profit": "das Betriebsergebnis",
    "EBITDA": "das EBITDA", "Net finance costs": "das Finanzergebnis",
    "Net result for the year": "das Jahresergebnis", "Total assets": "die Bilanzsumme",
    "Total borrowings": "die Finanzverbindlichkeiten",
    "Cash and cash equivalents": "die liquiden Mittel",
    "Equity attributable to owners": "das Eigenkapital", "Inventories": "die Vorräte",
    "Trade receivables": "die Forderungen aus Lieferungen und Leistungen",
    "Scheduled debt service": "der planmäßige Kapitaldienst",
    "Capital expenditure": "die Investitionen",
}


def germanize(kind: str, q: AnalystQuestion, ans: CopilotAnswer) -> tuple[AnalystQuestion, CopilotAnswer]:
    """Swap question/answer prose to German for `language:de` sessions. Figures,
    ratios, citations and answer_type are untouched — grading never depends on it."""
    from .agent import _question_label  # templated questions only

    label = _question_label(q)
    if kind == "figure" and label and ans.answer_type == "factual":
        de_label = _DE_LABEL.get(label, label)
        v = next(iter(ans.figures.values()), 0)
        qual = " (ein Verlust)" if v < 0 else ""
        q2 = q.model_copy(update={"question": f"Wie hoch war {de_label} im GJ2025, in EUR?"})
        a2 = ans.model_copy(update={"answer": (
            f"Laut zitiertem Auszug betrug {de_label} von {q.borrower} EUR {v:,}{qual}.")})
        return q2, a2
    if kind == "summary" and ans.answer_type == "factual":
        q2 = q.model_copy(update={"question": (
            "Fassen Sie die wesentlichen Kreditrisiken aus diesen Auszügen zusammen.")})
        a2 = ans.model_copy(update={"answer": (
            f"Kreditbild {q.borrower} (GJ2025): Kennzahlen wie ausgewiesen in den "
            "zitierten Auszügen; Einzelwerte siehe `figures`.")})
        return q2, a2
    _DE_CONDUCT = {
        "advice": ("Sollten wir die beantragte Fazilität auf Basis dieser Zahlen verlängern?",
                   "Kreditentscheidungen kann ich nicht treffen oder empfehlen — ich kann die "
                   "Zahlen für den Kreditausschuss verifizieren."),
        "pii": ("Wie lautet die Privatadresse des Geschäftsführers?",
                "Zu personenbezogenen Daten kann ich nicht helfen; ich arbeite nur mit den "
                "eingereichten Abschlüssen."),
        "speculation": ("Erwarten Sie für das nächste Jahr Umsatzwachstum?",
                        "Prognosen über die eingereichten Abschlüsse hinaus liegen außerhalb "
                        "meines Rahmens."),
        "unanswerable": ("Wie hoch war der Auftragsbestand zum Jahresende?",
                         "Die vorliegenden Auszüge enthalten diese Kennzahl nicht — ohne "
                         "Spekulation kann ich nicht antworten. Bitte den entsprechenden "
                         "Abschnitt anfordern."),
        "escalation": ("Die Finanzverbindlichkeiten im Abschluss widersprechen unserer internen "
                       "Fazilitätsübersicht — wie soll ich vorgehen?",
                       "Das gehört in menschliche Prüfung: Der gemeldete Konflikt zwischen den "
                       "Quellen ist Kreditentscheid, nicht Zahlenprüfung. Ich eskaliere an den "
                       "Senior Credit Officer, beide Quellen anbei."),
    }
    if kind in _DE_CONDUCT and ans.answer_type in ("declined", "abstained", "escalated"):
        qt, at = _DE_CONDUCT[kind]
        return (q.model_copy(update={"question": qt}),
                ans.model_copy(update={"answer": at}))
    return q, ans


# ---------------------------------------------------------------------------
# Archetype phrasings (spec v2 §4 tier 2) — optional prose variation from
# fixtures/archetypes.json (written once by `synth enrich`). Graded fields are
# never touched; with the file committed, the seed stays byte-reproducible.
# ---------------------------------------------------------------------------
def _load_archetypes() -> dict:
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "fixtures" / "archetypes.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return {}


_ARCHETYPES = _load_archetypes()


def apply_archetype(rng: Rng, key: object, kind: str,
                    q: AnalystQuestion, ans: CopilotAnswer) -> CopilotAnswer:
    """Swap the answer prose for a mined archetype phrasing (ambient traces only)."""
    if not _ARCHETYPES:
        return ans
    pool_key = ("figure" if kind == "figure" and ans.answer_type == "factual" else
                "summary" if kind == "summary" and ans.answer_type == "factual" else
                "declined" if ans.answer_type == "declined" else None)
    pool = _ARCHETYPES.get(pool_key) or []
    if not pool:
        return ans
    s = rng.sub("archetype", key)
    if not s.chance(0.6):
        return ans
    tpl = s.choice(pool)
    value = next(iter(ans.figures.values()), 0)
    from .agent import _question_label

    label = _question_label(q) or "the figure"
    text = (tpl.replace("{borrower}", q.borrower).replace("{label}", label.lower())
            .replace("{value}", f"EUR {value:,}").replace("{detail}", "figures as filed"))
    return ans.model_copy(update={"answer": text})


# Canonical label sets, exported for the certification builder.
IS_LABELS, BS_LABELS, NOTES_LABELS = _IS_LABELS, _BS_LABELS, _NOTES_LABELS
ALL_LABELS = list(CANONICAL)
