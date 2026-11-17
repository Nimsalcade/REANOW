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

### #13 — 19:00Z check-in: the direction sniper regressed to zero; why, and what scalping does
- Forward (266 asset-windows; F14–F17 27 slots incl. the 17:59–18:22Z gap, F18–F19 6 slots):
  F14 **+0.8 ¢/share** (355 fills, t 0.23, t_time 0.17) — down from +8.4 ¢ at 17 slots; F16 −0.2 ¢;
  F17 +0.3 ¢; F18 +2.0 ¢ (n 67); F19 −5.2 ¢ (n 77). Real-time bot since 18:24Z: F14 −7.4 ¢, F16 −5.4 ¢,
  F17 −6.4 ¢, F18 −0.1 ¢. F1–F4 snipers −1.5…+0.9 ¢. Early forward strength was regression-to-mean luck.
- The jump **information** itself is stable: jump ≥ .06, outcome − mid = +3.8 pts (15:17–16:30),
  +7.2 (16:30–18:00, t_time 5.9), +4.4 (18:22–19:00, t_time 2.0); jump ≥ .04 pooled over everything:
  +5.7 pts (4,092 triggers, se 0.7).
- Winner's curse in our fills (all data, 250 ms): filled triggers outcome − mid +4.9 pts with ask − mid
  +0.7 at fill; unfilled +6.5 pts but the ask had moved +5.1. Net at the real ask after fee ≈ +2.6 ¢ (all data);
  +0.6 ¢ at 500 ms.
- Jump scalping (`scalp.py`): taker exit after 3/5 s −2.1/−1.5 ¢ (t −7.5/−4.3: round-trip costs exceed the
  drift); after 20/40 s +0.9/+1.8 ¢; maker exits −1.7…−4.6 ¢. Hold-to-resolution from 16:30Z: −0.04 ¢
  (355 entries).
- **Reading:** forward information at jumps (~3 pts) ≈ taker costs (fee 0.07·p(1−p) ≈ 1.75 ¢ at mid plus
  ~0.7–1 ¢ half-spread). At ~250 ms latency the edge is about the size of the costs; the in-sample
  +8–12 ¢ results were selection.
- Price-bucket check of jump triggers (fee is lower away from 0.5): net ¢/share at the real ask
  [0.05,0.20) −8.5, [0.20,0.35) +4.1, [0.35,0.50) +5.2, [0.50,0.65) −0.6, [0.65,0.80) +9.6, [0.80,0.95) −7.4
  (se 4–8 ¢). Extremes are worst and adjacent middle buckets disagree → noise, not a fee effect. No
  candidate registered from it.

### #14 — Decoding gabagool22 (user-supplied fills + public data) and trying to reproduce it
- Structure: resting bids on **both** outcomes (a two-sided market maker on Up via mirrored books), merge
  matched pairs for $1 (500 MERGEs worth $256 k in 3 days), redeem the small leftover; 76–91 % maker fills;
  daily MAKER_REBATEs from 2026-01-08 ($50.5 k). Details and evidence: `../gabagool/README.md`.
- Track record (28,620 markets): $813 k realized; pair cost median 0.9852, balance 0.987; **103 % of P&L from
  locked pairs**, leftovers −$27 k (heavier side won 38 %). Decaying from 94 % to 71 % BTC 15 m win rate by Feb 2026.
- Reproduction on our data loses in every configuration: Oct 2025 tape replay −$2.5…−$7.4/market; Nov 2026
  tape −$1.8…−$4.4; live 5 m books −$0.7…−$2.4/window, even with full at-touch fills and 100 ms requotes and
  inventory-skewed quoting. Our replay captures 1.5–3 % of taker volume vs his 4.4–7.3 %, so its leftovers
  dominate. The edge was queue priority / speed in a thin, fee-free market; next step is an L2 queue model.

### #15 — 19:46Z check-in (329 forward asset-windows; direction sniper 36 slots, F18/F19 15 slots)
| cand | fills | ¢/share | t | t_time | real-time bot ¢/share (t) |
|---|---|---|---|---|---|
| F14 dir j.06 250 ms | 463 | −0.6 | −0.20 | −0.16 | −6.2 (−1.55) |
| F16 dir j.06 500 ms | 415 | −0.9 | −0.32 | −0.28 | −3.7 (−0.82) |
| F17 dir slack .04 2 s | 608 | −2.2 | −0.96 | −0.69 | −7.4 (−2.15) |
| F18 dir cap 2 | 146 | −5.5 | −1.21 | −0.97 | −6.4 (−1.37) |
| F19 dir no HYPE/BNB | 154 | −4.9 | −1.07 | −0.95 | −3.8 (−0.86) |

