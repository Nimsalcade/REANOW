# Paper-bot journal: Polymarket BTC 5-minute Up/Down

Paper trading only. Every entry records what was tried, on what data, and the
result. Numbers come from `engine.py` (history replay) or `live.py` (forward
paper on the recorder's live data). Nothing here is cherry-picked: failed
variants stay in the log.

## Ground truth used by every strategy

- Payoff: Up iff Chainlink BTC/USD TWAP60 at E ≥ TWAP60 at S (exact; see `../README.md`).
- Fees (recovered from Polymarket's web bundle, chunk `crypto fee` function):
  `fee_usdc = shares × 0.07 × (p(1−p))^1`, rounded to 4 dp, takers only.
  Per share: 1.75¢ at p=0.50, 1.26¢ at 0.80, 0.63¢ at 0.90, 0.07¢ at 0.99.
  The same bundle shows defaults `crypto_5_min: {rate .25, exponent 2}`, overridden by
  this market's `feeSchedule {rate .07, exponent 1, takerOnly: true, rebateRate .2}`.
- After the close, residual trading happens only at 0.999 (5 prints seen in one window).

## Data

| Set | Source | Coverage |
|---|---|---|
| History | Gamma events (open/close), `/api/crypto/price-history` (TWAP 5 s), data-api v2 trades, Binance 1 s klines (`data-api.binance.vision`) | 4 days, 1,153 windows (2026-11-12 15:10Z → 11-16 15:10Z) |
| Live | `recorder.cjs`: RTDS TWAP/Chainlink spot, Polymarket books (top of book + top-5 depth/s + trades), Binance, Coinbase, Kraken, official labels | from 2026-11-16 15:17Z, continuous |

## Iterations

### #0 — Complement arbitrage (buy Up + Down for < $1) — dead
- Data: live books 15:17–15:21Z (3,461 top-of-book states).
- Up ask = 1 − Down bid in 75 % of states; deviations of ±1–3 ticks are update-ordering transients.
- Fee-adjusted ask(Up)+ask(Down) < 1 occurred 4 times, each lasting **0 ms** (artifact between two messages).
- Conclusion: the two outcomes share one mirrored book (complementary matching), so this riskless trade cannot exist. Dropped.

### #1 — Model calibration on history (1,153 windows)
- Binance BTCUSDT leads the Chainlink TWAP's underlying price by **4–5 s**. With lag 5 s and a
  causal rolling offset (USDT basis, drifts ~$40 over 4 days), TWAP60 is predicted with median
  error **$0.61**, std $1.13, p99 $3.47.
- A stale offset (missing TWAP data) produced 23 confidently wrong windows — fixed by requiring fresh
  official TWAP points; lesson: the basis must be tracked live, never assumed.
- Model vs market (last trade) log loss by seconds-to-close, 269 windows:

  | τ | 285 | 240 | 180 | 120 | 60 | 45 | 30 | 20 | 15 | 10 | 5 |
  |---|---|---|---|---|---|---|---|---|---|---|---|
  | model | .658 | .629 | .541 | .358 | .195 | .127 | .044 | .014 | .009 | .008 | .008 |
  | market | .653 | .599 | .513 | .361 | .207 | .139 | .061 | .024 | .026 | .023 | .015 |

  Market beats the model early (it knows more than spot + σ); the TWAP-aware model wins from ~2 min out
  and is ~3× better in the last 15 s.

### #2 — First strategy round on history: look-ahead trap found, then no edge
- First pass showed large "edges" (value taker +8¢/share, maker +4.7¢/share, t up to 6).
- Cause: data-api v2 trade timestamps lag the real CLOB match by **+1.7 s median, +2.6 s p90**
  (measured by matching 387 live websocket fills), and are truncated to seconds. Fills on those
  prints were partly before the decision → look-ahead.
- Fix: a print is fillable only if `api_ts − 4 s ≥ decision + latency`.
- Corrected results (870 windows): taker value whole window −3.3¢/share (t −2.1); last 60 s −1.5¢;
  last 30 s +3.7¢ (t 0.15, n 77); spot-sign baseline −6¢; favorite baseline ≈0; makers −3 to −10¢/share
  (t −12 to −17).
- Limits of this sim: second-resolution prints with ±1 s timing, no ask prices, and data-api lists only
  ~1/6 of the fills the websocket shows (433 vs 2,485 in one window). Maker results are dominated by
  stale-quote adverse selection the 4 s shift exaggerates. Decision: evaluate takers and makers on
  the live recordings (ms timestamps, real books) instead.

### #3 — Live book reaction speed (event study, BTC, 77 jumps)
- Event: model fair value moves ≥ 4 points within 1 s (driven by Binance). Measure the share of that
  move reflected in Polymarket's Up mid afterwards.
- +200 ms: **4 %**, +500 ms: 22 %, +1 s: 37 %, +2 s: 58 %, +3 s: 61 %.
- The book is almost fully stale for the first ~300 ms after a spot-driven jump → a taker that reacts
  within ~250 ms sees stale asks. Candidate: **jump sniper** (`taker_snipe`).

### #4 — Pre-open / open bias (live) — already priced
- Mid at the open vs model fair at the open (BTC): 0.535 vs 0.529, 0.535 vs 0.534, 0.595 vs 0.591.
- Market makers already price the lagging price-to-beat. Dropped as a standalone edge.

### #5 — Scale-up and pre-registration
- All seven assets (BTC, ETH, SOL, XRP, DOGE, BNB, HYPE) run identical 5 m TWAP-60 markets with the
  same fees; recorder v2 records all of them from 15:30:43Z (7× the sample rate; thinner alt books
  may be slower).
- Live calibration (receive-time, 1 Hz TWAP): Binance lag 4 s, median error $0.28, p99 $1.27;
  Coinbase lag 4 s $0.32; Kraken lag 3 s $0.40; RTDS Chainlink spot lag 2 s $0.12 (std 0.62).
- `progress.py` sweeps 222 configs every 20 min (in-sample, for discovery only).
- **Forward test pre-registered** in `forward.json` at 15:35Z: 8 candidates (4 snipers, 2 makers,
  2 value takers), scored only on windows starting ≥ 1794843300 (15:35Z). Only these count as results.

### #6 — Real-time paper bot live; per-asset lag
- `paperbot.py` tails the recorder in real time (incremental state per asset), runs the 8 frozen
  candidates as paper orders, fills takers against the book as it is 250 ms later, fills makers on
  trade-through prints, settles from the TWAP stream, and writes `data/paper/ledger.md`. Started 15:37Z.
- Per-asset lag (Binance → Chainlink average, outcome-free calibration): 4 s for BTC/ETH/SOL/XRP/DOGE/BNB
  (errors 0.10–0.19 bp), **6 s for HYPE** (0.22 bp; its Chainlink feed evidently weights other venues).
