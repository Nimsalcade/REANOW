'use strict';
const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const pct = (x, d = 0) => x == null ? '–' : (100 * x).toFixed(d) + '%';
const dur = m => { m = Math.round(m); const s = m < 0 ? '-' : ''; m = Math.abs(m); return m >= 60 ? `${s}${Math.floor(m / 60)}h${String(m % 60).padStart(2, '0')}` : `${s}${m}m`; };
const hm = iso => iso ? iso.slice(11, 16) : '–';
const dayShift = (a, b) => { const d = (Date.parse(b.slice(0, 10)) - Date.parse(a.slice(0, 10))) / 864e5; return d > 0 ? `<sup>+${d}</sup>` : ''; };
const api = async (path, params) => {
  const r = await fetch(path + (params ? '?' + new URLSearchParams(params) : ''));
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
};
const tip = $('#tip');
function showTip(e, html) { tip.innerHTML = html; tip.hidden = false; tip.style.left = Math.min(e.clientX + 14, innerWidth - 290) + 'px'; tip.style.top = (e.clientY + 14) + 'px'; }
function hideTip() { tip.hidden = true; }
document.addEventListener('mousemove', e => { if (!e.target.closest('[data-tip]')) hideTip(); });
document.addEventListener('mouseover', e => { const t = e.target.closest('[data-tip]'); if (t) showTip(e, t.dataset.tip); });

// status levels always paired with a word, never color alone
function level(kind, v) {
  const t = { ontime: [[.8, 'good', 'good'], [.65, 'warning', 'fair'], [.5, 'serious', 'poor'], [-1, 'critical', 'bad']],
              miss: [[.03, 'good', 'low'], [.08, 'warning', 'moderate'], [.15, 'serious', 'high'], [9, 'critical', 'very high']] }[kind];
  for (const [th, c, w] of t) if (kind === 'ontime' ? v >= th : v < th) return [c, w];
  return t[t.length - 1].slice(1);
}
const chip = (label, value, lv) => `<span class="chip">${lv ? `<span class="dot" style="background:var(--${lv[0]})"></span>` : ''}${label} <b>${value}</b>${lv ? ` <span class="muted">${lv[1]}</span>` : ''}</span>`;

// ---------------- tabs ----------------
document.querySelectorAll('.tab').forEach(b => b.onclick = () => {
  document.querySelectorAll('.tab').forEach(x => x.classList.toggle('active', x === b));
  document.querySelectorAll('.panel').forEach(p => p.hidden = p.id !== 'tab-' + b.dataset.tab);
});

// ---------------- boot ----------------
let STATUS = null, SERVED = {}, LAST = null, SEL = 0;
(async function boot() {
  try {
    STATUS = await api('/api/status');
    const f = STATUS.flights;
    $('#databadge').textContent = f.rows ? `${f.rows.toLocaleString()} flights · ${f.first_date} → ${f.last_date}${STATUS.model ? '' : ' · model not built'}` : 'no flight data imported';
    $('#synthetic').hidden = !STATUS.synthetic;
    const d = new Date(); d.setDate(d.getDate() + 1);
    const iso = d.toISOString().slice(0, 10);
    document.querySelectorAll('input[type=date]').forEach(i => i.value = iso);
    if (STATUS.synthetic) { $('#search [name=origin]').value = 'BOS'; $('#search [name=destination]').value = 'LAX'; }
  } catch (e) { $('#databadge').textContent = 'error: ' + e.message; }
})();

let acTimer;
document.addEventListener('input', e => {
  if (!e.target.matches('input[list=ap-list]')) return;
  clearTimeout(acTimer);
  acTimer = setTimeout(async () => {
    const opts = await api('/api/airports', { q: e.target.value, limit: 15 }).catch(() => []);
    $('#ap-list').innerHTML = opts.map(o => `<option value="${esc(o.code)}">${esc(o.label)}</option>`).join('');
  }, 120);
});

async function served(date) {
  if (!SERVED[date]) SERVED[date] = Object.fromEntries((await api('/api/served', { date })).map(r => [r[0], r]));
  return SERVED[date];
}

