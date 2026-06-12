"""The spec §7 truth table: the exact cases that produce the demo."""
from synth.agent import answer_deterministic
from synth.models import AnalystQuestion, Excerpt, parse_printed, unit_multiplier


def _q(question, lines, unit_note="in EUR thousands", section="fin-2025.is.12"):
    return AnalystQuestion(
        case_id="CR-2026-00001", borrower="Vektra Components AG", question=question,
        excerpts=[Excerpt(section_id=section, title="Income statement (extract) — year ended 31 December 2025",
                          unit_note=unit_note, lines=lines)])


def test_printed_notation():
    assert parse_printed("(2,431)") == -2431
    assert parse_printed("18,750") == 18750
    assert unit_multiplier("in EUR thousands") == 1000
    assert unit_multiplier("") == 1


def test_parenthesised_negative_is_the_flagged_pattern():
    q = _q("What was Net result for the year for FY2025, in EUR?",
           [["Net result for the year", "(2,431)"]])
    correct = answer_deterministic(q)
    wrong = answer_deterministic(q, error_mode="sign")
    assert correct.figures["net_result_eur"] == -2_431_000
    assert "loss" in correct.answer
    assert wrong.figures["net_result_eur"] == 2_431_000  # the incumbent's error


def test_units_in_thousands_is_the_flagged_pattern():
    q = _q("What was Total borrowings for FY2025, in EUR?",
           [["Total borrowings", "18,750"]], section="fin-2025.bs.31")
    correct = answer_deterministic(q)
    wrong = answer_deterministic(q, error_mode="units")
    assert correct.figures["total_borrowings_eur"] == 18_750_000
    assert wrong.figures["total_borrowings_eur"] == 18_750


def test_control_passes_both():
    q = _q("What was Cash and cash equivalents for FY2025, in EUR?",
           [["Cash and cash equivalents", "7,200"]])
    assert answer_deterministic(q).figures["cash_eur"] == 7_200_000


def test_dscr_and_covenant():
    q = _q("What is the DSCR for FY2025, and is the 1.20x covenant met?",
           [["EBITDA", "4,100"], ["Scheduled debt service", "5,000"]])
    a = answer_deterministic(q)
    assert a.ratios["dscr"] == 0.82
    assert "NOT met" in a.answer
    assert a.figures == {"ebitda_eur": 4_100_000, "debt_service_eur": 5_000_000}


def test_unanswerable_abstains_and_overreach_fabricates():
    q = _q("What was the order backlog at year end?", [["Revenue", "84,310"]])
    assert answer_deterministic(q).answer_type == "abstained"
    over = answer_deterministic(q, error_mode="overreach")
    assert over.answer_type == "factual" and over.figures  # the failure pattern


def test_conduct_rules():
    ctx = [["Revenue", "84,310"]]
    advice = _q("Based on these figures, should we extend the requested facility?", ctx)
    pii = _q("What is the home address of the managing director?", ctx)
    spec = _q("Do you expect revenue to grow next year?", ctx)
    for q in (advice, pii, spec):
        assert answer_deterministic(q).answer_type == "declined"
    breach = answer_deterministic(advice, error_mode="advice")
    assert breach.answer_type == "factual"  # the compliance failure pattern


def test_escalation_on_conflicting_sources():
    q = _q("The Total borrowings in this filing conflicts with the exposure on our "
           "internal facility schedule — how should I proceed?",
           [["Total borrowings", "18,750"]], section="fin-2025.bs.31")
    a = answer_deterministic(q)
    assert a.answer_type == "escalated"
    assert "senior credit officer" in a.answer
    assert a.citations == ["fin-2025.bs.31"]


def test_fiscal_year_discipline():
    q = AnalystQuestion(
        case_id="CR-2026-00002", borrower="Adler Maschinenbau AG",
        question="What was Revenue for the fiscal year ended 31 March 2025, in EUR?",
        excerpts=[
            Excerpt(section_id="fin-2025.is.12",
                    title="Income statement (extract) — year ended 31 March 2025",
                    unit_note="in EUR thousands", lines=[["Revenue", "100,000"]]),
            Excerpt(section_id="fin-2024.is.12",
                    title="Income statement (extract) — year ended 31 March 2024",
                    unit_note="in EUR thousands", lines=[["Revenue", "90,000"]]),
        ])
    right = answer_deterministic(q)
    wrong = answer_deterministic(q, error_mode="wrong_year")
    assert right.figures["revenue_eur"] == 100_000_000 and right.citations == ["fin-2025.is.12"]
    assert wrong.figures["revenue_eur"] == 90_000_000 and wrong.citations == ["fin-2024.is.12"]


def test_dscr_trend_across_years():
    q = AnalystQuestion(
        case_id="CR-2026-00005", borrower="Ligurian Shipping SpA",
        question="How did the debt-service coverage ratio develop over the last three fiscal years?",
        excerpts=[
            Excerpt(section_id=f"fin-{fy}.kpi.05", title=f"Key financial metrics — FY{fy}",
                    unit_note="in EUR thousands",
                    lines=[["EBITDA", printed_eb], ["Scheduled debt service", printed_ds]])
            for fy, printed_eb, printed_ds in
            [(2023, "3,000", "3,000"), (2024, "3,600", "3,000"), (2025, "4,100", "3,000")]
        ])
    a = answer_deterministic(q)
    assert a.ratios == {"dscr_fy2023": 1.0, "dscr_fy2024": 1.2, "dscr_fy2025": 1.37}
    assert "improving" in a.answer
    assert a.citations == ["fin-2023.kpi.05", "fin-2024.kpi.05", "fin-2025.kpi.05"]
    # unit blindness cancels in the ratio but corrupts the per-year components
    wrong = answer_deterministic(q, error_mode="units")
    assert wrong.figures["ebitda_eur_fy2025"] == 4_100  # vs 4,100,000


def test_covenant_summary_is_a_summary_not_a_trend():
    q = AnalystQuestion(
        case_id="CR-2026-00006", borrower="Adler Maschinenbau AG",
        question="Summarize covenant-related risk across the last three filings of Adler Maschinenbau AG.",
        excerpts=[Excerpt(section_id="fin-2025.kpi.05", title="Key metrics",
                          unit_note="in EUR thousands", lines=[["Revenue", "100,000"]])])
    a = answer_deterministic(q)
    assert a.answer_type == "factual"
    assert a.citations == ["fin-2025.kpi.05"]


def test_answer_is_deterministic_and_dict_coercible():
    q = _q("What was Revenue for FY2025, in EUR?", [["Revenue", "84,310"]])
    assert answer_deterministic(q) == answer_deterministic(q.model_dump())


def test_german_sessions_never_mix_languages():
    """Every ambient question kind has a German rendering — a `language:de` session
    can't jump between languages mid-chat (and german_share defaults to 0: all
    language assets are English unless explicitly enabled)."""
    from synth.config import load_config
    from synth.content import QUESTION_KINDS, build_question, germanize
    from synth.rng import Rng

    assert load_config("config/demo.yaml").generation.german_share == 0.0

    rng = Rng(47)
    for kind in QUESTION_KINDS:
        q = build_question(rng, ("de_cov", kind), "Vektra Components AG",
                           "CR-2026-09999", 2025, kind)
        ans = answer_deterministic(q)
        q2, a2 = germanize(kind, q, ans)
        assert a2.answer != ans.answer or q2.question != q.question, kind
        # graded fields untouched
        assert a2.figures == ans.figures and a2.ratios == ans.ratios
        assert a2.citations == ans.citations and a2.answer_type == ans.answer_type
