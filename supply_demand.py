"""
supply_demand.py
=================
Supply / Demand zone detection, following the LearningAlpha course
methodology (Chapter 5 — Trader Mastery) exactly:

DEMAND ZONE (buy zone):
  1. Find a "basing candle" — a small-bodied candle showing selling
     exhaustion (a paused sell-off).
  2. It must be FOLLOWED by a strong move to the upside.
  3. Zone = from the TOP of the basing candle's body, to the BOTTOM of
     its lower wick (if any).
  4. We only buy when price RETURNS to this zone.

SUPPLY ZONE (sell zone):
  1. Find a "basing candle" — a small-bodied candle showing buying
     exhaustion (a paused bid-up).
  2. It must be FOLLOWED by a strong move to the downside.
  3. Zone = from the BOTTOM of the basing candle's body, to the TOP of
     its upper wick (if any).
  4. We only sell when price RETURNS to this zone.

FRESH vs USED:
  A zone is "fresh" if price has not returned into it since it formed.
  Each time price re-enters a zone, more institutional orders fill and
  the zone weakens. A zone that gets broken through (price closes beyond
  the far edge) is considered invalidated.

This module is intentionally rule-based and transparent (no ML, no black
box) so a student can read the code and see exactly which course rule
produced each zone — matching the product's "always show your work"
data-confidence philosophy used elsewhere in the app.
"""

from dataclasses import dataclass
from typing import Optional


# ── Tunable thresholds (documented so they can be adjusted as the course
# methodology is refined, without hunting through the detection logic) ──────

# A candle's body must be this fraction (or less) of the period's average
# TRUE range to qualify as a "basing" (paused) candle.
# Loosened from 0.35 -> 0.45 after reviewing a real GOOGL daily case
# (2026-03-27, body=2.93 vs a 0.35 threshold of 2.71) that a human eye
# would call an obvious pause but the stricter threshold rejected.
BASING_BODY_RATIO_MAX = 0.45

# Additionally, the basing candle's body must be smaller than this fraction
# of the AVERAGE body size of the few candles immediately before it — this
# is what actually captures "a pause" rather than just "a small candle in
# a generally quiet market". Loosened from 0.6 -> 0.75 for the same reason
# as above (avg_prior_body=4.57, 0.6 threshold=2.74 rejected a 2.93 body
# that visually reads as a clear slowdown after a sharp multi-day decline).
BASING_RELATIVE_TO_PRIOR_MAX = 0.75
PRIOR_CANDLES_FOR_COMPARISON = 4

# Default thresholds — tuned for WEEKLY candles. Daily and 4H candles move
# in smaller increments per bar, so detect_zones() lets the caller override
# these via lookahead_candles/min_move_pct (see app.py's TIMEFRAME_CONFIG).
DEFAULT_STRONG_MOVE_LOOKAHEAD_CANDLES = 3
DEFAULT_STRONG_MOVE_MIN_PCT = 0.10  # 10% — appropriate for weekly, too strict for daily

# How many candles back to scan for zones (~6-12 months of weekly candles)
LOOKBACK_CANDLES = 52  # ~1 year of weekly candles; we filter to 6-12mo in detect()


@dataclass
class Candle:
    date: str
    open: float
    high: float
    low: float
    close: float

    @property
    def body_top(self) -> float:
        return max(self.open, self.close)

    @property
    def body_bottom(self) -> float:
        return min(self.open, self.close)

    @property
    def body_size(self) -> float:
        return abs(self.close - self.open)

    @property
    def is_bullish(self) -> bool:
        return self.close >= self.open


@dataclass
class Zone:
    zone_type: str  # "demand" | "supply"
    top: float
    bottom: float
    formed_date: str
    basing_candle_index: int
    is_fresh: bool
    touches: int  # how many times price has re-entered this zone since formation
    is_broken: bool = False  # price closed beyond the far edge — invalidated per course rule


def _to_candles(raw: list) -> list:
    return [Candle(**c) for c in raw]


def _average_range(candles: list) -> float:
    ranges = [c.high - c.low for c in candles if c.high > c.low]
    return sum(ranges) / len(ranges) if ranges else 0.0


