#!/usr/bin/env node
// Pre-open pair PAPER bot (no orders are ever sent). For every 5 m Up/Down window S it subscribes to the
// NEXT window's books during the previous window and, at S - lead, rests virtual bids of `size` shares at
// `px` on both Up and Down. Fills follow the price-time queue model of preopen.py / pairmaker.py: a bid
// waits behind the size resting at its price (queue=book) or at the front (queue=front, as if posted
// hours earlier). Prints at our price eat the queue ahead first, and prints through it fill us. Both a
// taker SELL of that outcome and a taker BUY of the mirror outcome hit it. Unfilled size is cancelled at
// S + cancel. A bid at or above the ask is skipped, as a post-only order would be rejected. Pairs merge
// for $1. The leftover settles on Polymarket's official open/close (Up iff close >= open).
//
// Run:   node preopen_paper.cjs [--assets btc,eth,...] [--latency 250] [--out preopen-data]
// Output: placements.jsonl, settled.jsonl (one row per config x asset-window), ledger.md (per config).
'use strict';
const fs = require('fs');
const path = require('path');
const WebSocket = require('ws');

const arg = (k, d) => { const i = process.argv.indexOf(k); return i > 0 ? process.argv[i + 1] : d; };
const ASSETS = arg('--assets', 'btc,eth,sol,xrp,doge,bnb,hype').split(',');
const LATENCY_MS = Number(arg('--latency', '250'));
const OUT = path.resolve(arg('--out', 'preopen-data'));
fs.mkdirSync(OUT, { recursive: true });

let agent;
const PROXY = process.env.HTTPS_PROXY || process.env.https_proxy;
if (PROXY) { try { const { HttpsProxyAgent } = require('https-proxy-agent'); agent = new HttpsProxyAgent(PROXY); } catch { /* direct */ } }
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const append = (f, o) => fs.appendFileSync(path.join(OUT, f), JSON.stringify(o) + '\n');
const iso = (s) => new Date(s * 1000).toISOString().replace('.000', '');
const fee = (p) => 0.07 * p * (1 - p);
async function getJson(url) {
  const r = await fetch(url, { headers: { 'user-agent': 'Mozilla/5.0' } });
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
}

// ---------------------------------------------------------------- configs (all evaluated side by side)
const CONFIGS = [];
for (const lead of [240, 60, 10]) for (const px of [0.49, 0.48, 0.45]) for (const size of [10, 100])
  for (const cancel of [290, 30]) for (const queue of ['book', 'front'])
    CONFIGS.push({ id: `L${lead}_p${px}_s${size}_c${cancel}_${queue}`, lead, px, size, cancel, queue });

const windows = new Map();   // `${asset}:${S}` -> state
const pending = new Set();
const retryAt = new Map();

async function setup(asset, S) {
  const key = `${asset}:${S}`;
  if (windows.has(key) || Date.now() < (retryAt.get(key) || 0)) return;
  const st = { asset, S, tokens: {}, books: [null, null], placed: [], ws: null };
  windows.set(key, st);
  try {
    const evs = await getJson(`https://gamma-api.polymarket.com/events?slug=${asset}-updown-5m-${S}`);
    if (!evs.length) throw new Error('not listed');
    const toks = JSON.parse(evs[0].markets[0].clobTokenIds);
    st.tokens[toks[0]] = 0; st.tokens[toks[1]] = 1;
    st.books = [{ bids: new Map(), asks: new Map() }, { bids: new Map(), asks: new Map() }];
  } catch (e) {
    windows.delete(key); retryAt.set(key, Date.now() + 3000 + Math.random() * 2000);
    log(asset, 'setup failed', S, e.message); return;
  }
  const ws = new WebSocket('wss://ws-subscriptions-clob.polymarket.com/ws/market', agent ? { agent } : {});
  st.ws = ws;
  ws.on('open', () => ws.send(JSON.stringify({ assets_ids: Object.keys(st.tokens), type: 'market' })));
  const ping = setInterval(() => { if (ws.readyState === 1) ws.send('PING'); }, 10000);
  ws.on('close', () => clearInterval(ping));
  ws.on('error', (e) => log(asset, 'ws error', e.message));
  ws.on('message', (buf) => {
    const text = buf.toString();
    if (text[0] !== '{' && text[0] !== '[') return;
    let msgs; try { msgs = JSON.parse(text); } catch { return; }
    for (const m of Array.isArray(msgs) ? msgs : [msgs]) {
      const et = m.event_type || (m.bids ? 'book' : m.price_changes ? 'price_change' : null);
      if (et === 'book' && m.asset_id in st.tokens) {
        const b = st.books[st.tokens[m.asset_id]]; b.bids.clear(); b.asks.clear();
        for (const l of m.bids || m.buys || []) if (+l.size > 0) b.bids.set(+l.price, +l.size);
        for (const l of m.asks || m.sells || []) if (+l.size > 0) b.asks.set(+l.price, +l.size);
      } else if (et === 'price_change') {
        for (const c of m.price_changes || []) {
          if (!(c.asset_id in st.tokens)) continue;
          const b = st.books[st.tokens[c.asset_id]], side = c.side === 'BUY' ? b.bids : b.asks;
          if (+c.size > 0) side.set(+c.price, +c.size); else side.delete(+c.price);
        }
      } else if (et === 'last_trade_price' && m.asset_id in st.tokens) {
        onTrade(st, st.tokens[m.asset_id], +m.price, +m.size, m.side, Number(m.timestamp) || Date.now());
      }
    }
  });
  log(asset, 'subscribed to next window', S, `(opens in ${Math.round(S - Date.now() / 1000)} s)`);
}

