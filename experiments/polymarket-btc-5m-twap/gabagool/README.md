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
