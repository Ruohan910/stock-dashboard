"""
calculations.py
================
All valuation math, following the LearningAlpha course methodology exactly
(see Stock_Dashboard_Product_Spec_Prompt.md Section 4-5). This module knows
nothing about Yahoo Finance or any other data source — it only consumes
StockFinancials objects from data_provider.py.
"""

from dataclasses import dataclass, field
from typing import Optional


# ── Beta -> Discount rate lookup table ──────────────────────────────────────
BETA_DISCOUNT_TABLE = [
    (0.80, 0.052),
    (1.00, 0.059),
    (1.10, 0.063),
    (1.20, 0.066),
    (1.30, 0.070),
    (1.40, 0.074),
    (1.50, 0.077),
    (float("inf"), 0.081),
]

INFLATION_RATE = 0.04   # Year 11-20 growth assumption
PROJECTION_YEARS = 20


def beta_to_discount_rate(beta: Optional[float]) -> float:
    if beta is None:
        return 0.066  # mid-range default when beta is unavailable
    for threshold, rate in BETA_DISCOUNT_TABLE:
        if beta <= threshold:
            return rate
    return 0.081


@dataclass
class DCFResult:
    intrinsic_value_per_share: Optional[float]
    current_price: Optional[float]
    upside_pct: Optional[float]
    verdict: str  # "UNDERVALUED" | "FAIR VALUE" | "OVERVALUED" | "LOW CONFIDENCE" | "INSUFFICIENT DATA"
    discount_rate: float
    growth_yr_1_5: Optional[float]
    growth_yr_6_10: Optional[float]
    growth_yr_11_20: float
    cash_per_share: Optional[float]
    projection_table: list = field(default_factory=list)  # [(year, fcf, pv), ...]
    missing_inputs: list = field(default_factory=list)
    fcf_warning: Optional[str] = None
    # Auto (Yahoo Finance) values shown alongside any manual override, so the
    # user can always see what the data source originally said.
    growth_yr_1_5_auto: Optional[float] = None
    discount_rate_auto: Optional[float] = None
    growth_yr_1_5_is_override: bool = False
    discount_rate_is_override: bool = False


