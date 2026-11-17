"""Compare Polymarket RTDS `crypto_prices_twap_sixty` with a trailing mean of
`crypto_prices_chainlink` spot ticks recorded by `rtds_record.cjs`.

For each window length W and timestamp offset `lag`, the candidate is the mean
of forward-filled one-second spot values over (T - lag - W, T - lag]. Only TWAP
points whose value changed from the previous second are scored, because RTDS
repeats stale values. With --check-window START_ISO, also report the recorded
TWAP at START and START + 5 minutes for comparison with Polymarket's
openPrice/closePrice.

Usage: python3 -I verify_twap.py rtds.jsonl [--check-window 2026-11-16T15:00:00Z]
"""

import argparse
import datetime as dt
import json
from fractions import Fraction

SCALE = 10**18


def load(path):
    spot, twap = {}, {}
    for line in open(path, encoding="utf-8"):
        record = json.loads(line)
        if "raw" not in record:
            continue
        try:
            message = json.loads(record["raw"])
        except ValueError:
            continue
        topic = message.get("topic")
        payload = message.get("payload") or {}
        if topic == "crypto_prices_twap_sixty":
            target = twap
        elif topic in ("crypto_prices_chainlink", "crypto_prices"):
            target = spot
        else:
            continue
        rows = payload["data"] if isinstance(payload.get("data"), list) else [payload]
        for row in rows:
            if "timestamp" not in row or "value" not in row:
                continue
            if row.get("symbol", "btc/usd") != "btc/usd":
                continue
            exact = int(row["full_accuracy_value"]) if row.get("full_accuracy_value") else None
            # Subscription snapshots carry only floats; keep them as an approximation.
            approx = exact if exact is not None else int(Fraction(str(row["value"])) * SCALE)
            target[row["timestamp"] // 1000] = (approx, exact, row["value"])
    return spot, twap


def forward_fill(spot):
    filled, last = {}, None
    for second in range(min(spot), max(spot) + 1):
        if second in spot:
            last = spot[second][0]
        if last is not None:
            filled[second] = last
    return filled


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("recording")
    parser.add_argument("--check-window")
    args = parser.parse_args()

    spot, twap = load(args.recording)
    filled = forward_fill(spot)
    missing = sum(1 for s in range(min(spot), max(spot) + 1) if s not in spot)
    fresh, previous = {}, None
    for second in sorted(twap):
        value = twap[second][0]
        if value != previous:
            fresh[second] = value
        previous = value
    print(f"spot seconds {len(spot)} (missing {missing}); twap seconds {len(twap)}, changed {len(fresh)}")

    rows = []
    for window in (59, 60, 61):
        for lag in range(-3, 8):
            errors = []
            for second, value in fresh.items():
                span = range(second - lag - window + 1, second - lag + 1)
                if any(s not in filled for s in span):
                    continue
                mean = Fraction(sum(filled[s] for s in span), window)
                errors.append(float(abs(mean - value)) / SCALE)
            if len(errors) >= 10:
                errors.sort()
                rows.append((errors[len(errors) // 2], window, lag, len(errors), errors[-1]))
    for median, window, lag, count, largest in sorted(rows)[:5]:
        print(f"W={window}s lag={lag:+d}s n={count} median_abs_err=${median:.4f} max_abs_err=${largest:.4f}")

    if args.check_window:
        start = int(dt.datetime.fromisoformat(args.check_window.replace("Z", "+00:00")).timestamp())
        for label, second in (("open", start), ("close", start + 300)):
            hit = twap.get(second)
            print(f"recorded TWAP at {label} {second}: {hit[2] if hit else 'not recorded'}"
                  f" full_accuracy={hit[1] if hit else None}")


if __name__ == "__main__":
    main()