// ---------------- plan ----------------
$('#search').onsubmit = async e => {
  e.preventDefault();
  const fd = new FormData(e.target), p = {};
  for (const [k, v] of fd) if (v !== '') p[k] = v;
  p.origin = p.origin.toUpperCase(); p.destination = p.destination.toUpperCase();
  const out = $('#plan-out'), btn = $('#search .go');
  btn.disabled = true; out.innerHTML = '<p class="muted">Searching and simulating disruptions…</p>';
  try {
    const [res, geo] = await Promise.all([api('/api/search', p), served(p.date)]);
    LAST = { res, geo }; SEL = 0;
    renderPlan();
  } catch (err) { out.innerHTML = `<p class="err">${esc(err.message)}</p>`; }
  btn.disabled = false;
};

function renderPlan() {
  const { res, geo } = LAST, out = $('#plan-out');
  const basis = res.timetable_basis || {};
  const basisTxt = basis.kind === 'verified' ? `Verified schedule (${esc((basis.providers || []).join(', '))})`
    : basis.kind === 'pattern' ? `Recurring pattern from ${esc((basis.reference_dates || []).join(', '))} — not a verified schedule` : '';
  if (!res.results.length) {
    out.innerHTML = `<div class="box"><p>${esc((res.insights || []).join(' '))}</p><p class="muted">${basisTxt}</p></div>`;
    return;
  }
  const maxDelay = Math.max(120, ...res.results.map(r => r.p90_delay_min + 20));
  const cards = res.results.map((r, i) => card(r, i, maxDelay)).join('');
  out.innerHTML = `<div class="layout"><div><p class="muted">${res.considered} itineraries simulated · ${basisTxt}${res.widened_to_two_stops ? ' · widened to 2 stops' : ''}</p>${cards}</div>
    <div class="side"><div class="box"><h3>Route map</h3>${mapSvg(res, geo)}</div>
    <div class="box"><h3>Insights</h3><ul class="insights">${res.insights.map(x => `<li>${esc(x)}</li>`).join('')}</ul></div>
    <div class="box"><h3>Model</h3><p class="limits">${res.model ? `Trained on ${res.model.training_rows?.toLocaleString()} flights, ${esc(res.model.first_day)} → ${esc(res.model.last_day)}. ${esc(res.model.seasonal_coverage)}.` : ''}</p>
    <h3>Limitations</h3><ul class="limits">${res.limitations.map(x => `<li>${esc(x)}</li>`).join('')}</ul></div></div></div>`;
  out.querySelectorAll('.card').forEach(c => c.onclick = () => { SEL = +c.dataset.i; renderPlan(); });
}

function card(r, i, maxDelay) {
  const path = [r.origin, ...r.connections.map(c => c.airport), r.destination].join(' → ');
  const ot = level('ontime', r.p_on_time), mc = level('miss', r.p_misconnect);
  const fare = r.fare ? `$${r.fare.p50}<small>typical, $${r.fare.p25}–${r.fare.p75}</small>` : '<small>fare n/a</small>';
  const legs = r.legs.map((l, k) => `<div class="leg"><span>${esc(l.flight)}</span><span>${esc(l.origin)} ${hm(l.depart_local)} → ${esc(l.destination)} ${hm(l.arrive_local)} · ${dur(l.duration_min)}
      <div class="meta">cancel ${pct(l.p_cancel, 1)} · arrives 15+ late ${pct(l.p_arrive_15plus_late)} · ${esc(l.basis)} · ${esc(l.evidence)}${l.operating_carrier !== l.carrier ? ' · operated by ' + esc(l.operating_carrier) : ''}</div></span><span>${l.distance_mi} mi</span></div>
      ${r.connections[k] ? `<div class="conn">↳ ${r.connections[k].minutes} min at ${esc(r.connections[k].airport)} (${esc(r.connections[k].type.replace('_', ' '))}) · ${pct(r.connections[k].p_miss, 1)} miss risk</div>` : ''}`).join('');
  return `<div class="card ${i === SEL ? 'sel' : ''}" data-i="${i}">
    <div class="row1"><span class="rank">#${r.rank}</span>
      <span class="times">${hm(r.depart_local)} → ${hm(r.arrive_local)}${dayShift(r.depart_local, r.arrive_local)} <small>${dur(r.scheduled_minutes)} · ${r.stops ? r.stops + ' stop' + (r.stops > 1 ? 's' : '') : 'nonstop'}</small></span>
      ${r.pareto_optimal ? '<span class="pill" data-tip="No other option is better on expected time, fare and P90 time at once.">Pareto</span>' : ''}
      <span class="fare">${fare}</span></div>
    <div class="path">${esc(path)} · ${esc(r.legs.map(l => l.flight).join(' / '))}</div>
    <div class="chips">${chip('on time', pct(r.p_on_time), ot)}${r.stops ? chip('misconnect', pct(r.p_misconnect, 1), mc) : ''}${chip('cancel', pct(r.p_cancel, 1))}${r.p_by_deadline != null ? chip('by deadline', pct(r.p_by_deadline)) : ''}${chip('door-to-door', dur(r.door_to_door_expected_min) + ' exp.')}</div>
    ${fan(r, maxDelay)}
    ${r.notes.length ? `<ul class="notes">${r.notes.map(n => `<li>${esc(n)}</li>`).join('')}</ul>` : ''}
    <div class="legs">${legs}</div></div>`;
}