def calculate_dcf(fin, overrides: Optional[dict] = None) -> DCFResult:
    """
    fin: StockFinancials object from data_provider.py
    overrides: optional dict with manual analyst overrides, e.g.
        {"growth_rate_1_5": 0.25, "discount_rate": 0.07}
        Any key not present (or set to None) falls back to the
        auto-fetched / Beta-derived value. Overrides persist across
        refreshes — this function just applies whatever is passed in.
    """
    overrides = overrides or {}
    missing = []
    fcf = fin.fcf.value

    growth_1_5_auto = fin.growth_rate_1_5.value
    growth_1_5_override = overrides.get("growth_rate_1_5")
    growth_1_5 = growth_1_5_override if growth_1_5_override is not None else growth_1_5_auto

    shares = fin.shares_outstanding.value
    current_assets = fin.current_assets.value
    total_debt = fin.total_debt.value
    beta = fin.beta.value
    current_price = fin.info.current_price

    if fcf is None:
        missing.append("Free Cash Flow")
    if growth_1_5 is None:
        missing.append("Growth rate (Yr 1-5)")
    if shares is None:
        missing.append("Shares outstanding")

    discount_rate_auto = beta_to_discount_rate(beta)
    discount_rate_override = overrides.get("discount_rate")
    discount_rate = discount_rate_override if discount_rate_override is not None else discount_rate_auto
    if beta is None and discount_rate_override is None:
        missing.append("Beta (used default discount rate)")

    if fcf is None or growth_1_5 is None or shares is None:
        return DCFResult(
            intrinsic_value_per_share=None,
            current_price=current_price,
            upside_pct=None,
            verdict="INSUFFICIENT DATA",
            discount_rate=discount_rate,
            growth_yr_1_5=growth_1_5,
            growth_yr_6_10=(growth_1_5 / 2) if growth_1_5 is not None else None,
            growth_yr_11_20=INFLATION_RATE,
            cash_per_share=None,
            missing_inputs=missing,
            growth_yr_1_5_auto=growth_1_5_auto,
            discount_rate_auto=discount_rate_auto,
            growth_yr_1_5_is_override=growth_1_5_override is not None,
            discount_rate_is_override=discount_rate_override is not None,
        )

    growth_6_10 = growth_1_5 / 2
    growth_11_20 = INFLATION_RATE

    projection = []
    total_pv = 0.0
    projected_fcf = fcf

    for year in range(1, PROJECTION_YEARS + 1):
        if year <= 5:
            rate = growth_1_5
        elif year <= 10:
            rate = growth_6_10
        else:
            rate = growth_11_20

        projected_fcf = projected_fcf * (1 + rate)
        pv = projected_fcf / ((1 + discount_rate) ** year)
        total_pv += pv
        projection.append((year, round(projected_fcf, 0), round(pv, 0)))

    cash_per_share = None
    if current_assets is not None and total_debt is not None and shares:
        cash_per_share = (current_assets - total_debt) / shares
    else:
        missing.append("Current assets / Total debt (cash-per-share component skipped)")

    intrinsic_value = (total_pv / shares) + (cash_per_share or 0)

    upside_pct = None
    verdict = "INSUFFICIENT DATA"
    if current_price:
        upside_pct = (intrinsic_value - current_price) / current_price * 100
        if upside_pct > 15:
            verdict = "UNDERVALUED"
        elif upside_pct < -15:
            verdict = "OVERVALUED"
        else:
            verdict = "FAIR VALUE"

    # If the data provider flagged the base-year FCF as anomalous (e.g. a
    # capex spike crushing it near zero/negative), the whole 20-year
    # projection inherits that distortion. Downgrade the verdict rather
    # than presenting a confident UNDERVALUED/OVERVALUED call built on a
    # base figure we already know looks unreliable.
    fcf_warning = fin.fcf.note if fin.fcf.confidence == "low" and fin.fcf.note else None
    if fcf_warning and verdict not in ("INSUFFICIENT DATA",):
        verdict = "LOW CONFIDENCE"

    return DCFResult(
        intrinsic_value_per_share=round(intrinsic_value, 2),
        current_price=current_price,
        upside_pct=round(upside_pct, 1) if upside_pct is not None else None,
        verdict=verdict,
        discount_rate=discount_rate,
        growth_yr_1_5=growth_1_5,
        growth_yr_6_10=growth_6_10,
        growth_yr_11_20=growth_11_20,
        cash_per_share=round(cash_per_share, 2) if cash_per_share is not None else None,
        projection_table=projection,
        missing_inputs=missing,
        fcf_warning=fcf_warning,
        growth_yr_1_5_auto=growth_1_5_auto,
        discount_rate_auto=discount_rate_auto,
        growth_yr_1_5_is_override=growth_1_5_override is not None,
        discount_rate_is_override=discount_rate_override is not None,
    )


@dataclass
class HistoricalPERange:
    low: Optional[float]
    high: Optional[float]
    years_covered: int
    current_pe: Optional[float]
    position_text: str  # human-readable: "near the high end", "mid-range", etc.


