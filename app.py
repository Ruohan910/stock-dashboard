"""
app.py
======
Flask web app — Watchlist home page + Ticker detail page.

Run with:
    python app.py

Then open http://127.0.0.1:5000 in your browser.

Storage: a simple local JSON file (watchlist.json) holds the user's
tracked tickers and the last-fetched data for each. This is intentionally
simple for v1 — single user, no database, no auth. Swap for a real DB
later if multi-user support is added.
"""

import json
import os
import traceback
from dataclasses import asdict
from datetime import datetime
from typing import Optional

from flask import Flask, render_template, request, redirect, url_for, flash

from yahoo_provider import YahooFinanceProvider, resample_to_4h
from calculations import (
    calculate_dcf,
    calculate_multiples,
    calculate_profitability,
    calculate_health_screen,
    calculate_historical_pe_range,
)
from supply_demand import detect_zones, nearest_zones, calculate_rsi, suggest_action

app = Flask(__name__)
app.secret_key = "dev-only-change-if-this-ever-goes-public"

STORE_PATH = os.path.join(os.path.dirname(__file__), "watchlist.json")

provider = YahooFinanceProvider()

# In-memory cache of the last-fetched StockFinancials per ticker. This lets
# override changes recompute the DCF instantly without re-hitting Yahoo
# Finance. It resets when the Flask process restarts — that's fine, the
# first refresh after a restart repopulates it.
raw_inputs_cache = {}


# ── Simple JSON-backed storage ───────────────────────────────────────────────

def load_store():
    if not os.path.exists(STORE_PATH):
        return {"tickers": [], "data": {}, "overrides": {}}
    with open(STORE_PATH, "r", encoding="utf-8") as f:
        store = json.load(f)
        store.setdefault("overrides", {})  # backward-compat for existing watchlist.json
        return store


def save_store(store):
    with open(STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2, default=str)


def datapoint_to_dict(dp):
    return {"value": dp.value, "source": dp.source, "confidence": dp.confidence, "note": dp.note}


def fetch_and_compute(ticker: str, overrides: Optional[dict] = None) -> dict:
    """
    Fetch fresh data for one ticker from Yahoo Finance and run all
    calculations. This hits the network — only call on an explicit
    user-triggered refresh, never on override changes alone.

    overrides: manual analyst adjustments for this ticker (growth_rate_1_5,
    discount_rate). These persist across refreshes — refresh only updates
    the auto-fetched values, never touches what the user typed in.
    """
    fin = provider.fetch(ticker)

    # Yearly price history for the historical P/E range note (Multiples
    # section). Fetched here (not in compute_from_financials) because that
    # function is meant to stay network-free so override changes can
    # recompute without hitting the API again — see recompute_only().
    try:
        quarterly_candles = provider.fetch_price_history(ticker, period="10y", interval="3mo")
        yearly_prices = _yearly_close_from_quarterly(quarterly_candles)
    except Exception:
        yearly_prices = []

    raw_inputs_cache[ticker] = (fin, yearly_prices)  # cache so override changes can recompute without refetching
    return compute_from_financials(fin, overrides=overrides, yearly_prices=yearly_prices)


def _yearly_close_from_quarterly(quarterly_candles: list) -> list:
    """Reduces a list of quarterly candles down to one (year, close) pair
    per calendar year, using each year's last available close."""
    by_year = {}
    for c in quarterly_candles:
        date_str = c["date"] if isinstance(c["date"], str) else None
        if not date_str:
            continue
        year = date_str[:4]
        by_year[year] = c["close"]  # later entries in the list overwrite, leaving the last close per year
    return list(by_year.items())


def recompute_only(ticker: str, overrides: Optional[dict] = None) -> Optional[dict]:
    """
    Recalculate using the LAST FETCHED financials for this ticker, without
    hitting Yahoo Finance again. Used when the user only changes a manual
    override — no need to waste an API call just to re-run arithmetic.
    Returns None if we have no cached fetch yet (caller should fall back
    to fetch_and_compute in that case).
    """
    cached = raw_inputs_cache.get(ticker)
    if cached is None:
        return None
    fin, yearly_prices = cached
    return compute_from_financials(fin, overrides=overrides, yearly_prices=yearly_prices)