// Arrival-risk range: one series on a scale shared by all cards so options compare honestly.
function fan(r, maxDelay) {
  const W = 600, H = 46, L = 8, R = 96, min = -30;  // right gutter holds the P90 label
  const x = v => L + (Math.max(min, Math.min(maxDelay, v)) - min) / (maxDelay - min) * (W - L - R);
  const y = 16, tipTxt = `Delay vs scheduled arrival — median ${dur(r.p50_delay_min)}, expected ${dur(r.expected_delay_min)}, 90th percentile ${dur(r.p90_delay_min)}. Includes misconnect rebooking and cancellations.`;
  const ticks = [0, 60, 120, 180, 240, 360].filter(t => t <= maxDelay);
  return `<svg class="fan" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" data-tip="${esc(tipTxt)}" role="img" aria-label="${esc(tipTxt)}">
    ${ticks.map(t => `<line x1="${x(t)}" x2="${x(t)}" y1="6" y2="${y + 10}" stroke="var(--grid)" stroke-width="1"/><text x="${x(t)}" y="${H - 4}" text-anchor="middle">${t ? '+' + dur(t) : 'sched'}</text>`).join('')}
    <rect x="${x(r.p50_delay_min)}" y="${y - 4}" width="${Math.max(4, x(r.p90_delay_min) - x(r.p50_delay_min))}" height="8" rx="4" fill="var(--series-1-soft)"/>
    <line x1="${x(0)}" x2="${x(0)}" y1="${y - 9}" y2="${y + 9}" stroke="var(--text-primary)" stroke-width="2"/>
    <circle cx="${x(r.expected_delay_min)}" cy="${y}" r="5" fill="var(--series-1)" stroke="var(--surface-1)" stroke-width="2"/>
    <text class="val" x="${x(r.p90_delay_min) + 8}" y="${y + 4}">P90 +${dur(r.p90_delay_min)}</text>
  </svg>`;
}

function project(points) {
  const lats = points.map(p => p[1]), lons = points.map(p => p[2]);
  const lat0 = (Math.min(...lats) + Math.max(...lats)) / 2, k = Math.cos(lat0 * Math.PI / 180);
  const xs = lons.map(l => l * k), minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...lats), maxY = Math.max(...lats);
  const W = 520, H = Math.max(200, Math.min(360, W * (maxY - minY + 2) / (maxX - minX + 2))), pad = 18;
  const s = Math.min((W - 2 * pad) / (maxX - minX || 1), (H - 2 * pad) / (maxY - minY || 1));
  return { W, H, f: (lat, lon) => [pad + (lon * k - minX) * s, H - pad - (lat - minY) * s] };
}

