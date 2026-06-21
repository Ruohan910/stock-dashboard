"""
data_provider.py
=================
Abstract interface for stock data sources.

Why this exists: today we only use Yahoo Finance, but the product spec
calls for swappable data sources later (Macrotrends for historical PE,
a brokerage API for real-time quotes, news sentiment, etc). Every data
source must implement this same interface so the calculation layer and
UI never need to know which provider is being used.

Every fetch method returns a `DataPoint` — value + source + confidence —
never a bare number. This is intentional: yfinance has known data quality
issues (growth rates can be wildly wrong, some fields return None), and
the UI must always be able to show the user where a number came from.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class DataPoint:
    """
    A single fetched value, always carrying its provenance.

    confidence:
        "high"   — direct, reliable field from the source
        "medium" — derived/fallback value, generally trustworthy
        "low"    — fallback that may be inaccurate; show a warning in UI
    """
    value: Optional[float]
    source: str
    confidence: str  # "high" | "medium" | "low"
    note: Optional[str] = None  # extra context, e.g. "capped from 214%"

    @property
    def is_available(self) -> bool:
        return self.value is not None


@dataclass
class CompanyInfo:
    ticker: str
    name: str
    sector: Optional[str]
    current_price: Optional[float]
    market_cap: Optional[float]


@dataclass
class StockFinancials:
    """Bundle of everything the calculation layer needs for one ticker."""
    ticker: str
    info: CompanyInfo

    fcf: DataPoint                  # Free Cash Flow (TTM), absolute $
    growth_rate_1_5: DataPoint      # decimal, e.g. 0.42 = 42%
    current_assets: DataPoint
    total_debt: DataPoint
    total_cash: DataPoint
    shares_outstanding: DataPoint
    beta: DataPoint

    revenue: DataPoint
    net_income: DataPoint
    gross_profit: DataPoint
    shareholders_equity: DataPoint
    total_assets: DataPoint
    eps: DataPoint

    # 5-year trailing series for the Financial Health Screen and for the
    # Data Sources & Confidence sparklines. Each is a list of
    # (year_label, value) tuples, most recent first.
    revenue_history: list
    net_income_history: list
    fcf_history: list
    operating_cashflow_history: list
    current_assets_history: list
    total_debt_history: list
    shares_outstanding_history: list
    beta_history: list  # beta has no real "history" via Yahoo Finance —
                         # see yahoo_provider.py for how this is handled


class DataProvider(ABC):
    """
    Every concrete data source (YahooFinanceProvider, MacrotrendsProvider,
    a future brokerage API, etc.) implements this interface. Calculation
    code and routes should only ever depend on this class, never on a
    specific provider.
    """

    @abstractmethod
    def fetch(self, ticker: str) -> StockFinancials:
        """
        Fetch everything needed for one ticker in a single call.
        Must never raise on missing individual fields — wrap each field
        in a DataPoint with value=None and an explanatory note instead.
        Should raise only if the ticker itself cannot be resolved at all.
        """
        raise NotImplementedError

    @abstractmethod
    def fetch_price_history(self, ticker: str, period: str = "1y", interval: str = "1wk") -> list:
        """
        Fetch OHLC candle history for charting / zone detection.
        Returns a list of dicts, oldest first:
            [{"date": "2025-06-01", "open": .., "high": .., "low": .., "close": ..}, ...]
        period/interval follow yfinance conventions (e.g. period="1y",
        interval="1wk" for weekly candles over the last year).
        """
        raise NotImplementedError