const best = (m, hi) => { let b = null; for (const p of m.keys()) if (b === null || (hi ? p > b : p < b)) b = p; return b; };

function place(st, cfg) {
  const bb = st.books.map((b) => best(b.bids, true)), ba = st.books.map((b) => best(b.asks, false));
  const rec = { cfg: cfg.id, asset: st.asset, S: st.S, t: +(Date.now() / 1000 - st.S).toFixed(2), bb, ba };
  if (ba.some((a) => a !== null && cfg.px >= a - 1e-9) || bb.some((b) => b === null)) {
    rec.skip = true; append('placements.jsonl', rec); st.placed.push({ cfg, skip: true }); return;
  }
  const legs = [0, 1].map((s) => ({
    ahead: cfg.queue === 'front' || cfg.px > bb[s] + 1e-9 ? 0 : (st.books[s].bids.get(cfg.px) || 0), left: cfg.size, q: 0, first: null,
  }));
  rec.ahead = legs.map((l) => l.ahead); append('placements.jsonl', rec);
  st.placed.push({ cfg, legs, from: Date.now() });
}

function onTrade(st, a, price, size, side, ts) {
  for (const P of st.placed) {
    if (P.skip || ts < P.from || ts > (st.S + P.cfg.cancel) * 1000) continue;
    for (let s = 0; s < 2; s++) {
      const lg = P.legs[s];
      if (lg.left <= 0) continue;
      if (!((a === s && side === 'SELL') || (a === 1 - s && side === 'BUY'))) continue;
      const hit = a === s ? price : 1 - price;
      let avail;
      if (hit < P.cfg.px - 1e-9) avail = size;
      else if (Math.abs(hit - P.cfg.px) < 1e-9) { const eat = Math.min(lg.ahead, size); lg.ahead -= eat; avail = size - eat; }
      else continue;
      const x = Math.min(avail, lg.left);
      if (x > 0) { lg.left -= x; lg.q += x; if (lg.first === null) lg.first = ts; }
    }
  }
}

async function settle(st) {
  if (st.ws) st.ws.close();
  const live = st.placed.filter((p) => !p.skip);
  if (!st.placed.length) return;
  const q = `symbol=${st.asset.toUpperCase()}&eventStartTime=${iso(st.S)}&variant=fiveminute&endDate=${iso(st.S + 300)}&twapEnabled=true&twapLookbackSeconds=60`;
  for (let i = 0; i < 30; i++) {
    try {
      const c = await getJson(`https://polymarket.com/api/crypto/crypto-price?${q}`);
      if (c.completed && c.openPrice != null && c.closePrice != null) {
        const up = c.closePrice >= c.openPrice;
        for (const P of st.placed) {
          if (P.skip) { append('settled.jsonl', { cfg: P.cfg.id, asset: st.asset, S: st.S, skip: true }); continue; }
          const qs = P.legs.map((l) => l.q), pairs = Math.min(...qs), leftover = Math.abs(qs[0] - qs[1]);
          const heavy = qs[0] > qs[1] ? 0 : 1, won = leftover > 0 ? (heavy === 0) === up : null;
          const cost = P.cfg.px * (qs[0] + qs[1]), pnl = pairs + (won ? leftover : 0) - cost;
          append('settled.jsonl', { cfg: P.cfg.id, asset: st.asset, S: st.S, open: c.openPrice, close: c.closePrice, up, q: qs, pairs, leftover,
            leftover_won: won, cost, pnl, rebate_est: 0.2 * fee(P.cfg.px) * (qs[0] + qs[1]),
            pre_open_fill: P.legs.some((l) => l.first !== null && l.first < st.S * 1000), ahead0: P.legs.map((l) => l.ahead) });
        }
        const ex = live.find((p) => p.cfg.id === 'L60_p0.49_s10_c290_book');
        log(st.asset, `SETTLED ${st.S} ${up ? 'UP' : 'DOWN'}${ex ? `  example L60 0.49x10 hold: q ${ex.legs.map((l) => l.q.toFixed(0)).join('/')}` : ''}`);
        writeLedger(); return;
      }
    } catch (e) { /* retry */ }
    await new Promise((r) => setTimeout(r, 10000));
  }
  log(st.asset, 'settlement unavailable', st.S);
}

