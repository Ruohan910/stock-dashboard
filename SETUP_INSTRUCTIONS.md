# Stock Valuation Dashboard — v1 Setup

## Folder structure (must match exactly)

```
stock_dashboard/
├── app.py
├── data_provider.py
├── yahoo_provider.py
├── calculations.py
├── requirements.txt
└── templates/
    ├── base.html
    ├── watchlist.html
    └── detail.html
```

Download all files keeping this exact structure — `templates/` must be a
subfolder, not flat alongside the .py files (Flask requires this).

## Setup

```
cd stock_dashboard
pip install -r requirements.txt
python app.py
```

Then open your browser to: **http://127.0.0.1:5000**

## What it does

- Add any ticker to your watchlist (free text input)
- Click refresh to pull live data from Yahoo Finance and calculate:
  - Financial Health Screen (PASS / CONDITIONAL PASS / FAIL)
  - DCF Intrinsic Value (full 20-year projection, matches your Excel logic)
  - Pricing Multiples (P/E, PEG, P/FCF)
  - Profitability Ratios (Gross/Net margin, ROA, ROE, EPS)
- Click into any ticker for the full detail view, including a "Data Sources
  & Confidence" section showing exactly where every number came from and
  whether it was a fallback/estimate

## Known things to expect

- First fetch for a new ticker takes a few seconds (live API calls to Yahoo Finance)
- Growth rate will show "medium" or "low" confidence for many tickers —
  this is expected and intentional (see the note text under each field).
  Yahoo Finance's 5-year analyst estimate (LTG) is frequently unavailable;
  the app falls back transparently rather than guessing silently.
- `watchlist.json` will be created automatically in the same folder on first run —
  this stores your watchlist and cached data between sessions.
- If a ticker fails to fetch (bad symbol, Yahoo Finance rate limit, etc.)
  the card will show the error with a Retry button instead of crashing the page.

## Known limitations (v1, by design — see product spec)

- Single user, no login
- Manual refresh only (no auto-refresh / scheduling)
- Yahoo Finance only (architecture supports adding more sources later —
  see `data_provider.py`)
- No Supply/Demand zone charts yet (Chapter 5 methodology — future phase)

## If something breaks

Copy the full error message (including the traceback in the terminal) and
send it back — same process as debugging `dcf_auto_update.py`.
