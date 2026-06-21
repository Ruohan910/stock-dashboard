"""
Diagnostic — inspect AMZN's cashflow statement to find why FCF looks too low.
"""
import yfinance as yf

t = yf.Ticker("AMZN")

print("=" * 70)
print("cashflow — full table, all columns (years):")
print("=" * 70)
cf = t.cashflow
print("Columns (periods):", list(cf.columns))
print()
for label in cf.index:
    if "free cash" in str(label).lower() or "operating cash" in str(label).lower() or "capital expenditure" in str(label).lower():
        print(f"\n{label}:")
        print(cf.loc[label])

print("\n" + "=" * 70)
print("info dict — cash flow related keys:")
print("=" * 70)
info = t.info
for key in ["operatingCashflow", "freeCashflow", "capitalExpenditures", "totalCash", "totalDebt"]:
    print(f"  {key}: {info.get(key)}")

print("\n" + "=" * 70)
print("ttm cashflow (if available as separate property):")
print("=" * 70)
try:
    ttm_cf = t.ttm_cashflow
    print(ttm_cf)
except Exception as e:
    print(f"No ttm_cashflow property: {e}")

try:
    qcf = t.quarterly_cashflow
    print("\nQuarterly cashflow columns:", list(qcf.columns))
    if "Free Cash Flow" in qcf.index:
        print("\nQuarterly Free Cash Flow (last 4 quarters):")
        print(qcf.loc["Free Cash Flow"].head(4))
except Exception as e:
    print(f"No quarterly_cashflow: {e}")
