// Screener view of one market page: SMC table for the market's instruments + TradingView screener (realtime).
import { api, esc, on, badge, fmtPct, fmtPrice, fmtScore, fmtTime, scoreBar, segmented, signClass, sparkline, trendText, dirHex, arrow, dirClass, CLASS_LABEL } from './util.js';
import { screenerWidget } from './tv.js';
import { rowsOf } from './markets.js';

const COLUMNS = [
  ['symbol', 'SIMBOL'], ['price', 'HARGA', 'r'], ['changePct', '1D', 'r'], ['change5dPct', '5D', 'r'], ['spark', 'TREN 40 BAR'],
  ['swingTrend', 'SWING'], ['internalTrend', 'INTERNAL'], ['event', 'EVENT TERAKHIR'], ['zone', 'ZONA P/D'],
  ['ob', 'OB TERDEKAT', 'r'], ['fvg', 'FVG ▲/▼'], ['rsi', 'RSI', 'r'], ['score', 'SKOR', 'r'], ['direction', 'ARAH'],
];

const SORT_VALUE = {
  symbol: (r) => r.instrument.symbol,
  price: (r) => r.price ?? -Infinity,
  changePct: (r) => r.changePct ?? -Infinity,
  change5dPct: (r) => r.change5dPct ?? -Infinity,
  spark: (r) => r.changePct ?? -Infinity,
  swingTrend: (r) => r.swingTrend ?? '',
  internalTrend: (r) => r.internalTrend ?? '',
  event: (r) => (r.lastEvent ? -r.lastEvent.barsAgo : -9999),
  zone: (r) => r.zone?.position ?? -1,
  ob: (r) => (r.nearestOb ? Math.abs(r.nearestOb.distancePct) : Infinity),
  fvg: (r) => (r.openFvg ? r.openFvg.bullish - r.openFvg.bearish : 0),
  rsi: (r) => r.rsi ?? -1,
  score: (r) => r.score ?? 0,
  direction: (r) => r.score ?? 0,
};

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
      return `<td class="r num ${v >= 70 ? 'down' : v <= 30 ? 'up' : ''}">${v == null ? '–' : v.toFixed(0)}</td>`;
    }
    case 'score': return `<td class="r"><span class="num" style="color:${dirHex(r.direction)}">${fmtScore(r.score)}</span> ${scoreBar(r.score)}</td>`;
    case 'direction': return `<td>${badge(r.direction, r.label)}</td>`;
    default: return '<td></td>';
  }
}

export class ScreenerView {
  constructor(el, cfg, store) {
    this.el = el;
    this.cfg = cfg;
    this.store = store;
    this.filters = { bias: 'all', tf: store.screener?.tf || '4h', q: '' };
    this.sort = { key: 'score', dir: -1 };
    this.snapshot = store.screener;
    this.loadingTf = null;
    el.innerHTML = `
      <div class="panel tabbed fill">
        <div class="ptabs"><button data-t="smc" class="active">SMC SCREENER · ${esc(cfg.nav)}</button><button data-t="tv">TRADINGVIEW SCREENER (REALTIME)</button></div>
        <div class="pane active" data-t="smc">
          <div class="toolbar" style="padding:8px 10px;border-bottom:1px solid var(--line)">
            <div class="seg scr-bias"><button data-v="all" class="active">SEMUA BIAS</button><button data-v="bullish">BULLISH</button><button data-v="bearish">BEARISH</button><button data-v="neutral">SIDEWAYS</button></div>
            <div class="seg scr-tf"><button data-v="1h">1H · INTI</button><button data-v="4h">4H · INTI</button><button data-v="1d">1D · INTI</button><button data-v="all">1D · SEMUA ASET</button></div>
            <input class="input scr-search" placeholder="cari simbol…">
            <span class="sub scr-sub muted"></span>
            <span class="grow"></span><span class="time scr-updated"></span><span class="live-label">LIVE</span>
          </div>
          <div class="table-wrap"><table class="data scr-table"></table></div>
        </div>
        <div class="pane" data-t="tv"><div class="tv-fill tv-screener"></div></div>
      </div>`;
    const $ = (s) => el.querySelector(s);
    segmented($('.scr-bias'), (v) => { this.filters.bias = v; this.render(); });
    segmented($('.scr-tf'), (v) => { this.filters.tf = v; this.loadTf(v); }).set(this.filters.tf);
    $('.scr-search').addEventListener('input', (e) => { this.filters.q = e.target.value.trim(); this.render(); });
    $('.scr-table').addEventListener('click', (e) => {
      const th = e.target.closest('th[data-k]');
      if (th) {
        const k = th.dataset.k;
        this.sort = { key: k, dir: this.sort.key === k ? -this.sort.dir : k === 'symbol' ? 1 : -1 };
        this.render();
        return;
      }
      const tr = e.target.closest('tr[data-sym]');
      if (tr) window.SMC.go(cfg.key, 'analisa', tr.dataset.sym, this.filters.tf);
    });
    on('screener', (snap) => { if (snap.tf === this.filters.tf && this.filters.tf !== 'all') { this.snapshot = snap; this.render(); } });
    // TradingView widget is mounted the first time its tab is opened (it needs a visible, sized container).
    el.querySelector('.pane[data-t="tv"]').addEventListener('tabshow', () => {
      if (!this.tvMounted) { this.tvMounted = true; screenerWidget($('.tv-screener'), cfg.tvScreener); }
    });
    this.render();
  }

