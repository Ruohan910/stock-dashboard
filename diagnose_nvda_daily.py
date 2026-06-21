"""
Diagnostic — dump NVDA daily candles to manually verify why the algorithm
picked one basing candle over a visually similar neighboring candle.
"""
import yfinance as yf
from supply_demand import _to_candles, _average_range, _is_basing_candle, _strong_move_after, DEFAULT_STRONG_MOVE_LOOKAHEAD_CANDLES

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

print(f"Total candles: {len(candles_raw)}")
print(f"\n{'Date':<12} {'Open':>8} {'High':>8} {'Low':>8} {'Close':>8} {'Body':>8}")

# Print the most recent ~30 candles (covers the visible chart window)
recent = candles_raw[-30:]
for c in recent:
    body = abs(c["close"] - c["open"])
    print(f"{c['date']:<12} {c['open']:>8.2f} {c['high']:>8.2f} {c['low']:>8.2f} {c['close']:>8.2f} {body:>8.2f}")

print("\n" + "="*70)
print("Checking each candle in this window as a basing-candle candidate:")
print("="*70)
candles = _to_candles(candles_raw)
avg_range = _average_range(candles)
print(f"Global avg_range: {avg_range:.2f}\n")

start_idx = len(candles) - 30
for i in range(start_idx, len(candles) - 1 - DEFAULT_STRONG_MOVE_LOOKAHEAD_CANDLES):
    c = candles[i]
    is_basing = _is_basing_candle(c, avg_range, candles, i)
    if is_basing:
        strong_up = _strong_move_after(candles, i, "up")
        print(f"  idx={i} {c.date}: body={c.body_size:.2f} BASING=True, strong_move_up={strong_up}")
