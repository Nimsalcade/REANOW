"""Real-time paper bot: tails the recorder's live JSONL and paper-trades the frozen candidates.

State is incremental (per asset): per-second spot (Binance mid by receive time), EWMA volatility,
Binance->Chainlink offset from the TWAP stream, K/F from the TWAP stream, books, prints.
Takers: a decision at time r becomes a pending order; at r + lat the bot checks the book as it
actually is then and fills at the ask (walking top-5 depth) if within the limit.
Makers: post-only bids refreshed every requote_ms, active after lat, filled on trade-through prints.
Every fill is appended to data/paper/fills.jsonl; settlements to data/paper/settled.jsonl;
a running ledger is written to data/paper/ledger.md.

Usage: python3 -I paperbot.py   (warms up on the current hour's file, then trades live)
"""
import collections
import glob
import json
import math
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LIVE = os.path.join(HERE, "data", "live")
OUT = os.path.join(HERE, "data", "paper")
os.makedirs(OUT, exist_ok=True)
CFG = json.load(open(os.path.join(HERE, "forward.json")))
LAGS = {"hype": 6}          # seconds; calibrated per asset (default 4)
DELTA_N, HALFLIFE = 12, 600


def fee(p):
    return 0.07 * p * (1 - p)


def phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def num(v):
    if isinstance(v, str):
        return int(v) / 1e18 if v.isdigit() and len(v) > 12 else float(v)
    return float(v)


class Asset:
    def __init__(self, name):
        self.name = name
        self.lag = LAGS.get(name, 4)
        self.mid = None
        self.cur_sec = None
        self.sec = {}                      # second -> spot at end of second
        self.prefix = {}                   # second -> cumulative sum up to and including second
        self.var = None
        self.alpha = 1 - 0.5 ** (1 / HALFLIFE)
        self.raw = collections.deque(maxlen=DELTA_N)
        self.delta = None
        self.res = collections.deque(maxlen=600)
        self.tw = {}                       # T -> value
        self.tob = {}                      # (w, a) -> (bb, bbs, ba, bas)
        self.depth = {}                    # (w, a) -> (bids, asks)
        self.fair_hist = collections.defaultdict(collections.deque)  # w -> deque[(r, p_up)]

    def on_spot(self, r, mid):
        s = r // 1000
        if self.cur_sec is None:
            self.cur_sec = s
        while self.cur_sec < s:            # finalize completed seconds
            if self.mid is not None:
                prev = self.sec.get(self.cur_sec - 1)
                self.sec[self.cur_sec] = self.mid
                self.prefix[self.cur_sec] = self.prefix.get(self.cur_sec - 1, 0.0) + self.mid
                if prev is not None:
                    d = self.mid - prev
                    self.var = d * d if self.var is None else (1 - self.alpha) * self.var + self.alpha * d * d
            self.cur_sec += 1
        self.mid = mid

    def bmean(self, end, length=60):
        a, b = self.prefix.get(end), self.prefix.get(end - length)
        return None if a is None or b is None else (a - b) / length

    def on_twap(self, T, v):
        self.tw[T] = v
        bm = self.bmean(T - self.lag)
        if bm is None:
            return
        raw = v - bm
        if self.delta is not None:
            self.res.append(raw - self.delta)
        self.raw.append(raw)
        self.delta = statistics.median(self.raw)

    def meas(self):
        return statistics.pstdev(self.res) if len(self.res) > 30 else None

    def fair(self, w, r, sig=1.0, meas_mult=1.0):
        K = self.tw.get(w)
        meas = self.meas()
        if K is None or self.mid is None or self.delta is None or self.var is None or meas is None:
            return None
        E1 = w + 300 - self.lag
        t = r / 1000
        s_now = int(t)
        sigma = math.sqrt(self.var) * sig
        bnow = self.mid
        if s_now < E1 - 60:
            m = bnow + self.delta
            v = sigma ** 2 * ((E1 - 60 - t) + 20.5)
        elif s_now <= E1:
            hi = self.prefix.get(s_now - 1)
            lo = self.prefix.get(E1 - 60)
            if hi is None or lo is None:
                return None
            known = hi - lo if s_now - 1 > E1 - 60 else 0.0
            tau = E1 - (s_now - 1)
            m = (known + tau * bnow) / 60 + self.delta
            v = sigma ** 2 * tau * (tau + 1) * (2 * tau + 1) / (6 * 3600)
        else:
            bm = self.bmean(E1)
            if bm is None:
                return None
            m = bm + self.delta
            v = 0.0
        return phi((m - K) / math.sqrt(v + (meas * meas_mult) ** 2))