function mapSvg(res, geo) {
  const sel = res.results[SEL];
  const codes = new Set(res.results.flatMap(r => [r.origin, ...r.connections.map(c => c.airport), r.destination]));
  // frame: contiguous U.S. served airports plus anything on the results
  const pts = Object.values(geo).filter(a => (a[1] > 24 && a[1] < 50 && a[2] > -125 && a[2] < -66) || codes.has(a[0]));
  if (!pts.length) return '';
  const { W, H, f } = project(pts);
  const arc = (a, b) => { const [x1, y1] = f(a[1], a[2]), [x2, y2] = f(b[1], b[2]); const mx = (x1 + x2) / 2, my = (y1 + y2) / 2 - Math.hypot(x2 - x1, y2 - y1) * 0.18; return `M${x1},${y1} Q${mx},${my} ${x2},${y2}`; };
  const pathOf = r => [r.origin, ...r.connections.map(c => c.airport), r.destination];
  let arcs = '';
  res.results.forEach((r, i) => { if (i === SEL) return; const p = pathOf(r); for (let k = 0; k + 1 < p.length; k++) if (geo[p[k]] && geo[p[k + 1]]) arcs += `<path class="arc" d="${arc(geo[p[k]], geo[p[k + 1]])}"/>`; });
  const sp = pathOf(sel);
  let on = '';
  for (let k = 0; k + 1 < sp.length; k++) if (geo[sp[k]] && geo[sp[k + 1]]) on += `<path class="arc on" d="${arc(geo[sp[k]], geo[sp[k + 1]])}"/>`;
  const dots = pts.map(a => { const [x, y] = f(a[1], a[2]); const isOn = sp.includes(a[0]); return `<circle class="ap ${isOn ? 'on' : ''}" cx="${x}" cy="${y}" r="${isOn ? 5 : 2.2}" data-tip="${esc(a[0] + ' · ' + a[3])}"/>`; }).join('');
  const labels = sp.filter(c => geo[c]).map(c => { const [x, y] = f(geo[c][1], geo[c][2]); return `<text x="${x + 7}" y="${y - 6}">${esc(c)}</text>`; }).join('');
  return `<svg class="map" viewBox="0 0 ${W} ${H}" role="img" aria-label="Map of the selected itinerary ${esc(sp.join(' to '))}">${arcs}${on}${dots}${labels}</svg>
    <p class="limits">Selected: ${esc(sp.join(' → '))}. Gray arcs: other listed options. Projection is schematic.</p>`;
}

// ---------------- route report ----------------
$('#routeform').onsubmit = async e => {
  e.preventDefault();
  const p = Object.fromEntries([...new FormData(e.target)].filter(([, v]) => v !== ''));
  p.origin = p.origin.toUpperCase(); p.destination = p.destination.toUpperCase();
  const out = $('#route-out');
  out.innerHTML = '<p class="muted">Loading…</p>';
  try {
    const r = await api('/api/route', p);
    const carriers = r.carriers.length ? `<table><tr><th>Carrier</th><th>Operated by</th><th>Flights/day</th><th>Cancel</th><th>Arrive 15+ late</th><th>Mean arr. delay</th><th>Sched. minutes</th><th>Observations</th></tr>
      ${r.carriers.map(c => `<tr><td>${esc(c.mkt)}</td><td>${esc(c.op)}</td><td>${c.flights_per_day}</td><td>${pct(c.cxl, 1)}</td><td>${pct(c.late15)}</td><td>${c.mean_delay ?? '–'}</td><td>${c.elapsed ?? '–'}</td><td>${c.n.toLocaleString()} (${esc(c.first)} → ${esc(c.last)})</td></tr>`).join('')}</table>` : '<p class="muted">No observed nonstop flights on this route.</p>';
    const fares = r.fares.length ? `<table><tr><th>Period</th><th>Carrier</th><th>Routing</th><th>Passengers (sample)</th><th>P25</th><th>Median</th><th>P75</th></tr>${r.fares.map(f => `<tr><td>${esc(f.period)}</td><td>${esc(f.carrier === '*' ? 'all' : f.carrier)}</td><td class="txt">${f.nonstop === 1 ? 'nonstop' : f.nonstop === 0 ? 'connecting' : 'unknown'}</td><td>${f.passengers}</td><td>$${f.p25}</td><td>$${f.p50}</td><td>$${f.p75}</td></tr>`).join('')}</table>` : '<p class="muted">No fare records.</p>';
    const t100 = r.t100.length ? `<table><tr><th>Month</th><th>Carrier</th><th>Departures</th><th>Seats</th><th>Passengers</th><th>Load factor</th></tr>${r.t100.map(t => `<tr><td>${t.year}-${String(t.month).padStart(2, '0')}</td><td>${esc(t.carrier)}</td><td>${t.departures}</td><td>${t.seats?.toLocaleString()}</td><td>${t.passengers?.toLocaleString()}</td><td>${pct(t.load_factor)}</td></tr>`).join('')}</table>` : '<p class="muted">No T-100 rows.</p>';
    const tt = r.timetable.length ? `<table><tr><th>Flight</th><th>Departs</th><th>Arrives</th><th>Minutes</th><th>Basis</th></tr>${r.timetable.map(t => `<tr><td>${esc(t.flight)}</td><td>${hm(t.depart)}</td><td>${hm(t.arrive)}</td><td>${t.minutes}</td><td class="txt">${t.verified ? 'verified' : 'pattern ' + pct(t.frequency)}</td></tr>`).join('')}</table>` : '<p class="muted">No timetable entries for that date.</p>';
    out.innerHTML = `<div class="box"><h3>${esc(r.origin)} → ${esc(r.destination)} · ${r.distance_mi} mi · ${r.history_days} days of history</h3>${carriers}</div>
      <div class="two" style="margin-top:14px"><div class="box"><h3>Arrive 15+ min late, by scheduled departure hour</h3>${hourBars(r.by_departure_hour)}</div>
      <div class="box"><h3>Timetable ${esc(p.date || '')}</h3>${tt}</div></div>
      <div class="two" style="margin-top:14px"><div class="box"><h3>Historical fares (one-way market)</h3>${fares}</div><div class="box"><h3>T-100 traffic</h3>${t100}</div></div>
      <p class="limits">${esc(r.note)}</p>`;
  } catch (err) { out.innerHTML = `<p class="err">${esc(err.message)}</p>`; }
};

