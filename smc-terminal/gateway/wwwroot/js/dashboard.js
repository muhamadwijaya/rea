// Dashboard: KPIs, search, market tiles, SMC engine radar + verdict stack, and tabbed direction / signals / alerts / recent analyses.
import { $, esc, on, badge, dirClass, dirHex, fmtPct, fmtPrice, fmtScore, fmtTime, sparkline, timeAgo, arrow } from './util.js';
import { MARKETS, MARKET_KEYS, rowsOf } from './markets.js';

const GROUP_NAME = Object.fromEntries(MARKET_KEYS.map((k) => [k, MARKETS[k].nav]));

let store;
let radar = { rows: [], tips: [], rot: 0, running: false };

// ------------------------------------------------------------------ engine checks
const ICONS = {
  struct: '<svg width="26" height="26" viewBox="0 0 26 26"><path d="M2 20 L8 12 L12 16 L18 6 L24 10" fill="none" stroke="#6b9bff" stroke-width="1.6"/><path d="M14 6 H22" stroke="#2fd28c" stroke-dasharray="2 2"/></svg>',
  choch: '<svg width="26" height="26" viewBox="0 0 26 26"><path d="M2 6 L8 14 L12 10 L16 18 L24 8" fill="none" stroke="#f2c94c" stroke-width="1.6"/><path d="M10 10 H24" stroke="#ff4d5e" stroke-dasharray="2 2"/></svg>',
  ob: '<svg width="26" height="26" viewBox="0 0 26 26"><rect x="3" y="12" width="20" height="7" fill="rgba(167,139,250,.25)" stroke="#a78bfa"/><line x1="8" y1="4" x2="8" y2="22" stroke="#a78bfa"/><rect x="6" y="9" width="4" height="9" fill="#a78bfa"/></svg>',
  fvg: '<svg width="26" height="26" viewBox="0 0 26 26"><rect x="4" y="14" width="4" height="8" fill="#ff4d5e"/><rect x="11" y="5" width="4" height="15" fill="#2fd28c"/><rect x="18" y="3" width="4" height="7" fill="#2fd28c"/><rect x="2" y="10" width="22" height="4" fill="rgba(56,211,245,.25)"/></svg>',
  zone: '<svg width="26" height="26" viewBox="0 0 26 26"><rect x="3" y="3" width="20" height="9" fill="rgba(255,77,94,.2)"/><rect x="3" y="14" width="20" height="9" fill="rgba(47,210,140,.2)"/><line x1="3" y1="13" x2="23" y2="13" stroke="#8592a8" stroke-dasharray="2 2"/></svg>',
};

