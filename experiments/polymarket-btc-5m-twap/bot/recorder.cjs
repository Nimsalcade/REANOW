// Live recorder for Polymarket crypto 5-minute Up/Down paper trading (multi-asset).
// Writes compact JSONL (one file per UTC hour, gzipped on rotation) under data/live/.
// Every row: t = local receive ms, as = asset (btc, eth, sol, xrp, doge, bnb, hype).
//   twap/cl    RTDS Chainlink TWAP60 / Chainlink spot (v = full-accuracy string or float, ts = source ms)
//   bn/cb/kr   Binance (vision mirror) book ticker, Coinbase ticker, Kraken ticker (b, a)
//   tob        Polymarket top of book per window/outcome (a: 0 = Up, 1 = Down), emitted on price change
//   depth      Polymarket top-5 levels per side, once per second per outcome
//   trade      Polymarket last_trade_price events (ts = match ms)
//   window     market metadata; open/label = official openPrice / closePrice
'use strict';
const fs = require('fs');
const path = require('path');
const zlib = require('zlib');
const WebSocket = require('ws');
const { HttpsProxyAgent } = require('https-proxy-agent');

const ASSETS = ['btc', 'eth', 'sol', 'xrp', 'doge', 'bnb', 'hype'];
const BINANCE = { btc: 'btcusdt', eth: 'ethusdt', sol: 'solusdt', xrp: 'xrpusdt', doge: 'dogeusdt', bnb: 'bnbusdt', hype: 'hypeusdt' };
const COINBASE = { 'BTC-USD': 'btc', 'ETH-USD': 'eth', 'SOL-USD': 'sol', 'XRP-USD': 'xrp', 'DOGE-USD': 'doge', 'HYPE-USD': 'hype' };
const KRAKEN = { 'BTC/USD': 'btc', 'ETH/USD': 'eth', 'SOL/USD': 'sol', 'XRP/USD': 'xrp', 'DOGE/USD': 'doge' };

const PROXY = process.env.HTTPS_PROXY || process.env.https_proxy;
const agent = PROXY ? new HttpsProxyAgent(PROXY) : undefined;
// --dur 15m records the 15-minute markets instead (output data/live15m/); the default is 5m.
const DUR_TAG = (() => { const i = process.argv.indexOf('--dur'); return i > 0 ? process.argv[i + 1] : '5m'; })();
const DUR = { '5m': 300, '15m': 900 }[DUR_TAG];
if (!DUR) throw new Error(`unsupported --dur ${DUR_TAG}`);
const OUT = path.join(__dirname, 'data', DUR_TAG === '5m' ? 'live' : `live${DUR_TAG}`);
fs.mkdirSync(OUT, { recursive: true });

let sinkHour = null;
let sink = null;
function write(rec) {
  const now = Date.now();
  const hour = new Date(now).toISOString().slice(0, 13).replace(/[-T]/g, '');
  if (hour !== sinkHour) {
    if (sink) {
      const done = path.join(OUT, `${sinkHour}.jsonl`);
      sink.end(() => {
        fs.createReadStream(done).pipe(zlib.createGzip()).pipe(fs.createWriteStream(done + '.gz'))
          .on('finish', () => fs.unlinkSync(done));
      });
    }
    sink = fs.createWriteStream(path.join(OUT, `${hour}.jsonl`), { flags: 'a' });
    sinkHour = hour;
  }
  rec.t = now;
  sink.write(JSON.stringify(rec) + '\n');
}
const log = (...a) => console.log(new Date().toISOString(), ...a);

async function getJson(url) {
  const res = await fetch(url, { headers: { 'user-agent': 'Mozilla/5.0' } });
  if (!res.ok) throw new Error(`${res.status} ${url}`);
  return res.json();
}

function feed(name, url, { onOpen, onMessage, pingMs, pingPayload }) {
  let ws = null, timer = null, stopped = false, backoff = 1000;
  const connect = () => {
    if (stopped) return;
    ws = new WebSocket(url, agent ? { agent } : {});
    ws.on('open', () => {
      backoff = 1000;
      log(name, 'open');
      if (onOpen) onOpen(ws);
      if (pingMs) timer = setInterval(() => { if (ws.readyState === 1) ws.send(pingPayload || 'PING'); }, pingMs);
    });
    ws.on('message', (buf) => { try { onMessage(buf.toString(), ws); } catch (e) { log(name, 'handler error', e.message); } });
    ws.on('error', (e) => log(name, 'error', e.message));
    ws.on('close', (code) => {
      clearInterval(timer);
      if (stopped) return;
      log(name, 'closed', code, 'reconnect in', backoff);
      setTimeout(connect, backoff);
      backoff = Math.min(backoff * 2, 30000);
    });
  };
  connect();
  return { close: () => { stopped = true; clearInterval(timer); if (ws) ws.close(); } };
}

