"""
yahoo_provider.py
==================
Yahoo Finance implementation of DataProvider, via the `yfinance` library.

This encodes every data-quality fix we already validated by hand:
  - growth_estimates uses LTG -> +1y -> revenueGrowth fallback chain,
    capped at 60% (anything above that is almost certainly a distorted
    trailing/quarterly figure, not a real forward growth estimate)
  - totalCurrentAssets in .info is often None; falls back to reading
    the 'Current Assets' row from the balance sheet statement
"""

import yfinance as yf

from data_provider import (
    DataProvider,
    DataPoint,
    CompanyInfo,
    StockFinancials,
)


GROWTH_RATE_CAP = 0.60  # 60% — see module docstring


def resample_to_4h(hourly_candles: list) -> list:
    """
    Yahoo Finance (via yfinance) has no native 4-hour interval — the
    underlying API simply doesn't offer it. We fetch 1-hour candles and
    merge every 4 consecutive ones into a synthetic 4H candle:
        open  = first candle's open
        high  = max high across the 4
        low   = min low across the 4
        close = last candle's close
        date  = first candle's date/time (the bucket's start)

    This groups strictly by position (every 4 rows), not by wall-clock
    alignment to market open — simple and predictable, matching what
    most "resample 1h->4h" implementations do when market-session-aware
    bucketing isn't available.
    """
    merged = []
    for i in range(0, len(hourly_candles) - 3, 4):
        chunk = hourly_candles[i:i + 4]
        merged.append({
            "date": chunk[0]["date"],
            "open": chunk[0]["open"],
            "high": max(c["high"] for c in chunk),
            "low": min(c["low"] for c in chunk),
            "close": chunk[-1]["close"],
        })
    return merged