function renderChecks(stats) {
  const m = stats?.metrics || [];
  const series = (k) => m.map((p) => p[k]);
  const last = (k) => (m.length ? m[m.length - 1][k] : null);
  const card = (title, sub, value, color, data) => `
    <div class="check-card" style="border-color:${color}55">
      <div class="t" style="color:${color}">${title}</div><div class="s">${sub}</div>
      <div class="v" style="color:${color}">${value ?? '–'}</div>${sparkline(data, color, 140, 34)}
    </div>`;
  $('#engine-checks').innerHTML = `
    <div class="cap">LIMA CEK<span>DIUKUR TIAP REFRESH</span></div>
    ${card('BULLISH', '% WATCHLIST', last('bullishPct') != null ? `${last('bullishPct')}%` : null, '#2fd28c', series('bullishPct'))}
    ${card('BEARISH', '% WATCHLIST', last('bearishPct') != null ? `${last('bearishPct')}%` : null, '#ff4d5e', series('bearishPct'))}
    ${card('CHoCH BARU', '≤ 6 BAR TERAKHIR', last('chochRecent'), '#f2c94c', series('chochRecent'))}
    ${card('OB DEKAT', '≤ 3 ATR · AKTIF', last('nearOb'), '#a78bfa', series('nearOb'))}
    ${card('DISCOUNT', '% WATCHLIST', last('discountPct') != null ? `${last('discountPct')}%` : null, '#38d3f5', series('discountPct'))}`;

  const s = stats || {};
  const node = (icon, k, val, color) => `
    <div class="check-node" data-go="metode" title="Klik untuk penjelasan"><div class="ic" style="border-color:${color}66">${ICONS[icon]}</div>
      <div><div class="k" style="color:${color}">${k}</div><div class="val" style="color:${color}">${val}</div></div></div>`;
  $('#checks-row').innerHTML = [
    node('struct', 'STRUKTUR ▲/▼', `▲${s.bullish ?? '–'} ▼${s.bearish ?? '–'}`, '#6b9bff'),
    node('choch', 'CHoCH ≤ 6 BAR', s.chochRecent ?? '–', '#f2c94c'),
    node('ob', 'OB DEKAT HARGA', s.nearOb ?? '–', '#a78bfa'),
    node('fvg', 'FVG TERBUKA', s.nearFvg ?? '–', '#38d3f5'),
    node('zone', 'DISCOUNT / PREMIUM', `${s.inDiscount ?? '–'} / ${s.inPremium ?? '–'}`, '#2fd28c'),
  ].join('');
  $('#engine-updated').textContent = s.screenerAt ? `update ${fmtTime(s.screenerAt)} · ${String(s.screenerTf || '').toUpperCase()}` : '';
}

// ------------------------------------------------------------------ radar
function radarData(snapshot) {
  const rows = (snapshot?.rows || []).filter((r) => !r.error);
  const ordered = MARKET_KEYS.flatMap((m) => rows.filter((r) => r.instrument.market === m));
  radar.rows = ordered;
  if (!radar.running) startRadar();
}

