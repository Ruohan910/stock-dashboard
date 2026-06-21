# PRODUCT SPEC PROMPT — "Stock Valuation Dashboard" (working name)

Use this prompt to brief any developer (human or AI) on building this product.
Copy this whole document as the build instruction.

---

## 1. WHAT THIS IS

A web-based stock valuation dashboard that replaces a manual Excel DCF workbook.
Users add any stock ticker to a personal watchlist, click refresh, and the app
pulls live financial data, calculates intrinsic value via DCF, and displays
pricing multiples, profitability ratios, and (later) supply/demand chart zones
— all based on the LearningAlpha valuation methodology (see Section 5).

This is being built as a future product for other people to use, not just a
personal tool. Build accordingly: clean data layer, swappable data sources,
multi-user-ready architecture — even though v1 ships as single-user.

---

## 2. CORE USER FLOW (v1)

1. User opens the dashboard (web browser, desktop or mobile)
2. User adds a ticker to their Watchlist (free text input, e.g. "NVDA")
   — no preset list; user builds their own from scratch
   — user can remove tickers from the watchlist anytime
3. User clicks "Refresh" on a ticker (or "Refresh All")
   — this is a MANUAL trigger only in v1 — no auto-scheduling, no cron,
     no background polling
4. App fetches fresh data, runs all calculations, updates the display
5. User views: company snapshot, DCF intrinsic value, pricing multiples,
   profitability ratios, verdict (undervalued / fair / overvalued)
6. Data is cached after fetch — re-opening the dashboard shows last-fetched
   data immediately without auto re-fetching (avoids unnecessary API calls)

---

## 3. DATA ARCHITECTURE (important — build this abstracted, not hardcoded)

### 3.1 Data source abstraction layer
Even though v1 only uses Yahoo Finance (via `yfinance` Python library), the
code must NOT call yfinance directly from business logic. Build a
`DataProvider` interface/abstract class with methods like:

```
get_fcf(ticker) -> float
get_growth_estimate(ticker) -> dict  # {value, source, confidence}
get_balance_sheet_summary(ticker) -> dict
get_shares_outstanding(ticker) -> float
get_beta(ticker) -> float
get_current_price(ticker) -> float
get_company_info(ticker) -> dict  # name, sector, etc.
```

`YahooFinanceProvider` implements this interface for v1. Future providers
(Macrotrends for historical PE ranges, a brokerage API for real-time quotes,
a news/sentiment provider) plug in as additional classes implementing the
same interface — without touching calculation logic or UI code.

### 3.2 Known data quality issues to handle (learned from real testing)
These are real bugs discovered while testing yfinance — the spec must
explicitly handle them:

- **Growth rate field is unreliable.** yfinance's `growth_estimates` table
  is indexed by period (`0q`, `+1q`, `0y`, `+1y`, `LTG`). `LTG` (Long Term
  Growth) is the correct ~5yr analyst estimate but is frequently `NaN`.
  Fallback chain required:
  1. Try `LTG` row first
  2. Fall back to `+1y` (next fiscal year estimate)
  3. Fall back to `revenueGrowth` from ticker info
  4. **Sanity cap at 60%** — anything above this is almost certainly a
     distorted trailing/quarterly figure (e.g. `earningsGrowth` can show
     200%+ from easy prior-year comps), not a real forward growth rate.
  5. Always surface WHICH source was used and whether it was capped, so
     the user can see data confidence at a glance (not buried in logs).

- **`totalCurrentAssets` in `.info` is frequently `None`** in newer
  yfinance versions. Must fall back to reading the `Current Assets` row
  from the `.balance_sheet` DataFrame directly.

- **General principle:** Yahoo Finance (and any data source) WILL return
  incomplete or weird data for some tickers. Every data field shown to the
  user needs a visible confidence/source indicator, not a silent guess.
  Never show a number without the user being able to see where it came from.