function writeLedger() {
  let rows = [];
  try { rows = fs.readFileSync(path.join(OUT, 'settled.jsonl'), 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l)); } catch { /* none */ }
  const by = new Map();
  for (const r of rows) {
    const t = by.get(r.cfg) || { n: 0, skip: 0, any: 0, both: 0, pairs: 0, lo: 0, loWin: 0, loN: 0, pnl: 0, slots: new Set(), pre: 0 };
    if (r.skip) { t.skip++; by.set(r.cfg, t); continue; }
    t.n++; t.slots.add(r.S); t.pairs += r.pairs; t.lo += r.leftover; t.pnl += r.pnl;
    if (r.q[0] > 0 || r.q[1] > 0) t.any++;
    if (r.q[0] > 0 && r.q[1] > 0) t.both++;
    if (r.leftover > 0) { t.loN++; if (r.leftover_won) t.loWin++; }
    if (r.pre_open_fill) t.pre++;
    by.set(r.cfg, t);
  }
  const fmt = (x) => (x >= 0 ? '+' : '') + x.toFixed(2);
  const lines = [`# Pre-open pair paper ledger — ${new Date().toISOString()}`, '',
    `Assets ${ASSETS.join(',')}, latency ${LATENCY_MS} ms. L = seconds before the open the bids are posted, p = bid price on both legs,`,
    `s = shares per leg, c = cancel unfilled size at open + c s (290 = hold), queue book/front.`, '',
    '| config | asset-windows | skipped | any fill | both legs | filled pre-open | pairs | leftover sh | leftover won | P&L $ | $/window |',
    '|---|---|---|---|---|---|---|---|---|---|---|'];
  for (const [id, t] of [...by.entries()].sort((x, y) => y[1].pnl - x[1].pnl)) {
    const pct = (k) => (t.n ? `${Math.round(100 * k / t.n)}%` : '–');
    lines.push(`| ${id} | ${t.n} | ${t.skip} | ${pct(t.any)} | ${pct(t.both)} | ${pct(t.pre)} | ${t.pairs.toFixed(0)} | ${t.lo.toFixed(0)} | ${t.loN ? Math.round(100 * t.loWin / t.loN) + '%' : '–'} | ${fmt(t.pnl)} | ${t.n ? fmt(t.pnl / t.n) : '–'} |`);
  }
  fs.writeFileSync(path.join(OUT, 'ledger.md'), lines.join('\n') + '\n');
}

// ---------------------------------------------------------------- scheduler
function tick() {
  const nowMs = Date.now(), now = Math.floor(nowMs / 1000);
  const next = now - (now % 300) + 300;
  if (next - now <= 290) ASSETS.forEach((a, i) => {
    const key = `${a}:${next}`;
    if (windows.has(key) || pending.has(key)) return;
    pending.add(key); setTimeout(() => { pending.delete(key); setup(a, next); }, i * 300);   // staggered
  });
  for (const [key, st] of windows) {
    if (!st.ws) continue;
    for (const cfg of CONFIGS) {
      const due = (st.S - cfg.lead) * 1000 + LATENCY_MS;
      if (nowMs >= due && !st.placed.some((p) => p.cfg.id === cfg.id) && nowMs < due + 5000) place(st, cfg);
    }
    if (now >= st.S + 300 + 15 && !st.settling) { st.settling = true; settle(st).finally(() => windows.delete(key)); }
  }
}
setInterval(tick, 200); tick(); writeLedger();
log(`pre-open pair paper bot: ${ASSETS.join(',')} | ${CONFIGS.length} configs | latency ${LATENCY_MS} ms | output ${OUT}`);