def compute_from_financials(fin, overrides: Optional[dict] = None, yearly_prices: Optional[list] = None) -> dict:
    """Pure calculation step — no network calls. Takes an already-fetched
    StockFinancials object and runs DCF / multiples / profitability / health."""
    dcf = calculate_dcf(fin, overrides=overrides)
    multiples = calculate_multiples(fin)
    profitability = calculate_profitability(fin)
    health = calculate_health_screen(fin)
    historical_pe = calculate_historical_pe_range(
        yearly_close_prices=yearly_prices or [],
        current_eps=fin.eps.value,
        current_pe=multiples.pe_ratio,
    )

    return {
        "ticker": fin.ticker,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "company": {
            "name": fin.info.name,
            "sector": fin.info.sector,
            "current_price": fin.info.current_price,
            "market_cap": fin.info.market_cap,
        },
        "raw_inputs": {
            "fcf": {**datapoint_to_dict(fin.fcf), "history": fin.fcf_history},
            "growth_rate_1_5": datapoint_to_dict(fin.growth_rate_1_5),  # forward estimate — no trailing history to chart
            "current_assets": {**datapoint_to_dict(fin.current_assets), "history": fin.current_assets_history},
            "total_debt": {**datapoint_to_dict(fin.total_debt), "history": fin.total_debt_history},
            "shares_outstanding": {**datapoint_to_dict(fin.shares_outstanding), "history": fin.shares_outstanding_history},
            "beta": {**datapoint_to_dict(fin.beta), "history": fin.beta_history},  # always empty — Yahoo has no historical beta series
        },
        "dcf": asdict(dcf),
        "multiples": asdict(multiples),
        "historical_pe": asdict(historical_pe),
        "profitability": asdict(profitability),
        "health": asdict(health),
        "error": None,
    }


# ── Template adapters ────────────────────────────────────────────────────────
# The templates (watchlist.html, ticker.html, chart.html) were designed
# separately with their own flat variable-naming convention (s.ticker,
# dcf.verdict_class, rsi_class, etc). Rather than reshape our internal
# calculation dicts to match a template's taste, we keep calculations.py /
# app.py's data structures as the source of truth and adapt at the
# rendering boundary — that way the calculation layer never needs to know
# anything about how the UI happens to be styled today.

def _make_sparkline_svg(history: list, width: int = 80, height: int = 28) -> str:
    """
    Builds a minimal inline SVG sparkline from a (label, value) history
    list (most-recent-first, as returned by the data provider). Returns
    an empty string if there's not enough data to draw a meaningful line
    (need at least 2 points) — the template shows a text fallback instead
    of an empty/broken chart in that case.
    """
    values = [v for _, v in reversed(history) if v is not None]  # oldest first for left-to-right reading
    if len(values) < 2:
        return ""

    min_v, max_v = min(values), max(values)
    span = max_v - min_v
    pad = 3
    usable_h = height - 2 * pad

    def y_for(v):
        if span == 0:
            return height / 2
        return pad + (1 - (v - min_v) / span) * usable_h

    step = width / (len(values) - 1)
    points = [(round(i * step, 1), round(y_for(v), 1)) for i, v in enumerate(values)]

    trend_up = values[-1] >= values[0]
    color = "#54c08a" if trend_up else "#e7706a"

    path = " ".join(f"{x},{y}" for x, y in points)
    last_x, last_y = points[-1]

    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'xmlns="http://www.w3.org/2000/svg" style="display:block;">'
        f'<polyline points="{path}" fill="none" stroke="{color}" stroke-width="1.5" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{last_x}" cy="{last_y}" r="2" fill="{color}"/>'
        f'</svg>'
    )


def _fmt_money(value, decimals=2):
    if value is None:
        return "—"
    return f"${value:,.{decimals}f}"


def _fmt_pct(value, decimals=1, with_sign=True):
    if value is None:
        return "—"
    sign = "+" if with_sign and value > 0 else ""
    return f"{sign}{value:.{decimals}f}%"


def _verdict_class(verdict: str) -> str:
    mapping = {
        "UNDERVALUED": "undervalued",
        "OVERVALUED": "overvalued",
        "FAIR VALUE": "fair",
        "LOW CONFIDENCE": "fair",
        "INSUFFICIENT DATA": "gray",
    }
    return mapping.get(verdict, "gray")


