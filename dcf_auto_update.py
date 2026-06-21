"""
DCF Auto-Update Script
======================
Fetches live data from Yahoo Finance and updates your DCF Excel workbook.

Setup (run once):
    pip install yfinance openpyxl

Usage:
    python dcf_auto_update.py                        # update all sheets
    python dcf_auto_update.py --tickers NVDA MSFT    # update specific tickers only
    python dcf_auto_update.py --dry-run              # preview without saving

Output: saves a new file  <original_name>_updated_<YYYYMMDD>.xlsx
"""

import argparse
import shutil
import sys
from datetime import date
from pathlib import Path

import yfinance as yf
from openpyxl import load_workbook

# ── Beta → discount rate table (from your teacher's model) ──────────────────
BETA_DISCOUNT_TABLE = [
    (0.80, 1.052),
    (1.00, 1.059),
    (1.10, 1.063),
    (1.20, 1.066),
    (1.30, 1.070),
    (1.40, 1.074),
    (1.50, 1.077),
    (float("inf"), 1.081),
]

def beta_to_discount_rate(beta: float) -> float:
    """Return discount rate (as stored in Excel, e.g. 1.081) from beta value."""
    if beta is None or beta != beta:  # None or NaN
        return 1.066  # default mid-range
    for threshold, rate in BETA_DISCOUNT_TABLE:
        if beta <= threshold:
            return rate
    return 1.081


def get_yf_data(ticker_symbol: str) -> dict | None:
    """
    Fetch all needed fields from Yahoo Finance for one ticker.
    Returns a dict of values, or None if the ticker fails.
    """
    ticker_symbol = ticker_symbol.strip().upper()
    try:
        t = yf.Ticker(ticker_symbol)
        info = t.info

        # ── Free Cash Flow (TTM) ─────────────────────────────────────────────
        # Try cashflow statement first (most accurate)
        fcf = None
        try:
            cf = t.cashflow
            if cf is not None and not cf.empty:
                for label in ["Free Cash Flow", "FreeCashFlow"]:
                    if label in cf.index:
                        fcf = float(cf.loc[label].iloc[0])
                        break
        except Exception:
            pass

        # Fallback: operatingCashflow - capex from info
        if fcf is None:
            op_cf = info.get("operatingCashflow")
            capex = info.get("capitalExpenditures")
            if op_cf and capex:
                fcf = float(op_cf) + float(capex)   # capex is negative in yfinance
            elif op_cf:
                fcf = float(op_cf)

        # Convert to thousands (Excel stores values in thousands)
        fcf_thousands = round(fcf / 1_000) if fcf else None

        # ── Growth rate Year 1-5 ─────────────────────────────────────────────
        # Yahoo Finance analyst long-term growth estimate.
        # New yfinance (1.x) growth_estimates is indexed by period:
        #   '0q','+1q','0y','+1y','LTG'  (LTG = Long Term Growth, ~5yr analyst consensus)
        # 'LTG' is the correct field for "Year 1-5" — NOT earningsGrowth (that's
        # trailing quarterly growth and can be wildly distorted, e.g. 200%+).
        growth_5yr = None
        growth_source = None
        try:
            growth_df = t.growth_estimates
            if growth_df is not None and not growth_df.empty and "LTG" in growth_df.index:
                val = growth_df.loc["LTG", "stockTrend"]
                if val == val and val is not None:  # not NaN
                    growth_5yr = float(val)
                    growth_source = "LTG (analyst long-term growth)"
        except Exception:
            pass

        # Fallback 1: '+1y' forward estimate (next fiscal year growth) — far more
        # reasonable than trailing earningsGrowth, though shorter horizon than 5yr.
        if growth_5yr is None:
            try:
                growth_df = t.growth_estimates
                if growth_df is not None and "+1y" in growth_df.index:
                    val = growth_df.loc["+1y", "stockTrend"]
                    if val == val and val is not None:
                        growth_5yr = float(val)
                        growth_source = "+1y forward estimate (LTG unavailable)"
            except Exception:
                pass

        # Fallback 2: revenueGrowth from info (more stable than earningsGrowth,
        # which can spike due to easy prior-year comps / one-off items)
        if growth_5yr is None:
            rg = info.get("revenueGrowth")
            if rg:
                growth_5yr = float(rg)
                growth_source = "revenueGrowth (fallback — verify manually)"

        # Sanity cap: analyst long-term growth rates are essentially never above
        # 60% annualized. Anything higher is almost certainly a distorted
        # trailing/quarterly figure, not a real 5-yr forward estimate.
        if growth_5yr is not None and growth_5yr > 0.60:
            growth_source = f"{growth_source} [CAPPED — raw value {growth_5yr:.1%} looked unreliable, review manually]"
            growth_5yr = 0.60

        # Convert to teacher's format: e.g. 15% growth → stored as 1.15
        growth_1_5 = round(1 + growth_5yr, 4) if growth_5yr is not None else None

        # ── Balance Sheet ────────────────────────────────────────────────────
        # NOTE: 'totalCurrentAssets' is no longer populated in info for many
        # tickers in newer yfinance — pull from the balance sheet statement instead.
        total_assets = info.get("totalCurrentAssets")
        if total_assets is None:
            try:
                bs = t.balance_sheet
                if bs is not None and not bs.empty:
                    for label in ["Current Assets", "CurrentAssets", "Total Current Assets"]:
                        if label in bs.index:
                            total_assets = float(bs.loc[label].iloc[0])
                            break
            except Exception:
                pass

        total_debt   = info.get("totalDebt")
        total_cash   = info.get("totalCash")

        # Convert to thousands
        cur_assets_thousands = round(total_assets / 1_000) if total_assets else None
        total_debt_thousands = round(total_debt   / 1_000) if total_debt   else None

        # ── Shares Outstanding ───────────────────────────────────────────────
        shares = info.get("sharesOutstanding")
        shares_thousands = round(shares / 1_000) if shares else None

        # ── Beta → Discount Rate ─────────────────────────────────────────────
        beta = info.get("beta")
        discount_rate = beta_to_discount_rate(beta)

        # ── Company Name ─────────────────────────────────────────────────────
        name = info.get("longName") or info.get("shortName") or ticker_symbol

        return {
            "ticker":           ticker_symbol,
            "name":             name,
            "fcf_thousands":    fcf_thousands,
            "growth_1_5":       growth_1_5,
            "growth_source":    growth_source,
            "cur_assets_th":    cur_assets_thousands,
            "total_debt_th":    total_debt_thousands,
            "shares_th":        shares_thousands,
            "beta":             beta,
            "discount_rate":    discount_rate,
        }

    except Exception as e:
        print(f"  ⚠  Failed to fetch {ticker_symbol}: {e}")
        return None


