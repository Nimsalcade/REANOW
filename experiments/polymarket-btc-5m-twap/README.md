# Polymarket BTC 5-minute Up/Down: how resolution actually works

Target: [`btc-updown-5m-1794839700`](https://polymarket.com/event/btc-updown-5m-1794839700)
("Bitcoin Up or Down - November 16, 9:35AM-9:40AM ET"), resolution source
[`btc-usd-twap-60s-streams`](https://data.chain.link/streams/btc-usd-twap-60s-streams).
Investigated 2026-11-16 with pinned `rea-agents@6.5.0` (CLI), Chromium
153.0.8010.12, and direct reads of the public endpoints the page uses.

## Answer

The market resolves **Up iff `TWAP60(end) >= TWAP60(start)`**. `TWAP60(t)` is
Chainlink's BTC/USD 60-second time-weighted average price, reported at
timestamp `t`, and it averages the 60 seconds **before** `t`. For this market:

| | Timestamp (UTC) | Averages spot over | Value |
|---|---|---|---|
| Price to beat (`openPrice`) | 14:35:00 | 14:34:00–14:35:00 | 65,101.17634277858 |
| Final price (`closePrice`) | 14:40:00 | 14:39:00–14:40:00 | 65,370.04455727309 |

Final outcome: **Up** (margin $268.87). The outcome was resolved on-chain at
14:41:19 UTC, 79 s after the window closed.

The rules text reads as if it compares a TWAP "of the time range specified in
the title" (14:35–14:40) with "the price at the beginning of that range". The
implementation does neither. Both sides are 60-second trailing TWAPs, and the
four middle minutes of the window do not enter the result at all.

## Findings ledger

| # | Finding | Evidence | Provenance |
|---|---|---|---|
| 1 | Event 1395139, market 7070999, condition `0xc52248da…19ef3`. Window 14:35:00–14:40:00Z (`eventStartTime`, `endDate`). `cryptoMarketConfig = {id: "btc-5m-twap-60", twapEnabled: true, twapLookbackSeconds: 60}`. `resolutionMechanism: "oracle"`, `fallbackResolutionType: "none"` | `evidence/gamma-event.json` | observed (public Gamma API) |
| 2 | Gamma `eventMetadata.priceToBeat` and `finalPrice` equal the page API's `openPrice` and `closePrice` to all digits | `evidence/gamma-event.json`, `evidence/crypto-price.json` | observed |
| 3 | The page shows "Price To Beat $65,101.18", "Final price $65,370.04" and "Final outcome: Up" | REA `ev_ecfa22ce2d75769934a89ad19d435178c1be99830552a36182a1b21f93a5814d` (accessibility capture) | observed-behavior (REA scenario) |
| 4 | The page reads prices from `polymarket.com/api/crypto/crypto-price` and `/api/crypto/price-history`, called with `twapEnabled=true&twapLookbackSeconds=60`. Live values arrive over `wss://ws-live-data.polymarket.com/` on topic `crypto_prices_twap_sixty` (`window_s: 60`, 1 s updates, 18-decimal `full_accuracy_value`) | REA `ev_2200e5455279eae2a0d6ae553d0fcfd33e66b6aff0f7ed40a15b63cfd8fe0746` (819 events, 0 dropped, including the sent subscribe frame) | observed-behavior (REA scenario) |
| 5 | `openPrice` is exactly the TWAP history point at 14:35:00, and `closePrice` exactly the point at 14:40:00 (5 s series, 61 points) | `evidence/crypto-price.json`, `evidence/price-history-twap.json` | observed |
| 6 | Chainlink defines a 60 s TWAP as a rolling window anchored to the latest observation ("requested at 12:05:01 covers 12:04:01 → 12:05:01"): `TWAP = Σ(price × duration) / 60` over ~1 s underlying reports. The report schema is v2, whose `price` is the TWAP, "not a spot or median price" | `docs.chain.link/data-streams/concepts/twap-streams` (sha256 `107270af…8901`), `…/report-schema-v2` (sha256 `cd7b8eaa…8521`) | documentation-based |
| 7 | The live RTDS TWAP matches a trailing 60 s mean of RTDS Chainlink spot best with a +2 to +3 s timestamp offset: median absolute error $0.17 (~2.6 ppm), max ~$3, n = 353 one-second points over 14:56:47–15:05:40Z. 59 s and 61 s windows fit worse | `evidence/rtds-1500.jsonl` (sha256 `83e4f7d7…003a`), `verify_twap.py` | observed-behavior (independent recording) |
| 8 | Changed-input check on the next live window, 15:00–15:05Z (`btc-updown-5m-1794841200`). Polymarket's `openPrice` 65151.35038986651 and `closePrice` 64825.79502728783 equal, to every digit, the RTDS `crypto_prices_twap_sixty` values recorded at 15:00:00 and 15:05:00. The window resolves Down | `evidence/rtds-1500.jsonl`, `evidence/crypto-price-1794841200.json` | observed-behavior (independent recording + page API) |
| 9 | Resolution was written on-chain at 14:41:19Z with `price = 1e18` (first outcome, Up), tx `0xbabf5d15…cc24`, `wasDisputed: false` | `evidence/market-resolution.json` | observed (page API) |

## What this changes in practice

These are inferences from findings 1–8. They assume spot behaves like Brownian
motion with volatility σ.

- **The price to beat is set before the window opens.** It is the average over
  the minute before the start (14:34–14:35), so it is fully known at 14:35:00.
- **Late moves are damped.** With τ seconds left, a move in price shifts the
  final value by only τ/60 of its size. The remaining standard deviation of the
  final value, relative to a spot-settled market, is τ / (60·√3): about 10 % at
  10 s, 29 % at 30 s and 58 % at 60 s. Last-second spikes barely matter.
- **A live estimate is computable.** During the final minute, the expected
  final value is (∫ spot over the elapsed part of the last minute + τ·spot_now)
  / 60.
- **Overall variance is only slightly lower.** Comparing two 1-minute averages
  whose starts are 5 minutes apart has variance (5 − 1/3)·σ²·min, against 5·σ²
  for spot-to-spot. The standard deviation is about 3.4 % lower, so pricing
  intuition from spot-settled 5-minute markets mostly carries over.
- **The free feed is not precise enough for close calls.** A reconstruction
  from the RTDS spot feed carries ~$0.2 typical (~$3 worst) error, and RTDS
  repeats a stale TWAP value on ~20 % of seconds. When the margin is a few
  dollars, only Chainlink's own TWAP stream decides.

## Limitations and blockers

- **The Chainlink stream page itself was not observed.** `data.chain.link` sits
  behind a Vercel checkpoint: the document returns 429, and `POST
  /.well-known/vercel/security/request-challenge` returns **708**. Headless
  Chromium did not pass it, and no evasion was attempted. The stream's feed ID,
  node set and page metadata are therefore unverified. Its definition here is
  documentation-based (finding 6) and behavior-based (findings 7–8).
- **REA 6.5.0 crashes on that 708 response.** `capture_browser_scenario` exits
  with an uncaught `ZodError` ("expected number to be <=599") and returns no
  Evidence. The same bound exists at this repository's HEAD:
  `src/domain/browserScenarioCaptureValues.ts:186` caps `status` at 599, and
  `src/browser/network/PlaywrightScenarioNetwork.ts:229` passes
  `response.status()` unchecked. This should be fixed separately.
- **REA launch mode got no network here.** It did not use the container's
  HTTPS proxy (`ERR_NAME_NOT_RESOLVED`), so both captures used connect mode
  against a self-started Chromium with `--proxy-server`.
- **The first capture hit REA's event budget.** About 150 static assets
  exhausted it, so request and response bodies were dropped. The second
  capture, without bodies, retained all events.
- **The spot endpoints were not used.** `twapEnabled=false` queries to the
  same endpoints returned values inconsistent with the TWAP series on any
  timing convention (2-decimal, `cached: true`), so they are excluded.
- **Not used:** the Chainlink Data Streams API (it requires credentials) and
  Polymarket's on-chain contracts (the transaction hash was not decoded).

## Reproduce

```sh
npm install ws@8 https-proxy-agent@7
node rtds_record.cjs rtds.jsonl $(( $(date -u -d '2026-11-16T15:05:00Z' +%s) + 40 ))
python3 -I verify_twap.py rtds.jsonl --check-window 2026-11-16T15:00:00Z
curl -s 'https://polymarket.com/api/crypto/crypto-price?symbol=BTC&eventStartTime=2026-11-16T15:00:00Z&variant=fiveminute&endDate=2026-11-16T15:05:00Z&twapEnabled=true&twapLookbackSeconds=60'
```

Substitute a current window. Slugs are `btc-updown-5m-<window start, Unix seconds>`.