def _is_basing_candle(candle: Candle, avg_range: float, candles: list, idx: int) -> bool:
    if avg_range == 0:
        return False
    if candle.body_size > (avg_range * BASING_BODY_RATIO_MAX):
        return False

    # Compare against the immediately preceding candles' body sizes too —
    # this is what distinguishes "a genuine pause after a move" from "just
    # a small candle in a generally quiet stretch".
    start = max(0, idx - PRIOR_CANDLES_FOR_COMPARISON)
    prior = candles[start:idx]
    if not prior:
        return False
    avg_prior_body = sum(c.body_size for c in prior) / len(prior)
    if avg_prior_body == 0:
        return False
    return candle.body_size <= (avg_prior_body * BASING_RELATIVE_TO_PRIOR_MAX)


def _strong_move_after(candles: list, basing_idx: int, direction: str,
                        lookahead_candles: int = DEFAULT_STRONG_MOVE_LOOKAHEAD_CANDLES,
                        min_move_pct: float = DEFAULT_STRONG_MOVE_MIN_PCT) -> bool:
    """
    direction: "up" or "down"
    Checks whether the candles following the basing candle show a strong
    move in the given direction, at or above min_move_pct, within
    lookahead_candles bars.
    """
    start_idx = basing_idx + 1
    end_idx = min(start_idx + lookahead_candles, len(candles))
    if start_idx >= end_idx:
        return False

    basing_close = candles[basing_idx].close
    window = candles[start_idx:end_idx]
    if direction == "up":
        extreme = max(c.high for c in window)
        return (extreme - basing_close) / basing_close >= min_move_pct
    else:
        extreme = min(c.low for c in window)
        return (basing_close - extreme) / basing_close >= min_move_pct


def _count_touches_and_freshness(candles: list, zone: Zone, basing_idx: int,
                                  lookahead_candles: int = DEFAULT_STRONG_MOVE_LOOKAHEAD_CANDLES) -> tuple:
    """
    Looks at all candles AFTER the basing candle (and after the strong
    move that confirmed the zone) to see how many times price has come
    back into [zone.bottom, zone.top]. Returns (touches, is_fresh, is_broken).

    Course rule distinguishes two outcomes when price returns to a zone:
      - A "touch" (price enters the zone, even closes inside it, but does
        NOT close beyond the zone's far edge) — the zone still held, but
        it's now "used" and weaker. We keep showing it, de-emphasized.
      - A "break" (price CLOSES beyond the zone's far edge — below
        zone.bottom for demand, above zone.top for supply) — the zone is
        invalidated entirely. Per the course: "REMOVE it from your chart
        and look for the next zone." We flag this so detect_zones can
        drop it rather than just marking it used.
    """
    touches = 0
    is_broken = False
    check_start = basing_idx + 1 + lookahead_candles
    for c in candles[check_start:]:
        entered = c.low <= zone.top and c.high >= zone.bottom
        if entered:
            touches += 1
        # Break = candle CLOSES beyond the far edge of the zone (not just
        # wicks through it — a wick poking through and closing back
        # inside is still just a touch/test of the zone).
        if zone.zone_type == "demand" and c.close < zone.bottom:
            is_broken = True
        elif zone.zone_type == "supply" and c.close > zone.top:
            is_broken = True
    is_fresh = touches == 0
    return touches, is_fresh, is_broken


