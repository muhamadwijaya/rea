// SMC screener table (engine) + TradingView screener widget (realtime, whole market).
import { $, api, esc, on, badge, fmtPct, fmtPrice, fmtScore, fmtTime, scoreBar, segmented, signClass, sparkline, trendText, dirHex, arrow, dirClass, CLASS_LABEL } from './util.js';
import { screenerWidget } from './tv.js';

const GROUP_CLASSES = { all: null, forex: ['forex'], us: ['us'], idx: ['idx'], macro: ['index', 'commodity', 'crypto'] };
const COLUMNS = [
  ['symbol', 'SIMBOL'], ['price', 'HARGA', 'r'], ['changePct', '1D', 'r'], ['change5dPct', '5D', 'r'], ['spark', 'TREN 40 BAR'],
  ['swingTrend', 'SWING'], ['internalTrend', 'INTERNAL'], ['event', 'EVENT TERAKHIR'], ['zone', 'ZONA P/D'],
  ['ob', 'OB TERDEKAT', 'r'], ['fvg', 'FVG ▲/▼'], ['rsi', 'RSI', 'r'], ['score', 'SKOR', 'r'], ['direction', 'ARAH'],
];

let store;
let snapshot = null;
let filters = { group: 'all', bias: 'all', tf: '4h', q: '' };
let sort = { key: 'score', dir: -1 };
let loadingTf = null;

const sortValue = {
  symbol: (r) => r.instrument.symbol,
  price: (r) => r.price ?? -Infinity,
  changePct: (r) => r.changePct ?? -Infinity,
  change5dPct: (r) => r.change5dPct ?? -Infinity,
  spark: (r) => r.changePct ?? -Infinity,
  swingTrend: (r) => r.swingTrend,
  internalTrend: (r) => r.internalTrend,
  event: (r) => (r.lastEvent ? -r.lastEvent.barsAgo : -9999),
  zone: (r) => r.zone?.position ?? -1,
  ob: (r) => (r.nearestOb ? Math.abs(r.nearestOb.distancePct) : Infinity),
  fvg: (r) => (r.openFvg ? r.openFvg.bullish - r.openFvg.bearish : 0),
  rsi: (r) => r.rsi ?? -1,
  score: (r) => r.score ?? 0,
  direction: (r) => r.score ?? 0,
};

function filtered() {
  const classes = GROUP_CLASSES[filters.group];
  const q = filters.q.toUpperCase();
  return (snapshot?.rows || []).filter((r) => {
    if (classes && !classes.includes(r.instrument.assetClass)) return false;
    if (q && !r.instrument.symbol.includes(q) && !r.instrument.name.toUpperCase().includes(q)) return false;
    if (filters.bias !== 'all') {
      if (r.error) return false;
      if (filters.bias === 'neutral' ? r.direction !== 'neutral' : !r.direction?.includes(filters.bias)) return false;
    }
    return true;
  });
}

function cell(r, key) {
  const d = r.instrument.decimals;
  switch (key) {
    case 'symbol': return `<td><span class="sym">${esc(r.instrument.symbol)}<span class="cls">${CLASS_LABEL[r.instrument.assetClass] || ''}</span><small>${esc(r.instrument.name)}</small></span></td>`;
    case 'price': return `<td class="r num">${fmtPrice(r.price, d)}</td>`;
    case 'changePct': return `<td class="r num ${signClass(r.changePct)}">${fmtPct(r.changePct)}</td>`;
    case 'change5dPct': return `<td class="r num ${signClass(r.change5dPct)}">${fmtPct(r.change5dPct)}</td>`;
    case 'spark': {
      const s = r.spark || [];
      return `<td style="width:110px">${sparkline(s, s.length > 1 && s[s.length - 1] >= s[0] ? '#2fd28c' : '#ff4d5e', 100, 24)}</td>`;
    }
    case 'swingTrend': return `<td>${trendText(r.swingTrend)}</td>`;
    case 'internalTrend': return `<td>${trendText(r.internalTrend)}</td>`;
    case 'event': {
      const e = r.lastEvent;
      if (!e) return '<td class="dim">–</td>';
      return `<td><span class="tag ${dirClass(e.direction)}">${e.scope === 'internal' ? 'i' : ''}${esc(e.kind)} ${arrow(e.direction)}</span> <span class="dim">${e.barsAgo} bar</span></td>`;
    }
    case 'zone': {
      if (!r.zone) return '<td class="dim">–</td>';
      const p = Math.round(r.zone.position * 100);
      return `<td title="${esc(r.zone.area)} ${p}%"><span class="zonebar"><i style="left:${p}%"></i></span> <span class="dim">${p}%</span></td>`;
    }
    case 'ob': {
      const ob = r.nearestOb;
      if (!ob) return '<td class="r dim">–</td>';
      return `<td class="r"><span style="color:${dirHex(ob.direction)}">${arrow(ob.direction)}</span> ${ob.inside ? '<b class="flat">DI DALAM</b>' : `<span class="num">${fmtPct(ob.distancePct)}</span>`}</td>`;
    }
    case 'fvg': return `<td><span class="up">▲${r.openFvg?.bullish ?? 0}</span> <span class="down">▼${r.openFvg?.bearish ?? 0}</span></td>`;
    case 'rsi': {
      const v = r.rsi;
      const cls = v >= 70 ? 'down' : v <= 30 ? 'up' : '';
      return `<td class="r num ${cls}">${v == null ? '–' : v.toFixed(0)}</td>`;
    }
    case 'score': return `<td class="r"><span class="num" style="color:${dirHex(r.direction)}">${fmtScore(r.score)}</span> ${scoreBar(r.score)}</td>`;
    case 'direction': return `<td>${badge(r.direction, r.label)}</td>`;
    default: return '<td></td>';
  }
}

