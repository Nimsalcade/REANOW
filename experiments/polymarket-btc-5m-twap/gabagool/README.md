# Decoding gabagool22's Polymarket crypto Up/Down bot

Inputs: the user's fill exports for two 15-minute markets on 2025-10-29 09:00–09:15 UTC (BTC, ETH), and
the wallet's deposit/withdrawal history (`evidence/`). Cross-checked against Polymarket's public Gamma
and data-api v2 endpoints (`evidence/gamma-events-1761728400.json`, `evidence/data-api-trades-*.json`).
Wallet: `0x6031b6eed1c97e853c6e0f03ad3ce3529351f96d` (name `gabagool22`).

## What the bot does

It is a **two-sided market maker on each Up/Down book, expressed as two buy books plus merges.**

1. It keeps resting **bids on both outcomes**: Up and Down. Polymarket's two outcome books mirror each
   other (a Down bid at q is an Up ask at 1 − q), so bids on both sides are an ordinary two-sided quote
   on Up.
2. Whichever side takers dump hits its bid, so it accumulates the side that just got cheap, switching
   sides 25–26 times in a 15-minute window and keeping the two inventories close.
3. The average cost of a matched Up+Down pair is below $1 by the spread it captured:
   **pair cost = avg(Up) + avg(Down) = 0.982** in both sample windows.
4. Matched pairs are **merged** back into $1 of USDC each through the outcome-token contract — no sale,
   no wait for resolution, no net direction. Only the small unmatched remainder is redeemed after
   resolution.
5. Since January 2026 it also collected daily **maker rebates** from the taker fees.

## Evidence

| Claim | Evidence | Provenance |
|---|---|---|
| Near-balanced two-sided inventory, pair cost 0.982 | BTC: 307.2 Up @ 0.460 + 291.8 Down @ 0.521; ETH: 319.6 Up @ 0.444 + 300.9 Down @ 0.538 (`decode.py`) | user export |
| Locked profit on matched pairs | BTC 291.8 pairs × 1.82 ¢ = $5.31; ETH 300.9 pairs × 1.79 ¢ = $5.38 | derived |
| Residual is small and directional | leftover Up 15.3 (BTC), 18.8 (ETH); both windows resolved **Down** → realized −$1.75 and −$2.94 | user export + Gamma `outcomePrices ["0","1"]` |
| Mostly maker | data-api lists taker-side rows only; it shows 8 of 86 BTC fills and 22 of 90 ETH fills for this wallet, all in transactions where it is the tape taker → **91 % / 76 % maker** | data-api v2 trades |
| Merges instead of sells | `activity?type=MERGE`: 500 merges worth $256,010 on 2026-02-17…20 alone (more pages exist) | data-api v2 activity |
| Maker rebates | `activity?type=MAKER_REBATE`: 44 daily rebates, 2026-01-08…02-21, **$50,511** (these are the ~$1k "deposits" at ~6 PM CST in the export) | data-api v2 activity |
| Referral rewards | 40 payments, 2026-03-25…06-24, $1,805 (the small evening "deposits") | data-api v2 activity |
| Scale | ~$986 initial funding on 2025-10-29; **$855,640 withdrawn** by 2026-02-20; trading appears to stop ~2026-02-20/21 | user export + activity |
| Market regime then | Oct 2025 15-minute markets: `feesEnabled: false`, resolved on the Chainlink **spot** stream | Gamma |

## Why it worked then, and what changed

- With no taker fees (Oct 2025) spreads were wider and takers more numerous; a maker captured the
  spread on both sides, and merging recycled capital instantly (capital ~$600 per window, turned over
  every 15 minutes across assets).
- The risk is inventory: in a trending window one side keeps filling while the other's bid is left
  behind, and the remainder resolves against it (both sample windows show exactly this).
- From January 2026 the fee program made takers pay and makers earn rebates; the account's rebates
  alone were ~$1k/day.
- The current 5-minute markets (Nov 2026) are tighter (BTC spread usually 1 tick → at most 1 ¢ per
  pair at the touch), so the same structure must be re-tested on today's books; see `../bot/pairmaker.py`
  and `../bot/JOURNAL.md`.

## Full track record (28,620 closed markets, `evidence/positions-closed.json`, not committed: 70 MB)

| Metric | Value |
|---|---|
| Realized P&L | **$812,993** over 2025-10-29 … 2026-02-20 |
| Markets with both outcomes held | 99.9 % |
| Median pair cost / median balance (min/max shares) | **0.9852 / 0.987** |
| Pair cost < 1 | 80.1 % of markets |
| Profitable markets | 81.3 % (mean $28.41, median $20.80) |
| P&L from locked pairs (pairs × (1 − pair cost)) | **$840,363 = 103 %** of total; leftovers −$27,353 |
| Heavier side won | **38.4 %** — no directional skill; leftovers are adversely selected but tiny (median 1.34 % of size) |
| Mix | BTC/ETH 15 m (≈10.6 k each), BTC/ETH hourly (≈2.7 k each), BTC 5 m from Feb 2026 (1,756), ETH 5 m (122) |

