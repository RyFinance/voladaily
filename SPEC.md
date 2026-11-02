# Voladaily (formerly Barometer)

Working name. Design is the user's call; this spec covers what it does, where the data comes from, and how we prove it works.

## The core idea

Nobody can forecast direction reliably, but **volatility can be forecast**. Big-move days cluster, calm follows calm, and scheduled events carry a measurable volatility bump. A weather app is the right format: it deals in probabilities and ranges, and it gets scored on whether the probabilities hold up.

Every number Barometer shows is a probability or a range. It never says "up" or "down" with confidence. Where direction appears at all (the month outlook), it's shown as a base rate.

---

## Features

### 1. Today
- **Main reading:** expected move (±%) and the odds of a big day.
  - Example: "SPY ±0.7% · 18% chance of a 1%+ move."
- **Condition:** calm / normal / choppy / storm, from the forecast move compared with that asset's own history.
- **What's driving it:** the scheduled events, the current volatility regime, and what options imply.
  - Example: "CPI at 8:30 adds about 0.3 points."
- **Weather readings (each one a real metric, not decoration):**

  | Weather reading | What it actually measures |
  |---|---|
  | Temperature | Volatility level vs its own 1-year range (percentile) |
  | Chance of rain | P(\|move\| ≥ 1%) (threshold set per asset) |
  | Feels like | Realized volatility vs implied ("VIX says 18, it's moving like 24") |
  | Pressure | Credit/funding stress trend: high-yield spreads, MOVE index, VIX term-structure slope |
  | UV / tail risk | Crash pricing: SKEW, VVIX, put skew |
  | Visibility | Forecast confidence (width of the uncertainty band) |
  | Storm warning | A high-impact scheduled event within 48 hours |

### 2. Hourly strip (event days)
- Volatility has a known daily shape: a U-curve, with spikes at 8:30 (macro data), 10:00 (ISM and other surveys), 14:00 (Fed decision), 14:30 (Fed press conference) and the close.
- On event days it shows when the storm hits. Example: "most of today's move is expected between 8:30 and 9:45."

### 3. 10-day forecast
- One row per day: condition, expected move, chance of a big day, and the event chips for that day (CPI, FOMC, NFP, NVDA earnings, OpEx).
- Volatility mean-reverts, so days further out drift toward normal and the bands widen. The display has to show that honestly.

### 4. Month outlook
Monte Carlo simulation of the next 21 trading days, using the daily forecasts with volatility allowed to keep changing along each path.
- **Likely monthly range:** 50% and 90% bands.
- **P(5% pullback) and P(10% pullback)** at some point in the month.
- **Expected number of storm days** and which dates they're likely to fall on.
- **Direction:** shown only as the historical base rate ("up in 62% of months like this"), with a confidence interval, never as a call.
- **Calendar heatmap** of expected volatility for each day.

### 5. Per stock
- **Watchlist and holdings:** each one gets its own forecast card.
- **Earnings:**
  - The implied move from options (a straddle priced the day before).
  - Its last 12 actual earnings moves.
  - A **rich/cheap** reading. Example: "options imply ±6.1%; it has moved ±4.3% on average, so options look about 40% expensive."
  - Whether the report lands before the open or after the close.
- **Stock-specific events:** lockup expiries, index adds and deletes, shareholder meetings, FDA decision dates (biotech), debt maturities, investor days.

### 6. Portfolio forecast
- Enter holdings and weights (or import from Meridian or a broker CSV).
- Portfolio volatility comes from a correlation forecast, not a simple sum. Correlations jump in storms, and the model has to capture that.
- **Example output:** "Your book: ±1.3% tomorrow, 9% chance of a −2% day. 60% of your risk this week is NVDA earnings."
- **Risk sizing:** "to keep your usual risk level, cut exposure about 20% this week." (Volatility-targeted position sizing.)

### 7. Radar
- A grid of sectors and asset classes (equities, rates, oil, gold, crypto, FX), coloured by forecast storminess.
- Shows where the weather is heading, for example "storm forming in rates, calm in equities."

### 8. Almanac (history, kept honest)
- **"On days like today":** analog days with similar volatility regime and event mix, and what happened next, with sample sizes and confidence intervals.
- **Per event type:** the average move by event, how it has changed over time (CPI mattered far more in 2022–23 than in 2017), and when the move usually happens.