Fill-independent jump information (jump ≥ .06, outcome − mid): +3.8 / +7.2 / +4.4 / **+5.7 pts** for
15:17–16:30 / 16:30–18:00 / 18:22–19:00 / 19:00–19:45Z (t_time 1.99 / 5.90 / 1.98 / 1.94). The signal is
stable; taker execution at ~250 ms with fee 0.07·p(1−p) does not harvest it. No new candidate registered.

### #16 — Pair maker with a price-time queue fill model (live 5 m books, all 7 assets, ~300 windows)
- New `fill=queue` mode in `pairmaker.py`: a bid joins behind the size resting at its price (1 s depth
  snapshot; a fresh better price starts first), sells printing at that price eat the queue ahead first,
  prints through the price fill fully, re-pricing resets the queue, cancellations ahead are ignored.
- 48 configs (join/improve × lean × margin/imbalance × stop 60/120 s × price floor 0.1/0.3), 250 ms
  requotes: **all negative**, −$0.59…−$2.17 per window (best: improve, max_imb 10, t_stop 120, pmin 0.1:
  −$0.59, t_time −2.0). Locked pair profit $55…$464; leftovers −$259…−$1,050; estimated rebates
  +$0.10…+$0.28 per window.
- Reading: with realistic queue priority the structure still loses on today's 5 m TWAP books — leftovers
  are adversely selected and outweigh the 1–3 ¢ captured per pair. Consistent with gabagool's own
  decline (ETH 5 m, Feb 2026: −$0.51/market) and his stopping in Feb 2026.

### #17 — New structural ideas (after "stop everything"; offline tests only)
- **REA**: `analyze-javascript-application` on Polymarket's 178 web chunks → Evidence
  `ev_830299ffc69e84798be24108b8ca4568d9b7515a4a2dd2c8d6fc3267c2e2e196` (107 MB, scratch). The bundle maps
  variants `5m→fiveminute, 15m→fifteen, 1h→onehour, 4h→fourhour, 1d→oneday`, builds slugs
  `${asset}-updown-${dur}-${unix}`, groups `chainlink` resolution with fiveminute/fifteen/fourhour, and has
  gold/silver/Brent/WTI/natgas Up/Down families.
- **Strike ladder** (`../ladder/`): 5 m and 15 m settle on the same TWAP at shared boundaries (verified to
  every digit). Up(lower strike) + Down(higher strike) ≥ $1 always, $2 in the middle band (32–36 %).
  Riskless cost < $1 in 3.6 % (BTC) / 5.3 % (ETH) of windows: +0.44 / +2.52 ¢ per pair, never negative;
  rare, brief, small.
- **Commodity ties** (`../commodity/ties.py`): a tie resolves Up, but Polymarket lists gold/silver windows only
  during trading hours (182 of 384 possible 15 m windows in 4 days) and 0 ties occurred; WTI/natgas launched
  2026-11-16 with $0 volume. Dead.
- **Metal ladder** (15 m vs 1 h, Chainlink spot): 88 of 92 hours share F exactly, but the 1 h books are nearly
  empty ($746–$805 over 45 windows) — no tradable pairs.
- **Informed maker** (`informed_maker.py`, queue fill model, ~5 h live books): post a bid on the favoured side
  after a jump instead of taking. −5.0…+3.2 ¢/share, t ≤ 0.5, sign flips between early and late data; fills
  arrive when sellers disagree (win 0.49–0.58 at price 0.47–0.53). No edge.

### #18 — Ladder paper run, first 3¼ h cross-checked from public data (2026-11-17 00:47Z)
- The ladder bot runs on the user's Mac since 2026-11-16 21:32Z (fixed code: revised-strike refresh, per-leg
  official settlement, staggered setup, ws 8.23.0). Its first window's K15/K5 matched the cloud copy exactly
  for all 7 assets. This session cannot read the Mac's `ladder-data`, so the same period was re-measured from
  public data (`ladder/ladder_check.py`, Gamma official strikes/finals + data-api tapes).
