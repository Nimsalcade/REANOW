#!/usr/bin/env node
// Strike-ladder PAPER bot for Polymarket crypto Up/Down markets. No orders are ever sent.
//
// In the final 5 minutes of every 15-minute window, the 15m market and its last 5m market settle on the
// same Chainlink TWAP value F. With strikes Ka < Kb, the pair Up(Ka) + Down(Kb) pays $1, or $2 when
// Ka <= F < Kb. The bot watches the four books (Up/Down of both markets), walks both ask ladders, and
// paper-buys every share pair whose cost incl. taker fees is <= 1 - MIN_EDGE, against the book as it is
// LATENCY_MS after detection. Settlement uses Polymarket's official open/close prices.
//
// Run:   npm install ws        (once; Node 18+)
//        node ladder_paper.cjs [--assets btc,eth,...] [--min-edge 0.005] [--latency 150] [--max-pairs 500]
// Output (./ladder-data/): opportunities.jsonl (every sub-$1 episode: duration, size, best cost),
//        fills.jsonl, settled.jsonl, ledger.md (running totals). Ctrl-C to stop.
// Behind a proxy: set HTTPS_PROXY and `npm install https-proxy-agent`.
'use strict';
const fs = require('fs');
const path = require('path');
const WebSocket = require('ws');

const arg = (k, d) => { const i = process.argv.indexOf(k); return i > 0 ? process.argv[i + 1] : d; };
const ASSETS = arg('--assets', 'btc,eth,sol,xrp,doge,bnb,hype').split(',');
const MIN_EDGE = Number(arg('--min-edge', '0.005'));
const LATENCY_MS = Number(arg('--latency', '150'));
const MAX_PAIRS = Number(arg('--max-pairs', '500'));
const OUT = path.resolve(arg('--out', 'ladder-data'));
fs.mkdirSync(OUT, { recursive: true });

let agent;
if (process.env.HTTPS_PROXY || process.env.https_proxy) {
  try { const { HttpsProxyAgent } = require('https-proxy-agent'); agent = new HttpsProxyAgent(process.env.HTTPS_PROXY || process.env.https_proxy); } catch { /* direct */ }
}
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const append = (f, o) => fs.appendFileSync(path.join(OUT, f), JSON.stringify(o) + '\n');
async function getJson(url) {
  const r = await fetch(url, { headers: { 'user-agent': 'Mozilla/5.0' } });
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return r.json();
}
const iso = (s) => new Date(s * 1000).toISOString().replace('.000', '');

// ---------------------------------------------------------------- state per 15-minute window
const active = new Map();   // key `${asset}:${S}` -> window state
const totals = { pairs: 0, cost: 0, pnl: 0, windows: new Set(), settledPairs: 0, opps: 0 };

async function officialOpen(asset, start, variant, dur) {
  const q = `symbol=${asset.toUpperCase()}&eventStartTime=${iso(start)}&variant=${variant}&endDate=${iso(start + dur)}&twapEnabled=true&twapLookbackSeconds=60`;
  return getJson(`https://polymarket.com/api/crypto/crypto-price?${q}`);
}