### 9. Scoreboard (the trust layer, always visible)
Weather services publish their own verification; so does Barometer.
- **Logged before the open:** every forecast is saved before 09:30 ET with a timestamp and hash. It can't be edited afterwards.
- **Calibration chart:** did "30% chance of rain" days get rain about 30% of the time?
- **Scores:** Brier score for the probabilities, QLIKE loss (the standard volatility-forecast loss) and CRPS for the distributions.
- **Baselines it has to beat, all shown:**
  - Naive: tomorrow = the trailing 20-day volatility.
  - VIX alone. VIX is the hard one to beat.
  - A plain HAR model with no events.
- **When it loses to a baseline,** the scoreboard says so plainly.

### 10. Alerts and delivery
- **Morning brief at 07:30 ET:** push notification, email or Discord. Example: "Storm warning: CPI at 8:30, ±1.1% expected."
- **Updates:** a refresh after the 8:30 data, and an intraday change if the regime shifts.
- **JSON API and CLI** (`barometer today`, `barometer month NVDA`), so other tools can read it. Pulse and Audition could use it for volatility-aware sizing.
- Possibly a macOS widget later.

---

## The model

**Forecast target:** next-day realized variance for each asset. Probabilities come from a full predictive distribution, not just one number.

1. **Baseline volatility: HAR-RV** (Corsi 2009). Next-day log variance is regressed on yesterday's, the past week's and the past month's realized variance.
   - Realized variance comes from intraday data where we have it. Otherwise it uses a range-based estimator on daily OHLC (Yang-Zhang or Garman-Klass), which is far better than close-to-close.
   - Asymmetry term: down days raise future volatility more than up days.
2. **Implied information:** VIX, VIX9D, VIX1D, the VIX3M/VIX slope and VVIX enter as regressors (HAR-IV). For single stocks, use ATM implied volatility where we have it.
3. **Event bumps:** a multiplier on expected variance for each event type, estimated from history and allowed to change over time (rolling or exponentially weighted).
   - Per stock, the earnings bump comes from that stock's own history, blended with the options-implied move.
4. **Distribution:** returns are drawn as a fat-tailed (Student-t) distribution, scaled by the variance forecast with its own uncertainty. P(|move| > x) and the quantiles come from that.
5. **Multi-day:** volatility mean-reverts at the speed the HAR model estimates. Event days are layered on top. The month outlook is a Monte Carlo path simulation (10k paths) using GARCH-style volatility updating.
6. **Portfolio:** an EWMA or DCC-lite correlation matrix, with a correlation-stress adjustment on high-volatility days.
7. **Walk-forward only:** every historical forecast uses only data available at 07:30 ET that day. This includes the event calendar *as it was known then*: earnings dates move, and using the final date is a leak. Audition's leakage checks apply here too.

---

## Data sources

