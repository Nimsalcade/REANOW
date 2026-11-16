// Records Polymarket RTDS BTC/USD Chainlink spot + 60s TWAP topics to JSONL.
// Usage: npm install ws@8 https-proxy-agent@7 && node rtds_record.cjs <out.jsonl> <stopAtUnixSeconds>
// Sends PING every 5 s; honors HTTPS_PROXY when set.

const WebSocket = require('ws');
const { HttpsProxyAgent } = require('https-proxy-agent');
const fs = require('fs');
const [out, stopAt] = [process.argv[2], Number(process.argv[3])];
const sink = fs.createWriteStream(out, { flags: 'a' });
const proxy = process.env.HTTPS_PROXY || process.env.https_proxy;
const ws = new WebSocket('wss://ws-live-data.polymarket.com/', proxy ? { agent: new HttpsProxyAgent(proxy) } : {});
const sym = JSON.stringify({ symbol: 'btc/usd' });
ws.on('open', () => {
  ws.send(JSON.stringify({ action: 'subscribe', subscriptions: [
    { topic: 'crypto_prices_twap_sixty', type: 'update', filters: sym },
    { topic: 'crypto_prices_chainlink', type: '*', filters: sym },
  ]}));
  sink.write(JSON.stringify({ recv_ms: Date.now(), event: 'open' }) + '\n');
});
const ping = setInterval(() => { if (ws.readyState === 1) ws.send('PING'); }, 5000);
ws.on('message', (buf) => {
  const text = buf.toString();
  if (text === 'PONG' || text === '') return;
  sink.write(JSON.stringify({ recv_ms: Date.now(), raw: text }) + '\n');
});
ws.on('error', (e) => sink.write(JSON.stringify({ recv_ms: Date.now(), event: 'error', message: String(e && e.message) }) + '\n'));
ws.on('close', (c) => { sink.write(JSON.stringify({ recv_ms: Date.now(), event: 'close', code: c }) + '\n'); clearInterval(ping); sink.end(() => process.exit(0)); });
const t = setInterval(() => { if (Date.now() / 1000 >= stopAt) { clearInterval(t); ws.close(); } }, 500);