function hourBars(rows) {
  rows = rows.filter(r => r.late15 != null);
  if (!rows.length) return '<p class="muted">No delay observations.</p>';
  const W = 520, H = 180, L = 36, B = 24, top = 10, max = Math.max(0.3, ...rows.map(r => r.late15));
  const bw = (W - L - 8) / 24, y = v => H - B - v / max * (H - B - top);
  const grid = [0, max / 2, max].map(v => `<line x1="${L}" x2="${W - 4}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${pct(v)}</text>`).join('');
  const bars = rows.map(r => { const x = L + r.hour * bw + 1; const t = `${String(r.hour).padStart(2, '0')}:00–${String(r.hour).padStart(2, '0')}:59 · ${pct(r.late15)} arrive 15+ late · cancel ${pct(r.cxl, 1)} · ${r.n} flights`;
    return `<g data-tip="${esc(t)}"><rect class="hit" x="${L + r.hour * bw}" y="${top}" width="${bw}" height="${H - B - top}"/><rect class="bar" x="${x}" y="${y(r.late15)}" width="${Math.max(2, bw - 2)}" height="${H - B - y(r.late15)}" rx="2"/></g>`; }).join('');
  const xt = [0, 6, 12, 18, 23].map(h => `<text x="${L + h * bw + bw / 2}" y="${H - 6}" text-anchor="middle">${String(h).padStart(2, '0')}h</text>`).join('');
  return `<svg class="bars" viewBox="0 0 ${W} ${H}" role="img" aria-label="Share of flights arriving 15+ minutes late by departure hour">${grid}${bars}${xt}</svg>`;
}

// ---------------- network ----------------
$('#netform').onsubmit = async e => {
  e.preventDefault();
  const date = new FormData(e.target).get('date'), out = $('#net-out');
  out.innerHTML = '<p class="muted">Computing connectivity and opportunities…</p>';
  try {
    const [h, o] = await Promise.all([api('/api/hubs', { date }), api('/api/opportunities', { date })]);
    out.innerHTML = `<div class="two"><div class="box"><h3>Connectivity ranking</h3><table><tr><th>Airport</th><th>Departures</th><th>Nonstop dest.</th><th data-tip="${esc(h.method)}">Connecting markets ⓘ</th><th>Carriers</th></tr>
      ${h.hubs.map(x => `<tr><td>${esc(x.airport)}</td><td>${x.departures}</td><td>${x.nonstop_destinations}</td><td>${x.connecting_markets.toLocaleString()}</td><td class="txt">${esc(x.carriers.join(' '))}</td></tr>`).join('')}</table></div>
      <div class="box"><h3>Nonstop opportunities</h3>${o.opportunities.length ? `<table><tr><th>Market</th><th>Miles</th><th>Pax/day (est.)</th><th>Best now</th><th>Nonstop est.</th><th data-tip="Estimated daily passengers × minutes saved by a nonstop">Pax-hours saved/day ⓘ</th></tr>
      ${o.opportunities.map(x => `<tr><td>${esc(x.origin)}→${esc(x.destination)}</td><td>${x.distance_mi}</td><td>${x.est_daily_passengers}</td><td>${dur(x.best_connection_min)} via ${esc(x.via)}</td><td>${dur(x.est_nonstop_min)}</td><td>${x.passenger_hours_saved_per_day}</td></tr>`).join('')}</table>` : `<p class="muted">${esc(o.note || 'None found')}</p>`}
      <p class="limits">${esc(o.method || '')}</p></div></div>`;
  } catch (err) { out.innerHTML = `<p class="err">${esc(err.message)}</p>`; }
};