| Need | Source | Cost / limits | Notes |
|---|---|---|---|
| Daily OHLCV (stocks, ETFs, indices) | yfinance (already used in Audition) | Free, can be flaky | Fallbacks: Stooq CSV; Alpaca/Polygon if the user connects keys |
| Intraday bars (realized variance, hourly shape) | Alpaca market data (free tier, IEX), LSE terminal `get_candles`, Polygon | Alpaca needs keys, so **ask first** | Only for the hourly strip and better realized variance; daily-only version works without it |
| VIX family history | CBOE free CSVs (`cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv`, also VIX9D/VIX3M/VIX6M/VVIX/SKEW/VIX1D); Yahoo `^VIX` etc. | Free | VIX1D only from 2023 |
| VIX futures curve | CBOE Futures Exchange daily settlement files | Free | Term-structure slope and contango/backwardation |
| MOVE (bond volatility) | Yahoo `^MOVE` | Free | Rates stress |
| Credit spreads, yields, funding | FRED API: HY OAS `BAMLH0A0HYM2`, `DGS10`, `DGS2`, `SOFR`, NFCI | Free key | The pressure reading |
| Macro release dates, past (for backtests) | FRED/ALFRED `releases/dates` | Free key | Exact historical dates for CPI, NFP, PCE, GDP, retail sales and others |
| Macro release dates, future | BLS schedule (bls.gov/schedule, .ics), BEA schedule, Census economic indicator calendar | Free | Scrape or ICS; cross-check with FRED |
| FOMC meetings, minutes, Fed speakers | federalreserve.gov FOMC calendar page | Free | Hand-checked once a year; pressers at 14:30 |
| Treasury auctions | TreasuryDirect API (upcoming and past auctions) | Free | Big 10Y/30Y auctions move rates |
| Market holidays and half days | `exchange_calendars` / `pandas_market_calendars` | Free library | |
| Options expiry, quad witching, VIX expiry | Computed (3rd Friday, VIX Wednesday) | Free | |
| Index rebalances | S&P quarterly (3rd Friday of Mar/Jun/Sep/Dec); FTSE Russell reconstitution schedule; Nasdaq-100 December reconstitution | Free (published) | Russell's 2026 schedule change needs checking against the official notice |
| Earnings dates, past (exact, no leak) | SEC EDGAR 8-K Item 2.02 filings: the acceptance time says before the open or after the close | Free (approved user-agent) | Best free historical source; avoids Yahoo's revised dates |
| Earnings dates, future | yfinance `get_earnings_dates`, Nasdaq earnings calendar JSON, Alpha Vantage `EARNINGS_CALENDAR` (free key) | Free | Mark "confirmed" vs "estimated"; companies move dates |
| Options chains (implied moves now) | yfinance option chains (snapshot); CBOE delayed quotes | Free, delayed 15 min | Straddle price of the nearest expiry after earnings = implied move |
| Historical implied moves (backtest rich/cheap) | LSE terminal vault: OPRA option prints from 2014 (export ~5 per hour, 2.5M-row cap) | Already available, but slow | Build a sample of about 50 big names × 12 years of earnings over time |
| Lockup expiries | EDGAR S-1/424B prospectus (lockup length) + IPO date | Free | Usually 180 days; parse the text |
| Index adds/deletes | S&P press releases; Wikipedia change table (already parsed in Audition's `universes.py`) | Free | |
| Uncertainty indices | Economic Policy Uncertainty daily (policyuncertainty.com); Caldara-Iacoviello geopolitical risk index | Free CSV | Slow-moving context, not daily drivers |
| Crypto volatility | Deribit DVOL (public API); Binance klines | Free | For BTC/ETH cards |

**Storage:** SQLite. Tables for bars, events (with an "as-known-on" date), forecasts (append-only and hashed) and realized outcomes.

**Scheduling:** a LaunchAgent, the same pattern as Pulse.
- 06:00 ET: data pull.
- 07:30 ET: forecast logged and alerts sent.
- 08:35 ET: refresh after the data releases.
- 16:30 ET: realized outcomes scored.

---

## Proof plan (before any UI)

1. **Build the scoreboard first.** Run a walk-forward on SPY, QQQ, IWM, TLT and GLD from 2010 to 2026, plus about 30 large stocks. Compare against the naive, VIX-only and plain HAR baselines.
2. **Go/no-go:**
   - SPY must beat VIX-only on QLIKE, or at least match it while adding events and per-stock coverage.
   - The calibration chart has to sit close to the diagonal.
   - If it only matches VIX, Barometer is still useful (per-stock, events, portfolio, presentation), but the claims get toned down to match.
3. **Event study:** measure the bump for each event type, how it has changed over time, and when in the day it hits.
4. **Earnings rich/cheap:** using the OPRA sample, test whether implied moves are systematically too high. The literature says they usually are, and this is the single most useful number for options traders.

## Build order

1. Data layer and event calendar (with as-known-on dates).
2. HAR + events + implied model, plus the walk-forward scoreboard. This decides whether the claims are allowed.
3. Today, the 10-day forecast and the month Monte Carlo for indices and ETFs.
4. Per-stock cards and earnings rich/cheap.
5. Portfolio forecast and the radar.
6. Live pre-open logging, the public scoreboard, morning alerts and the API/CLI.
7. Hourly strip, once intraday data is connected.
8. Design pass (the user's call), then a slimecode.tech page and a public repo.

## Research round 2 (2026-11-01): what else matters

### Who's already here, and the gap
- **Market Chameleon, EarningsWatcher, VolRadar:** expected moves from options straddles, aimed at earnings traders and premium sellers. All per-ticker and options-only; no daily or monthly forecast, no calibration record.
- **NYU V-Lab:** free GARCH volatility forecasts for thousands of assets. Academic, one number per asset, no events, no product.
- **TradingView, Trading Economics, Koyfin:** calendars with no forecasts attached.
- **SpotGamma, FlashAlpha, SqueezeMetrics:** dealer-gamma (GEX) dashboards, paid ($200–1,000 a month).
- **Nobody combines** the event calendar, volatility model, implied data, per-stock and portfolio forecasts, and a public calibration record. That combination is Barometer's lane. V-Lab is a free baseline to beat and to cite.

### Additional volatility drivers to model
Each is an input to test on the scoreboard, kept only if it improves QLIKE or calibration.

| Driver | Why it matters | Source | Evidence quality |
|---|---|---|---|
| **Dealer gamma (GEX)** | Dealers long gamma damp moves (pinning, mean reversion); short gamma amplifies them (trend days). Biggest intraday factor practitioners cite. | Computed from yfinance or CBOE open interest by strike (snapshot); SqueezeMetrics historical GEX back to 2011 (free CSV claimed, **verify**) | Practitioner, mostly vendors. No peer-reviewed out-of-sample test found, so it's a hypothesis to test |
| **0DTE share of options volume** | Same-day options now dominate SPX volume; changes intraday behaviour around the close | CBOE daily market statistics | Medium |
| **VIX1D vs VIX** | VIX1D is the market's own next-day volatility price, the closest thing to a competing forecast | CBOE CSV (2023 onward) | High, but short history |
| **Vol-control and CTA deleveraging** | These funds sell when *realized* volatility rises, creating feedback loops (about $15B of forced selling in one April 2024 session, per sell-side estimates) | Modelled: trailing 1-month and 3-month realized volatility vs typical 10–12% targets; trend signals on futures | Medium (sell-side models) |
| **Buyback blackout windows** | Company buybacks are a large source of equity demand and pause roughly 4–5 weeks before earnings. Blackout peaks line up with weaker market support | Estimated from earnings dates (EDGAR); share of S&P 500 in blackout each day | Practitioner, needs testing |
| **Turn-of-month and quarter-end rebalancing** | Pension/fund flows at month and quarter end; turn-of-month return effect is well documented | Computed from the calendar | High (decades of literature) |
| **Fed blackout period** | No Fed speakers from the second Saturday before each FOMC, so fewer surprise headlines | Fed calendar | Medium |
| **Treasury quarterly refunding** | Refunding announcements moved rates hard in 2023–24 | Treasury.gov schedule | Medium |
| **Prediction-market odds for events** | Kalshi/Polymarket price CPI ranges and Fed decisions. A lopsided Fed market means little surprise, so a smaller bump; split odds mean a bigger storm | Kalshi API (needs account keys, **ask**); Polymarket public API | New; worth testing |
| **Election and political dates** | Elections, debt-ceiling deadlines, government-shutdown dates | Hand-curated calendar | High for the big ones |

### Facts checked
- **Russell is now twice a year:** FTSE Russell moved to semi-annual reconstitution in 2026. June took effect after the close on 27 June; December takes effect after the close on **11 December 2026** (rank day was 30 October). Both are big closing-auction volume days. ([LSEG](https://www.lseg.com/en/media-centre/press-releases/ftse-russell/2026/ftse-russell-announces-december-2026-russell-us-indexes-reconstitution-schedule))
- **Earnings implied moves aren't reliably rich.** Implied moves often overstate realized moves on average (TSLA implied 8.4% vs 6.6% actual), but NFLX was underpriced, and ORATS reports the 2026 season ran *bigger* than implied. A 2023 JRFM paper found the useful signal is **past actual move ÷ implied move**, which predicts straddle returns. So the rich/cheap card should show that ratio with its track record, not assume options are always expensive. ([JRFM 2023](https://ideas.repec.org/a/gam/jjrfmx/v16y2023i5p270-d1145938.html), [ORATS](https://orats.com/blog/earnings-straddles-strong-season-2026))
- **Implied-move formulas differ:** the straddle price ≈ 0.8σ under a normal distribution, while ORATS strips out leftover time value. Pick one formula and state it.
- **Reddit API closes March 2027,** so no Reddit sentiment input. Not needed anyway.

### Features worth adding
- **Forecast vs market:** next to each forecast, show VIX1D and VIX (and the implied earnings move for stocks). Where the model and the market disagree, that's the interesting bit. A "the market is pricing more storm than we see" signal is directly useful to options sellers.
- **"Why today is different":** a short, auto-written breakdown of the forecast, for example "base volatility 0.6% + CPI 0.3% + short gamma 0.1%." Every component traceable.
- **Event surprise tracker:** after each release, the actual vs consensus surprise, and how far the market moved per unit of surprise. Over time this shows which data the market currently cares about (CPI in 2022, jobs in 2024).
- **Gamma regime banner:** "positive gamma, expect a pinned day" vs "short gamma, trend days likely," shown only once the scoreboard says it adds value.
- **Storm season view:** a yearly calendar of the market's own seasonality (Sept/Oct volatility, the December lull, summer calm) with confidence intervals.
- **Portfolio "insurance quote":** what it would cost to hedge the forecast storm (put price for a −X% move), from the live chain.

## Limits to say out loud

- **Not direction, not advice.** It forecasts how much things move, not which way.
- **VIX is already a strong forecaster.** The value Barometer adds is per-stock coverage, event structure, portfolio forecasts, calibration and clarity, not beating options markets by a mile.
- **Free data is delayed or flaky.** Intraday and historical options data are the gaps. The vault and paid connectors fill them.
- **Event effects shift between regimes.** Weights that change over time help, but a new kind of shock will still surprise it.