class Bot:
    def __init__(self, live_from_ms=0):
        self.live_from = live_from_ms   # events before this only warm up state; no paper trading
        self.assets = {}
        self.pending = []      # taker orders: dict(due, as, w, a, limit, shares, tag, fair, r)
        self.quotes = {}       # (tag, as, w, a) -> dict(q, active_from, until, shares_left, fair)
        self.maker_pos = collections.defaultdict(float)
        self.fills = collections.defaultdict(list)   # (as, w) -> fills
        self.state = collections.defaultdict(dict)   # (tag, as, w) -> {"n": trades, "last": r}
        self.settled = set()
        self.fills_f = open(os.path.join(OUT, "fills.jsonl"), "a")
        self.settled_f = open(os.path.join(OUT, "settled.jsonl"), "a")
        self.totals = collections.defaultdict(lambda: {"fills": 0, "shares": 0.0, "pnl": 0.0, "windows": set(), "wpnl": collections.defaultdict(float)})
        self.windows_seen = collections.defaultdict(set)
        self.last_tick = 0

    def A(self, name):
        if name not in self.assets:
            self.assets[name] = Asset(name)
        return self.assets[name]

    # ------------------------------------------------------------ events
    def on_row(self, row):
        k, as_ = row["k"], row.get("as", "btc")
        r = row["t"]
        if k == "bn":
            self.A(as_).on_spot(r, (num(row["b"]) + num(row["a"])) / 2)
        elif k == "twap":
            T = row["ts"] // 1000
            a = self.A(as_)
            a.on_twap(T, num(row["v"]))
            if T % 300 == 0:
                self.settle(as_, T - 300)
        elif k == "tob":
            self.A(as_).tob[(row["w"], row["a"])] = (row["bb"], row["bbs"], row["ba"], row["bas"])
            self.windows_seen[as_].add(row["w"])
        elif k == "depth":
            self.A(as_).depth[(row["w"], row["a"])] = (row["bids"], row["asks"])
        elif k == "trade":
            self.on_print(as_, row)
        while self.pending and self.pending[0]["due"] <= r:
            self.execute(self.pending.pop(0))
        if r - self.last_tick >= 100:
            self.last_tick = r - r % 100
            self.tick(self.last_tick)

    # ------------------------------------------------------------ strategies
    def tick(self, r):
        now_s = r // 1000
        for as_, a in self.assets.items():
            for w in (now_s - now_s % 300,):
                if (w, 0) not in a.tob or w not in a.tw:
                    continue
                for tag, name, P in (CFG["candidates"] if r >= self.live_from and w * 1000 >= self.live_from - 300000 else ()):
                    f = a.fair(w, r, P.get("sig", 1.0), P.get("meas", 1.0))
                    if f is None:
                        continue
                    if name == "taker_snipe":
                        self.snipe(tag, as_, a, w, r, f, P)
                    elif name == "taker_value":
                        self.value(tag, as_, a, w, r, f, P)
                    elif name == "maker_value":
                        self.maker(tag, as_, a, w, r, f, P)
                hist = a.fair_hist[w]
                f0 = a.fair(w, r)
                if f0 is not None:
                    hist.append((r, f0))
                    while hist and hist[0][0] < r - 2500:
                        hist.popleft()

    def fair_ago(self, a, w, r, ms):
        for rr, f in a.fair_hist[w]:
            if rr >= r - ms - 50:
                return f if rr <= r - ms + 50 else None
        return None

    def order(self, tag, as_, w, a_idx, r, limit, shares, fp, lat):
        st = self.state[(tag, as_, w)]
        st["n"] = st.get("n", 0) + 1
        st["last"] = r
        self.pending.append({"due": r + lat, "as": as_, "w": w, "a": a_idx, "limit": min(0.999, limit),
                             "shares": shares, "tag": tag, "fair": fp, "r": r})
        self.pending.sort(key=lambda o: o["due"])

    def snipe(self, tag, as_, a, w, r, f, P):
        st = self.state[(tag, as_, w)]
        if st.get("n", 0) >= P.get("max_trades", 3) or r - st.get("last", -10 ** 12) < P.get("gap_ms", 5000):
            return
        if r < w * 1000 + P.get("t_from", 5) * 1000 or r > w * 1000 + P.get("t_to", 299) * 1000:
            return
        f0 = self.fair_ago(a, w, r, P.get("look_ms", 1000))
        if f0 is None or abs(f - f0) < P.get("jump", 0.04):
            return
        ai, fp = (0, f) if f > f0 else (1, 1 - f)
        bk = a.tob.get((w, ai))
        if not bk or bk[2] is None or not (P.get("pmin", 0.03) <= bk[2] <= P.get("pmax", 0.97)):
            return
        ask, edge = bk[2], P.get("edge", 0.02)
        if fp - ask - fee(ask) > edge:
            self.order(tag, as_, w, ai, r, fp - fee(ask) - edge / 2, P.get("shares", 100), fp, P.get("lat_ms", 250))

    def value(self, tag, as_, a, w, r, f, P):
        st = self.state[(tag, as_, w)]
        if st.get("n", 0) >= P.get("max_trades", 1):
            return
        if r < w * 1000 + P.get("t_from", 0) * 1000 or r >= w * 1000 + P.get("t_to", 300) * 1000:
            return
        for ai, fp in ((0, f), (1, 1 - f)):
            bk = a.tob.get((w, ai))
            if not bk or bk[2] is None or not (P.get("pmin", 0.02) <= bk[2] <= P.get("pmax", 0.99)):
                continue
            ask, edge = bk[2], P.get("edge", 0.02)
            if fp - ask - fee(ask) > edge:
                self.order(tag, as_, w, ai, r, fp - fee(ask) - edge / 2, P.get("shares", 100), fp, P.get("lat_ms", 250))
                return

    def maker(self, tag, as_, a, w, r, f, P):
        if r < w * 1000 + P.get("t_from", 5) * 1000 or r > w * 1000 + P.get("t_to", 295) * 1000:
            for ai in (0, 1):
                self.quotes.pop((tag, as_, w, ai), None)
            return
        rq = P.get("requote_ms", 1000)
        for ai, fp in ((0, f), (1, 1 - f)):
            key = (tag, as_, w, ai)
            qt = self.quotes.get(key)
            if qt and r < qt["posted"] + rq:
                continue
            q = math.floor((fp - P.get("margin", 0.03)) * 100) / 100
            bk = a.tob.get((w, ai))
            if (not bk or bk[2] is None or q >= bk[2] or not (P.get("pmin", 0.05) <= q <= P.get("pmax", 0.95))
                    or (P.get("join_only", 0) and bk[0] is not None and q < bk[0])):
                self.quotes.pop(key, None)
                continue
            self.quotes[key] = {"q": round(q, 2), "posted": r, "active_from": r + P.get("lat_ms", 250),
                                "shares": P.get("shares", 50), "fair": fp, "cap": P.get("cap", 300)}

    def on_print(self, as_, row):
        w, ta, price, size, side, r = row["w"], row["a"], row["p"], row["s"], row["sd"], row["t"]
        for key, qt in list(self.quotes.items()):
            tag, qa, qw, ai = key
            if qa != as_ or qw != w or r < qt["active_from"] or qt["shares"] <= 0:
                continue
            q = qt["q"]
            hit = (ta == ai and side == "SELL" and price < q) or (ta == 1 - ai and side == "BUY" and price > 1 - q)
            pos_key = (tag, as_, w, ai)
            if hit and self.maker_pos[pos_key] < qt["cap"]:
                x = min(size, qt["shares"], qt["cap"] - self.maker_pos[pos_key])
                qt["shares"] -= x
                self.maker_pos[pos_key] += x
                self.record({"tag": tag, "as": as_, "w": w, "a": ai, "t": (r - w * 1000) / 1000, "price": q,
                             "q": x, "fee": 0.0, "fair": qt["fair"], "kind": "maker"})

    def execute(self, o):
        a = self.assets[o["as"]]
        bk = a.tob.get((o["w"], o["a"]))
        if not bk or bk[2] is None or bk[2] > o["limit"]:
            return
        levels = [[bk[2], bk[3] or 0]]
        dp = a.depth.get((o["w"], o["a"]))
        if dp:
            levels += [lv for lv in dp[1] if lv[0] > bk[2]]
        left, cost, got, fees = o["shares"], 0.0, 0.0, 0.0
        for price, size in levels:
            if price > o["limit"] or left <= 0:
                break
            q = min(left, size)
            got += q; cost += q * price; fees += q * fee(price); left -= q
        if got > 0:
            self.record({"tag": o["tag"], "as": o["as"], "w": o["w"], "a": o["a"], "t": (o["due"] - o["w"] * 1000) / 1000,
                         "price": round(cost / got, 4), "q": got, "fee": round(fees, 4), "fair": o["fair"], "kind": "taker"})

    def record(self, f):
        self.fills[(f["as"], f["w"])].append(f)
        self.fills_f.write(json.dumps(f) + "\n"); self.fills_f.flush()

    # ------------------------------------------------------------ settlement
    def settle(self, as_, w):
        a = self.assets[as_]
        if (as_, w) in self.settled or w not in a.tw or w + 300 not in a.tw or (w + 300) * 1000 < self.live_from:
            return
        self.settled.add((as_, w))
        up = a.tw[w + 300] >= a.tw[w]
        for f in self.fills.pop((as_, w), []):
            won = (f["a"] == 0) == up
            pnl = f["q"] * ((1.0 if won else 0.0) - f["price"]) - f["fee"]
            f.update({"won": won, "pnl": round(pnl, 4), "K": a.tw[w], "F": a.tw[w + 300]})
            self.settled_f.write(json.dumps(f) + "\n")
            T = self.totals[f["tag"]]
            T["fills"] += 1; T["shares"] += f["q"]; T["pnl"] += pnl; T["windows"].add((as_, w)); T["wpnl"][(as_, w)] += pnl
        self.settled_f.flush()
        for k in [k for k in self.quotes if k[1] == as_ and k[2] == w]:
            del self.quotes[k]
        self.write_ledger()

    def write_ledger(self):
        nwin = len(self.settled)
        lines = [f"# Real-time paper ledger — {time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime())}", "",
                 f"Settled windows (all assets): {nwin}", "",
                 "| tag | strategy | fills | windows | shares | PnL $ | ¢/share | t (by window) |", "|---|---|---|---|---|---|---|---|"]
        names = {t: n for t, n, _ in CFG["candidates"]}
        for tag, _, _ in CFG["candidates"]:
            T = self.totals.get(tag)
            if not T or not T["fills"]:
                lines.append(f"| {tag} | {names[tag]} | 0 | 0 | 0 | 0 | – | – |"); continue
            wp = list(T["wpnl"].values())
            tstat = (statistics.mean(wp) / (statistics.stdev(wp) / math.sqrt(len(wp)))) if len(wp) > 2 and statistics.stdev(wp) > 0 else None
            lines.append(f"| {tag} | {names[tag]} | {T['fills']} | {len(T['windows'])} | {T['shares']:.0f} | {T['pnl']:.2f} | "
                         f"{100 * T['pnl'] / T['shares']:.2f} | {'–' if tstat is None else round(tstat, 2)} |")
        open(os.path.join(OUT, "ledger.md"), "w").write("\n".join(lines) + "\n")


def tail():
    """Warm up on the current hour file (no trading), then trade on new events as they arrive."""
    bot = Bot(live_from_ms=int(time.time() * 1000))
    cur, fh = None, None
    while True:
        files = sorted(glob.glob(os.path.join(LIVE, "*.jsonl")))
        if not files:
            time.sleep(1); continue
        if files[-1] != cur:
            if fh:
                for line in fh:   # drain the rest of the previous hour
                    if line.endswith(b"\n"):
                        bot.on_row(json.loads(line))
            cur = files[-1]
            fh = open(cur, "rb")
        line = fh.readline()
        if not line:
            time.sleep(0.02); continue
        if not line.endswith(b"\n"):
            fh.seek(-len(line), 1); time.sleep(0.02); continue
        try:
            bot.on_row(json.loads(line))
        except (ValueError, KeyError):
            pass


if __name__ == "__main__":
    tail()