const retryAt = new Map();   // key -> earliest next setup attempt (ms), with backoff after failures
async function setupWindow(asset, S) {
  const key = `${asset}:${S}`;
  if (active.has(key) || Date.now() < (retryAt.get(key) || 0)) return;
  const st = { asset, S, books: {}, tokens: {}, fee: { rate: 0.07, exponent: 1 }, legs: null, pairs: 0, cost: 0, fills: [], opp: null, ws: null };
  active.set(key, st);
  try {
    const evs = await getJson(`https://gamma-api.polymarket.com/events?limit=10&slug=${asset}-updown-15m-${S}&slug=${asset}-updown-5m-${S + 600}`);
    const m15 = evs.find((e) => e.slug.includes('-15m-')); const m5 = evs.find((e) => e.slug.includes('-5m-'));
    if (!m15 || !m5) throw new Error('markets not listed');
    for (const [tag, ev] of [['m15', m15], ['m5', m5]]) {
      const mk = ev.markets[0];
      const toks = JSON.parse(mk.clobTokenIds);
      st.tokens[toks[0]] = [tag, 0]; st.tokens[toks[1]] = [tag, 1];
      if (mk.feeSchedule) st.fee = mk.feeSchedule;
      st[tag] = { slug: ev.slug, cfg: mk.cryptoMarketConfig };
    }
    const [o15, o5] = await Promise.all([officialOpen(asset, S, 'fifteen', 900), officialOpen(asset, S + 600, 'fiveminute', 300)]);
    if (o15.openPrice == null || o5.openPrice == null) throw new Error('open price not yet published');
    const K15 = o15.openPrice, K5 = o5.openPrice;
    if (K15 === K5) throw new Error('equal strikes');
    // Up on the lower strike, Down on the higher strike
    st.legs = K15 < K5 ? { a: ['m15', 0], b: ['m5', 1] } : { a: ['m5', 0], b: ['m15', 1] };
    st.K15 = K15; st.K5 = K5; st.Ka = Math.min(K15, K5); st.Kb = Math.max(K15, K5);
    log(asset, 'window', S, `K15 ${K15.toFixed(4)} K5 ${K5.toFixed(4)} legs Up(${st.legs.a[0]}) + Down(${st.legs.b[0]})`);
    for (const delay of [15000, 45000]) setTimeout(() => refreshStrikes(st), delay);
  } catch (e) {
    const n = (st.attempt = (retryAt.get(key + ':n') || 0) + 1);
    retryAt.set(key + ':n', n);
    retryAt.set(key, Date.now() + Math.min(8000, 1000 * 2 ** Math.min(n, 3)) + Math.random() * 1000);   // backoff + jitter (429s)
    log(asset, 'setup failed', S, e.message.split('?')[0]); active.delete(key); return;
  }
  for (const tk of Object.keys(st.tokens)) st.books[tk] = { bids: new Map(), asks: new Map() };
  const tokenOf = (tag, a) => Object.keys(st.tokens).find((t) => st.tokens[t][0] === tag && st.tokens[t][1] === a);
  st.legTok = { a: tokenOf(...st.legs.a), b: tokenOf(...st.legs.b) };
  const ws = new WebSocket('wss://ws-subscriptions-clob.polymarket.com/ws/market', agent ? { agent } : {});
  st.ws = ws;
  ws.on('open', () => ws.send(JSON.stringify({ assets_ids: Object.keys(st.tokens), type: 'market' })));
  const ping = setInterval(() => { if (ws.readyState === 1) ws.send('PING'); }, 10000);
  ws.on('close', () => clearInterval(ping));
  ws.on('error', (e) => log(asset, 'ws error', e.message));
  ws.on('message', (buf) => {
    const text = buf.toString();
    if (!text.startsWith('{') && !text.startsWith('[')) return;
    let msgs; try { msgs = JSON.parse(text); } catch { return; }
    for (const m of Array.isArray(msgs) ? msgs : [msgs]) {
      const et = m.event_type || (m.bids ? 'book' : m.price_changes ? 'price_change' : null);
      if (et === 'book' && st.books[m.asset_id]) {
        const b = st.books[m.asset_id]; b.bids.clear(); b.asks.clear();
        for (const l of m.bids || m.buys || []) if (+l.size > 0) b.bids.set(l.price, +l.size);
        for (const l of m.asks || m.sells || []) if (+l.size > 0) b.asks.set(l.price, +l.size);
      } else if (et === 'price_change') {
        for (const c of m.price_changes || []) {
          const b = st.books[c.asset_id]; if (!b) continue;
          const side = c.side === 'BUY' ? b.bids : b.asks;
          if (+c.size > 0) side.set(c.price, +c.size); else side.delete(c.price);
        }
      }
    }
    check(st);
  });
}