def find_sym_row(ws) -> int | None:
    """Find the row number where 'Stock symbol' label appears in column B."""
    for row in ws.iter_rows(min_row=10, max_row=25, min_col=2, max_col=2):
        for cell in row:
            if cell.value and "Stock symbol" in str(cell.value):
                return cell.row
    return None


def update_sheet(ws, data: dict, dry_run: bool = False) -> bool:
    """
    Write fetched data into the correct cells of one sheet.
    All Excel formulas (DCF rows, intrinsic value) remain untouched.
    Returns True if any cell was changed.
    """
    sym_row = find_sym_row(ws)
    if sym_row is None:
        return False

    # Relative offsets from 'Stock symbol' row
    # (verified across nvda/msft/googl/avgo layouts)
    fcf_row      = sym_row + 1   # D: FCF value
    g1_row       = sym_row + 4   # C: growth yr 1-5   (or sym+3 for avgo-style)
    assets_row   = sym_row + 1   # H: current assets
    debt_row     = sym_row + 2   # H: total debt
    shares_row   = sym_row + 7   # C: shares outstanding
    disc_row     = sym_row + 7   # H: discount rate

    # Some sheets have FCF one row earlier — detect by checking if D(sym+1) is numeric
    fcf_cell = ws.cell(row=fcf_row, column=4)
    if fcf_cell.value is None or not isinstance(fcf_cell.value, (int, float)):
        # Try one row up
        fcf_cell = ws.cell(row=sym_row, column=4)
        if not isinstance(fcf_cell.value, (int, float)):
            print(f"  ⚠  Could not locate FCF cell in sheet '{ws.title}'")
            return False
        fcf_row = sym_row
        assets_row = sym_row
        debt_row   = sym_row + 1
        g1_row     = sym_row + 3
        shares_row = sym_row + 6
        disc_row   = sym_row + 6

    # Re-detect g1 row: look for "Year 1-5" label
    for rr in range(sym_row + 2, sym_row + 8):
        label = ws.cell(row=rr, column=2).value
        if label and "Year 1-5" in str(label):
            g1_row     = rr
            shares_row = rr + 3
            disc_row   = rr + 3
            break

    changes = []

    def maybe_write(cell, value, label):
        if value is None:
            return
        if cell.value != value:
            changes.append(f"    {label}: {cell.value!r} → {value!r}  [{cell.coordinate}]")
            if not dry_run:
                cell.value = value

    maybe_write(ws.cell(row=fcf_row,    column=4), data["fcf_thousands"],  "FCF (thousands)")
    g1_label = f"Growth Yr 1-5 [{data.get('growth_source', 'unknown source')}]"
    maybe_write(ws.cell(row=g1_row,     column=3), data["growth_1_5"],     g1_label)
    maybe_write(ws.cell(row=assets_row, column=8), data["cur_assets_th"],  "Current assets")
    maybe_write(ws.cell(row=debt_row,   column=8), data["total_debt_th"],  "Total debt")
    maybe_write(ws.cell(row=shares_row, column=3), data["shares_th"],      "Shares outstanding")
    maybe_write(ws.cell(row=disc_row,   column=8), data["discount_rate"],  "Discount rate")

    if changes:
        print(f"  Changes for {data['ticker']}:")
        for c in changes:
            print(c)
    else:
        print(f"  {data['ticker']}: no changes needed")

    return bool(changes)


