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
from langfuse_synth_core.rng import Rng

# ---------------------------------------------------------------------------
# Analyst population — language is a property of the ANALYST: German-named users
# work in German, their sessions are fully German, every trace in them is tagged
# `language:de`. No session or user ever mixes languages.
# ---------------------------------------------------------------------------
_SURNAMES_DE = [
    "krause", "weiss", "fischer", "vogel", "bauer", "wagner", "zimmer", "stein",
    "keller", "lang", "hoffmann", "schreiber",
]
_SURNAMES_INTL = [
    "moreau", "lindqvist", "novak", "ferraro", "dubois", "hansen", "kowalski",
    "berg", "rossi", "jansen", "horvath", "andersen", "marek", "leroy", "santos",
    "nilsson", "petrov", "drake", "fontana", "meijer", "carvalho", "okafor",
    "byrne", "sato", "costa", "halonen", "bianchi", "kovacs", "duarte", "olsen",
]


def user_population(rng: Rng, n_users: int, power_share: float,
                    german_share: float = 0.0) -> list[dict]:
    """Synthetic analysts; senior analysts (power users) get outsized weight (Zipf-ish).
    ``german_share`` of analysts are German-speaking (German surnames, language 'de')."""
    rsub = rng.sub("users")
    n_power = max(1, int(n_users * power_share))
    n_german = int(round(n_users * german_share))
    # German analysts sit outside the Zipf-heavy senior block — otherwise one heavy
    # user pushes the German trace share far past the configured band
    pool = max(1, n_users - n_power)
    german_idx = ({n_power + int(i * pool / n_german) for i in range(n_german)}
                  if n_german else set())
    users = []
    de_i = intl_i = 0
    for i in range(n_users):
        is_power = i < n_power
        german = i in german_idx
        if german:
            surname = _SURNAMES_DE[de_i % len(_SURNAMES_DE)]
            suffix = "" if de_i < len(_SURNAMES_DE) else str(de_i // len(_SURNAMES_DE) + 1)
            de_i += 1
        else:
            surname = _SURNAMES_INTL[intl_i % len(_SURNAMES_INTL)]
            suffix = "" if intl_i < len(_SURNAMES_INTL) else str(intl_i // len(_SURNAMES_INTL) + 1)
            intl_i += 1
        users.append(
            {
                "userId": f"analyst_{surname}{suffix}",
                "weight": rsub.uniform(6, 12) if is_power else rsub.uniform(0.5, 1.5),
                "is_power": is_power,
                "language": "de" if german else "en",
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


def format_excerpts(q: AnalystQuestion) -> str:
    """The retrieved filing extracts as a readable text block (what RAG injects into
    the turn) — NOT a JSON dump. Lines render as printed in the statement."""
    blocks = []
    for e in q.excerpts:
        head = f"[{e.section_id}] {e.title}"
        if e.unit_note:
            head += f" ({e.unit_note})"
        rows = "\n".join(f"  {lab}: {val}" for lab, val in e.lines)
        blocks.append(head + ("\n" + rows if rows else ""))
    return "\n\n".join(blocks)


def user_turn(q: AnalystQuestion) -> str:
    """One analyst turn as a human-readable message: the natural-language question
    with the retrieved extracts attached as context (the RAG turn the copilot saw)."""
    extracts = format_excerpts(q)
    if not extracts:
        return q.question
    return f"{q.question}\n\n— Retrieved filing extracts —\n{extracts}"


def answer_messages(system_text: str, q: AnalystQuestion,
                    history: list | None = None) -> list[dict]:
    """The answer turn as a real chat: system prompt, the prior turns of this case
    review threaded as user/assistant messages, then the current natural-language
    user turn (question + retrieved extracts). ``history`` is a list of
    ``(prev_question, prev_answer)`` for earlier turns in the same session — so a
    multi-turn session reads as a progressing conversation and the rendered history
    matches the per-turn context-token growth.
    """
    msgs = [{"role": "system", "content": system_text}]
    for prev_q, prev_ans in (history or []):
        msgs.append({"role": "user", "content": prev_q.question})
        msgs.append({"role": "assistant", "content": prev_ans.answer})
    msgs.append({"role": "user", "content": user_turn(q)})
    return msgs


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
    # ratio + trend kinds — covered so a German session NEVER mixes languages mid-chat
    if kind in ("dscr", "covenant", "leverage") and ans.answer_type == "factual" and ans.ratios:
        if "dscr" in ans.ratios:
            v = ans.ratios["dscr"]
            cov = ""
            if "covenant" in q.question.lower():
                cov = (" Der 1,20x-Covenant ist eingehalten." if v >= 1.2
                       else " Der 1,20x-Covenant ist NICHT eingehalten (Bruch).")
            qt = ("Wie hoch ist die Kapitaldienstdeckung (DSCR) im GJ2025, und ist der "
                  "1,20x-Covenant eingehalten?" if cov else
                  "Wie hoch ist die Kapitaldienstdeckung (DSCR) im GJ2025?")
            at = (f"Die Kapitaldienstdeckung von {q.borrower} beträgt im GJ2025 "
                  f"{v:.2f}x (EBITDA / planmäßiger Kapitaldienst, gemäß zitierten "
                  f"Auszügen).{cov}")
        else:
            v = ans.ratios.get("net_leverage", 0)
            qt = "Wie hoch ist die Nettoverschuldung im Verhältnis zum EBITDA im GJ2025?"
            at = (f"Die Nettoverschuldungsquote von {q.borrower} beträgt im GJ2025 "
                  f"{v:.2f}x (Nettoverschuldung / EBITDA, gemäß zitierten Auszügen).")
        return q.model_copy(update={"question": qt}), ans.model_copy(update={"answer": at})
    if kind == "trend" and ans.answer_type == "factual":
        if any(k.startswith("dscr_") for k in ans.ratios):
            series = ", ".join(f"GJ{k[-4:]}: {v:.2f}x" for k, v in sorted(ans.ratios.items()))
            vals = [v for _, v in sorted(ans.ratios.items())]
            richtung = "verbessert" if vals and vals[-1] >= vals[0] else "verschlechtert"
            qt = ("Wie hat sich die Kapitaldienstdeckung über die letzten drei "
                  "Geschäftsjahre entwickelt?")
            at = (f"Die Kapitaldienstdeckung von {q.borrower} hat sich {richtung}: "
                  f"{series}. Werte je Geschäftsjahr aus den zitierten Auszügen.")
        else:
            series = ", ".join(f"GJ{k[-4:]}: EUR {v:,}" for k, v in sorted(ans.figures.items()))
            qt = "Wie hat sich die Kennzahl über die letzten drei Geschäftsjahre entwickelt?"
            at = f"Entwicklung bei {q.borrower}: {series} (gemäß zitierten Auszügen)."
        return q.model_copy(update={"question": qt}), ans.model_copy(update={"answer": at})
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
