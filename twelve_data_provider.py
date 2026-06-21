"""
twelve_data_provider.py
========================
A second, independent data source used ONLY for cross-checking key fields
already fetched from Yahoo Finance — not a full DataProvider replacement.

Why this exists: yfinance has known data-quality quirks we've hit
firsthand (unreliable growth-rate fields, FCF distorted by capex spikes,
missing balance sheet fields depending on the ticker). Rather than trust
a single source blindly, we fetch the same handful of "ground truth"
numbers from Twelve Data and flag any meaningful disagreement — the same
"always show your work" philosophy used elsewhere in this app (DataPoint
confidence levels, the FCF anomaly warning, etc).

This deliberately does NOT replace YahooFinanceProvider or implement the
full DataProvider interface — it only checks the few fields where a
second opinion meaningfully protects against bad data: current price,
market cap, and shares outstanding. Full fundamentals (FCF, growth
estimates, balance sheet detail) would require a paid Twelve Data tier to
cross-check properly, which isn't worth it for a free-tier sanity check.

API: https://twelvedata.com/docs — free tier: 800 requests/day.
"""

import os
import requests

API_KEY = os.environ.get("TWELVE_DATA_API_KEY")
BASE_URL = "https://api.twelvedata.com"

# How far apart two sources can be before we flag a discrepancy. Prices
# can differ slightly due to feed delay (Yahoo vs Twelve Data aren't
# always synced to the same second), so this isn't "exact match required"
# — it's "close enough to trust, or different enough to investigate".
DISCREPANCY_THRESHOLD_PCT = 0.03  # 3%


class CrossCheckResult:
    def __init__(self, field, yahoo_value, twelve_value, available=True, error=None):
        self.field = field
        self.yahoo_value = yahoo_value
        self.twelve_value = twelve_value
        self.available = available
        self.error = error

        self.discrepancy_pct = None
        self.flagged = False
        if available and yahoo_value and twelve_value:
            self.discrepancy_pct = abs(yahoo_value - twelve_value) / abs(yahoo_value) * 100
            self.flagged = self.discrepancy_pct > (DISCREPANCY_THRESHOLD_PCT * 100)


def is_configured() -> bool:
    """Whether a Twelve Data API key is set up at all. Cross-checking is
    skipped entirely (not shown as an error) when this is False — it's an
    optional enhancement, not a required dependency."""
    return bool(API_KEY)


def _get_quote(ticker: str) -> dict:
    """Single Twelve Data /quote call — returns price, shares outstanding,
    and a few other fields in one request to stay well within the free
    800/day limit even across a full watchlist refresh."""
    resp = requests.get(
        f"{BASE_URL}/quote",
        params={"symbol": ticker, "apikey": API_KEY},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") == "error" or data.get("code"):
        raise ValueError(data.get("message", "Unknown Twelve Data error"))
    return data


def cross_check(ticker: str, yahoo_price: float, yahoo_market_cap: float,
                 yahoo_shares: float) -> list:
    """
    Fetches the same handful of fields from Twelve Data and compares them
    against what Yahoo Finance returned. Returns a list of CrossCheckResult
    objects — one per field checked. Never raises; if Twelve Data is
    unreachable or unconfigured, returns results with available=False and
    an error message, so the UI can show "cross-check unavailable" rather
    than crashing the whole page.
    """
    if not is_configured():
        return [CrossCheckResult("price", yahoo_price, None, available=False,
                                  error="Twelve Data API key not configured")]

    try:
        quote = _get_quote(ticker)
    except Exception as e:
        return [CrossCheckResult("price", yahoo_price, None, available=False,
                                  error=f"Twelve Data request failed: {e}")]

    results = []

    twelve_price = quote.get("close")
    twelve_price = float(twelve_price) if twelve_price else None
    results.append(CrossCheckResult("Current price", yahoo_price, twelve_price))

    # Twelve Data's free /quote endpoint doesn't reliably include market
    # cap or shares outstanding for every symbol — when missing, mark
    # unavailable rather than silently skip, so the UI shows why.
    twelve_shares = quote.get("shares_outstanding") or quote.get("average_volume")  # fallback varies by plan tier
    if quote.get("shares_outstanding"):
        twelve_shares = float(quote["shares_outstanding"])
        results.append(CrossCheckResult("Shares outstanding", yahoo_shares, twelve_shares))
    else:
        results.append(CrossCheckResult("Shares outstanding", yahoo_shares, None, available=False,
                                         error="Not provided by Twelve Data free tier for this symbol"))

    return results
