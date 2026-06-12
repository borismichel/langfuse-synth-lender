"""The shared deterministic grader: red cells must be exactly the documented errors."""
from synth.agent import answer_deterministic
from synth.grading import grade, item_passes
from synth.models import AnalystQuestion, Excerpt


def _q():
    return AnalystQuestion(
        case_id="CR-2026-00003", borrower="Baltic Foods Group",
        question="What was Net result for the year for FY2025, in EUR?",
        excerpts=[Excerpt(section_id="fin-2025.is.12", title="Income statement (extract)",
                          unit_note="in EUR thousands",
                          lines=[["Net result for the year", "(2,431)"]])])


def test_correct_answer_passes_everything():
    q = _q()
    expected = answer_deterministic(q)
    checks = grade(expected, expected)
    assert all(ok for ok, _ in checks.values())


def test_sign_error_fails_figures_with_reason():
    q = _q()
    expected = answer_deterministic(q)
    wrong = answer_deterministic(q, error_mode="sign")
    ok, detail = grade(expected, wrong)["figure_accuracy"]
    assert not ok and "net_result_eur" in detail
    assert item_passes("numeric_lookup", expected, wrong)[0] is False


def test_miscite_fails_grounded_but_not_figures():
    q = _q()
    expected = answer_deterministic(q)
    wrong = answer_deterministic(q, error_mode="miscite")
    checks = grade(expected, wrong)
    assert checks["figure_accuracy"][0] is True
    assert checks["citation_accuracy"][0] is False
    assert checks["grounded_ok"][0] is False


def test_ratio_tolerance():
    q = AnalystQuestion(
        case_id="CR-2026-00004", borrower="Adler Maschinenbau AG",
        question="What is the debt-service coverage ratio for FY2025?",
        excerpts=[Excerpt(section_id="fin-2025.is.12", title="t", unit_note="in EUR thousands",
                          lines=[["EBITDA", "4,100"], ["Scheduled debt service", "5,000"]])])
    expected = answer_deterministic(q)
    near = expected.model_copy(update={"ratios": {"dscr": expected.ratios["dscr"] + 0.01}})
    far = expected.model_copy(update={"ratios": {"dscr": expected.ratios["dscr"] + 0.05}})
    assert grade(expected, near)["figure_accuracy"][0] is True
    assert grade(expected, far)["figure_accuracy"][0] is False