def detect_zones(raw_candles: list, months_lookback: int = 9,
                  lookahead_candles: int = DEFAULT_STRONG_MOVE_LOOKAHEAD_CANDLES,
                  min_move_pct: float = DEFAULT_STRONG_MOVE_MIN_PCT,
                  candles_per_month: float = 4.33) -> list:
    """
    raw_candles: list of dicts as returned by DataProvider.fetch_price_history
                 (oldest first).
    months_lookback: how far back to report zones from (course guidance:
                      show zones from the last 6-12 months; we default to 9
                      as a middle ground, parameterized for flexibility).
    lookahead_candles / min_move_pct: define what counts as a "strong move"
                      confirming a basing candle. Defaults are tuned for
                      WEEKLY candles. Daily and 4H candles move in smaller
                      increments per bar and need looser values — see
                      app.py's TIMEFRAME_CONFIG, which passes timeframe-
                      appropriate values rather than relying on these
                      weekly-tuned defaults for every timeframe.
    candles_per_month: how many candles make up one month at this timeframe
                      (≈4.33 for weekly, ≈21 for daily trading days, much
                      higher for intraday). The caller knows its own
                      timeframe explicitly (app.py passes this per
                      TIMEFRAME_CONFIG) — this is far more reliable than
                      trying to infer it from how many candles happen to be
                      in the list, which breaks the moment weekly history
                      is fetched for more than ~14 months (60 candles).

    Returns a list of Zone objects, most recently formed first.
    """
    if len(raw_candles) < 10:
        return []

    candles = _to_candles(raw_candles)
    avg_range = _average_range(candles)
    if avg_range == 0:
        return []

    report_window_candles = min(len(candles), int(months_lookback * candles_per_month))
    earliest_report_idx = max(0, len(candles) - report_window_candles)

    zones = []

    # Scan every candle as a potential basing candle (skip the last few —
    # need room to check for the follow-through move)
    for i in range(len(candles) - 1 - lookahead_candles):
        candle = candles[i]
        if not _is_basing_candle(candle, avg_range, candles, i):
            continue

        # Check for demand zone (basing candle -> strong move UP)
        if _strong_move_after(candles, i, "up", lookahead_candles, min_move_pct):
            zone = Zone(
                zone_type="demand",
                top=candle.body_top,
                bottom=candle.low,  # bottom of the lower wick
                formed_date=candle.date,
                basing_candle_index=i,
                is_fresh=True,
                touches=0,
            )
            touches, is_fresh, is_broken = _count_touches_and_freshness(candles, zone, i, lookahead_candles)
            zone.touches = touches
            zone.is_fresh = is_fresh
            zone.is_broken = is_broken
            # Per course rule: a broken zone (price closed beyond it) is
            # invalidated and should be removed, not just shown as "used".
            if not is_broken and i >= earliest_report_idx:
                zones.append(zone)

        # Check for supply zone (basing candle -> strong move DOWN)
        if _strong_move_after(candles, i, "down", lookahead_candles, min_move_pct):
            zone = Zone(
                zone_type="supply",
                top=candle.high,  # top of the upper wick
                bottom=candle.body_bottom,
                formed_date=candle.date,
                basing_candle_index=i,
                is_fresh=True,
                touches=0,
            )
            touches, is_fresh, is_broken = _count_touches_and_freshness(candles, zone, i, lookahead_candles)
            zone.touches = touches
            zone.is_fresh = is_fresh
            zone.is_broken = is_broken
            if not is_broken and i >= earliest_report_idx:
                zones.append(zone)

    # Most recently formed first
    zones.sort(key=lambda z: z.basing_candle_index, reverse=True)
    return zones


def calculate_rsi(candles: list, period: int = 14) -> Optional[float]:
    """
    Standard RSI(14) on closing prices, used per course guidance to confirm
    a zone entry: "Load up when Weekly RSI <40, Daily RSI <30" (oversold)
    or overbought conditions near a supply zone for taking profits.
    Returns the most recent RSI value, or None if there isn't enough
    history to compute it.
    """
    candle_objs = _to_candles(candles) if candles and isinstance(candles[0], dict) else candles
    closes = [c.close for c in candle_objs]
    if len(closes) < period + 1:
        return None

    gains, losses = [], []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