// Official opens can be revised shortly after a window starts: re-read them and re-orient the legs.
async function refreshStrikes(st) {
  try {
    const [o15, o5] = await Promise.all([officialOpen(st.asset, st.S, 'fifteen', 900), officialOpen(st.asset, st.S + 600, 'fiveminute', 300)]);
    if (o15.openPrice == null || o5.openPrice == null) return;
    if (o15.openPrice !== st.K15 || o5.openPrice !== st.K5) {
      const legs = o15.openPrice < o5.openPrice ? { a: ['m15', 0], b: ['m5', 1] } : { a: ['m5', 0], b: ['m15', 1] };
      const flipped = legs.a[0] !== st.legs.a[0];
      log(st.asset, `strikes revised: K15 ${st.K15} -> ${o15.openPrice}, K5 ${st.K5} -> ${o5.openPrice}${flipped ? ' (legs re-oriented)' : ''}`);
      st.K15 = o15.openPrice; st.K5 = o5.openPrice; st.Ka = Math.min(st.K15, st.K5); st.Kb = Math.max(st.K15, st.K5);
      if (flipped) {
        const tokenOf = (tag, a) => Object.keys(st.tokens).find((t) => st.tokens[t][0] === tag && st.tokens[t][1] === a);
        st.legs = legs; st.legTok = { a: tokenOf(...legs.a), b: tokenOf(...legs.b) };
      }
    }
  } catch (e) { /* keep current strikes */ }
}

const feeOf = (st, p) => st.fee.rate * Math.pow(p * (1 - p), st.fee.exponent);
const asksOf = (st, tok) => [...st.books[tok].asks.entries()].map(([p, s]) => [+p, s]).sort((x, y) => x[0] - y[0]);

// Walk both ask ladders; return pairs buyable at marginal pair cost <= limit and their total cost.
function walk(st, limit, cap) {
  const A = asksOf(st, st.legTok.a).map((x) => x.slice()), B = asksOf(st, st.legTok.b).map((x) => x.slice());
  let i = 0, j = 0, pairs = 0, cost = 0, best = null;
  while (i < A.length && j < B.length && pairs < cap) {
    const pa = A[i][0], pb = B[j][0];
    const unit = pa + pb + feeOf(st, pa) + feeOf(st, pb);
    if (best === null) best = unit;
    if (unit > limit) break;
    const q = Math.min(A[i][1], B[j][1], cap - pairs);
    pairs += q; cost += q * unit; A[i][1] -= q; B[j][1] -= q;
    if (A[i][1] <= 1e-9) i++; if (B[j][1] <= 1e-9) j++;
  }
  if (best === null && A.length && B.length) best = A[0][0] + B[0][0] + feeOf(st, A[0][0]) + feeOf(st, B[0][0]);
  return { pairs, cost, best };
}

function check(st) {
  if (!st.legs) return;
  const now = Date.now() / 1000;
  if (now < st.S + 600 || now > st.S + 898) return;
  const w = walk(st, 1 - MIN_EDGE, Infinity);
  if (w.pairs > 0) {
    if (!st.opp) {
      st.opp = { start: Date.now(), maxPairs: w.pairs, best: w.best };
      totals.opps++;
      if (!st.pending && st.pairs < MAX_PAIRS) {
        st.pending = true;
        setTimeout(() => {   // paper fill against the book as it is LATENCY_MS later
          st.pending = false;
          const f = walk(st, 1 - MIN_EDGE, MAX_PAIRS - st.pairs);
          if (f.pairs > 0) {
            st.pairs += f.pairs; st.cost += f.cost;
            const rec = { asset: st.asset, S: st.S, t: (Date.now() / 1000 - st.S).toFixed(1), pairs: +f.pairs.toFixed(2), unit: +(f.cost / f.pairs).toFixed(4), legs: st.legs };
            st.fills.push(rec); append('fills.jsonl', rec);
            log(st.asset, `PAPER FILL ${f.pairs.toFixed(1)} pairs @ ${(f.cost / f.pairs).toFixed(4)} (window +${rec.t}s)`);
          }
        }, LATENCY_MS);
      }
    } else {
      st.opp.maxPairs = Math.max(st.opp.maxPairs, w.pairs); st.opp.best = Math.min(st.opp.best, w.best);
    }
  } else if (st.opp) {
    const o = { asset: st.asset, S: st.S, duration_ms: Date.now() - st.opp.start, max_pairs: +st.opp.maxPairs.toFixed(2), best_cost: +st.opp.best.toFixed(4) };
    append('opportunities.jsonl', o); st.opp = null;
  }
}