def process_workbook(excel_path: str, filter_tickers: list[str] | None = None,
                     dry_run: bool = False):
    """Main entry point: load workbook, update all iv sheets, save."""
    path = Path(excel_path)
    if not path.exists():
        print(f"Error: file not found → {excel_path}")
        sys.exit(1)

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Loading: {path.name}")
    wb = load_workbook(path)

    updated = 0
    skipped = 0
    failed  = 0

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        sym_row = find_sym_row(ws)
        if sym_row is None:
            continue  # not a DCF sheet

        raw_ticker = ws.cell(row=sym_row, column=3).value
        if not raw_ticker or str(raw_ticker).strip().lower() in ["us$", ""]:
            continue

        ticker = str(raw_ticker).strip().upper()

        # Skip sample sheets
        if "sample" in sheet_name.lower():
            print(f"\n[{sheet_name}] Skipping sample sheet")
            continue

        # Filter by requested tickers if provided
        if filter_tickers and ticker not in [t.upper() for t in filter_tickers]:
            continue

        # Skip tickers with no valid Yahoo Finance symbol (non-US, private)
        SKIP_TICKERS = {"FUTU", "RCECAP", "LSXMK"}
        if ticker in SKIP_TICKERS:
            print(f"\n[{sheet_name}] Skipping {ticker} (not on Yahoo Finance / manual only)")
            skipped += 1
            continue

        print(f"\n[{sheet_name}] Fetching {ticker} from Yahoo Finance...")
        data = get_yf_data(ticker)
        if data is None:
            failed += 1
            continue

        print(f"  Beta={data['beta']:.2f}  →  Discount rate={data['discount_rate']}")
        changed = update_sheet(ws, data, dry_run=dry_run)
        if changed:
            updated += 1

    print(f"\n{'─'*50}")
    print(f"Sheets updated : {updated}")
    print(f"Sheets skipped : {skipped}")
    print(f"Fetch failures : {failed}")

    if not dry_run and updated > 0:
        today = date.today().strftime("%Y%m%d")
        out_path = path.parent / f"{path.stem}_updated_{today}.xlsx"
        wb.save(out_path)
        print(f"\n✅  Saved → {out_path}")
        print("    Open in Excel to recalculate — press Ctrl+Alt+F9 if values look stale.")
    elif dry_run:
        print("\n[DRY RUN] No file saved.")
    else:
        print("\nNo changes detected — file unchanged.")


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Auto-update DCF Excel with Yahoo Finance data")
    parser.add_argument("file", nargs="?",
                        default="Ruo_Han_IV_excel_-_DCF_PRICING_MULTIPLES___INTRINSIC_VALUE_V2.xlsx",
                        help="Path to your DCF Excel file")
    parser.add_argument("--tickers", nargs="+", metavar="TICKER",
                        help="Only update these tickers (e.g. --tickers NVDA MSFT)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview changes without saving")
    args = parser.parse_args()

    process_workbook(args.file, filter_tickers=args.tickers, dry_run=args.dry_run)
