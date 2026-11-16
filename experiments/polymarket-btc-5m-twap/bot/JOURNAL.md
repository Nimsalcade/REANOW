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

### #7 — First forward results (2 time slots × 7 assets = 14 asset-windows, 15:35–15:45Z) — too early
| cand | real-time bot ¢/share (t) | replay forward ¢/share |
|---|---|---|
| F1 snipe j.04 e.02 1 s 250 ms | +11.7 (1.74) | +0.7 |
| F2 snipe j.06 e.04 | +13.3 (1.82) | +6.9 |
| F3 snipe j.04 e.04 2 s | +14.9 (2.19) | +3.3 |
| F4 snipe j.04 e.02 500 ms | +13.6 (1.91) | −2.7 |
| F5/F6 makers | ≈0 | ≈0 |
| F7 value last 60 s | +27.6 (n=5) | +33.9 (n=6) |
- The bot restarted at 15:38 (per-asset lag) so it missed part of slot 1; the replay covers both slots fully.
- Per asset (replay F1): BTC −34¢, XRP −25¢, BNB −16¢; ETH +21¢, SOL +21¢, DOGE +11¢, HYPE +16¢.
- Bug fixed: pooled summaries grouped by window start only (7 assets collapsed into 1). Now reported
  both by asset-window (`t_stat`) and by time slot (`t_time`, conservative: crypto moves together).
- Hypothesis for BTC losses: single-exchange (Binance-only) jumps are noise for Chainlink's
  multi-venue aggregate; BTC makers leave those quotes up knowingly. Composite (Binance+Coinbase)
  barely changes the 60 s-average fit (BTC 0.098 vs 0.099 bp), so this needs outcome data → registered
  as a forward variant, not assumed.

### #8 — Book reaction speed by asset (live, 15:17–15:48Z)
Median share of a ≥4-point fair-value jump reflected in the Up mid:

| asset | jumps | +500 ms | +1 s | +2 s | +3 s | +8 s |
|---|---|---|---|---|---|---|
| BTC | 120 | 13 % | 23 % | 49 % | 52 % | 69 % |
| ETH | 46 | 0 | 5 % | 16 % | 28 % | 59 % |
| SOL | 66 | 0 | 10 % | 9 % | 23 % | 39 % |
| XRP | 52 | 0 | 9 % | 0 | −8 % | 36 % |
| DOGE | 43 | 0 | 9 % | 17 % | 16 % | 36 % |
| BNB | 45 | 0 | 0 | 0 | 24 % | 89 % |
| HYPE | 29 | 0 | 7 % | 3 % | 32 % | 3 % |

Alt books stay stale for seconds. Either free edge or makers who correctly ignore the jump — the
alt-only forward candidates (F10, F12, F13, scored from 15:50Z) decide it.

### #9 — Outage 15:53:07–15:59:23Z (environment), fixed
- The VM was suspended ~5 min after the session went idle; detached (`setsid nohup`) processes died
  (uptime reset at 15:58:17). Data gap: 15:53:07–15:59:23Z; the 15:50 window is only partly recorded.
- Fix: recorder, paper bot and sweep now run as harness-tracked background tasks (2 h, re-launched).
- `labels.py` backfills official open/close for any recorded window that lacks one (`data/labels/`).
- The bot's in-memory fills for the 15:50 window were lost with the process; the replay forward
  test (`forward.py`) still scores that window from the recording and the official label.

### #10 — Forward after 62 asset-windows (9 slots): sniper ≈ 0; price bias = selection; jump info is real
- Forward (F1–F8, 15:35–16:20Z): snipers +2.8 / −0.9 / +2.8 / −1.3 ¢/share (t_time 0.75 / −0.2 / 0.65 / −0.38);
  makers −7.8 / −2.4 ¢; late value +3.4 ¢ (n 28); endgame −14.6 ¢ (n 11). Real-time ledger agrees (−8…+2 ¢).
  Snipers bought at win rate 0.60 ≈ price 0.60: no edge after fees with the model-level gate.
- History price-bias study (1.48 M BTC taker buys, time split): buying at 0.5–0.8 earned +2…+8 ¢/share and
  0.2–0.5 lost −4…−10 ¢ in **both** halves (ETH similar, noisier). But a clean rule buying the favourite at
  fixed checkpoints earns nothing (win 0.604 vs price 0.595; −0.7…−2.7 ¢ after fees). The print-level
  "bias" is **selection**: the takers whose buys win are informed at that moment. The market is calibrated
  at random times.
- At live fair-value jumps (|Δ| ≥ 4 pts in 1 s), direction-adjusted **outcome − mid = +7.2 pts**
  (1,049 triggers, se 1.5; per-asset window-clustered t +1.1…+4.1; HYPE ≈ 0). The model's own fair − mid is
  only +4.9 pts: the information is in the **direction** of the jump; the model-level gate (fair − ask − fee >
  edge) selected the most overconfident triggers.