// Throttle per (kind, asset): keep the latest value, flush every 100 ms when it changed.
const pending = new Map();
const lastFlushed = new Map();
function stage(kind, as, rec) { pending.set(kind + ':' + as, Object.assign({ k: kind, as }, rec)); }
setInterval(() => {
  for (const [key, rec] of pending) {
    const sig = rec.b + '|' + rec.a;
    if (lastFlushed.get(key) !== sig) { write(rec); lastFlushed.set(key, sig); }
  }
  pending.clear();
}, 100);

// ---------- RTDS (Chainlink TWAP60 + spot for every asset) ----------
const symOf = (as) => `${as}/usd`;
for (const as of ASSETS) {
  // RTDS keeps one filter per topic per connection, so each asset gets its own socket.
  feed(`rtds-${as}`, 'wss://ws-live-data.polymarket.com/', {
    pingMs: 5000,
    onOpen: (ws) => ws.send(JSON.stringify({ action: 'subscribe', subscriptions: [
      { topic: 'crypto_prices_twap_sixty', type: 'update', filters: JSON.stringify({ symbol: symOf(as) }) },
      { topic: 'crypto_prices_chainlink', type: '*', filters: JSON.stringify({ symbol: symOf(as) }) },
    ] })),
    onMessage: (text) => {
      if (text === 'PONG' || !text.startsWith('{')) return;
      const m = JSON.parse(text);
      const p = m.payload || {};
      const kind = m.topic === 'crypto_prices_twap_sixty' ? 'twap' : (m.topic === 'crypto_prices_chainlink' || m.topic === 'crypto_prices') ? 'cl' : null;
      if (!kind) return;
      if (Array.isArray(p.data)) {
        for (const r of p.data) write({ k: kind + '_snap', as, ts: r.timestamp, v: r.full_accuracy_value || r.value });
        return;
      }
      if (p.timestamp === undefined || (p.symbol && p.symbol !== symOf(as))) return;
      write({ k: kind, as, ts: p.timestamp, v: p.full_accuracy_value || p.value });
    },
  });
}

// ---------- Exchanges ----------
const bnStreams = ASSETS.map((as) => `${BINANCE[as]}@bookTicker`).join('/');
const bnAsset = Object.fromEntries(ASSETS.map((as) => [BINANCE[as].toUpperCase(), as]));
feed('binance', `wss://data-stream.binance.vision/stream?streams=${bnStreams}`, {
  onMessage: (text) => {
    const d = JSON.parse(text).data;
    if (d && bnAsset[d.s]) stage('bn', bnAsset[d.s], { b: d.b, a: d.a });
  },
});
feed('coinbase', 'wss://ws-feed.exchange.coinbase.com', {
  onOpen: (ws) => ws.send(JSON.stringify({ type: 'subscribe', product_ids: Object.keys(COINBASE), channels: ['ticker'] })),
  onMessage: (text) => {
    const m = JSON.parse(text);
    if (m.type === 'ticker' && COINBASE[m.product_id]) stage('cb', COINBASE[m.product_id], { b: m.best_bid, a: m.best_ask, p: m.price, ts: Date.parse(m.time) });
  },
});
feed('kraken', 'wss://ws.kraken.com/v2', {
  pingMs: 20000,
  pingPayload: JSON.stringify({ method: 'ping' }),
  onOpen: (ws) => { for (const s of Object.keys(KRAKEN)) ws.send(JSON.stringify({ method: 'subscribe', params: { channel: 'ticker', symbol: [s] } })); },
  onMessage: (text) => {
    const m = JSON.parse(text);
    if (m.channel !== 'ticker' || !Array.isArray(m.data)) return;
    for (const d of m.data) if (KRAKEN[d.symbol]) stage('kr', KRAKEN[d.symbol], { b: d.bid, a: d.ask, p: d.last });
  },
});

// ---------- Polymarket windows (one CLOB connection per window start, all assets) ----------
const windows = new Map(); // start -> {tokens: Map(token -> [as, a]), books: Map(as:a -> book), conn}
const LEVELS = 5;
function bookFrom(levels) {
  const m = new Map();
  for (const l of levels || []) if (Number(l.size) > 0) m.set(l.price, Number(l.size));
  return m;
}
function top(book, side) {
  let best = null;
  for (const [p, s] of book[side]) {
    const v = Number(p);
    if (best === null || (side === 'bids' ? v > best[0] : v < best[0])) best = [v, s];
  }
  return best;
}
function levels(book, side) {
  return [...book[side].entries()].map(([p, s]) => [Number(p), s])
    .sort((x, y) => side === 'bids' ? y[0] - x[0] : x[0] - y[0]).slice(0, LEVELS);
}