### 3.3 Caching
- Cache fetched data per ticker with a timestamp ("Last updated: [time]")
- Manual refresh only — no auto-refresh, no polling, no scheduled jobs in v1
- Cache persists across sessions (don't refetch on every page load)

---

## 4. CALCULATIONS REQUIRED (exact formulas — do not approximate)

### 4.1 DCF Intrinsic Value
- Inputs: FCF (TTM), Growth Yr 1–5 (from data layer above), Shares
  Outstanding, Current Assets, Total Debt, Beta
- Growth Yr 6–10 = Growth Yr 1–5 ÷ 2 (half the rate — tapering assumption)
- Growth Yr 11–20 = 4% (fixed, long-run inflation rate)
- Discount rate from Beta lookup table:

  | Beta | Discount Rate |
  |------|---------------|
  | < 0.80 | 5.2% |
  | ~1.00 | 5.9% |
  | ~1.10 | 6.3% |
  | ~1.20 | 6.6% |
  | ~1.30 | 7.0% |
  | ~1.40 | 7.4% |
  | ~1.50 | 7.7% |
  | > 1.60 | 8.1% |

- Project FCF for 20 years using the three growth-rate tiers above
- Discount each year's projected FCF back to present value:
  `PV = FCF_year / (1 + discount_rate)^year`
- Sum all 20 years of PV = Total PV of future cash flows
- Cash per share = (Current Assets − Total Debt) ÷ Shares Outstanding
- **Intrinsic Value per share = (Total PV ÷ Shares Outstanding) + Cash per share**
- Compare to current market price → % upside/downside → verdict

### 4.2 Pricing Multiples
- P/E = Market Cap ÷ Net Income (also show vs 5–10yr historical range)
- PEG = P/E ÷ (Expected Net Income Growth × 100); <1.0 = undervalued signal
- P/FCF = Market Cap ÷ Free Cash Flow

### 4.3 Profitability Ratios (5 indicators)
- Gross Profit Margin = Gross Profit ÷ Revenue × 100%
- Net Profit Margin = Net Income ÷ Revenue × 100%
- ROA = Net Income ÷ Average Total Assets × 100%
- ROE = Net Income ÷ Shareholders' Equity × 100%
- EPS = Net Income ÷ Average Outstanding Shares (show trend, not just current)

### 4.4 Financial Health Screen (pass/fail gate, shown prominently)
Over trailing 5 years, flag:
- Is revenue growing YoY? (flag any down year)
- Is net income growing YoY?
- Is FCF positive and growing?
- Does operating cash flow consistently exceed net income? (sound financial
  position signal per the course methodology)
Output: PASS / CONDITIONAL PASS / FAIL with one-line reasoning

---

## 5. METHODOLOGY SOURCE OF TRUTH

All formulas, thresholds, and verdict logic must match the LearningAlpha
"U.S. Stock Market Valuation & Option Derivatives" course handbook exactly
— this is a teaching methodology the user is actively studying, not generic
finance. Do not substitute alternate industry-standard formulas (e.g. WACC-based
discount rates, perpetuity growth models) without flagging the deviation
explicitly. If in doubt about a number, default to the course's simpler
heuristic over a more "technically correct" finance textbook approach.

---

## 6. UI / SCREENS (v1 scope)

### Screen 1 — Watchlist (home)
- List of user's tracked tickers as cards: Ticker, Company name, Current
  price, Intrinsic value, Verdict badge (color-coded: green=undervalued,
  yellow=fair, red=overvalued), Last updated timestamp
- "+ Add ticker" input field
- "Refresh" button per card + "Refresh All" button
- Remove ticker action per card

### Screen 2 — Ticker Detail (click into a card)
- Company snapshot: name, sector, current price, market cap
- Financial Health Screen result (pass/fail badge + reasoning)
- DCF section: full 20-year projection table (collapsible), intrinsic value,
  upside/downside %, verdict
- Pricing Multiples section: P/E, PEG, P/FCF with historical context
- Profitability Ratios section: the 5 indicators in a clean table
- Data source/confidence indicators next to any field that used a fallback
  or was capped (see Section 3.2)
- "Last updated: [timestamp]" + manual Refresh button

### Out of scope for v1 (future phases — note in code comments where these
would hook in, but do not build yet):
- Supply/Demand zone chart overlay (Chapter 5 methodology)
- Multi-ticker comparison / leaderboard view
- Option seller (CSP) setup calculator
- News/earnings sentiment analysis
- Multi-user accounts, auth, billing
- Auto-refresh / scheduled data pulls

---

## 7. TECH STACK GUIDANCE

- Backend: Python (reuses logic already validated in `dcf_auto_update.py`
  and the diagnostic scripts — port the working data-fetch + calculation
  logic rather than rewriting from scratch)
- Suggested: FastAPI or Flask backend + simple frontend (server-rendered
  templates or a lightweight JS frontend — no need for a heavy framework
  for v1)
- Must run locally first (user's own machine) before any consideration of
  public deployment
- Keep the `DataProvider` abstraction (Section 3.1) as a clean Python
  interface/ABC regardless of framework choice

---

## 8. COMPLIANCE / DISCLAIMER (non-negotiable, every screen)

Every page showing valuation output must display:
"For educational purposes only. Not investment advice. [Product name] is
not a licensed financial advisor."

This mirrors the disclaimer in the LearningAlpha course material and must
carry through even if this becomes a public product later.

---

## 9. OPEN QUESTIONS (intentionally deferred — revisit after v1 works)

- Business model (subscription vs free vs personal brand tool) — not decided
- Additional data sources beyond Yahoo Finance — not decided, architecture
  just needs to support adding them later
- Multi-user / auth — not needed for v1, but don't paint the architecture
  into a single-user-only corner
- Mobile app vs responsive web — start responsive web, native app TBD