class YahooFinanceProvider(DataProvider):

    def fetch(self, ticker: str) -> StockFinancials:
        ticker = ticker.strip().upper()
        t = yf.Ticker(ticker)
        info = t.info or {}

        company_info = CompanyInfo(
            ticker=ticker,
            name=info.get("longName") or info.get("shortName") or ticker,
            sector=info.get("sector"),
            current_price=info.get("currentPrice") or info.get("regularMarketPrice"),
            market_cap=info.get("marketCap"),
        )

        fcf_point = self._get_fcf(t, info)
        growth_point = self._get_growth_rate(t, info)
        assets_point = self._get_current_assets(t, info)
        debt_point = self._wrap(info.get("totalDebt"), "Yahoo Finance info.totalDebt", "high")
        cash_point = self._wrap(info.get("totalCash"), "Yahoo Finance info.totalCash", "high")
        shares_point = self._wrap(info.get("sharesOutstanding"), "Yahoo Finance info.sharesOutstanding", "high")
        beta_point = self._wrap(info.get("beta"), "Yahoo Finance info.beta", "high")

        revenue_point = self._wrap(info.get("totalRevenue"), "Yahoo Finance info.totalRevenue", "high")
        net_income_point = self._wrap(info.get("netIncomeToCommon"), "Yahoo Finance info.netIncomeToCommon", "high")
        gross_profit_point = self._get_from_income_stmt(t, "Gross Profit")
        equity_point = self._get_from_balance_sheet(t, "Stockholders Equity")
        total_assets_point = self._get_from_balance_sheet(t, "Total Assets")
        eps_point = self._wrap(info.get("trailingEps"), "Yahoo Finance info.trailingEps", "high")

        revenue_history = self._get_history_from_income_stmt(t, "Total Revenue")
        net_income_history = self._get_history_from_income_stmt(t, "Net Income")
        fcf_history = self._get_history_from_cashflow(t, ["Free Cash Flow", "FreeCashFlow"])
        op_cf_history = self._get_history_from_cashflow(
            t, ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities", "Total Cash From Operating Activities"]
        )
        current_assets_history = self._get_history_from_balance_sheet(t, "Current Assets")
        total_debt_history = self._get_history_from_balance_sheet(t, "Total Debt")
        # yfinance has no "shares outstanding over time" series — the
        # closest available proxy on the balance sheet is "Ordinary Shares
        # Number" per fiscal year-end, which is what we use here.
        shares_outstanding_history = self._get_history_from_balance_sheet(t, "Ordinary Shares Number")
        # Beta has NO real history via Yahoo Finance — it's a single
        # current value computed over a fixed lookback window, not a
        # year-by-year series. We deliberately return an empty list rather
        # than fabricating one, and the UI shows "no historical data"
        # instead of a fake flat sparkline.
        beta_history = []

        return StockFinancials(
            ticker=ticker,
            info=company_info,
            fcf=fcf_point,
            growth_rate_1_5=growth_point,
            current_assets=assets_point,
            total_debt=debt_point,
            total_cash=cash_point,
            shares_outstanding=shares_point,
            beta=beta_point,
            revenue=revenue_point,
            net_income=net_income_point,
            gross_profit=gross_profit_point,
            shareholders_equity=equity_point,
            total_assets=total_assets_point,
            eps=eps_point,
            revenue_history=revenue_history,
            net_income_history=net_income_history,
            fcf_history=fcf_history,
            operating_cashflow_history=op_cf_history,
            current_assets_history=current_assets_history,
            total_debt_history=total_debt_history,
            shares_outstanding_history=shares_outstanding_history,
            beta_history=beta_history,
        )

    def fetch_price_history(self, ticker: str, period: str = "1y", interval: str = "1wk") -> list:
        ticker = ticker.strip().upper()
        t = yf.Ticker(ticker)
        hist = t.history(period=period, interval=interval)
        if hist is None or hist.empty:
            return []

        # Daily/weekly/monthly candles use a plain "YYYY-MM-DD" string,
        # which is what lightweight-charts expects for non-intraday data.
        # Intraday intervals (1h, etc) must use a UNIX timestamp (seconds)
        # instead — lightweight-charts only accepts a bare date string for
        # day-or-coarser granularity; anything with a time component needs
        # a numeric UTCTimestamp. Losing the time part here would also
        # collapse same-day candles onto an identical label, corrupting
        # both the chart and zone detection.
        is_intraday = any(interval.endswith(suffix) for suffix in ("m", "h")) and interval not in ("1mo", "3mo")

        candles = []
        for idx, row in hist.iterrows():
            if is_intraday:
                date_value = int(idx.timestamp())
            else:
                date_value = str(idx.date()) if hasattr(idx, "date") else str(idx)
            candles.append({
                "date": date_value,
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
            })
        return candles

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _wrap(value, source, confidence, note=None):
        return DataPoint(
            value=float(value) if value is not None else None,
            source=source,
            confidence=confidence,
            note=note,
        )

    def _get_fcf(self, t, info):
        try:
            cf = t.cashflow
            if cf is not None and not cf.empty:
                for label in ["Free Cash Flow", "FreeCashFlow"]:
                    if label in cf.index:
                        row = cf.loc[label]
                        val = row.iloc[0]
                        if val == val:  # not NaN
                            note = self._check_fcf_anomaly(row)
                            confidence = "low" if note else "high"
                            return self._wrap(
                                val, "Yahoo Finance cashflow.Free Cash Flow", confidence, note=note
                            )
        except Exception:
            pass

        # Fallback: operating cash flow - capex (capex is stored negative)
        op_cf = info.get("operatingCashflow")
        capex = info.get("capitalExpenditures")
        if op_cf and capex:
            return self._wrap(
                op_cf + capex,
                "Yahoo Finance info.operatingCashflow - capitalExpenditures",
                "medium",
                note="Derived fallback — cashflow statement FCF unavailable",
            )
        if op_cf:
            return self._wrap(
                op_cf, "Yahoo Finance info.operatingCashflow", "low",
                note="No capex data — this is operating cash flow, not true FCF",
            )
        return DataPoint(None, "Yahoo Finance", "low", note="FCF unavailable from any source")

    @staticmethod
    def _check_fcf_anomaly(fcf_row):
        """
        Flag when the latest FCF figure looks distorted relative to recent
        history — e.g. a capex spike (common in AI-infrastructure-heavy
        years) crushing FCF near zero or negative even though the
        business itself is healthy. This does NOT change which number is
        used (per product decision: keep latest year, just warn) — it only
        attaches a note so the user knows to sanity-check before trusting
        a DCF built on this base figure.

        Returns a warning string, or None if the latest figure looks
        consistent with recent history.
        """
        values = [v for v in fcf_row.tolist() if v == v]  # drop NaN
        if len(values) < 3:
            return None  # not enough history to judge

        latest = values[0]
        prior_years = values[1:4]  # up to 3 prior years
        prior_positive = [v for v in prior_years if v > 0]

        if not prior_positive:
            return None  # prior years were also weak/negative — not a new anomaly

        avg_prior = sum(prior_positive) / len(prior_positive)
        if avg_prior == 0:
            return None

        if latest <= 0:
            return (
                f"Latest FCF is zero or negative ({latest:,.0f}) versus a "
                f"{avg_prior:,.0f} average over the prior {len(prior_positive)} year(s). "
                f"Likely a capex spike or one-off item — verify manually before trusting "
                f"the DCF output for this stock."
            )

        ratio = latest / avg_prior
        if ratio < 0.4:
            return (
                f"Latest FCF ({latest:,.0f}) is {ratio:.0%} of the prior "
                f"{len(prior_positive)}-year average ({avg_prior:,.0f}) — a sharp drop, "
                f"likely from a capex spike or one-off item. The DCF below uses this "
                f"depressed figure as its base year; verify manually before trusting the output."
            )
        return None

    def _get_growth_rate(self, t, info):
        # 1. Try LTG (Long Term Growth — the real ~5yr analyst estimate)
        try:
            ge = t.growth_estimates
            if ge is not None and not ge.empty and "LTG" in ge.index:
                val = ge.loc["LTG", "stockTrend"]
                if val == val and val is not None:
                    return self._cap_growth(float(val), "Yahoo Finance growth_estimates.LTG", "high")
        except Exception:
            pass

        # 2. Fall back to +1y forward estimate
        try:
            ge = t.growth_estimates
            if ge is not None and "+1y" in ge.index:
                val = ge.loc["+1y", "stockTrend"]
                if val == val and val is not None:
                    return self._cap_growth(
                        float(val), "Yahoo Finance growth_estimates.+1y", "medium",
                        note="LTG unavailable — using next fiscal year estimate instead of 5yr",
                    )
        except Exception:
            pass

        # 3. Fall back to revenueGrowth from info
        rg = info.get("revenueGrowth")
        if rg:
            return self._cap_growth(
                float(rg), "Yahoo Finance info.revenueGrowth", "low",
                note="Analyst long-term growth estimate unavailable — using trailing revenue growth as a rough proxy. Verify manually.",
            )

        return DataPoint(None, "Yahoo Finance", "low", note="No growth estimate available from any source")

    def _cap_growth(self, value, source, confidence, note=None):
        if value > GROWTH_RATE_CAP:
            capped_note = f"Raw value {value:.1%} looked unreliable (capped at {GROWTH_RATE_CAP:.0%}) — verify manually"
            note = f"{note} | {capped_note}" if note else capped_note
            value = GROWTH_RATE_CAP
            confidence = "low"
        return DataPoint(value, source, confidence, note)

    def _get_current_assets(self, t, info):
        val = info.get("totalCurrentAssets")
        if val is not None:
            return self._wrap(val, "Yahoo Finance info.totalCurrentAssets", "high")

        # Fallback: read directly from balance sheet statement
        point = self._get_from_balance_sheet(t, "Current Assets")
        if point.value is not None:
            point.note = "info.totalCurrentAssets was None — read from balance sheet statement instead"
            point.confidence = "high"  # balance sheet row is just as reliable, just a different path
        return point

    def _get_from_balance_sheet(self, t, label):
        try:
            bs = t.balance_sheet
            if bs is not None and not bs.empty and label in bs.index:
                val = bs.loc[label].iloc[0]
                if val == val:
                    return self._wrap(val, f"Yahoo Finance balance_sheet.{label}", "high")
        except Exception:
            pass
        return DataPoint(None, "Yahoo Finance balance_sheet", "low", note=f"'{label}' row unavailable")

    def _get_from_income_stmt(self, t, label):
        try:
            inc = t.income_stmt
            if inc is not None and not inc.empty and label in inc.index:
                val = inc.loc[label].iloc[0]
                if val == val:
                    return self._wrap(val, f"Yahoo Finance income_stmt.{label}", "high")
        except Exception:
            pass
        return DataPoint(None, "Yahoo Finance income_stmt", "low", note=f"'{label}' row unavailable")

    def _get_history_from_income_stmt(self, t, label):
        """Returns list of (year_label, value) tuples, most recent first."""
        try:
            inc = t.income_stmt
            if inc is not None and not inc.empty and label in inc.index:
                row = inc.loc[label]
                return [(str(col.date()) if hasattr(col, "date") else str(col), float(val))
                        for col, val in row.items() if val == val]
        except Exception:
            pass
        return []

    def _get_history_from_cashflow(self, t, label_candidates):
        try:
            cf = t.cashflow
            if cf is not None and not cf.empty:
                for label in label_candidates:
                    if label in cf.index:
                        row = cf.loc[label]
                        return [(str(col.date()) if hasattr(col, "date") else str(col), float(val))
                                for col, val in row.items() if val == val]
        except Exception:
            pass
        return []

    def _get_history_from_balance_sheet(self, t, label):
        """Returns list of (year_label, value) tuples, most recent first."""
        try:
            bs = t.balance_sheet
            if bs is not None and not bs.empty and label in bs.index:
                row = bs.loc[label]
                return [(str(col.date()) if hasattr(col, "date") else str(col), float(val))
                        for col, val in row.items() if val == val]
        except Exception:
            pass
        return []