async function openWindow(start) {
  if (windows.has(start)) return;
  const w = { start, tokens: new Map(), books: new Map(), lastTob: new Map(), conn: null };
  windows.set(start, w);
  try {
    const q = ASSETS.map((as) => `slug=${as}-updown-${DUR_TAG}-${start}`).join('&');
    const evs = await getJson(`https://gamma-api.polymarket.com/events?limit=50&${q}`);
    for (const ev of evs) {
      const as = ev.slug.split('-')[0];
      const m = ev.markets[0];
      const toks = JSON.parse(m.clobTokenIds);
      toks.forEach((tk, a) => { w.tokens.set(tk, [as, a]); w.books.set(`${as}:${a}`, { bids: new Map(), asks: new Map() }); });
      write({ k: 'window', as, w: start, tokens: toks, cond: m.conditionId, outcomes: m.outcomes, fee: m.feeSchedule, tick: m.orderPriceMinTickSize, min: m.orderMinSize, cfg: m.cryptoMarketConfig });
    }
  } catch (e) {
    log('window lookup failed', start, e.message);
    windows.delete(start);
    return;
  }
  const emitTob = (as, a) => {
    const book = w.books.get(`${as}:${a}`);
    const bb = top(book, 'bids'), ba = top(book, 'asks');
    const key = `${bb && bb[0]}|${ba && ba[0]}`;
    if (w.lastTob.get(`${as}:${a}`) === key) return;
    w.lastTob.set(`${as}:${a}`, key);
    write({ k: 'tob', as, w: start, a, bb: bb && bb[0], bbs: bb && bb[1], ba: ba && ba[0], bas: ba && ba[1] });
  };
  w.conn = feed(`clob-${start}`, 'wss://ws-subscriptions-clob.polymarket.com/ws/market', {
    pingMs: 10000,
    onOpen: (ws) => ws.send(JSON.stringify({ assets_ids: [...w.tokens.keys()], type: 'market' })),
    onMessage: (text) => {
      if (!text.startsWith('{') && !text.startsWith('[')) return;
      const parsed = JSON.parse(text);
      for (const m of Array.isArray(parsed) ? parsed : [parsed]) {
        const et = m.event_type || (m.bids ? 'book' : m.price_changes ? 'price_change' : null);
        if (et === 'book') {
          const id = w.tokens.get(m.asset_id); if (!id) continue;
          w.books.set(`${id[0]}:${id[1]}`, { bids: bookFrom(m.bids || m.buys), asks: bookFrom(m.asks || m.sells) });
          emitTob(id[0], id[1]);
        } else if (et === 'price_change') {
          const touched = new Map();
          for (const c of m.price_changes || []) {
            const id = w.tokens.get(c.asset_id); if (!id) continue;
            const book = w.books.get(`${id[0]}:${id[1]}`);
            const side = c.side === 'BUY' ? 'bids' : 'asks';
            if (Number(c.size) > 0) book[side].set(c.price, Number(c.size)); else book[side].delete(c.price);
            touched.set(`${id[0]}:${id[1]}`, id);
          }
          for (const id of touched.values()) emitTob(id[0], id[1]);
        } else if (et === 'last_trade_price') {
          const id = w.tokens.get(m.asset_id); if (!id) continue;
          write({ k: 'trade', as: id[0], w: start, a: id[1], p: Number(m.price), s: Number(m.size), sd: m.side, ts: Number(m.timestamp) });
        } else if (et === 'tick_size_change') {
          const id = w.tokens.get(m.asset_id); if (!id) continue;
          write({ k: 'tick', as: id[0], w: start, a: id[1], old: m.old_tick_size, new: m.new_tick_size });
        }
      }
    },
  });
}

setInterval(() => {
  for (const w of windows.values()) {
    for (const [key, book] of w.books) {
      const [as, a] = key.split(':');
      write({ k: 'depth', as, w: w.start, a: Number(a), bids: levels(book, 'bids'), asks: levels(book, 'asks') });
    }
  }
}, 1000);

// ---------- Official labels ----------
const labelled = new Set();
const VARIANT = { '5m': 'fiveminute', '15m': 'fifteen' }[DUR_TAG];
async function fetchLabel(as, start) {
  const key = `${as}:${start}`;
  const iso = (s) => new Date(s * 1000).toISOString().replace('.000', '');
  const q = `symbol=${as.toUpperCase()}&eventStartTime=${iso(start)}&variant=${VARIANT}&endDate=${iso(start + DUR)}&twapEnabled=true&twapLookbackSeconds=60`;
  try {
    const r = await getJson(`https://polymarket.com/api/crypto/crypto-price?${q}`);
    if (r.completed && r.closePrice != null) {
      if (!labelled.has(key)) write({ k: 'label', as, w: start, open: r.openPrice, close: r.closePrice });
      labelled.add(key);
    }
  } catch (e) { log('label error', key, e.message); }
}

function tick() {
  const now = Math.floor(Date.now() / 1000);
  const cur = now - (now % DUR);
  for (const s of [cur, cur + DUR]) openWindow(s);
  for (const [s, w] of windows) {
    if (now > s + DUR + 45) { if (w.conn) w.conn.close(); windows.delete(s); log('closed window', s); }
  }
  for (const s of [cur - DUR, cur - 2 * DUR]) for (const as of ASSETS) if (!labelled.has(`${as}:${s}`)) fetchLabel(as, s);
}
tick();
setInterval(tick, 10000);
process.on('SIGTERM', () => { if (sink) sink.end(); process.exit(0); });
log('recorder v2 started', DUR_TAG, ASSETS.join(','));