async function settle(st) {
  if (st.ws) st.ws.close();
  if (st.pairs <= 0) return;
  for (let i = 0; i < 20; i++) {
    try {
      const [c15, c5] = await Promise.all([officialOpen(st.asset, st.S, 'fifteen', 900), officialOpen(st.asset, st.S + 600, 'fiveminute', 300)]);
      if (c15.completed && c5.completed && c15.closePrice != null && c5.closePrice != null) {
        const F = c15.closePrice;
        const same = Math.abs(F - c5.closePrice) < 1e-9;
        // each leg settles exactly like its own market: Up wins iff close >= open, Down wins otherwise
        const up = { m15: c15.closePrice >= c15.openPrice, m5: c5.closePrice >= c5.openPrice };
        let payout = 0;
        for (const f of st.fills) {
          const [ta, oa] = f.legs.a, [tb, ob] = f.legs.b;
          const winA = oa === 0 ? up[ta] : !up[ta], winB = ob === 0 ? up[tb] : !up[tb];
          payout += f.pairs * ((winA ? 1 : 0) + (winB ? 1 : 0));
        }
        const payoff = st.pairs ? payout / st.pairs : 0;
        const pnl = payout - st.cost;
        totals.pairs += st.pairs; totals.cost += st.cost; totals.pnl += pnl; totals.windows.add(`${st.asset}:${st.S}`);
        append('settled.jsonl', { asset: st.asset, S: st.S, pairs: st.pairs, cost: st.cost, K15: c15.openPrice, K5: c5.openPrice, F, F5: c5.closePrice, same_F: same, payoff, pnl });
        log(st.asset, `SETTLED ${st.S}: ${st.pairs.toFixed(1)} pairs, payoff $${payoff.toFixed(2)}/pair, P&L ${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}${same ? '' : '  (WARNING: F mismatch)'}`);
        writeLedger(); return;
      }
    } catch (e) { /* retry */ }
    await new Promise((r) => setTimeout(r, 15000));
  }
}

function writeLedger() {
  const lines = [`# Ladder paper ledger — ${new Date().toISOString()}`, '',
    `Settings: min edge ${MIN_EDGE}, latency ${LATENCY_MS} ms, max ${MAX_PAIRS} pairs per window, assets ${ASSETS.join(',')}`, '',
    `| windows traded | pairs | cost $ | P&L $ | P&L per pair ¢ | sub-$1 episodes seen |`, '|---|---|---|---|---|---|',
    `| ${totals.windows.size} | ${totals.pairs.toFixed(1)} | ${totals.cost.toFixed(2)} | ${totals.pnl.toFixed(2)} | ${totals.pairs ? (100 * totals.pnl / totals.pairs).toFixed(2) : '–'} | ${totals.opps} |`];
  fs.writeFileSync(path.join(OUT, 'ledger.md'), lines.join('\n') + '\n');
}

// ---------------------------------------------------------------- scheduler
function tick() {
  const now = Math.floor(Date.now() / 1000);
  const S = now - (now % 900);
  if (now >= S + 600 && now < S + 890) ASSETS.forEach((a, i) => setTimeout(() => setupWindow(a, S), i * 350));   // final 5 minutes, staggered
  for (const [key, st] of active) {
    if (now >= st.S + 900 && !st.settling) { st.settling = true; settle(st).finally(() => active.delete(key)); }
  }
}
// Rebuild running totals from earlier runs in the same output folder, so restarts keep the ledger cumulative.
function loadTotals() {
  const read = (f) => { try { return fs.readFileSync(path.join(OUT, f), 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l)); } catch { return []; } };
  for (const r of read('settled.jsonl')) {
    totals.pairs += r.pairs; totals.cost += r.cost; totals.pnl += r.pnl; totals.windows.add(`${r.asset}:${r.S}`);
  }
  totals.opps += read('opportunities.jsonl').length;
}
loadTotals();
setInterval(tick, 2000); tick(); writeLedger();
log(`ladder paper bot started: ${ASSETS.join(',')} | min edge ${MIN_EDGE} | latency ${LATENCY_MS} ms | output ${OUT}`);
log('waiting for the final 5 minutes of the current 15-minute window (minute 10-15, 25-30, 40-45, 55-60)');