def _health_class(verdict: str) -> str:
    mapping = {"PASS": "pass", "CONDITIONAL PASS": "fair", "FAIL": "fail", "INSUFFICIENT DATA": "gray"}
    return mapping.get(verdict, "gray")


def build_watchlist_stock(ticker: str, data: dict) -> dict:
    """Adapts one cached result dict (from compute_from_financials) into
    the flat shape watchlist.html expects."""
    if data.get("error"):
        return {
            "ticker": ticker, "name": "—", "price": "—", "intrinsic_value": "—",
            "upside": "—", "verdict": "ERROR", "verdict_class": "gray",
            "updated": data.get("fetched_at", "—"),
        }

    dcf = data.get("dcf", {})
    company = data.get("company", {})
    fetched_at = data.get("fetched_at", "")
    updated_display = fetched_at.split("T")[1] if "T" in fetched_at else fetched_at

    return {
        "ticker": ticker,
        "name": company.get("name", ticker),
        "price": _fmt_money(company.get("current_price")),
        "intrinsic_value": _fmt_money(dcf.get("intrinsic_value_per_share")),
        "upside": _fmt_pct(dcf.get("upside_pct")),
        "verdict": dcf.get("verdict", "—").replace("_", " ").title(),
        "verdict_class": _verdict_class(dcf.get("verdict", "")),
        "updated": updated_display,
    }