  rows() {
    const q = this.filters.q.toUpperCase();
    return rowsOf(this.snapshot, this.cfg.key).filter((r) => {
      if (q && !r.instrument.symbol.includes(q) && !r.instrument.name.toUpperCase().includes(q)) return false;
      if (this.filters.bias === 'all') return true;
      if (r.error) return false;
      return this.filters.bias === 'neutral' ? r.direction === 'neutral' : r.direction?.includes(this.filters.bias);
    });
  }

  render() {
    const table = this.el.querySelector('.scr-table');
    if (!this.snapshot) {
      table.innerHTML = `<tbody><tr><td class="loading">${this.loadingTf ? `Memindai ${this.loadingTf.toUpperCase()} (pertama kali bisa ±20 detik)` : 'Menunggu data screener'}</td></tr></tbody>`;
      return;
    }
    const { key, dir } = this.sort;
    const rows = this.rows().sort((a, b) => {
      const va = SORT_VALUE[key](a), vb = SORT_VALUE[key](b);
      return (va > vb ? 1 : va < vb ? -1 : 0) * dir;
    });
    const head = COLUMNS.map(([k, label, align]) =>
      `<th data-k="${k}" class="${align || ''} ${key === k ? 'sorted' : ''}" data-dir="${dir > 0 ? '▲' : '▼'}">${label}</th>`).join('');
    const body = rows.map((r) => r.error
      ? `<tr data-sym="${esc(r.instrument.symbol)}">${cell(r, 'symbol')}<td colspan="${COLUMNS.length - 1}" class="down">${esc(r.error)}</td></tr>`
      : `<tr data-sym="${esc(r.instrument.symbol)}">${COLUMNS.map(([k]) => cell(r, k)).join('')}</tr>`).join('');
    table.innerHTML = `<thead><tr>${head}</tr></thead><tbody>${body || `<tr><td colspan="${COLUMNS.length}" class="empty">Tidak ada instrumen yang cocok dengan filter.</td></tr>`}</tbody>`;
    const ok = rowsOf(this.snapshot, this.cfg.key).filter((r) => !r.error);
    const bull = ok.filter((r) => r.direction?.includes('bullish')).length;
    const bear = ok.filter((r) => r.direction?.includes('bearish')).length;
    this.el.querySelector('.scr-sub').innerHTML = `· ${rows.length} instrumen${this.snapshot.scope === 'all' ? ' (semua aset)' : ''} · TF ${esc(this.snapshot.tf.toUpperCase())} · <span class="up">▲${bull}</span> <span class="down">▼${bear}</span>`;
    this.el.querySelector('.scr-updated').textContent = `update ${fmtTime(this.snapshot.generatedAt)} · ${this.snapshot.elapsedMs} ms`;
  }

  async loadTf(tf) {
    clearTimeout(this.retry);
    if (tf === this.store.screener?.tf) { this.snapshot = this.store.screener; this.render(); return; }
    this.snapshot = null;
    this.loadingTf = tf;
    this.render();
    try {
      if (tf === 'all') {
        // Full universe (hundreds of assets) comes from the engine's background 1D scan.
        const res = await api(`/api/screener?market=${encodeURIComponent(this.cfg.key)}&scope=all`);
        if (res.status === 'warming') {
          const p = res.progress || {};
          this.el.querySelector('.scr-table').innerHTML = `<tbody><tr><td class="loading">Engine sedang memindai seluruh aset (${p.done ?? 0}/${p.total ?? 0} pasar) — otomatis dimuat ulang</td></tr></tbody>`;
          this.retry = setTimeout(() => { if (this.filters.tf === 'all') this.loadTf('all'); }, 15000);
          return;
        }
        this.snapshot = res;
      } else {
        this.snapshot = await api(`/api/screener?market=${encodeURIComponent(this.cfg.key)}&tf=${tf}`);
      }
    } catch (e) {
      this.el.querySelector('.scr-table').innerHTML = `<tbody><tr><td><div class="error-box">${esc(e.message)}</div></td></tr></tbody>`;
      return;
    } finally {
      this.loadingTf = null;
    }
    this.render();
  }
}
