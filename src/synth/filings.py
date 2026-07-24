"""The deterministic borrower / filing corpus (spec §4).

Everything an analyst question needs — borrower names, per-(borrower, fiscal-year)
financials, and printed statement extracts — is derived from id-keyed RNG substreams,
so the corpus is byte-reproducible and any module (seeder, dossier, playground) can
re-derive the same filing from the same key.

Figures are generated **in EUR thousands** (the unit the statements print) and exposed
in full EUR; extracts print them statement-style — comma separators, parentheses for
negatives, a ``unit_note`` header. Reading that notation correctly is the certified
skill (spec §7), so the printer here and the conventions in the pinned prompt are two
renderings of the same rule.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import Excerpt
from langfuse_synth_core.rng import Rng


@dataclass(frozen=True)
class Borrower:
    name: str
    sector: str


BORROWERS: list[Borrower] = [
    Borrower("Vektra Components AG", "industrial components"),
    Borrower("Baltic Foods Group", "food processing"),
    Borrower("Meridian Logistics GmbH", "freight & logistics"),
    Borrower("Nordwind Energie SE", "renewable energy"),
    Borrower("Cobalt Fertigungstechnik GmbH", "precision manufacturing"),
    Borrower("Helvetia Packaging AG", "packaging"),
    Borrower("Arcadia Hotels Group", "hospitality"),
    Borrower("Lindenhof Klinikgruppe", "healthcare"),
    Borrower("TerraBau Infrastruktur GmbH", "construction"),
    Borrower("Quantum Textiles BV", "textiles"),
    Borrower("Adler Maschinenbau AG", "machinery"),
    Borrower("Borealis Marine Services", "marine services"),
    Borrower("Castello Beverage Group", "beverages"),
    Borrower("Drava Chemicals d.o.o.", "specialty chemicals"),
    Borrower("Elbtal Pharma GmbH", "pharmaceuticals"),
    Borrower("Fenix Retail Holdings", "retail"),
    Borrower("Gotland Timber AB", "timber & wood products"),
    Borrower("Hanse Print & Media", "print media"),
    Borrower("Istria Agrar Group", "agriculture"),
    Borrower("Juno Datacenters BV", "data centres"),
    Borrower("Kappa Automotive Parts", "automotive supply"),
    Borrower("Ligurian Shipping SpA", "shipping"),
    Borrower("Morava Steelworks a.s.", "steel"),
    Borrower("Numa Robotics GmbH", "robotics"),
    Borrower("Oderwerk Schiffbau", "shipbuilding"),
    Borrower("Pannonia Glass Kft.", "glass manufacturing"),
    Borrower("Quercus Furniture Group", "furniture"),
    Borrower("Rhin Plastics SARL", "plastics"),
    Borrower("Silesia Mining Services", "mining services"),
    Borrower("Tatra Foodstuffs s.r.o.", "food distribution"),
    Borrower("Ural Window Systems", "building products"),
    Borrower("Vasa Paper Mills AB", "pulp & paper"),
    Borrower("Wexford Dairy Co-op", "dairy"),
    Borrower("Xanten Logistics Park", "industrial real estate"),
    Borrower("Ybbs Hydraulik GmbH", "hydraulics"),
    Borrower("Zeeland Offshore BV", "offshore services"),
    Borrower("Aurum Jewellery Group", "consumer goods"),
    Borrower("Bastion Security Systems", "security services"),
    Borrower("Civetta Fashion SpA", "apparel"),
    Borrower("Donau Catering Group", "catering"),
]

# Canonical figure keys (the `figures` dict in CopilotAnswer) per printed label.
CANONICAL: dict[str, str] = {
    "Revenue": "revenue_eur",
    "Operating profit": "operating_profit_eur",
    "EBITDA": "ebitda_eur",
    "Net finance costs": "net_finance_costs_eur",
    "Net result for the year": "net_result_eur",
    "Total assets": "total_assets_eur",
    "Total borrowings": "total_borrowings_eur",
    "Cash and cash equivalents": "cash_eur",
    "Equity attributable to owners": "equity_eur",
    "Inventories": "inventories_eur",
    "Trade receivables": "trade_receivables_eur",
    "Scheduled debt service": "debt_service_eur",
    "Capital expenditure": "capex_eur",
}
LABEL_FOR: dict[str, str] = {v: k for k, v in CANONICAL.items()}


def print_thousands(value_eur: int) -> str:
    """Render a full-EUR figure the way the statement prints it (in EUR thousands):
    comma separators, parentheses for negatives. ``-2_431_000`` -> ``"(2,431)"``."""
    thousands = abs(value_eur) // 1000
    s = f"{thousands:,}"
    return f"({s})" if value_eur < 0 else s


# ---------------------------------------------------------------------------
# Per-(borrower, fiscal year) financials — full EUR, on a 1,000 grid
# ---------------------------------------------------------------------------
def financials(rng: Rng, borrower: str, fy: int) -> dict[str, int]:
    """A coherent small-cap financial profile, keyed by (borrower, fy) — re-derivable
    anywhere. All values are full EUR and multiples of 1,000 (statements print
    thousands). ``net_result_eur`` is negative for ~22% of profiles (the parenthesised
    figure the golden cases exercise); ``net_finance_costs_eur`` is always negative."""
    r = rng.sub("financials", borrower, fy)
    k = 1000  # thousand-grid

    revenue = int(r.uniform(18_000, 420_000)) * k
    ebitda = int(revenue * r.uniform(0.07, 0.19)) // k * k
    op_profit = int(ebitda * r.uniform(0.55, 0.85)) // k * k
    fin_costs = -(int(ebitda * r.uniform(0.10, 0.35)) // k * k or k)
    if r.chance(0.22):
        net_result = -(int(ebitda * r.uniform(0.15, 0.6)) // k * k or k)
    else:
        net_result = int(op_profit * r.uniform(0.5, 0.8)) // k * k

    total_assets = int(revenue * r.uniform(0.7, 1.6)) // k * k
    borrowings = int(ebitda * r.uniform(1.5, 4.5)) // k * k
    cash = int(borrowings * r.uniform(0.08, 0.45)) // k * k
    equity = int(total_assets * r.uniform(0.25, 0.5)) // k * k
    inventories = int(revenue * r.uniform(0.05, 0.2)) // k * k
    receivables = int(revenue * r.uniform(0.08, 0.18)) // k * k
    # debt service sized off EBITDA so DSCR lands in a believable 0.7–2.3 band
    debt_service = int(ebitda / r.uniform(0.7, 2.3)) // k * k or k
    capex = int(revenue * r.uniform(0.02, 0.08)) // k * k

    return {
        "revenue_eur": revenue,
        "operating_profit_eur": op_profit,
        "ebitda_eur": ebitda,
        "net_finance_costs_eur": fin_costs,
        "net_result_eur": net_result,
        "total_assets_eur": total_assets,
        "total_borrowings_eur": borrowings,
        "cash_eur": cash,
        "equity_eur": equity,
        "inventories_eur": inventories,
        "trade_receivables_eur": receivables,
        "debt_service_eur": debt_service,
        "capex_eur": capex,
    }


# ---------------------------------------------------------------------------
# Statement extracts (what the retriever returns / what the item input carries)
# ---------------------------------------------------------------------------
UNIT_NOTE = "in EUR thousands"


def _lines(fin: dict[str, int], keys: list[str]) -> list[tuple[str, str]]:
    return [(LABEL_FOR[key], print_thousands(fin[key])) for key in keys]


def excerpt_income(fin: dict[str, int], fy: int, *, fye: str = "31 December") -> Excerpt:
    return Excerpt(
        section_id=f"fin-{fy}.is.12",
        title=f"Income statement (extract) — year ended {fye} {fy}",
        unit_note=UNIT_NOTE,
        lines=_lines(fin, ["revenue_eur", "operating_profit_eur", "ebitda_eur",
                           "net_finance_costs_eur", "net_result_eur"]),
    )


def excerpt_balance(fin: dict[str, int], fy: int, *, fye: str = "31 December") -> Excerpt:
    return Excerpt(
        section_id=f"fin-{fy}.bs.31",
        title=f"Statement of financial position (extract) — at {fye} {fy}",
        unit_note=UNIT_NOTE,
        lines=_lines(fin, ["total_assets_eur", "total_borrowings_eur", "cash_eur",
                           "equity_eur", "inventories_eur", "trade_receivables_eur"]),
    )


def excerpt_debt(fin: dict[str, int], fy: int) -> Excerpt:
    return Excerpt(
        section_id=f"fin-{fy}.notes.74",
        title=f"Notes — borrowings and debt service (extract), FY{fy}",
        unit_note=UNIT_NOTE,
        lines=_lines(fin, ["total_borrowings_eur", "debt_service_eur", "capex_eur"]),
    )


def excerpt_metrics(fin: dict[str, int], fy: int) -> Excerpt:
    """Key-metrics table (one per fiscal year) — what the trend questions extract from
    across periods (spec v2 golden trace 4)."""
    return Excerpt(
        section_id=f"fin-{fy}.kpi.05",
        title=f"Key financial metrics (extract) — FY{fy}",
        unit_note=UNIT_NOTE,
        lines=_lines(fin, ["revenue_eur", "ebitda_eur", "debt_service_eur",
                           "total_borrowings_eur", "cash_eur"]),
    )


def filing_type(rng: Rng, borrower: str, fy: int) -> str:
    """The filing this session reads from — tag ambience (spec v2 §5 metadata)."""
    return rng.sub("filingtype", borrower, fy).choices(
        ["annual-report", "10-K", "10-Q"], [0.55, 0.25, 0.2], k=1)[0]


def desk_for(borrower: str) -> str:
    """Stable desk assignment per borrower (corporate vs mid-market)."""
    return "corporate" if hash_stable(borrower) % 3 == 0 else "mid-market"


def hash_stable(s: str) -> int:
    import hashlib

    return int.from_bytes(hashlib.blake2b(s.encode(), digest_size=4).digest(), "big")


EXCERPT_BUILDERS = {"is": excerpt_income, "bs": excerpt_balance, "notes": excerpt_debt}


def pick_borrower(rng: Rng, key: object) -> Borrower:
    return rng.sub("borrower", key).choice(BORROWERS)


def case_id(rng: Rng, key: object) -> str:
    return f"CR-2026-{rng.sub('case', key).randint(10_000, 89_999):05d}"