def build_ticker_context(ticker: str, data: dict, overrides: dict) -> dict:
    """Adapts one cached result dict into the nested shape ticker.html
    expects (s / dcf / health / multiples / profit / sources)."""
    company = data.get("company", {})
    dcf_raw = data.get("dcf", {})
    health_raw = data.get("health", {})
    multiples_raw = data.get("multiples", {})
    profit_raw = data.get("profitability", {})
    raw_inputs = data.get("raw_inputs", {})

    fetched_at = data.get("fetched_at", "")
    updated_display = fetched_at.split("T")[1] if "T" in fetched_at else fetched_at

    s = {
        "ticker": ticker,
        "name": company.get("name", ticker),
        "sector": company.get("sector") or "—",
        "market_cap": f"${company.get('market_cap'):,.0f}" if company.get("market_cap") else "—",
        "price": company.get("current_price") or 0,
        "updated": updated_display,
    }

    dcf = {
        "intrinsic_value": dcf_raw.get("intrinsic_value_per_share"),
        "upside": _fmt_pct(dcf_raw.get("upside_pct")),
        "verdict_class": _verdict_class(dcf_raw.get("verdict", "")),
        "discount_rate": _fmt_pct(dcf_raw.get("discount_rate") * 100 if dcf_raw.get("discount_rate") else None, with_sign=False),
        "growth_1_5": _fmt_pct(dcf_raw.get("growth_yr_1_5") * 100 if dcf_raw.get("growth_yr_1_5") else None, with_sign=False),
        "growth_6_10": _fmt_pct(dcf_raw.get("growth_yr_6_10") * 100 if dcf_raw.get("growth_yr_6_10") else None, with_sign=False),
        "growth_11_20": _fmt_pct(dcf_raw.get("growth_yr_11_20") * 100 if dcf_raw.get("growth_yr_11_20") else None, with_sign=False),
        "cash_per_share": _fmt_money(dcf_raw.get("cash_per_share")),
        "projection": [
            {"year": year, "fcf": fcf, "pv": pv}
            for year, fcf, pv in dcf_raw.get("projection_table", [])
        ],
    }

    checks = []
    health_checks_map = [
        ("revenue_growing", "Revenue growing"),
        ("net_income_growing", "Net income growing"),
        ("fcf_positive_and_growing", "FCF positive & growing"),
        ("ocf_exceeds_net_income", "OCF exceeds net income"),
    ]
    for key, label in health_checks_map:
        val = health_raw.get(key)
        checks.append({"label": label, "ok": bool(val)})

    health = {
        "verdict": health_raw.get("verdict", "—"),
        "verdict_class": _health_class(health_raw.get("verdict", "")),
        "summary": health_raw.get("reasoning", ""),
        "checks": checks,
    }

    peg = multiples_raw.get("peg_ratio")
    historical_pe = data.get("historical_pe", {})
    pe_range_text = None
    if historical_pe.get("low") is not None and historical_pe.get("high") is not None:
        pe_range_text = (
            f"Historical range ({historical_pe['years_covered']}yr, approx.): "
            f"{historical_pe['low']:.1f}x – {historical_pe['high']:.1f}x — "
            f"currently {historical_pe['position_text']}"
        )
    multiples = {
        "pe": multiples_raw.get("pe_ratio") if multiples_raw.get("pe_ratio") is not None else "—",
        "peg": peg if peg is not None else "—",
        "pfcf": multiples_raw.get("price_fcf_ratio") if multiples_raw.get("price_fcf_ratio") is not None else "—",
        "note_class": "green" if (peg is not None and peg < 1.0) else "gray",
        "note_label": multiples_raw.get("assessment", "—"),
        "pe_range_text": pe_range_text,
    }

    profit = {
        "gross": _fmt_pct(profit_raw.get("gross_margin_pct"), with_sign=False),
        "net": _fmt_pct(profit_raw.get("net_margin_pct"), with_sign=False),
        "roa": _fmt_pct(profit_raw.get("roa_pct"), with_sign=False),
        "roe": _fmt_pct(profit_raw.get("roe_pct"), with_sign=False),
        "eps": _fmt_money(profit_raw.get("eps")),
    }

    def _format_source_value(key, value):
        if not isinstance(value, (int, float)):
            return "—"
        if key == "growth_rate_1_5":
            return f"{value * 100:.1f}%"
        if key == "beta":
            return f"{value:.2f}"
        # Dollar-figure fields (FCF, assets, debt, shares) — use human
        # readable K/M/B/T abbreviations instead of scientific notation,
        # which is accurate but unreadable for a non-technical audience.
        abs_value = abs(value)
        if abs_value >= 1e12:
            return f"${value / 1e12:,.2f}T"
        if abs_value >= 1e9:
            return f"${value / 1e9:,.2f}B"
        if abs_value >= 1e6:
            return f"${value / 1e6:,.2f}M"
        if abs_value >= 1e3:
            return f"${value / 1e3:,.1f}K"
        return f"${value:,.0f}"

    sources = []
    label_map = {
        "fcf": "Free Cash Flow",
        "growth_rate_1_5": "Growth Rate (Yr 1-5)",
        "current_assets": "Current Assets",
        "total_debt": "Total Debt",
        "shares_outstanding": "Shares Outstanding",
        "beta": "Beta",
    }
    for key, label in label_map.items():
        dp = raw_inputs.get(key, {})
        value = dp.get("value")
        value_display = _format_source_value(key, value)
        note = dp.get("source", "")
        if dp.get("note"):
            note += f" — {dp['note']}"

        history = dp.get("history", [])
        sparkline = _make_sparkline_svg(history)
        trend_text = None
        if not sparkline and key != "growth_rate_1_5":
            # No history available for this field at all (e.g. Beta) —
            # say so explicitly rather than leaving a blank gap, so it
            # reads as "data unavailable" rather than "broken".
            trend_text = "No historical data available"
        elif history and len(history) >= 2:
            oldest_val = history[-1][1]
            newest_val = history[0][1]
            if oldest_val and oldest_val != 0:
                change_pct = (newest_val - oldest_val) / abs(oldest_val) * 100
                trend_text = f"{_format_source_value(key, oldest_val)} → {_format_source_value(key, newest_val)} ({change_pct:+.0f}% over {len(history)} periods)"

        sources.append({
            "label": label,
            "value": value_display,
            "note": note,
            "confidence": dp.get("confidence", "low"),
            "sparkline": sparkline,
            "trend_text": trend_text,
        })

    return {
        "s": s,
        "dcf": dcf,
        "health": health,
        "multiples": multiples,
        "profit": profit,
        "sources": sources,
        "overrides": {
            "growth_rate_1_5": round(overrides.get("growth_rate_1_5", 0) * 100, 2) if overrides.get("growth_rate_1_5") is not None else "",
            "discount_rate": round(overrides.get("discount_rate", 0) * 100, 2) if overrides.get("discount_rate") is not None else "",
        },
    }