def suggest_action(current_price: float, nearest_demand: Optional[Zone], nearest_supply: Optional[Zone],
                    rsi: Optional[float], fair_value_low: Optional[float] = None,
                    fair_value_high: Optional[float] = None,
                    proximity_threshold_pct: float = 0.03) -> dict:
    """
    Implements the course's decision rule (Prompt 10, output step 5):
      - AT demand zone + within fair value + RSI oversold -> LOAD UP
      - AT supply zone + above fair value + RSI overbought -> TAKE PARTIAL PROFITS
      - BETWEEN zones -> WAIT

    "AT a zone" is treated as price being within proximity_threshold_pct of
    the zone's near edge (default 3%) — exact equality is too strict on
    real price data. Each condition that's met or unmet is surfaced
    explicitly so the UI can show its reasoning, not just the verdict.
    """
    result = {
        "action": "WAIT",
        "reasoning": [],
        "at_demand_zone": False,
        "at_supply_zone": False,
        "within_fair_value": None,
        "rsi_signal": None,
    }

    if rsi is not None:
        if rsi < 40:
            result["rsi_signal"] = "oversold"
        elif rsi > 60:
            result["rsi_signal"] = "overbought"
        else:
            result["rsi_signal"] = "neutral"

    if nearest_demand is not None:
        distance_pct = abs(current_price - nearest_demand.top) / current_price
        if distance_pct <= proximity_threshold_pct:
            result["at_demand_zone"] = True

    if nearest_supply is not None:
        distance_pct = abs(nearest_supply.bottom - current_price) / current_price
        if distance_pct <= proximity_threshold_pct:
            result["at_supply_zone"] = True

    if fair_value_low is not None and fair_value_high is not None:
        result["within_fair_value"] = fair_value_low <= current_price <= fair_value_high

    if result["at_demand_zone"]:
        result["reasoning"].append(f"Price is within {proximity_threshold_pct:.0%} of a fresh demand zone.")
        if result["within_fair_value"]:
            result["reasoning"].append("Current price is also within the DCF fair value range (confluence).")
        elif result["within_fair_value"] is False:
            result["reasoning"].append("Current price is OUTSIDE the DCF fair value range — no confluence.")
        if result["rsi_signal"] == "oversold":
            result["reasoning"].append(f"RSI({14}) = {rsi} — oversold, supports the buy signal.")

        if result["within_fair_value"] and result["rsi_signal"] == "oversold":
            result["action"] = "LOAD UP"
        elif result["within_fair_value"] or result["rsi_signal"] == "oversold":
            result["action"] = "WATCH CLOSELY"  # partial confluence, not full per course rule
        else:
            result["action"] = "WAIT"

    elif result["at_supply_zone"]:
        result["reasoning"].append(f"Price is within {proximity_threshold_pct:.0%} of a fresh supply zone.")
        if result["within_fair_value"] is False:
            result["reasoning"].append("Current price is above the DCF fair value range.")
        if result["rsi_signal"] == "overbought":
            result["reasoning"].append(f"RSI({14}) = {rsi} — overbought, supports taking profits.")

        above_fair_value = result["within_fair_value"] is False and fair_value_high is not None and current_price > fair_value_high
        if above_fair_value and result["rsi_signal"] == "overbought":
            result["action"] = "TAKE PARTIAL PROFITS"
        elif above_fair_value or result["rsi_signal"] == "overbought":
            result["action"] = "WATCH CLOSELY"
        else:
            result["action"] = "WAIT"
    else:
        result["reasoning"].append("Price is between zones — no actionable signal per course rule.")
        result["action"] = "WAIT"

    return result


def detect_zones_for_timeframe(raw_candles: list, timeframe: str) -> list:
    """
    Safer entry point than calling detect_zones() directly with manually
    matched parameters. This exists because of a real mistake made during
    testing: calling detect_zones(months_lookback=12) without also passing
    the matching candles_per_month silently used the weekly default
    (4.33) against daily data, shrinking the report window from "the last
    12 months" to roughly "the last 2.4 months" — with no error, just
    quietly missing real zones older than that.

    timeframe: one of "weekly", "daily", "4h" — must match app.py's
    TIMEFRAME_CONFIG keys exactly.
    """
    configs = {
        "weekly": dict(months_lookback=9, lookahead_candles=3, min_move_pct=0.10, candles_per_month=4.33),
        "daily":  dict(months_lookback=4, lookahead_candles=5, min_move_pct=0.07, candles_per_month=21),
        "4h":     dict(months_lookback=2, lookahead_candles=5, min_move_pct=0.04, candles_per_month=21 * 1.625),
    }
    if timeframe not in configs:
        raise ValueError(f"Unknown timeframe '{timeframe}' — must be one of {list(configs)}")
    return detect_zones(raw_candles, **configs[timeframe])


def nearest_zones(zones: list, current_price: float) -> dict:
    """
    Convenience helper for the UI: given all detected zones and the current
    price, find the nearest FRESH demand zone (below price) and nearest
    FRESH supply zone (above price) — these are the actionable ones per
    course guidance ("prioritise fresh zones").
    """
    fresh = [z for z in zones if z.is_fresh]

    demand_candidates = [z for z in fresh if z.zone_type == "demand" and z.top <= current_price]
    supply_candidates = [z for z in fresh if z.zone_type == "supply" and z.bottom >= current_price]

    # Closest by distance from current price to the near edge of the zone
    nearest_demand = min(demand_candidates, key=lambda z: current_price - z.top) if demand_candidates else None
    nearest_supply = min(supply_candidates, key=lambda z: z.bottom - current_price) if supply_candidates else None

    return {"nearest_demand": nearest_demand, "nearest_supply": nearest_supply}