- 21:30–00:45Z, 84 asset-windows: F identical in the 15 m and final 5 m market **84/84**; middle band 26.2 %.
- Riskless pair (cost incl. fees ≤ $1.00) in **5 windows** (BTC 22:30, ETH 21:45 and 23:00, SOL 23:00, XRP 00:00):
  +4.15 ¢/pair mean, min +0.25 ¢; at the bot's ≤ $0.995: 4 windows, +5.13 ¢/pair; ≤ $0.98: 2 windows, +8.70 ¢.
  Total time below $1: **14 s**; trade size at the trigger instant: median 8–9 shares (max 21).
- Reading: the structure is exact and every trigger wins, but at the sizes seen on the tape the run is worth
  roughly $0.5–1 per hour. Book depth at those instants (the bot's `opportunities.jsonl` `max_pairs`) decides
  whether it scales; pending the Mac files.

### #19 — Ladder verdict from the Mac bot's live books (13 windows, 21:22Z–00:49Z)
- Bot results: 5 sub-$1 episodes (DOGE 21:30 1 ms / 5 pairs; SOL 21:45 3 ms / 7.5; SOL 23:00 280 ms / 30 and
  507 ms / 18; XRP 00:00 368 ms / 9.8), best costs 0.951–0.991. Paper fills: 47.8 pairs in 2 windows (SOL 23:00,
  XRP 00:00) at $0.985–0.987 per pair (150 ms latency) → **P&L +$0.67, +1.41 ¢/pair**; same_F true in both.
- Tape vs book: the tape-only "opportunities" (BTC 22:30, ETH 21:45, ETH 23:00) combine prints from different
  instants of fast moves — e.g. BTC 22:30 +63 s: leg A printed 0.63…0.76 within one second including a
  1,171-share sweep — so they were never simultaneously available. data-api's 1 s resolution cannot separate
  them; the live book is the ground truth. The ms-long DOGE/SOL 21:xx episodes are book-update transients.
- **Verdict:** the ladder is exact (F identical 84/84) and riskless when it fills, but capacity is 5–30 pairs per
  episode, a few episodes per 3 hours, ~1–2 ¢ per pair at realistic latency: ≈ $0.2/hour. Not worth running for
  money; recommended to stop.

### #20 — Taker-rebate volume farming: rejected by arithmetic (2026-11-17)
- Weighted volume, from Polymarket's worked example: a $50 crypto trade at 50 ¢ earns 50 × 0.5 × 2.3 = $57.50 wV,
  at 5 ¢ 50 × 0.95 × 2.3 = $109.25. So wV = notional × (1 − p) × weight = shares × p(1 − p) × weight, while the
  taker fee is shares × rate × p(1 − p): **fee / wV = rate / weight at every price**.
- Live fee schedules (`rebates.py`, 13,242 markets in the top-500 events): crypto 0.07; economics, culture,
  weather, general 0.05; politics, finance, tech, mentions 0.04; sports v2 0.03 (sports v3 0.05); geopolitics and
  part of world fee-free (no wV). The category weights 2.3 / 1.7 / 1.3 / 1.0 are these rates ÷ 0.03, so
  **wV ≈ taker fees ÷ 3 %** (2.94–3.08 %). The tiers are fee tiers: Bronze ≈ $61 of fees in 30 days, Obsidian ≈ $304 K.
- Best case (target-tier rebate on every fee, no spread, every bonus): refunds are 19–61 % of the fees paid, net
  −$49 (Bronze) … −$117,864 (Obsidian); with a hedged Up+Down pair at 50 ¢ and a 1-tick spread (+0.87 % of wV)
  −$66 … −$204,820. Constant-volume path to Obsidian: month-1 average rebate 37.2 %, net −$243,777; each later month
  −$239,130. Break-even would need a wV multiplier of ≈ 7× (8× in month 1), capped at the $34,310 of one-time bonuses.
- For our strategies the program is a fee discount, not a source of profit: the ladder's ~48 pairs per 3½ h, extrapolated, stays
  at Bronze (3 % of fees). Obsidian halves the crypto fee (1.75 → 0.875 ¢/share at 50 ¢) but requires ~17 M shares
  a month at 50 ¢.
- Where participation itself pays: liquidity-reward pools, $43.8 K/day over 1,143 markets (tech $13.1 K,
  politics $7.5 K, fee-free $7.3 K, culture $5.9 K; largest $500–1,000/day on long-dated Fed, NVIDIA-CEO and
  election markets; max spread 2.5–6.5 ¢, min size 50–200). Live crypto Up/Down markets: 0/8 with a pool.
  Plus maker rebates (20 % of crypto taker fees, 25 % elsewhere) on filled volume — gabagool's $50.5 K.
  Next candidate: a paper quoting bot for reward pools in slow markets.

### #21 — Pre-open pair resting (user idea): rest both legs of the next window before it opens (`preopen.py`)
- Replay on the recorded live books (the recorder holds each next window's book for its last 5 minutes
  before the open), queue model of #16. Your rule — bids 0.49 × 10 on Up and Down posted 60 s before the
  open, held: 290 asset-windows, both legs filled 54 %, only one leg 43 % and that leg won **2 %**;
  P&L −$599 (−$2.07/window): locked pairs +$28, leftovers −$627. At the front of the queue (as if posted
  hours earlier): −$553. All 108 variants (lead 240/60/10 s, px 0.49/0.48/0.45, 10/100 shares, hold or cancel
  at +30 s, taker completion) lose except a few 10 s-lead cells at t < 1. BTC is closest (both legs 83–89 %):
  −$0.38/window, +$0.05 at the queue front.
- Mechanism: a static bid fills when the price moves against it; the next window is already being
  priced before its open (60 s TWAP start: BTC at 0.63 ten seconds before the 10:50Z open). Break-even
  would need ~24 balanced windows per one-sided window; observed ≈ 1.
- Live paper run (`preopen_paper.cjs`, 72 configs, all 7 assets) started 2026-11-17 10:49Z.

### #22 — Buy both legs on dips (user idea): first side at ≤ X, second when the pair locks a margin (`dips.py`)
- Up and Down share one price (Down ask = 1 − Up bid), so pair cost = $1 − (rise of leg 1's price between
  the two buys): a buy-the-dip / sell-the-bounce trade. Under a fair game the bounce to H happens with
  probability mid/H and the expected value is 0 before fees.
- Hindsight (339 asset-windows): after the first dip to X the other side later got cheap enough for a pair
  under $1 in 61–79 % of windows (X 0.10–0.45), under $0.90 in 32–68 %; when it never did, leg 1 won 0–1.4 %.
- Strategy grid, 84 configs (X 0.45…0.15, margin 0…0.10, leg 2 taker / resting bid): **all negative**.
  Observed completion is below the fair-game rate (e.g. X 0.35, margin 0.10: 62 % vs 66 %; margin 0: 77 %
  vs 86 %) — dips tend to continue. X 0.35 / margin 0.10: completed +$1.20, not completed −$2.65 (leg 1
  won 0 %), −$0.26/window. Best: X 0.25 / margin 0.10, −$0.12/window (t −0.64).

### #23 — Dip thresholds searched, chosen on one half, scored on the other (`dips_search.py`)
- 1,620 combinations: first leg ≤ 0.05…0.45, bought by +30…+240 s; second leg the first time the pair
  costs ≤ $1.00…$0.70 incl. fees; give up after 30/60/120 s (sell leg 1 at the bid) or hold. 367 sessions,
  halves split by time (171 / 196).
- Best chosen on half 1: +5.95 c/session per share → **−3.75 c** on half 2. Best chosen on half 2: +1.95 c →
  **−1.33 c** on half 1. Overall 12 of 1,620 positive, none with t > 2. Positive in both halves: 3
  combinations (first leg ≤ 0.15–0.20 by +180 s, pair ≤ 0.70–0.80, hold), +0.3…+1.5 c/session,
  t ≤ 0.38. Resolving an effect of about 1 c at a per-session SD of about 35 c would take ~5,000 sessions,
  about 2½ days on 7 assets.
- Reading: deeper discounts complete proportionally less often, as in a fair game; no threshold pair
  beats it after fees on this data.

### #24 — gabagool's session anatomy and a clone with his settings (`../gabagool/legs.py`)
- 340 sampled sessions, every fill: first fill +14/+21/+25 s after the open (5 m/15 m/1 h), never before it, at
  0.49–0.50 on a random side (Up 51–57 %), 10-share clips; the other leg follows 3–10 s later; first pair
  ≥ $1 in 35–48 % of sessions with no effect on P&L; running pair cost ≈ 0.98 from the first 10 % of fills;
  stops 81/103/324 s before the close; imbalance ≤ 3–9 %; no sells. Details: `../gabagool/README.md`.
- Clone on today's 5 m books (pairmaker, queue model, his timing and clip): pair cost 1.02–1.03, −$4.1…−$5.4/window
  without a cap, −$1.3…−$1.9 with one. His fills were 1.5–2 ¢ under $1 per pair; ours are 2–3 ¢ over.

### #25 — New gaps outside crypto Up/Down (`../structural/`)
- **Guaranteed baskets** (`scan.py`, all 2,100 active events, CLOB re-check with fees): all-No baskets on
  mutually exclusive outcomes (sum of Yes bids > $1) and out-of-order nested deadlines / price ladders
  (Yes on the market that must be at least as likely, below the bid of the one it implies). Confirmed now:
  12 pairs, **$1.64 locked in total** (largest $0.41); 0 of 4 baskets survived the re-check. Policed by bots.
  (A first pass flagged hundreds; they were parse errors: Dec 31 deadlines resolve after midnight UTC, mixed ↑/↓
  ladders, "$2T", "how low" ladders.)
- **Liquidity-reward pools** (`rewards.py`, 2,572 rewarded markets, $45.9 K/day): estimated share for 1,000 shares
  per side one tick inside the touch, with competition = the larger side's qualifying book score (an upper
  bound on others' score, so the share is a lower bound for that snapshot). Best estimates $25–80/day per $1k
  (e.g. Democratic-trifecta 60 % $79, Musk 1000+ tweets $55, Kimberly Powell resignation $39, Lindsay Clancy $25),
  with 100–1,300 shares/day trading through those prices and 24 h moves of 0.03–0.11. The two largest
  ($107–187/day) are Iran-ceasefire markets past their deadline, awaiting resolution — the risk there is the
  resolution jump. Untested: fills, news jumps and how competition changes during the day; needs a multi-day paper run.

### #26 — Live pre-open run, 15 m clone, and the reward farmers who are profitable now
- **Pre-open live run** (10:49–12:49Z, 161 asset-sessions, 72 configs): hold-to-end 0/36 configs positive
  (100-share configs ≈ −$15/session, t −6 to −7, leftover won 13–15 %); cancel at +30 s 5/36 positive, best t +0.62.
  Confirms #21.
- **15 m clone** (`clone.py 15m`, 27–28 sessions 12:00–13:00Z, gabagool's 15 m timing): 10/12 variants positive, best
  join + pair ≤ $1.00 + imbalance 120: +$186 (+$6.90/session, t 0.57) — but slot 12:30 made +$287 while 12:00/12:15/12:45
  lost $1/$29/$71; without the best 3 sessions −$60; median session +$0.64. One favourable quarter-hour, not an edge.
- **Reward farmers** (`../structural/lp_wallets.py`): of the 80 wallets present in the most rewarded markets, 72 received
  liquidity-reward payouts, $4.87 M in total. Largest: ArmageddonRewardsBil… $1.20 M (since 2025-01), Haradwaith $0.89 M,
  lissartter $0.46 M, Istaroth $0.36 M, hfv $0.35 M. Pure quoters (near-zero open value) earning most now:
  Istaroth $6,813/day (Oct $144 K, Nov 1–17 $95 K), hfv $6,100/day (Oct $240 K), WarriorH $1,947/day (Oct $68 K);
  their closed-position P&L on the up to 20,000 positions fetched is +$1.38 M / +$1.17 M / +$2.37 M.
  Profile: 150–250 slow markets at once (weather, central banks, elections, tweet counts, AI rankings, box office),
  median fill 20–31 shares, buys and sells, merges constantly. Next: decode their quoting rules and paper-run a
  multi-market reward quoter.

### #27 — Six new edges built from the rules, each tested on live data (2026-11-17)
| idea (`../structural/`) | mechanism | result |
|---|---|---|
| Holding-reward carry | hold Yes + No (= $1, no price risk) in markets paying 4 %/yr on position value; buy as a maker | 511 eligible markets, mostly 2028 elections, already thick with resting size; netting of hedged pairs unpublished; 4 %/yr at best |
| Noon cross-product ladder (`noon_ladder.py`) | "above X", "price on" buckets and the 11 AM ET hourly Up/Down all settle on the Binance close at 12:00 ET | 290 guaranteed pairs (BTC/ETH/SOL/XRP, today and tomorrow): cheapest $1.0003; hourly link only checkable 16:00–17:00Z |
| Decided weather buckets (`weather.py`) | buckets beaten by an observed METAR are dead; sell their bids | 1 left, $0.21 (a first pass flagged 65: a "64-65°F" parse bug and a METAR row cap on multi-station queries) |
| Weather rule bias (`weather_bias.py`) | settlement uses hourly routine reports only, below the continuous high that forecasts predict | 330 settled US events: winning bucket − market-expected high = +0.12 °F (se 0.08) at 10:00, +0.01 at 13:00, −0.03 at 15:00 — no bias |
| Slow-updating weather crowd | favourite under-priced late in the day? | favourite priced 55.9/66.6/86.3 % won 60.9/68.5/89.1 % (10/13/15 h), within 1–2 se and below costs |
| Forecast-anchored Munich | 10 °C observed at 14:20 local vs 13 °C favourite | wind shift and gusts in the TAF; the market prices a forecast warm-up, not stale data |
- Pattern: every gap provable from public prices and public data is already harvested; markets are calibrated.

### #28 — The dawn-low edge in weather markets (first edge found by rule homework) (`../structural/`)
- `metar_snipe.py` replays each settled US event's routine METARs with the settlement rules: the replay equals the
  official winner in **289/292** events. Selling into buckets a published report had already killed captured only
  $195 in 14 days (11 US cities); the fastest such sale came 4 s after publication — METAR sniping is taken.
- `weather_trades.py` scores every taker buy (46 stations, 5 days, replay = official in 380/380) against what
  the published reports showed at that moment: win rate ≈ price in almost every cell, except buying the bucket that
  holds the observed **low** with ≥ 10 h of the local day left.
- 14 days, all cities, complete METAR history (`metar_history`, 6-day windows under the API's 400-row cap; the capped
  first pass kept only 405/542 events and inflated the result): replay = official **541/542**. Buying the low's bucket
  at **04:00–08:00 local**: 378 city-days, +5.4 c/share after fees (+$2,489), per city-day +4.3 c (t 3.35), 11/14 days
  positive. 00–04: +3.6 c (t 1.98); 08–12: −0.5 c; 12–14: +1.2 c. Days not used to find it (Nov 3–11, 00–14 local):
  +3.2 c/share, t 2.77, 8/9 days positive. Once the low is clearly behind (now ≥ low + 2 °C) it is priced: +0.7 c.
- Capacity is the limit: ~46 K taker shares in the window over 14 days market-wide; first live asks had 5 shares.
- `weather_low_paper.py` paper-trades the rule live (first entries 2026-11-17 14:31Z: Los Angeles and Seattle 5 @ 0.68).

### #29 — Patterns in BTC 5 m / 15 m results and hourly betting progressions (`patterns.py`)
- 60 days of official results (Gamma outcomePrices), 2026-09-18 … 11-17: 17,256 5 m and 5,752 15 m windows. Break-even
  win rate at the measured opening ask (median 0.51, recorded books) plus fee: 52.75 %.
- Up 49.6 % / 49.0 %; same as previous 49.9 % / 48.7 %; against 2 in a row 50.7 % / 52.5 %; against 5 in a row 52.9 % /
  49.4 %. Round of the hour, hour of day and weekday: chi-square at chance level. Split-half: "Thursday Down"
  48.5 → 54.4 % (5 m) and 50.5 → 61.2 % (15 m), i.e. recent Thursdays only; "03:00 UTC Down" 54.7 / 54.4 % (5 m) and
  53.3 / 59.2 % (15 m) — consistent but the most extreme of 24 hours, ~1.8 pts over break-even; unproven.
- Progressions per clock hour (24 variants: always Up / repeat last / opposite; double or recover-losses+$1; 4 or 6
  rounds): hours won 93–99 %, total −$40 … −$559 over 1,439 h (best +$5); losing hours cost $15–84. No pattern beats
  break-even, so no progression can.