function startRadar() {
  radar.running = true;
  const canvas = $('#radar');
  const ctx = canvas.getContext('2d');
  const frame = () => {
    if (!$('#tab-dashboard').classList.contains('active')) { radar.running = false; return; }
    drawRadar(canvas, ctx);
    requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
}

function drawRadar(canvas, ctx) {
  const w = canvas.clientWidth, h = canvas.clientHeight, dpr = window.devicePixelRatio || 1;
  if (canvas.width !== w * dpr || canvas.height !== h * dpr) { canvas.width = w * dpr; canvas.height = h * dpr; }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const rows = radar.rows;
  const cx = w / 2, cy = h / 2 + 6, R = Math.min(w, h) / 2 - 26;
  const t = performance.now() / 1000;
  radar.rot += 0.0008;

  for (let k = 1; k <= 4; k++) {
    ctx.beginPath(); ctx.arc(cx, cy, (R * k) / 4, 0, Math.PI * 2);
    ctx.strokeStyle = k === 4 ? 'rgba(38,50,74,.8)' : 'rgba(38,50,74,.45)'; ctx.setLineDash(k === 4 ? [] : [2, 4]); ctx.stroke();
  }
  ctx.setLineDash([]);
  if (!rows.length) {
    ctx.fillStyle = '#56627a'; ctx.font = '11px JetBrains Mono'; ctx.textAlign = 'center';
    ctx.fillText('menunggu data screener…', cx, cy);
    return;
  }
  const n = rows.length;
  radar.tips = [];
  let lastClass = null;
  rows.forEach((r, i) => {
    const a = radar.rot + (i / n) * Math.PI * 2 - Math.PI / 2;
    const strength = Math.min(1, Math.abs(r.score) / 100);
    const L = R * (0.3 + 0.7 * strength);
    const color = dirHex(r.direction);
    const cos = Math.cos(a), sin = Math.sin(a);
    if (r.instrument.market !== lastClass) {
      lastClass = r.instrument.market;
      ctx.strokeStyle = 'rgba(107,155,255,.35)'; ctx.beginPath();
      ctx.moveTo(cx + cos * R * 0.18, cy + sin * R * 0.18); ctx.lineTo(cx + cos * (R + 6), cy + sin * (R + 6)); ctx.stroke();
      ctx.save(); ctx.translate(cx + cos * (R + 14), cy + sin * (R + 14)); ctx.rotate(a + Math.PI / 2 + (sin > 0 ? Math.PI : 0));
      ctx.fillStyle = '#6b9bff'; ctx.font = '9px JetBrains Mono'; ctx.textAlign = 'left'; ctx.fillText(GROUP_NAME[lastClass] || '', sin > 0 ? -60 : 2, 0); ctx.restore();
    }
    ctx.strokeStyle = `${color}40`; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(cx + cos * R * 0.14, cy + sin * R * 0.14); ctx.lineTo(cx + cos * L, cy + sin * L); ctx.stroke();
    const spark = r.spark || [];
    if (spark.length > 2) {
      const min = Math.min(...spark), max = Math.max(...spark), span = max - min || 1;
      const m = 22;
      for (let k = 0; k < m; k++) {
        const v = spark[Math.floor((k / m) * spark.length)];
        const rr = R * 0.16 + ((L - R * 0.16) * (k + 1)) / m;
        const off = ((v - min) / span - 0.5) * 10;
        const px = cx + cos * rr - sin * off, py = cy + sin * rr + cos * off;
        ctx.fillStyle = `${color}${k > m - 5 ? 'cc' : '66'}`;
        ctx.beginPath(); ctx.arc(px, py, 1.1 + (k / m) * 0.9, 0, Math.PI * 2); ctx.fill();
      }
    }
    const tx = cx + cos * L, ty = cy + sin * L;
    const fresh = r.lastEvent && r.lastEvent.barsAgo <= 3;
    if (fresh) {
      const pr = 4 + ((Math.sin(t * 3 + i) + 1) / 2) * 6;
      ctx.strokeStyle = `${color}88`; ctx.beginPath(); ctx.arc(tx, ty, pr, 0, Math.PI * 2); ctx.stroke();
    }
    ctx.fillStyle = color; ctx.beginPath(); ctx.arc(tx, ty, 2.5 + strength * 2.5, 0, Math.PI * 2); ctx.fill();
    if (strength > 0.45) {
      ctx.fillStyle = '#c3cbd8'; ctx.font = '9px JetBrains Mono'; ctx.textAlign = cos >= 0 ? 'left' : 'right';
      ctx.fillText(r.instrument.symbol, tx + (cos >= 0 ? 7 : -7), ty + 3);
    }
    radar.tips.push({ x: tx, y: ty, r });
  });
  ctx.fillStyle = '#0f1622'; ctx.strokeStyle = '#6b9bff'; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.arc(cx, cy, R * 0.12, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
  ctx.fillStyle = '#e6ebf3'; ctx.font = 'bold 15px JetBrains Mono'; ctx.textAlign = 'center'; ctx.fillText(String(n), cx, cy + 2);
  ctx.fillStyle = '#56627a'; ctx.font = '8px JetBrains Mono'; ctx.fillText('INSTRUMEN', cx, cy + 13);
}

function radarHover(e) {
  const canvas = $('#radar');
  const rect = canvas.getBoundingClientRect();
  const x = e.clientX - rect.left, y = e.clientY - rect.top;
  let best = null, bestD = 14;
  for (const tip of radar.tips) {
    const d = Math.hypot(tip.x - x, tip.y - y);
    if (d < bestD) { best = tip; bestD = d; }
  }
  const el = $('#radar-tip');
  canvas.style.cursor = best ? 'pointer' : 'default';
  radar.hover = best;
  if (!best) { el.style.display = 'none'; return; }
  const r = best.r;
  el.style.display = 'block';
  el.style.left = `${Math.min(x + 12, rect.width - 200)}px`;
  el.style.top = `${y + 12}px`;
  el.innerHTML = `<b>${esc(r.instrument.symbol)}</b> ${badge(r.direction, r.label)} <span class="num">${fmtScore(r.score)}</span><br>
    <span class="muted">${fmtPrice(r.price, r.instrument.decimals)} · ${fmtPct(r.changePct)} · ${r.lastEvent ? `${esc(r.lastEvent.kind)} ${esc(r.lastEvent.direction)} ${r.lastEvent.barsAgo} bar` : 'tanpa event'}</span>`;
}

// ------------------------------------------------------------------ verdicts & lists
function renderVerdicts() {
  const recent = store.analyses || [];
  let items = recent.map((a) => ({ symbol: a.symbol, tf: a.tf, score: a.score, label: a.label, direction: a.direction, note: a.summary || '' }));
  if (!items.length && store.screener) {
    items = [...store.screener.rows].filter((r) => !r.error).sort((a, b) => Math.abs(b.score) - Math.abs(a.score)).slice(0, 10)
      .map((r) => ({ symbol: r.instrument.symbol, tf: r.tf, score: r.score, label: r.label, direction: r.direction,
        note: `swing ${r.swingTrend} · ${r.lastEvent ? `${r.lastEvent.kind} ${r.lastEvent.barsAgo} bar lalu` : 'tanpa event'}` }));
  }
  $('#verdict-stack').innerHTML = items.length ? items.slice(0, 10).map((v) => {
    const c = dirClass(v.direction);
    return `<div class="verdict ${c}" data-sym="${esc(v.symbol)}" data-tf="${esc(v.tf)}">
      <div class="row1"><span class="tag ${c}">${esc(v.label)}</span><b>${esc(v.symbol)}</b><span class="dim">${esc(String(v.tf).toUpperCase())}</span><span class="sc" style="color:${dirHex(v.direction)}">${fmtScore(v.score)}</span></div>
      <div class="row2">${esc(v.note)}</div>
      <div class="bar"><i style="width:${Math.min(100, Math.abs(v.score))}%;background:${dirHex(v.direction)}"></i></div></div>`;
  }).join('') : '<div class="empty">belum ada analisa</div>';
}

function renderRecent() {
  const list = store.analyses || [];
  $('#recent-list').innerHTML = list.length ? list.slice(0, 12).map((a) => `
    <div class="list-row cols-4" data-sym="${esc(a.symbol)}" data-tf="${esc(a.tf)}">
      <span><b>${esc(a.symbol)}</b> <span class="dim">${esc(a.tf.toUpperCase())}</span></span>
      <span>${badge(a.direction, a.label)}</span>
      <span class="num" style="color:${dirHex(a.direction)}">${fmtScore(a.score)}</span>
      <span class="time">${timeAgo(a.time)}</span>
    </div>`).join('') : '<div class="empty">Belum ada analisa. Ketik simbol di atas atau klik instrumen di radar.</div>';
}

function renderSignals() {
  const rows = (store.screener?.rows || []).filter((r) => r.lastEvent && r.lastEvent.barsAgo <= 6)
    .sort((a, b) => b.lastEvent.t - a.lastEvent.t).slice(0, 18);
  $('#signal-list').innerHTML = rows.length ? rows.map((r) => {
    const e = r.lastEvent;
    const c = dirClass(e.direction);
    return `<div class="list-row" style="grid-template-columns: 52px 74px 1fr" data-sym="${esc(r.instrument.symbol)}" data-tf="${esc(r.tf)}">
      <span class="time">${fmtTime(e.t)}</span>
      <span class="tag ${c}" style="text-align:center">${e.scope === 'internal' ? 'i' : ''}${esc(e.kind)} ${arrow(e.direction)}</span>
      <span><b>${esc(r.instrument.symbol)}</b> <span class="muted">· ${esc(r.tf.toUpperCase())} · level ${fmtPrice(e.level, r.instrument.decimals)} · ${e.barsAgo} bar lalu</span></span>
    </div>`;
  }).join('') : '<div class="empty">Belum ada break struktur baru di watchlist.</div>';
}

function renderAlerts() {
  const list = store.alerts || [];
  $('#alert-count').textContent = list.length ? `(${list.length})` : '';
  $('#alert-list').innerHTML = list.length ? list.slice(0, 30).map((a) => `
    <div class="alert-row sev-${esc(a.severity)}" ${a.symbol && a.kind !== 'calendar' && a.kind !== 'news' ? `data-sym="${esc(a.symbol)}"` : ''}>
      <div class="h"><span class="tag ${a.kind === 'calendar' ? 'neu' : dirClass(a.direction)}">${esc(a.kind.toUpperCase())}</span><b>${esc(a.title)}</b><span class="time">${timeAgo(a.time)}</span></div>
      <div class="d">${esc(a.detail)}</div>
    </div>`).join('') : '<div class="empty">Belum ada alert. Alert muncul saat CHoCH/BOS baru, bias berubah, berita besar, atau event high-impact mendekat.</div>';
}

function renderDirection(d) {
  if (!d) return;
  const reg = d.regime;
  const rc = reg.regime === 'RISK-ON' ? 'up' : reg.regime === 'RISK-OFF' ? 'down' : 'flat';
  $('#dir-strip').innerHTML = `<div class="dir-list">
    <div class="regime">
      <span class="eyebrow">REGIME PASAR HARI INI</span>
      <span class="r ${rc}">${esc(reg.regime)}</span>
      <span class="h">${esc(reg.text)}</span>
    </div>
    ${d.outlooks.map((o) => `
      <div class="mini-dir" data-sym="${esc(o.symbols[0])}" data-tf="1d">
        <span class="t">${esc(o.title.toUpperCase())}</span>
        <span class="v" style="color:${dirHex(o.direction)}">${arrow(o.direction)} ${esc(o.label)}</span>
        <span class="c">skor ${fmtScore(o.score)} · keyakinan ${esc(o.confidence)} · ${esc(o.drivers[0] || '')}</span>
      </div>`).join('')}
  </div>`;
}

function renderKpis(s) {
  if (!s) return;
  const next = s.nextHighImpact;
  const kpi = (t, v, sub, color = 'var(--text)') => `<div class="kpi"><div class="t">${t}</div><div class="v num" style="color:${color}">${v}</div><div class="s">${sub}</div></div>`;
  const pct = (n) => (s.instruments ? Math.round((n / s.instruments) * 100) : 0);
  $('#kpis').innerHTML = [
    kpi(`INSTRUMEN · ${String(s.screenerTf || '').toUpperCase()}`, s.instruments ?? '–', s.engineOnline ? '4 pasar · refresh otomatis' : `<span class="down">${esc(s.engineError || 'engine offline')}</span>`),
    kpi('BIAS BULLISH', s.bullish ?? '–', `${pct(s.bullish)}% watchlist`, 'var(--bull)'),
    kpi('BIAS BEARISH', s.bearish ?? '–', `${pct(s.bearish)}% watchlist`, 'var(--bear)'),
    kpi('CHoCH BARU', s.chochRecent ?? '–', '≤ 6 bar terakhir', 'var(--neu)'),
    kpi('BERITA · 24H', s.news24h ?? '–', `${s.feedsOk}/${s.feedsTotal} sumber aktif`, 'var(--accent)'),
    kpi('HIGH IMPACT · MINGGU', s.highImpactWeek ?? '–', next ? `${esc(next.currency)} ${esc(next.title)} · ${fmtTime(next.date, true)}` : '<a data-go="kalender">kalender</a>', 'var(--orange)'),
  ].join('');
}

function renderTiles() {
  const el = $('#market-tiles');
  el.innerHTML = MARKET_KEYS.map((k) => {
    const cfg = MARKETS[k];
    const rows = rowsOf(store.screener, k).filter((r) => !r.error);
    const bull = rows.filter((r) => r.direction?.includes('bullish')).length;
    const bear = rows.filter((r) => r.direction?.includes('bearish')).length;
    const neu = rows.length - bull - bear;
    const avg = rows.length ? rows.reduce((sum, r) => sum + r.score, 0) / rows.length : 0;
    const top = [...rows].filter((r) => r.changePct != null).sort((a, b) => Math.abs(b.changePct) - Math.abs(a.changePct))[0];
    const news = store.stats?.newsByMarket?.[k];
    const pct = (n) => (rows.length ? (n / rows.length) * 100 : 0);
    const dir = avg > 10 ? 'bull' : avg < -10 ? 'bear' : '';
    return `<div class="panel market-tile" data-go="${k}">
      <div class="mt-head"><b>${esc(cfg.nav)}</b><span class="muted">${rows.length} instrumen</span><span class="mt-go">BUKA →</span></div>
      <div class="mt-dir" style="color:${dirHex(dir)}">${arrow(dir)} ${avg > 10 ? 'CONDONG NAIK' : avg < -10 ? 'CONDONG TURUN' : 'CAMPURAN'} <span class="num">${fmtScore(avg)}</span></div>
      <div class="stack" style="height:6px"><i style="width:${pct(bull)}%;background:var(--bull)"></i><i style="width:${pct(neu)}%;background:var(--line-2)"></i><i style="width:${pct(bear)}%;background:var(--bear)"></i></div>
      <div class="mt-foot">
        <span><span>breadth SMC</span><span><span class="up">▲${bull}</span> <span class="down">▼${bear}</span> <span class="dim">◆${neu}</span></span></span>
        <span><span>penggerak</span><span>${top ? `${esc(top.instrument.symbol)} <span class="${top.changePct > 0 ? 'up' : 'down'}">${fmtPct(top.changePct)}</span>` : '–'}</span></span>
        <span><span>berita 24 jam</span><span class="accent">${news ?? '–'}</span></span>
      </div>
    </div>`;
  }).join('');
}

function quickChips() {
  const picks = ['EURUSD', 'XAUUSD', 'GBPJPY', 'NVDA', 'AAPL', 'BBCA', 'BBRI', 'IHSG', 'BTCUSD', 'SOLUSD'];
  $('#quick').innerHTML = picks.map((p) => `<button type="button" data-sym="${p}">${p}</button>`).join('');
}

export default {
  init(s) {
    store = s;
    quickChips();
    $('#hero-search').addEventListener('submit', (e) => {
      e.preventDefault();
      const v = $('#hero-input').value.trim();
      if (v) window.SMC.openAnalysis(v);
    });
    $('#tab-dashboard').addEventListener('click', (e) => {
      const t = e.target.closest('[data-sym]');
      if (t) window.SMC.openAnalysis(t.dataset.sym, t.dataset.tf || undefined);
    });
    const canvas = $('#radar');
    canvas.addEventListener('mousemove', radarHover);
    canvas.addEventListener('mouseleave', () => { $('#radar-tip').style.display = 'none'; });
    canvas.addEventListener('click', () => { if (radar.hover) window.SMC.openAnalysis(radar.hover.r.instrument.symbol, radar.hover.r.tf); });

    on('stats', (st) => { renderChecks(st); renderKpis(st); renderTiles(); });
    on('screener', (snap) => { radarData(snap); renderSignals(); renderVerdicts(); renderTiles(); });
    on('analyses', () => { renderRecent(); renderVerdicts(); });
    on('alerts', renderAlerts);
    on('alert', renderAlerts);
    on('direction', renderDirection);
    on('directionError', (e) => { $('#dir-strip').innerHTML = `<div class="empty">${esc(e.message)}</div>`; });

    renderChecks(store.stats); renderKpis(store.stats); renderRecent(); renderAlerts(); renderSignals(); renderVerdicts(); renderTiles();
    if (store.screener) radarData(store.screener);
    if (store.direction) renderDirection(store.direction);
    setInterval(() => { renderRecent(); renderAlerts(); }, 60_000);
  },
  show() {
    if (!radar.running && radar.rows.length) startRadar();
    if (!radar.rows.length) startRadar();
  },
};