- New **direction-only sniper** (`taker_dir`): on a jump buy the favoured side at ≤ mid + slack. In-sample
  sweep (96 configs, all live data): **93 % positive, median +6.7 ¢/share**; central config
  jump .06 / slack .02 / 1 s / 250 ms: +12.5 ¢/share, 119 fills, t 2.76, t_time 2.61; positive on all 7 assets
  (BTC +10, ETH +19, SOL +17, XRP +6, DOGE +7, BNB +6, HYPE +32 ¢).
- Pre-registered **F14–F17** at 16:25Z, scored from 16:30Z only; added to the real-time bot.

### #11 — Final checkpoint (recording 15:17–17:59Z; stopped by the session's background time limit)
Jump-signal information, out of sample (fill-independent; outcome − mid in the jump's direction):

| jump | sample | triggers | slots | outcome − mid | t_time |
|---|---|---|---|---|---|
| .04 | in (< 16:30Z) | 1,307 | 15 | +6.8 pts | 2.35 |
| .04 | forward | 685 | 5 | +2.2 pts | 2.17 |
| .06 | in | 814 | 15 | +3.8 pts | 2.00 |
| .06 | forward | 403 | 5 | +3.2 pts | 3.35 |

The information is real out of sample but ~2–3 pts — close to taker costs (fee 1.75 ¢ + ~1 ¢ half-spread at mid).

Forward P&L, replay (`forward-final.txt`; F1–F13: 196 asset-windows / 28 slots, F14–F17: 17 slots) and
real-time bot (`ledger-final.md`, 106 settled asset-windows):

| cand | fills | ¢/share | t | t_time | real-time ¢/share (t) |
|---|---|---|---|---|---|
| **F14 dir j.06 s.02 250 ms** | 210 | **+8.4** | **1.86** | **1.55** | +4.0 (0.82) |
| **F16 dir j.06 500 ms** | 187 | **+7.7** | 1.78 | 1.40 | +7.3 (1.50) |
| F17 dir j.06 s.04 2 s | 286 | +4.9 | 1.60 | 1.18 | +5.5 (1.67) |
| F15 dir j.04 | 239 | +0.4 | 0.12 | 0.10 | +0.4 (0.08) |
| F1–F4 model-gated snipers | 396–454 | −0.8…+2.5 | ≤0.96 | ≤0.92 | −5.4…+1.3 |
| F9 composite source | 375 | +3.0 | 1.09 | 0.89 | n/a (replay only) |
| F10 alts only / F11 σ×1.3 | 367 / 416 | −1.7 / −3.7 | | | −5.9 / −6.8 |
| makers F5/F6/F13 | | −4.1 / +0.3 / −4.3 | | | +1.1 / −12.0 / −5.9 |
| value/endgame F7/F8/F12 | | ≈0 | | | |

Status: the **direction-only sniper with jump ≥ 6 pts** is the only candidate positive in both the replay
and the real-time bot, at 250 ms and 500 ms latency. It is not yet significant (t_time 1.4–1.55 on 17 slots).
At the observed effect size, t_time ≈ 2 needs ~28 slots (~1 more hour of recording) and ≈ 3 needs ~64 slots
(~4 more hours). Everything else tested is ≈ 0 or negative after fees.

### #12 — Restart (18:22Z) and F14 neighbourhood study (all data 15:17–17:59Z, discovery only)
- Recording resumed 18:22Z after the background-limit stop (gap 17:59–18:22Z; VM had slept).
- F14 fills by feature (365 fills, +9.2 ¢/share): most triggers fall 180–300 s before close (cap of 3
  per window is spent early); if the favoured side's mid already moved > 5 pts during the jump, −3.1 ¢;
  BNB −5.1 ¢ and HYPE −3.0 ¢ over the whole recording.
- Variants vs F14 (+9.5 ¢, t_time 2.76 over 31 slots, in+forward mixed):
  cap 10 → +3.2 ¢ (later triggers in a window carry less information); cap 2 → +10.7 ¢ (t_time 3.26);
  cap 1 → +6.6 ¢; repricing filter → +9.7 ¢; no HYPE/BNB → +10.3 ¢; jump .07 → +5.3 ¢; jump .08 → +2.4 ¢;
  look 0.7 s → +7.7 ¢; look 1.5 s → +7.4 ¢; slack .01 → +8.8 ¢; 500 ms latency with cap 1 → +1.9 ¢.
- Reading: the neighbourhood is positive almost everywhere but bumpy, so F14 sits near a noisy peak;
  a fair estimate of the true edge is the neighbourhood's typical +5–7 ¢/share, not +9.5 ¢.
- Pre-registered F18 (cap 2) and F19 (F14 without HYPE/BNB), scored from 18:30Z.