Decay: BTC 15 m win rate 94 % (Oct) → 92 % (Nov) → 96 % (Dec) → 82 % (Jan, fees + rebates start) → 71 % (Feb);
ETH 5 m in Feb −$0.51/market (48 % win). Trading stops ~2026-02-20.

## Reproduction attempt

- **Accounting reproduced exactly** from the user's fills (`decode.py`): pair cost 0.9818 / 0.9821, matched
  pairs 291.8 / 300.9, realized −$1.75 / −$2.94 in the sample windows (both resolved Down).
- **Strategy simulation does not reproduce the profit.** Same structure (bids on both outcomes, cap on pair
  cost, inventory limit, optional taker top-ups, inventory-skewed "lean" quotes, early stop, price bounds):
  - Oct 2025 15 m BTC/ETH, 384 markets, tape-based replay with the touch rebuilt from prints, no fees
    (`tape_pair.py`): **−$2.5 … −$7.4 per market**, win share 16–25 % in every configuration
    (gabagool actual in Oct: +$10–15 per market, 87–94 % wins).
  - Nov 2026 15 m BTC/ETH, 384 markets, same replay with today's fee: −$1.8 … −$4.4 per market.
  - Nov 2026 5 m, all 7 assets, live recorded books (`../bot/pairmaker.py`), trade-through or full at-touch
    fills, 1 s / 250 ms / 100 ms requotes: −$0.7 … −$2.4 per window.
- Validation on the exact 09:00 windows: the replay fills 55–85 times at broadly similar prices but ends
  far less balanced (30–50 shares over) and collects ~1.5–3 % of taker volume vs gabagool's
  **4.4 % (BTC) / 7.3 % (ETH)**.
- **Reading:** the structure is fully decoded, but the profit came from execution — queue priority at the
  touch on both sides with fast re-pricing (and thin competition, fee-free, in late 2025). That buys 2–3× the
  two-sided volume, which keeps leftovers at ~1 % and lets the 1.5 ¢ per-pair spread win. Paper replays
  from tapes or 1 s books cannot grant queue priority, so they systematically under-fill the benign flow and
  over-weight adverse sweeps. Validating it needs an L2 queue-position model on recorded books (next
  step) or real orders.

## How each session is built (`legs.py`, 340 sampled sessions, every fill from data-api `/v2/activity`)

Stratified sample: 15 sessions per month × coin × market length (BTC/ETH; 15 m, 1 h, 5 m), Oct 2025 – Feb 2026.

| | 5 m (Feb 2026) | 15 m | 1 h |
|---|---|---|---|
| First fill after the open (median; p10–p90) | +14 s (12–29) | +21 s (16–38) | +25 s (17–79) |
| Fills before the open | 0 % | 0 % | 0 % |
| First side Up | 53 % | 57 % | 51 % |
| First-leg price median (p10–p90) | 0.50 (0.36–0.58) | 0.49 (0.39–0.56) | 0.49 (0.43–0.54) |
| First fill size / leg-1 shares before the other leg starts | 8.4 / 10 | 10 / 40 | 10 / 40 |
| Other leg's first fill after the first fill | 3 s | 8 s | 10 s |
| First fill of each leg combined: median, share under $1 | 0.990, 53 % | 0.980, 65 % | 0.990, 52 % |
| Fills / side switches / shares per session (median) | 64 / 23 / 447 | 275 / 58 / 2,423 | 511 / 126 / 4,801 |
| Last fill before the close (median) | 81 s | 103 s | 324 s |
| Fills where price + other side's average < $1 | 55 % | 57 % | 54 % |
| Final pair cost / balance / max imbalance in session | 0.9864 / 0.961 / 9 % | 0.9805 / 0.985 / 5 % | 0.9889 / 0.991 / 3 % |
| Sells | 0 | 0 | 0 |

- 15 m running pair cost: 0.976 after 10 % of his fills (below $1 in 77 % of sessions), 0.982 at 50 %, 0.9805 at the
  end (88 %). Session P&L is the same whether the first pair cost < $1 (median $18.70) or ≥ $1 ($18.61).
- 69 % of fills share a second with the previous fill: takers sweep several stacked 10–12-share bids at once.
  12.8 % of 15 m fills are below 0.20 and 9.6 % above 0.80 — he keeps quoting as the market turns one-sided.
- When imbalanced by ≥ 30 shares the next fill is on the light side 58 % of the time, at the same price as that
  side's previous fill (no price lean visible).
- 15 m edge by month: pair cost 0.957 → 0.975 → 0.980 → 0.985 → 0.992 (Oct → Feb) while shares per session
  grew 691 → 5,188.
- Reading: a continuous two-sided maker from ~20 s after the open to 1½–5 min before the close, small clips at
  the touch, inventory kept within a few percent; profit is the ~1–2 ¢ spread captured per pair on average, not
  cheap entries. His exact settings on today's 5 m books (`../bot/pairmaker.py`, queue model, start +14 s, stop
  81 s before close, 10-share clips, no pair cap): pair cost **1.02–1.03**, −$4.1…−$5.4 per window (t ≈ −10);
  with a pair cap ≤ $1.00/0.98: −$1.3…−$1.9 per window (locked pairs +, leftovers −). Today's 15 m books were not
  recorded, so the 15 m version is untested.