def calculate_historical_pe_range(yearly_close_prices: list, current_eps: Optional[float],
                                   current_pe: Optional[float]) -> HistoricalPERange:
    """
    yearly_close_prices: list of (year_label, close_price) tuples, any order.
    current_eps: trailing EPS used for the approximation below.

    This is a simplification, not a precise historical P/E series: Yahoo
    Finance doesn't expose trailing EPS for each historical year for free,
    so we approximate historical P/E as (historical year-end price) /
    (CURRENT trailing EPS) rather than each year's own EPS at the time.
    This means the range reflects "what P/E would this price level imply
    today" rather than "what was the P/E investors actually saw back then"
    — close enough to gauge whether the current multiple looks rich or
    cheap relative to where the stock has traded, but not a substitute for
    a true point-in-time P/E history. The note text says so explicitly.
    """
    if not current_eps or current_eps <= 0 or not yearly_close_prices:
        return HistoricalPERange(None, None, 0, current_pe, "insufficient data")

    implied_pes = [price / current_eps for _, price in yearly_close_prices if price]
    if len(implied_pes) < 2:
        return HistoricalPERange(None, None, 0, current_pe, "insufficient data")

    low, high = round(min(implied_pes), 1), round(max(implied_pes), 1)

    position_text = "insufficient data"
    if current_pe is not None and high > low:
        position_pct = (current_pe - low) / (high - low)
        if position_pct <= 0.25:
            position_text = "near the low end of its historical range"
        elif position_pct <= 0.45:
            position_text = "below mid-range"
        elif position_pct <= 0.65:
            position_text = "mid-range"
        elif position_pct <= 0.85:
            position_text = "above mid-range"
        else:
            position_text = "near the high end of its historical range"

    return HistoricalPERange(
        low=low, high=high, years_covered=len(implied_pes),
        current_pe=current_pe, position_text=position_text,
    )


@dataclass
class MultiplesResult:
    pe_ratio: Optional[float]
    peg_ratio: Optional[float]
    price_fcf_ratio: Optional[float]
    assessment: str
    notes: list = field(default_factory=list)


def calculate_multiples(fin, growth_rate_for_peg: Optional[float] = None) -> MultiplesResult:
    notes = []
    market_cap = fin.info.market_cap
    net_income = fin.net_income.value
    fcf = fin.fcf.value
    growth_for_peg = growth_rate_for_peg if growth_rate_for_peg is not None else fin.growth_rate_1_5.value

    pe_ratio = None
    if market_cap and net_income and net_income > 0:
        pe_ratio = round(market_cap / net_income, 2)
    elif net_income is not None and net_income <= 0:
        notes.append("Net income is negative or zero — P/E not meaningful")

    peg_ratio = None
    if pe_ratio and growth_for_peg and growth_for_peg > 0:
        peg_ratio = round(pe_ratio / (growth_for_peg * 100), 2)

    price_fcf_ratio = None
    if market_cap and fcf and fcf > 0:
        price_fcf_ratio = round(market_cap / fcf, 2)
    elif fcf is not None and fcf <= 0:
        notes.append("FCF is negative or zero — P/FCF not meaningful")

    # Simple heuristic assessment per course methodology
    assessment = "INSUFFICIENT DATA"
    if peg_ratio is not None:
        if peg_ratio < 1.0:
            assessment = "UNDERVALUED (PEG < 1.0)"
        elif peg_ratio < 1.5:
            assessment = "FAIRLY VALUED"
        else:
            assessment = "OVERVALUED (PEG > 1.5)"
    elif pe_ratio is not None:
        if pe_ratio < 20:
            assessment = "REASONABLE (P/E < 20x)"
        else:
            assessment = "ELEVATED (P/E > 20x) — check growth justifies it"

    return MultiplesResult(
        pe_ratio=pe_ratio,
        peg_ratio=peg_ratio,
        price_fcf_ratio=price_fcf_ratio,
        assessment=assessment,
        notes=notes,
    )


@dataclass
class ProfitabilityResult:
    gross_margin_pct: Optional[float]
    net_margin_pct: Optional[float]
    roa_pct: Optional[float]
    roe_pct: Optional[float]
    eps: Optional[float]