@app.route("/")
def watchlist():
    store = load_store()
    stocks = [build_watchlist_stock(ticker, store["data"].get(ticker, {})) for ticker in store["tickers"]]

    # "Flagged" = needs a closer look (low-confidence data or insufficient
    # inputs) — distinct from "Fair Value", which is a normal, trustworthy
    # valuation outcome, not a data-quality problem. Every stock's
    # verdict_class falls into exactly one of these four buckets, so the
    # counts always sum to len(stocks).
    counts = {
        "undervalued": sum(1 for s in stocks if s["verdict_class"] == "undervalued"),
        "overvalued": sum(1 for s in stocks if s["verdict_class"] == "overvalued"),
        "fair": sum(1 for s in stocks if s["verdict_class"] == "fair" and s["verdict"] != "Low Confidence"),
        "flagged": sum(1 for s in stocks if s["verdict_class"] == "gray" or s["verdict"] == "Low Confidence"),
    }
    return render_template("watchlist.html", stocks=stocks, counts=counts)


@app.route("/add", methods=["POST"])
def add_ticker():
    ticker = request.form.get("ticker", "").strip().upper()
    if not ticker:
        flash("Enter a ticker symbol first.")
        return redirect(url_for("watchlist"))

    store = load_store()
    if ticker in store["tickers"]:
        flash(f"{ticker} is already in your watchlist.")
        return redirect(url_for("watchlist"))

    store["tickers"].append(ticker)
    save_store(store)

    # Fetch immediately so the new card isn't empty
    return redirect(url_for("refresh_ticker", ticker=ticker))


@app.route("/remove/<ticker>", methods=["POST"])
def remove_ticker(ticker):
    store = load_store()
    ticker = ticker.upper()
    if ticker in store["tickers"]:
        store["tickers"].remove(ticker)
        store["data"].pop(ticker, None)
        store["overrides"].pop(ticker, None)
        save_store(store)
    return redirect(url_for("watchlist"))


@app.route("/refresh/<ticker>", methods=["GET", "POST"])
def refresh_ticker(ticker):
    ticker = ticker.upper()
    store = load_store()
    if ticker not in store["tickers"]:
        store["tickers"].append(ticker)

    try:
        ticker_overrides = store["overrides"].get(ticker, {})
        result = fetch_and_compute(ticker, overrides=ticker_overrides)
    except Exception as e:
        result = {
            "ticker": ticker,
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "error": f"{type(e).__name__}: {e}",
            "traceback": traceback.format_exc(),
        }
        flash(f"Could not fetch {ticker}: {e}")

    store["data"][ticker] = result
    save_store(store)

    # If this came from the detail page, go back there; otherwise watchlist
    next_page = request.args.get("next")
    if next_page == "detail":
        return redirect(url_for("detail", ticker=ticker))
    return redirect(url_for("watchlist"))


@app.route("/refresh-all", methods=["POST"])
def refresh_all():
    store = load_store()
    for ticker in store["tickers"]:
        try:
            ticker_overrides = store["overrides"].get(ticker, {})
            store["data"][ticker] = fetch_and_compute(ticker, overrides=ticker_overrides)
        except Exception as e:
            store["data"][ticker] = {
                "ticker": ticker,
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
                "error": f"{type(e).__name__}: {e}",
            }
    save_store(store)
    return redirect(url_for("watchlist"))


@app.route("/set-override/<ticker>", methods=["POST"])
def set_override(ticker):
    """
    Manual analyst overrides for growth rate / discount rate. These persist
    in store["overrides"][ticker] and survive future refreshes — refresh
    only ever updates the auto-fetched values, never these.

    Submitting an empty field clears that specific override (falls back
    to the auto-fetched value again).
    """
    ticker = ticker.upper()
    store = load_store()
    overrides = store["overrides"].setdefault(ticker, {})

    growth_raw = request.form.get("growth_rate_1_5", "").strip()
    discount_raw = request.form.get("discount_rate", "").strip()

    if growth_raw == "":
        overrides.pop("growth_rate_1_5", None)
    else:
        try:
            # User enters a percentage, e.g. "25" means 25% -> 0.25
            overrides["growth_rate_1_5"] = float(growth_raw) / 100
        except ValueError:
            flash(f"Could not parse growth rate '{growth_raw}' — must be a number (e.g. 25 for 25%).")

    if discount_raw == "":
        overrides.pop("discount_rate", None)
    else:
        try:
            overrides["discount_rate"] = float(discount_raw) / 100
        except ValueError:
            flash(f"Could not parse discount rate '{discount_raw}' — must be a number (e.g. 7 for 7%).")

    save_store(store)

    # Recompute immediately with the new overrides — uses the cached fetch,
    # no new network call needed just to apply a manual number.
    ticker_overrides = store["overrides"].get(ticker, {})
    try:
        result = recompute_only(ticker, overrides=ticker_overrides)
        if result is None:
            # No cached fetch yet (e.g. fresh server restart) — fetch once
            result = fetch_and_compute(ticker, overrides=ticker_overrides)
        store["data"][ticker] = result
        save_store(store)
    except Exception as e:
        flash(f"Could not recompute {ticker}: {e}")

    return redirect(url_for("detail", ticker=ticker))


