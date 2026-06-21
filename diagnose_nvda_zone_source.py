"""
Diagnostic — find exactly which basing candle formed the $194.51-$199.27
demand zone, using the app's real production config (detect_zones_for_timeframe).
"""
import yfinance as yf
from supply_demand import detect_zones_for_timeframe

t = yf.Ticker("NVDA")
hist = t.history(period="2y", interval="1d")

candles_raw = []
for idx, row in hist.iterrows():
    candles_raw.append({
        "date": str(idx.date()),
        "open": float(row["Open"]),
        "high": float(row["High"]),
        "low": float(row["Low"]),
        "close": float(row["Close"]),
    })

zones = detect_zones_for_timeframe(candles_raw, "daily")
print(f"Total zones detected: {len(zones)}\n")
for z in zones:
    print(f"[{z.zone_type.upper():6}] ${z.bottom:7.2f} - ${z.top:7.2f}  formed {z.formed_date}  "
          f"idx={z.basing_candle_index}  fresh={z.is_fresh}  touches={z.touches}")

# Show context around the zone matching the screenshot ($194.51-$199.27)
print("\n" + "="*70)
print("Looking for the zone matching the screenshot ($194.51-$199.27):")
print("="*70)
target = [z for z in zones if abs(z.bottom - 194.51) < 1 or abs(z.top - 199.27) < 1]
for z in target:
    idx = z.basing_candle_index
    print(f"\nFound: idx={idx}, formed={z.formed_date}")
    print(f"\nContext (10 candles before and after idx={idx}):")
    print(f"{'Date':<12} {'Open':>8} {'High':>8} {'Low':>8} {'Close':>8} {'Body':>8}")
    for i in range(max(0, idx-5), min(len(candles_raw), idx+10)):
        c = candles_raw[i]
        body = abs(c["close"] - c["open"])
        marker = " <-- BASING CANDLE" if i == idx else ""
        print(f"{c['date']:<12} {c['open']:>8.2f} {c['high']:>8.2f} {c['low']:>8.2f} {c['close']:>8.2f} {body:>8.2f}{marker}")