def calculate_profitability(fin) -> ProfitabilityResult:
    revenue = fin.revenue.value
    net_income = fin.net_income.value
    gross_profit = fin.gross_profit.value
    total_assets = fin.total_assets.value
    equity = fin.shareholders_equity.value
    eps = fin.eps.value

    gross_margin = round(gross_profit / revenue * 100, 1) if gross_profit and revenue else None
    net_margin = round(net_income / revenue * 100, 1) if net_income is not None and revenue else None
    roa = round(net_income / total_assets * 100, 1) if net_income is not None and total_assets else None
    roe = round(net_income / equity * 100, 1) if net_income is not None and equity else None

    return ProfitabilityResult(
        gross_margin_pct=gross_margin,
        net_margin_pct=net_margin,
        roa_pct=roa,
        roe_pct=roe,
        eps=eps,
    )


@dataclass
class HealthScreenResult:
    verdict: str  # "PASS" | "CONDITIONAL PASS" | "FAIL" | "INSUFFICIENT DATA"
    reasoning: str
    revenue_growing: Optional[bool]
    net_income_growing: Optional[bool]
    fcf_positive_and_growing: Optional[bool]
    ocf_exceeds_net_income: Optional[bool]


def _is_monotonic_increasing(history_oldest_first):
    """history_oldest_first: list of values, oldest to newest."""
    if len(history_oldest_first) < 2:
        return None
    declines = sum(
        1 for i in range(1, len(history_oldest_first))
        if history_oldest_first[i] < history_oldest_first[i - 1]
    )
    # Allow at most one down year out of the series (per course guidance:
    # flag any year of decline, but one blip shouldn't auto-fail a quality company)
    return declines <= 1


def calculate_health_screen(fin) -> HealthScreenResult:
    rev_hist = [v for _, v in reversed(fin.revenue_history)]   # oldest first
    ni_hist = [v for _, v in reversed(fin.net_income_history)]
    fcf_hist = [v for _, v in reversed(fin.fcf_history)]
    ocf_hist = [v for _, v in reversed(fin.operating_cashflow_history)]

    if not rev_hist or not ni_hist:
        return HealthScreenResult(
            verdict="INSUFFICIENT DATA",
            reasoning="Not enough historical data returned by the data provider to run the screen.",
            revenue_growing=None,
            net_income_growing=None,
            fcf_positive_and_growing=None,
            ocf_exceeds_net_income=None,
        )

    revenue_growing = _is_monotonic_increasing(rev_hist)
    net_income_growing = _is_monotonic_increasing(ni_hist)

    fcf_positive_and_growing = None
    if fcf_hist:
        fcf_positive_and_growing = fcf_hist[-1] > 0 and _is_monotonic_increasing(fcf_hist)

    ocf_exceeds_net_income = None
    if ocf_hist and ni_hist:
        ocf_exceeds_net_income = ocf_hist[-1] > ni_hist[-1]

    checks = [revenue_growing, net_income_growing, fcf_positive_and_growing, ocf_exceeds_net_income]
    known_checks = [c for c in checks if c is not None]
    passed = [c for c in known_checks if c]

    if not known_checks:
        verdict = "INSUFFICIENT DATA"
        reasoning = "Could not evaluate — missing historical financial data."
    elif len(passed) == len(known_checks):
        verdict = "PASS"
        reasoning = "Revenue, net income, and FCF all show a consistent growth trend, and cash generation backs up reported earnings."
    elif len(passed) >= len(known_checks) - 1:
        verdict = "CONDITIONAL PASS"
        reasoning = "Most quality checks pass, but at least one area (see flags below) needs a closer look before treating this as a high-conviction quality company."
    else:
        verdict = "FAIL"
        reasoning = "Multiple quality checks failed — revenue, earnings, or cash flow trends do not support the 'great business' bar this course's framework requires."

    return HealthScreenResult(
        verdict=verdict,
        reasoning=reasoning,
        revenue_growing=revenue_growing,
        net_income_growing=net_income_growing,
        fcf_positive_and_growing=fcf_positive_and_growing,
        ocf_exceeds_net_income=ocf_exceeds_net_income,
    )