@app.route("/ticker/<ticker>")
def detail(ticker):
    ticker = ticker.upper()
    store = load_store()
    data = store["data"].get(ticker)
    if data is None:
        return redirect(url_for("refresh_ticker", ticker=ticker, next="detail"))
    if data.get("error"):
        flash(f"Could not load {ticker}: {data['error']}")
        return redirect(url_for("watchlist"))

    ticker_overrides = store["overrides"].get(ticker, {})
    context = build_ticker_context(ticker, data, ticker_overrides)
    return render_template("ticker.html", **context)


@app.route("/ticker/<ticker>/chart")
def chart(ticker):
    """
    Supply/Demand zone chart, per supply_demand.py (Chapter 5 methodology).
    Supports three timeframes, matching the course's "start from a higher
    timeframe and move down" guidance: Weekly -> Daily -> 4H.

    Yahoo Finance limits intraday data (4H) to roughly the last 60 days —
    this is a Yahoo Finance constraint, not something we control, so the
    UI surfaces it rather than silently truncating.
    """
    ticker = ticker.upper()
    store = load_store()

    timeframe = request.args.get("tf", "weekly")
    TIMEFRAME_CONFIG = {
        "weekly": {"period": "3y",  "interval": "1wk", "months_lookback": 9,
                   "label": "Weekly", "lookahead_candles": 3, "min_move_pct": 0.10,
                   "candles_per_month": 4.33},
        "daily":  {"period": "2y",  "interval": "1d",   "months_lookback": 4,
                   "label": "Daily", "lookahead_candles": 5, "min_move_pct": 0.07,
                   "candles_per_month": 21},
        # Yahoo Finance has no native 4-hour interval — we fetch hourly
        # candles (max ~60 days available) and merge every 4 into one
        # 4H candle ourselves. See yahoo_provider.resample_to_4h.
        "4h":     {"period": "60d", "interval": "1h",   "months_lookback": 2,
                   "label": "4-Hour", "lookahead_candles": 5, "min_move_pct": 0.04,
                   "candles_per_month": 21 * 1.625},  # ~6.5 4H candles per trading day * 21 days
    }
    if timeframe not in TIMEFRAME_CONFIG:
        timeframe = "weekly"
    cfg = TIMEFRAME_CONFIG[timeframe]

    try:
        candles = provider.fetch_price_history(ticker, period=cfg["period"], interval=cfg["interval"])
        if timeframe == "4h":
            candles = resample_to_4h(candles)
    except Exception as e:
        flash(f"Could not load price history for {ticker}: {e}")
        return redirect(url_for("detail", ticker=ticker))

    if not candles:
        flash(f"No price history available for {ticker} at {cfg['label']} timeframe.")
        return redirect(url_for("detail", ticker=ticker))

    zones = detect_zones(
        candles,
        months_lookback=cfg["months_lookback"],
        lookahead_candles=cfg["lookahead_candles"],
        min_move_pct=cfg["min_move_pct"],
        candles_per_month=cfg["candles_per_month"],
    )
    current_price = candles[-1]["close"]
    nearest = nearest_zones(zones, current_price)

    def _display_date(raw_date):
        """formed_date is a plain 'YYYY-MM-DD' string for daily/weekly,
        or a unix timestamp (int) for intraday — normalize both to a
        readable string for the UI's text listings."""
        if isinstance(raw_date, (int, float)):
            return datetime.fromtimestamp(raw_date).strftime("%Y-%m-%d %H:%M")
        return str(raw_date)

    # Convert dataclasses to plain dicts for the template (Jinja's |tojson
    # filter needs JSON-serializable data, and the JS in chart.html reads
    # zone.zone_type / zone.top / etc as plain object properties)
    zones_dicts = []
    for z in zones:
        d = asdict(z)
        d["formed_date_display"] = _display_date(d["formed_date"])
        zones_dicts.append(d)

    nearest_demand_dict = asdict(nearest["nearest_demand"]) if nearest["nearest_demand"] else None
    if nearest_demand_dict:
        nearest_demand_dict["formed_date_display"] = _display_date(nearest_demand_dict["formed_date"])
    nearest_supply_dict = asdict(nearest["nearest_supply"]) if nearest["nearest_supply"] else None
    if nearest_supply_dict:
        nearest_supply_dict["formed_date_display"] = _display_date(nearest_supply_dict["formed_date"])

    # Pull company name AND DCF fair-value context from cached financials,
    # for the confluence check (Prompt 10, output step 4-5): is the nearest
    # demand zone also within the DCF-derived fair value range?
    cached = store["data"].get(ticker, {})
    company_name = cached.get("company", {}).get("name", ticker)

    dcf_cached = cached.get("dcf", {})
    intrinsic_value = dcf_cached.get("intrinsic_value_per_share")
    # Treat "fair value range" as intrinsic value +/- 15%, matching the
    # same +/-15% band calculations.py already uses to call something
    # FAIR VALUE rather than UNDER/OVERVALUED — keeps the two features
    # using one consistent definition of "fair value" rather than two.
    fair_value_low = round(intrinsic_value * 0.85, 2) if intrinsic_value else None
    fair_value_high = round(intrinsic_value * 1.15, 2) if intrinsic_value else None

    rsi = calculate_rsi(candles)
    action = suggest_action(
        current_price=current_price,
        nearest_demand=nearest["nearest_demand"],
        nearest_supply=nearest["nearest_supply"],
        rsi=rsi,
        fair_value_low=fair_value_low,
        fair_value_high=fair_value_high,
    )

    # ── Adapt into the flat shape chart.html expects ────────────────────
    def _pct_away(zone_edge, current):
        if zone_edge is None or current == 0:
            return "—"
        return f"{abs(zone_edge - current) / current * 100:.1f}%"

    nearest_demand_view = None
    if nearest_demand_dict:
        nearest_demand_view = {
            "low": nearest_demand_dict["bottom"],
            "high": nearest_demand_dict["top"],
            "pct_away": _pct_away(nearest_demand_dict["top"], current_price),
        }
    nearest_supply_view = None
    if nearest_supply_dict:
        nearest_supply_view = {
            "low": nearest_supply_dict["bottom"],
            "high": nearest_supply_dict["top"],
            "pct_away": _pct_away(nearest_supply_dict["bottom"], current_price),
        }

    rsi_signal_to_class = {"oversold": "green", "overbought": "red", "neutral": "gray"}
    action_to_class = {
        "LOAD UP": "green", "TAKE PARTIAL PROFITS": "red",
        "WATCH CLOSELY": "gold", "WAIT": "gray",
    }

    signal = {
        "label": action["action"],
        "class": action_to_class.get(action["action"], "gray"),
        "text": " ".join(action["reasoning"]),
    }

    s_view = {"ticker": ticker, "name": company_name, "price": current_price}

    return render_template(
        "chart.html",
        s=s_view,
        tf=timeframe,
        timeframe_label=cfg["label"],
        candle_data=candles,
        zones=zones_dicts,
        nearest_demand=nearest_demand_view,
        nearest_supply=nearest_supply_view,
        rsi=rsi if rsi is not None else "—",
        rsi_class=rsi_signal_to_class.get(action.get("rsi_signal"), "gray"),
        rsi_label=action.get("rsi_signal") or "—",
        dcf_low=fair_value_low,
        dcf_high=fair_value_high,
        position=(
            "At demand zone" if action["at_demand_zone"]
            else "At supply zone" if action["at_supply_zone"]
            else "Between zones"
        ),
        signal=signal,
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
