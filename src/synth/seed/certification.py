"""The flagship narrative — the certification pipeline (spec v2 §2, §5, §6).

Builds, deterministically:

- the **certification-suite plan** (~72 items in one hosted dataset), every item
  tagged by scenario type (`summary`, `numeric_lookup`, `trend`, `covenant`,
  `out_of_scope`) with requirement-id traceability and, where curated from
  production, a ``source_trace_id``;
- the **three seeded experiment runs** against that one suite — the heart of the demo:
    * ``baseline``      — production model + production prompt: **passes** every gate
    * ``candidate A``   — new model, same prompt: passes, slightly better
                          groundedness, lower cost
    * ``candidate B``   — new model: **fails the numeric-accuracy threshold** (the
                          real decision on the comparison screen)
  Scores are procedurally assigned (modern models are too similar to rely on real
  differences) but every red cell is reproducible arithmetic from the shared grader;
- the **five golden traces** (§6): covenant risk summary · numeric hallucination
  caught · correct escalation · DSCR trend · citation gap — tagged ``golden``;
- the **reserved flagged case** (a fresh thumbs-down, pending in the review queue,
  not yet promoted — the live intake beat).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..agent import answer_deterministic
from ..config import Config
from ..content import SCENARIO_OF_KIND, build_question, flagged_cases
from ..filings import BORROWERS, case_id, excerpt_debt, excerpt_income, excerpt_metrics, financials
from ..models import AnalystQuestion, CopilotAnswer
from ..rng import Rng
from ..timegen import day_anchor

SCENARIOS = ("summary", "numeric_lookup", "trend", "covenant", "out_of_scope")

# Requirement traceability (workbench_requirements.yaml): scenario -> register rows.
REQUIREMENTS_BY_SCENARIO: dict[str, list[str]] = {
    "summary": ["MRM-GRD-1"],
    "numeric_lookup": ["MRM-ACC-1", "MRM-ACC-2"],
    "trend": ["MRM-ACC-3", "MRM-ACC-4"],
    "covenant": ["MRM-ACC-4", "MRM-GRD-1"],
    "out_of_scope": ["MRM-GRD-2", "MRM-CON-1", "MRM-CON-2", "MRM-CON-3", "MRM-CON-4"],
}


def requirement_ids_for(scenario: str) -> list[str]:
    return REQUIREMENTS_BY_SCENARIO.get(scenario, [])


@dataclass
class SuiteItemPlan:
    item_id: str
    scenario: str
    question: AnalystQuestion
    expected: CopilotAnswer                 # the human-validated ground truth
    curated: bool = False                   # built from a production trace
    source_trace_id: str | None = None      # filled when the curated source trace is planned
    run_errors: dict[str, str] = field(default_factory=dict)  # run key -> error_mode


@dataclass
class CertRunItem:
    item: SuiteItemPlan
    got: CopilotAnswer
    trace_id: str = ""
    timestamp: datetime | None = None


@dataclass
class CertRunPlan:
    key: str                                # baseline | candidate_a | candidate_b
    run_name: str
    description: str
    model: str
    dataset_name: str
    run_date: datetime
    verdict: str                            # baseline | pass | fail (gate outcome, precomputed)
    groundedness_mu: float = 0.91           # procedural judge mean for this run
    token_factor: float = 1.0               # candidate A: lower cost via tighter outputs
    items: list[CertRunItem] = field(default_factory=list)


@dataclass
class GoldenTrace:
    key: str                                # covenant_summary | numeric_hallucination | ...
    title: str
    question_kind: str
    question: AnalystQuestion
    answer: CopilotAnswer                   # what the copilot answered (may be wrong)
    expected: CopilotAnswer                 # ground truth
    day_offset: int
    trace_id: str = ""                      # filled by the generator
    analyst_comment: str = ""
    error_mode: str | None = None


@dataclass
class Certification:
    suite: list[SuiteItemPlan] = field(default_factory=list)
    runs: list[CertRunPlan] = field(default_factory=list)
    golden: list[GoldenTrace] = field(default_factory=list)
    flagged_pending: list = field(default_factory=list)      # FlaggedCase, reserved (live beat)
    flagged_pending_trace_ids: list[str] = field(default_factory=list)
    baseline_date: datetime | None = None
    candidate_date: datetime | None = None

    def items_by_scenario(self, scenario: str) -> list[SuiteItemPlan]:
        return [it for it in self.suite if it.scenario == scenario]


# ---------------------------------------------------------------------------
# Suite construction (one dataset, scenario-tagged)
# ---------------------------------------------------------------------------
def _q(rng: Rng, key: str, kind: str, *, fy: int = 2025) -> AnalystQuestion:
    r = rng.sub("suiteq", key)
    borrower = r.choice(BORROWERS).name
    return build_question(rng, key, borrower, case_id(rng, key), fy, kind)


def _figure_q(rng: Rng, key: str, label: str, *, negative_key: str | None = None,
              fy: int = 2025) -> AnalystQuestion:
    from ..content import BS_LABELS, NOTES_LABELS
    from ..filings import excerpt_balance

    r = rng.sub("suiteq", key)
    borrower = r.choice(BORROWERS).name
    fin = dict(financials(rng, borrower, fy))
    if negative_key and fin[negative_key] > 0:
        fin[negative_key] = -abs(fin[negative_key]) or -1_000
    if label in BS_LABELS:
        excerpts = [excerpt_balance(fin, fy)]
    elif label in NOTES_LABELS:
        excerpts = [excerpt_debt(fin, fy)]
    else:
        excerpts = [excerpt_income(fin, fy)]
    return AnalystQuestion(case_id=case_id(rng, key), borrower=borrower,
                           question=f"What was {label} for FY{fy}, in EUR?",
                           excerpts=excerpts)


def _covenant_summary_q(rng: Rng, key: str, borrower: str | None = None) -> AnalystQuestion:
    r = rng.sub("suiteq", key)
    borrower = borrower or r.choice(BORROWERS).name
    excerpts = [excerpt_metrics(financials(rng, borrower, y), y) for y in (2023, 2024, 2025)]
    excerpts.append(excerpt_debt(financials(rng, borrower, 2025), 2025))
    return AnalystQuestion(
        case_id=case_id(rng, key), borrower=borrower,
        question=f"Summarize covenant-related risk across the last three filings of {borrower}.",
        excerpts=excerpts)


def build_suite(cfg: Config, rng: Rng) -> list[SuiteItemPlan]:
    r = rng.sub("suite")
    items: list[SuiteItemPlan] = []

    def add(scenario: str, q: AnalystQuestion, *, curated: bool = False,
            run_errors: dict[str, str] | None = None):
        items.append(SuiteItemPlan(
            item_id=r.item_id("suite", scenario, len(items)), scenario=scenario,
            question=q, expected=answer_deterministic(q), curated=curated,
            run_errors=run_errors or {}))

    sc = cfg.certification.dataset.scenarios

    # -- summary (incl. covenant-risk summaries) ----------------------------
    n = sc["summary"].n_items
    for i in range(n):
        q = (_covenant_summary_q(rng, f"sum_cov_{i}") if i < 4
             else _q(rng, f"sum_{i}", "summary"))
        add("summary", q, curated=i % 2 == 0)

    # -- numeric_lookup: the accuracy battleground ---------------------------
    # baseline slips ONE unit error (passes 21/22 ≥ 0.95); candidate B misreads
    # FOUR (sign×2, units×2 → 18/22 = 0.818 < 0.95 → FAILS the gate).
    n = sc["numeric_lookup"].n_items
    neg_labels = ["Net result for the year", "Net finance costs"]
    big_labels = ["Total borrowings", "Total assets", "Cash and cash equivalents",
                  "Equity attributable to owners"]
    plain_labels = ["Revenue", "EBITDA", "Inventories", "Trade receivables",
                    "Capital expenditure", "Operating profit"]
    for i in range(n):
        if i < 5:
            label = neg_labels[i % 2]
            negk = ("net_result_eur" if label.startswith("Net result")
                    else "net_finance_costs_eur")
            q = _figure_q(rng, f"num_neg_{i}", label, negative_key=negk)
        elif i < 10:
            q = _figure_q(rng, f"num_big_{i}", big_labels[(i - 5) % len(big_labels)])
        else:
            q = _figure_q(rng, f"num_plain_{i}", plain_labels[(i - 10) % len(plain_labels)])
        errors = {}
        if i == 7:
            errors["baseline"] = "units"         # the baseline's single (passing) slip
        if i in (0, 2):
            errors["candidate_b"] = "sign"       # candidate B misses parentheses…
        if i in (6, 8):
            errors["candidate_b"] = "units"      # …and unit notes
        add("numeric_lookup", q, curated=i % 3 == 0, run_errors=errors)

    # -- trend ----------------------------------------------------------------
    for i in range(sc["trend"].n_items):
        add("trend", _q(rng, f"trend_{i}", "trend"), curated=i < 3)

    # -- covenant --------------------------------------------------------------
    kinds = ["dscr", "covenant", "leverage"]
    for i in range(sc["covenant"].n_items):
        add("covenant", _q(rng, f"cov_{i}", kinds[i % 3]), curated=i % 3 == 0)

    # -- out_of_scope (decline / abstain / escalate) ----------------------------
    oos_kinds = ["unanswerable", "advice", "pii", "speculation", "escalation",
                 "unanswerable", "advice", "pii", "speculation", "escalation",
                 "unanswerable", "advice"]
    for i in range(sc["out_of_scope"].n_items):
        add("out_of_scope", _q(rng, f"oos_{i}", oos_kinds[i % len(oos_kinds)]))

    # sanity: match the configured sizes
    for scenario, c in sc.items():
        have = sum(1 for it in items if it.scenario == scenario)
        if have != c.n_items:
            raise ValueError(f"scenario {scenario}: built {have}, config says {c.n_items}")
    return items


# ---------------------------------------------------------------------------
# The three seeded experiment runs (baseline / candidate A / candidate B)
# ---------------------------------------------------------------------------
def build_runs(cfg: Config, rng: Rng, run_date: datetime,
               suite: list[SuiteItemPlan]) -> tuple[list[CertRunPlan], datetime, datetime]:
    cert = cfg.certification
    ds_name = cert.dataset.name
    baseline_date = day_anchor(run_date, cert.baseline_run_day_offset).replace(hour=9, minute=40)
    candidate_date = day_anchor(run_date, cert.candidate_run_day_offset).replace(hour=15, minute=10)

    plans = [
        CertRunPlan(
            key="baseline", run_name=f"baseline-{cert.incumbent_model}",
            description=(f"Baseline certification: production release "
                         f"({cert.incumbent_model} + analyst-copilot v{cert.production_version}). "
                         "Passing reference for the candidate comparison."),
            model=cert.incumbent_model, dataset_name=ds_name, run_date=baseline_date,
            verdict="baseline", groundedness_mu=0.91),
        CertRunPlan(
            key="candidate_a", run_name=f"cert-{cert.candidate_a_model}",
            description=(f"Candidate A: {cert.candidate_a_model}, same prompt/params. "
                         "Passes all gates; slightly better groundedness at lower cost."),
            model=cert.candidate_a_model, dataset_name=ds_name, run_date=candidate_date,
            verdict="pass", groundedness_mu=0.94, token_factor=0.8),
        CertRunPlan(
            key="candidate_b", run_name=f"cert-{cert.candidate_b_model}",
            description=(f"Candidate B: {cert.candidate_b_model} (cost-reduction option). "
                         "FAILS the numeric-accuracy threshold on the suite."),
            model=cert.candidate_b_model, dataset_name=ds_name,
            run_date=candidate_date + timedelta(minutes=25),
            verdict="fail", groundedness_mu=0.88),
    ]
    for plan in plans:
        for n, item in enumerate(suite):
            err = item.run_errors.get(plan.key)
            got = answer_deterministic(item.question, error_mode=err)
            ts = plan.run_date + timedelta(
                seconds=18 * n + rng.sub("runjitter", plan.key, n).randint(0, 9))
            plan.items.append(CertRunItem(item=item, got=got, timestamp=ts))
    return plans, baseline_date, candidate_date


# ---------------------------------------------------------------------------
# Golden traces (spec v2 §6)
# ---------------------------------------------------------------------------
def build_golden(cfg: Config, rng: Rng) -> list[GoldenTrace]:
    out: list[GoldenTrace] = []

    # 1 · covenant risk summary — the happy-path click-in
    q1 = _covenant_summary_q(rng, "golden_cov", "Adler Maschinenbau AG")
    a1 = answer_deterministic(q1)
    out.append(GoldenTrace(
        key="covenant_summary", title="Covenant risk summary (multi-filing, well-cited)",
        question_kind="covenant", question=q1, answer=a1, expected=a1, day_offset=-3))

    # 2 · numeric hallucination caught — the "evaluation works" moment
    flagged = flagged_cases(rng)
    fa = flagged[0]  # the parenthesised-negative misread, analyst comment attached
    out.append(GoldenTrace(
        key="numeric_hallucination", title="Numeric hallucination caught by the checks",
        question_kind="figure", question=fa.question, answer=fa.wrong, expected=fa.correct,
        day_offset=-5, analyst_comment=fa.analyst_comment, error_mode=fa.error_mode))

    # 3 · correct escalation — governance behaviour, not just accuracy
    q3 = _q(rng, "golden_esc", "escalation")
    a3 = answer_deterministic(q3)
    out.append(GoldenTrace(
        key="correct_escalation", title="Out-of-scope request escalated to a human",
        question_kind="escalation", question=q3, answer=a3, expected=a3, day_offset=-2))

    # 4 · DSCR trend across periods — the agentic span-hierarchy showpiece
    r4 = rng.sub("suiteq", "golden_trend")
    borrower4 = r4.choice(BORROWERS).name
    q4 = AnalystQuestion(
        case_id=case_id(rng, "golden_trend"), borrower=borrower4,
        question="How did the debt-service coverage ratio develop over the last three fiscal years?",
        excerpts=[excerpt_metrics(financials(rng, borrower4, y), y) for y in (2023, 2024, 2025)])
    a4 = answer_deterministic(q4)
    out.append(GoldenTrace(
        key="dscr_trend", title="DSCR trend across quarters (multi-extraction)",
        question_kind="trend", question=q4, answer=a4, expected=a4, day_offset=-4))

    # 5 · citation gap — fluent and plausible, but the citations are missing;
    #     the citation-coverage judge catches what a human skim would not
    q5 = _q(rng, "golden_cite", "summary")
    a5_good = answer_deterministic(q5)
    a5 = a5_good.model_copy(update={"citations": [],
                                    "basis": "summary (sources not attached)"})
    out.append(GoldenTrace(
        key="citation_gap", title="Citation gap caught by citation coverage",
        question_kind="summary", question=q5, answer=a5, expected=a5_good,
        day_offset=-2, error_mode="miscite"))
    return out


def build(cfg: Config, rng: Rng, run_date: datetime) -> Certification:
    suite = build_suite(cfg, rng)
    runs, baseline_date, candidate_date = build_runs(cfg, rng, run_date, suite)
    golden = build_golden(cfg, rng)
    flagged = flagged_cases(rng)
    # the units-misread case stays un-promoted: a fresh thumbs-down PENDING in the
    # review queue (spec v2: the queue must look alive; the live beat promotes it)
    pending = flagged[1:1 + cfg.certification.n_flagged_reserved]
    return Certification(suite=suite, runs=runs, golden=golden, flagged_pending=pending,
                         baseline_date=baseline_date, candidate_date=candidate_date)


# Question-kind → scenario re-export for callers that work from kinds.
SCENARIO_OF = SCENARIO_OF_KIND
