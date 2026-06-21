"""
Diagnostic — dump GOOGL daily candles, focused on the March-April reversal,
and run the actual detect_zones() function with debug output to see why
no fresh demand zone is surfacing on the Daily timeframe.
"""
import yfinance as yf
from supply_demand import detect_zones, _to_candles, _average_range, _is_basing_candle, _strong_move_after, STRONG_MOVE_LOOKAHEAD_CANDLES

t = yf.Ticker("GOOGL")
hist = t.history(period="6mo", interval="1d")

print(f"Total daily candles fetched: {len(hist)}")
print(f"\n{'Date':<12} {'Open':>8} {'High':>8} {'Low':>8} {'Close':>8} {'Body':>8} {'Range':>8}")

candles_raw = []
for idx, row in hist.iterrows():
    date_str = str(idx.date())
    candles_raw.append({
        "date": date_str,
        "open": float(row["Open"]),
        "high": float(row["High"]),
        "low": float(row["Low"]),
        "close": float(row["Close"]),
    })

for c in candles_raw:
    body = abs(c["close"] - c["open"])
    rng = c["high"] - c["low"]
    print(f"{c['date']:<12} {c['open']:>8.2f} {c['high']:>8.2f} {c['low']:>8.2f} {c['close']:>8.2f} {body:>8.2f} {rng:>8.2f}")

print("\n" + "="*70)
print("Running detect_zones with months_lookback=6:")
print("="*70)
zones = detect_zones(candles_raw, months_lookback=6)
print(f"Zones returned (after broken-zone filtering): {len(zones)}")
for z in zones:
    print(f"  [{z.zone_type}] {z.bottom:.2f}-{z.top:.2f} fresh={z.is_fresh} touches={z.touches} broken={z.is_broken} formed={z.formed_date}")

print("\n" + "="*70)
print("Manually scanning for ALL basing candles + strong move up (before broken-filter):")
print("="*70)
candles = _to_candles(candles_raw)
avg_range = _average_range(candles)
print(f"Average range across all candles: {avg_range:.2f}\n")

for i in range(len(candles) - 1 - STRONG_MOVE_LOOKAHEAD_CANDLES):
    candle = candles[i]
    is_basing = _is_basing_candle(candle, avg_range, candles, i)
    if is_basing:
        strong_up = _strong_move_after(candles, i, "up")
        if strong_up:
            print(f"  CANDIDATE at index {i}: date={candle.date} body={candle.body_size:.2f} "
                  f"zone=[{candle.low:.2f}-{candle.body_top:.2f}] -> strong move UP confirmed")
EOF
