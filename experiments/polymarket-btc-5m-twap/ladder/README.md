# Strike-ladder arbitrage across nested windows

**Structure (verified exactly).** The last 5-minute window of every 15-minute window settles on the same
Chainlink TWAP-60 value F at the same second (both markets use `btc-…-twap-60` configs; Gamma
`finalPrice` equal to every digit, and the 15 m `priceToBeat` equals the first 5 m `priceToBeat`). With
strikes Ka < Kb (K15 and the third K5), the pair **Up(Ka) + Down(Kb) pays $1, or $2 if Ka ≤ F < Kb**.
If the two asks plus fees sum below $1 the profit is riskless; the middle band is a free option.
The same structure exists for gold/silver (15 m vs 1 h, Chainlink spot; 88 of 92 hours share F exactly),
but the metal 1 h books are nearly empty.

**Measurement** (`ladder.py`; asks reconstructed from taker prints, data-api ts − 2 s, fee 0.07·p(1−p)):

| | BTC (192 windows) | ETH (187 windows) |
|---|---|---|
| middle band (pair pays $2) | 32.3 % | 35.8 % |
| median pair cost incl. fees | 1.336 | 1.350 (≈ 1 + P(middle): books are consistent on average) |
| windows with cost < $1 at some second | 7 (3.6 %); 0.09 % of seconds | 10 (5.3 %); 0.44 % of seconds |
| buy at the first second cost ≤ $1.00 | +0.44 ¢/pair, never negative | +2.52 ¢/pair, never negative |
| buy at the first second cost ≤ $0.98 | n = 1, +1.6 ¢ | n = 5, +4.40 ¢/pair, all positive |
| buy at a fixed time (no selection) | −4.6…−9.5 ¢/pair | −11.5…+0.7 ¢/pair |

**Status.** Real and riskless when it triggers, but rare (≈4–5 % of windows), brief (seconds) and small
(cents per pair). The size available at those instants is unknown from tapes: it needs both books
recorded live (5 m and 15 m together) and a fast taker. Other 15-minute rungs: 7 crypto assets; the
Polymarket bundle also defines `fourhour` (Chainlink) windows, which would add a third strike at
4-hour boundaries.
