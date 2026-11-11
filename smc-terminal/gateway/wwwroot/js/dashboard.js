// Dashboard: KPIs, search, five SMC checks, market orbs (all assets + floating news tags), and tabbed direction / signals / alerts / verdicts / recent analyses.
import { $, esc, on, badge, dirClass, dirHex, fmtPrice, fmtScore, fmtTime, sparkline, timeAgo, arrow } from './util.js';
import { Orbs } from './orbs.js';
import { AssetMap } from './asset-map.js';


let store;
let orbs = null;

// ------------------------------------------------------------------ engine checks
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
    </div>`).join('') : '<div class="empty">Belum ada analisa. Ketik simbol di atas atau klik aset di orbs.</div>';
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
    orbs = new Orbs($('#orbs-stage'));
    window.SMC.orbs = orbs; // handy for inspecting the orbs from the browser console
    orbs.setQuotes(store.quotes ? [...store.quotes.values()] : []);
    orbs.addNews(store.recentNews || [], true);
    on('quotesFull', (rows) => orbs.setQuotes(rows));
    const assetMap = new AssetMap($('#asset-map'), orbs, store);
    window.SMC.assetMap = assetMap;
    const setMode = (mode) => {
      $('#orbs-modes').querySelectorAll('button').forEach((b) => b.classList.toggle('active', b.dataset.mode === mode));
      document.querySelectorAll('.orbs-panel .panel-head .sub[data-for]').forEach((el) => el.classList.toggle('hidden', el.dataset.for !== mode));
      $('#orbs-stage').classList.toggle('hidden', mode !== 'orbs');
      $('#asset-map').classList.toggle('hidden', mode !== 'map');
      if (mode === 'map') assetMap.show(); else { assetMap.hide(); orbs.start(); }
      try { localStorage.setItem('smc.orbs.mode', mode); } catch { /* per-session only */ }
    };
    $('#orbs-modes').addEventListener('click', (e) => { const b = e.target.closest('button[data-mode]'); if (b) setMode(b.dataset.mode); });
    let savedMode = 'orbs';
    try { savedMode = localStorage.getItem('smc.orbs.mode') === 'map' ? 'map' : 'orbs'; } catch { /* default */ }
    setMode(savedMode);
    on('quotes', (msg) => {
      orbs.applyCompact(msg.rows);
      const p = msg.progress || {};
      $('#orbs-status').textContent = `harga ${msg.rows.length} aset · sweep ${p.done ?? 0}/${p.total ?? 0}`;
    });
    on('news', (items) => orbs.addNews(items));
    on('recentNews', (items) => orbs.addNews(items, true));
    orbs.start();

    on('stats', (st) => { renderChecks(st); renderKpis(st); });
    on('screener', () => { renderSignals(); renderVerdicts(); });
    on('analyses', () => { renderRecent(); renderVerdicts(); });
    on('alerts', renderAlerts);
    on('alert', renderAlerts);
    on('direction', renderDirection);
    on('directionError', (e) => { $('#dir-strip').innerHTML = `<div class="empty">${esc(e.message)}</div>`; });

    renderChecks(store.stats); renderKpis(store.stats); renderRecent(); renderAlerts(); renderSignals(); renderVerdicts();
    if (store.direction) renderDirection(store.direction);
    setInterval(() => { renderRecent(); renderAlerts(); }, 60_000);
  },
  show() { orbs?.start(); },
};