function render() {
  const table = $('#scr-table');
  if (!snapshot) {
    table.innerHTML = `<tbody><tr><td class="loading">${loadingTf ? `Memindai watchlist ${loadingTf.toUpperCase()} (pertama kali bisa ±20 detik)` : 'Menunggu data screener'}</td></tr></tbody>`;
    return;
  }
  const rows = filtered().sort((a, b) => {
    const va = sortValue[sort.key](a), vb = sortValue[sort.key](b);
    return (va > vb ? 1 : va < vb ? -1 : 0) * sort.dir;
  });
  const head = COLUMNS.map(([k, label, align]) =>
    `<th data-k="${k}" class="${align || ''} ${sort.key === k ? 'sorted' : ''}" data-dir="${sort.dir > 0 ? '▲' : '▼'}">${label}</th>`).join('');
  const body = rows.map((r) => r.error
    ? `<tr data-sym="${esc(r.instrument.symbol)}">${cell(r, 'symbol')}<td colspan="${COLUMNS.length - 1}" class="down">${esc(r.error)}</td></tr>`
    : `<tr data-sym="${esc(r.instrument.symbol)}">${COLUMNS.map(([k]) => cell(r, k)).join('')}</tr>`).join('');
  table.innerHTML = `<thead><tr>${head}</tr></thead><tbody>${body || `<tr><td colspan="${COLUMNS.length}" class="empty">Tidak ada instrumen yang cocok dengan filter.</td></tr>`}</tbody>`;
  const ok = (snapshot.rows || []).filter((r) => !r.error);
  const bull = ok.filter((r) => r.direction?.includes('bullish')).length;
  const bear = ok.filter((r) => r.direction?.includes('bearish')).length;
  $('#scr-sub').innerHTML = `· ${rows.length} instrumen · TF ${esc(snapshot.tf.toUpperCase())} · <span class="up">▲${bull}</span> <span class="down">▼${bear}</span>`;
  $('#scr-updated').textContent = `update ${fmtTime(snapshot.generatedAt)} · ${snapshot.elapsedMs} ms`;
}

async function loadTf(tf) {
  if (tf === store.screener?.tf) { snapshot = store.screener; render(); return; }
  snapshot = null;
  loadingTf = tf;
  render();
  try {
    snapshot = await api(`/api/screener?group=all&tf=${tf}`);
  } catch (e) {
    $('#scr-table').innerHTML = `<tbody><tr><td><div class="error-box">${esc(e.message)}</div></td></tr></tbody>`;
    return;
  } finally {
    loadingTf = null;
  }
  render();
}

export default {
  init(s) {
    store = s;
    snapshot = store.screener;
    if (snapshot) filters.tf = snapshot.tf;
    segmented($('#scr-group'), (v) => { filters.group = v; render(); });
    segmented($('#scr-bias'), (v) => { filters.bias = v; render(); });
    const tfSeg = segmented($('#scr-tf'), (v) => { filters.tf = v; loadTf(v); });
    tfSeg.set(filters.tf);
    $('#scr-search').addEventListener('input', (e) => { filters.q = e.target.value.trim(); render(); });
    $('#scr-table').addEventListener('click', (e) => {
      const th = e.target.closest('th[data-k]');
      if (th) {
        const k = th.dataset.k;
        sort = { key: k, dir: sort.key === k ? -sort.dir : k === 'symbol' ? 1 : -1 };
        render();
        return;
      }
      const tr = e.target.closest('tr[data-sym]');
      if (tr) window.SMC.openAnalysis(tr.dataset.sym, filters.tf);
    });
    on('screener', (snap) => { if (snap.tf === filters.tf) { snapshot = snap; render(); } });
    segmented($('#tvs-market'), (v) => screenerWidget($('#tv-screener'), v));
    screenerWidget($('#tv-screener'), 'forex');
    render();
  },
  show(params) {
    if (params[0] && GROUP_CLASSES[params[0]] !== undefined) {
      filters.group = params[0];
      $('#scr-group').querySelectorAll('button').forEach((b) => b.classList.toggle('active', b.dataset.v === params[0]));
      render();
    }
  },
};
