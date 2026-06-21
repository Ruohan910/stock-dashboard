"""
Diagnostic — dump GOOGL weekly candles so we can manually verify whether
a basing-candle pattern exists near $274 that the algorithm should have
caught (or correctly didn't).
"""
import yfinance as yf

t = yf.Ticker("GOOGL")
hist = t.history(period="1y", interval="1wk")

print(f"{'Date':<12} {'Open':>8} {'High':>8} {'Low':>8} {'Close':>8} {'Body':>8} {'Range':>8}")
for idx, row in hist.iterrows():
    body = abs(row['Close'] - row['Open'])
    rng = row['High'] - row['Low']
    date_str = str(idx.date())
    print(f"{date_str:<12} {row['Open']:>8.2f} {row['High']:>8.2f} {row['Low']:>8.2f} {row['Close']:>8.2f} {body:>8.2f} {rng:>8.2f}")
