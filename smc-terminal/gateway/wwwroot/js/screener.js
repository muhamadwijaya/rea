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

// SMC conditions a row must meet (all selected ones), the core of "screening versi SMC".
const CONDITIONS = [
  ['choch', 'CHoCH BARU ≤3 BAR', (r) => r.lastEvent?.kind === 'CHoCH' && r.lastEvent.barsAgo <= 3],
  ['bos', 'BOS BARU ≤3 BAR', (r) => r.lastEvent?.kind === 'BOS' && r.lastEvent.barsAgo <= 3],
  ['bull', 'SWING BULLISH', (r) => r.swingTrend === 'bullish'],
  ['bear', 'SWING BEARISH', (r) => r.swingTrend === 'bearish'],
  ['aligned', 'SWING = INTERNAL', (r) => r.swingTrend && r.swingTrend !== 'neutral' && r.swingTrend === r.internalTrend],
  ['inob', 'HARGA DI OB', (r) => !!r.nearestOb?.inside],
  ['disc', 'ZONA DISCOUNT', (r) => r.zone?.area === 'discount'],
  ['prem', 'ZONA PREMIUM', (r) => r.zone?.area === 'premium'],
  ['fvgbull', 'FVG BULLISH', (r) => (r.openFvg?.bullish ?? 0) > 0],
  ['fvgbear', 'FVG BEARISH', (r) => (r.openFvg?.bearish ?? 0) > 0],
  ['rsilow', 'RSI ≤ 30', (r) => r.rsi != null && r.rsi <= 30],
  ['rsihigh', 'RSI ≥ 70', (r) => r.rsi != null && r.rsi >= 70],
];
const MAX_ROWS = 600;

export class ScreenerView {
  constructor(el, cfg, store) {
    this.el = el;
    this.cfg = cfg;
    this.store = store;
    this.filters = { bias: 'all', tf: 'all-1d', q: '', sector: '', conds: new Set() };
    this.sort = { key: 'score', dir: -1 };
    this.snapshot = null;
    this.loadingTf = null;
    el.innerHTML = `
      <div class="panel tabbed fill">
        <div class="ptabs"><button data-t="smc" class="active">SMC SCREENER · ${esc(cfg.nav)}</button><button data-t="tv">TRADINGVIEW SCREENER (REALTIME)</button></div>
        <div class="pane active" data-t="smc">
          <div class="toolbar scr-bar">
            <div class="seg scr-tf"><button data-v="all-1d">1D · SEMUA ASET</button><button data-v="all-4h">4H · SEMUA ASET</button><button data-v="1m">1M</button><button data-v="5m">5M</button><button data-v="10m">10M</button><button data-v="15m">15M</button><button data-v="1h">1H · INTI</button><button data-v="4h">4H · INTI</button><button data-v="1d">1D · INTI</button></div>
            <div class="seg scr-bias"><button data-v="all" class="active">SEMUA BIAS</button><button data-v="bullish">BULLISH</button><button data-v="bearish">BEARISH</button><button data-v="neutral">SIDEWAYS</button></div>
            <select class="input scr-sector" style="min-width:0;max-width:200px"><option value="">Semua sektor</option></select>
            <input class="input scr-search" placeholder="cari simbol / nama…" style="min-width:0;width:170px">
            <span class="grow"></span><span class="time scr-updated"></span><span class="live-label">LIVE</span>
          </div>
          <div class="toolbar scr-bar"><span class="dim" style="font-size:10.5px;letter-spacing:.1em">KONDISI SMC</span>
            <div class="chips scr-conds">${CONDITIONS.map(([k, l]) => `<button class="chip" data-c="${k}">${l}</button>`).join('')}</div>
            <button class="btn danger sm scr-reset" type="button">RESET</button><span class="grow"></span><span class="sub scr-sub muted"></span></div>
          <div class="table-wrap"><table class="data scr-table"></table></div>
        </div>
        <div class="pane" data-t="tv"><div class="tv-fill tv-screener"></div></div>
      </div>`;
    const $ = (s) => el.querySelector(s);
    segmented($('.scr-bias'), (v) => { this.filters.bias = v; this.render(); });
    this.tfSeg = segmented($('.scr-tf'), (v) => { this.filters.tf = v; this.loadTf(v); });
    this.tfSeg.set(this.filters.tf);
    $('.scr-search').addEventListener('input', (e) => { this.filters.q = e.target.value.trim(); this.render(); });
    $('.scr-sector').addEventListener('change', (e) => { this.filters.sector = e.target.value; this.render(); });
    $('.scr-conds').addEventListener('click', (e) => {
      const c = e.target.closest('[data-c]');
      if (!c) return;
      if (this.filters.conds.has(c.dataset.c)) this.filters.conds.delete(c.dataset.c); else this.filters.conds.add(c.dataset.c);
      c.classList.toggle('active');
      this.render();
    });
    $('.scr-reset').addEventListener('click', () => {
      this.filters.conds.clear();
      el.querySelectorAll('.scr-conds .chip').forEach((x) => x.classList.remove('active'));
      this.render();
    });
    $('.scr-table').addEventListener('click', (e) => {
      const th = e.target.closest('th[data-k]');
      if (th) {
        const k = th.dataset.k;
        this.sort = { key: k, dir: this.sort.key === k ? -this.sort.dir : k === 'symbol' ? 1 : -1 };
        this.render();
        return;
      }
      const tr = e.target.closest('tr[data-sym]');
      if (tr) window.SMC.go(cfg.key, 'analisa', tr.dataset.sym, this.snapshot?.tf || '4h');
    });
    on('screener', (snap) => { if (snap.tf === this.filters.tf) { this.snapshot = snap; this.render(); } });
    // TradingView widget is mounted the first time its tab is opened (it needs a visible, sized container).
    el.querySelector('.pane[data-t="tv"]').addEventListener('tabshow', () => {
      if (!this.tvMounted) { this.tvMounted = true; screenerWidget($('.tv-screener'), cfg.tvScreener); }
    });
    this.loadTf(this.filters.tf);
  }

  rows() {
    const q = this.filters.q.toUpperCase();
    const conds = CONDITIONS.filter(([k]) => this.filters.conds.has(k)).map(([, , fn]) => fn);
    return rowsOf(this.snapshot, this.cfg.key).filter((r) => {
      if (q && !r.instrument.symbol.includes(q) && !r.instrument.name.toUpperCase().includes(q)) return false;
      if (this.filters.sector && r.instrument.sector !== this.filters.sector) return false;
      if (r.error) return !conds.length && this.filters.bias === 'all';
      if (this.filters.bias !== 'all' && !(this.filters.bias === 'neutral' ? r.direction === 'neutral' : r.direction?.includes(this.filters.bias))) return false;
      return conds.every((fn) => fn(r));
    });
  }

  renderSectors() {
    const sectors = [...new Set(rowsOf(this.snapshot, this.cfg.key).map((r) => r.instrument.sector).filter(Boolean))].sort();
    const sel = this.el.querySelector('.scr-sector');
    sel.classList.toggle('hidden', !sectors.length);
    if (!sectors.includes(this.filters.sector)) this.filters.sector = '';
    sel.innerHTML = `<option value="">Semua sektor (${sectors.length})</option>${sectors.map((s) => `<option ${s === this.filters.sector ? 'selected' : ''}>${esc(s)}</option>`).join('')}`;
  }

  render() {
    const table = this.el.querySelector('.scr-table');
    if (!this.snapshot) {
      table.innerHTML = `<tbody><tr><td class="loading">${this.loadingTf ? `Memindai ${this.loadingTf.toUpperCase()} (pertama kali bisa ±20 detik)` : 'Menunggu data screener'}</td></tr></tbody>`;
      return;
    }
    const { key, dir } = this.sort;
    const matched = this.rows().sort((a, b) => {
      const va = SORT_VALUE[key](a), vb = SORT_VALUE[key](b);
      return (va > vb ? 1 : va < vb ? -1 : 0) * dir;
    });
    const rows = matched.slice(0, MAX_ROWS);
    const head = COLUMNS.map(([k, label, align]) =>
      `<th data-k="${k}" class="${align || ''} ${key === k ? 'sorted' : ''}" data-dir="${dir > 0 ? '▲' : '▼'}">${label}</th>`).join('');
    const body = rows.map((r) => r.error
      ? `<tr data-sym="${esc(r.instrument.symbol)}">${cell(r, 'symbol')}<td colspan="${COLUMNS.length - 1}" class="down">${esc(r.error)}</td></tr>`
      : `<tr data-sym="${esc(r.instrument.symbol)}">${COLUMNS.map(([k]) => cell(r, k)).join('')}</tr>`).join('');
    table.innerHTML = `<thead><tr>${head}</tr></thead><tbody>${body || `<tr><td colspan="${COLUMNS.length}" class="empty">Tidak ada instrumen yang cocok dengan filter.</td></tr>`}</tbody>`;
    const ok = rowsOf(this.snapshot, this.cfg.key).filter((r) => !r.error);
    const bull = ok.filter((r) => r.direction?.includes('bullish')).length;
    const bear = ok.filter((r) => r.direction?.includes('bearish')).length;
    this.el.querySelector('.scr-sub').innerHTML = `<b>${matched.length}</b> lolos dari ${ok.length} aset${matched.length > rows.length ? ` (tampil ${rows.length} teratas)` : ''} · TF ${esc(this.snapshot.tf.toUpperCase())}${this.snapshot.scope === 'all' ? ' · semua aset' : ' · inti'} · <span class="up">▲${bull}</span> <span class="down">▼${bear}</span>`;
    this.el.querySelector('.scr-updated').textContent = `update ${fmtTime(this.snapshot.generatedAt)} · ${this.snapshot.elapsedMs} ms`;
  }

  async loadTf(tf) {
    clearTimeout(this.retry);
    const all = tf.startsWith('all-');
    if (!all && tf === this.store.screener?.tf) { this.snapshot = this.store.screener; this.renderSectors(); this.render(); return; }
    this.snapshot = null;
    this.loadingTf = all ? `${tf.slice(4)} semua aset` : tf;
    this.render();
    try {
      if (all) {
        // Full universe (hundreds of assets) comes from the engine's background scans.
        const res = await api(`/api/screener?market=${encodeURIComponent(this.cfg.key)}&scope=all&tf=${tf.slice(4)}`);
        if (res.status === 'warming') {
          const p = res.progress || {};
          this.el.querySelector('.scr-table').innerHTML = `<tbody><tr><td class="loading">${p.total ? `Engine sedang memindai seluruh aset ${esc(tf.slice(4).toUpperCase())} (${p.done ?? 0}/${p.total} pasar)` : `Pemindaian seluruh aset ${esc(tf.slice(4).toUpperCase())} dijadwalkan (mulai ±${tf === 'all-4h' ? 3 : 1} menit setelah aplikasi dibuka)`} — otomatis dimuat ulang</td></tr></tbody>`;
          this.retry = setTimeout(() => { if (this.filters.tf === tf) this.loadTf(tf); }, 15000);
          return;
        }
        this.snapshot = res;
        // Universe scans refresh every 20–40 minutes; reload quietly so the table follows them.
        this.retry = setTimeout(() => { if (this.filters.tf === tf) this.reloadQuietly(tf); }, 5 * 60_000);
      } else {
        this.snapshot = await api(`/api/screener?market=${encodeURIComponent(this.cfg.key)}&tf=${tf}`);
      }
    } catch (e) {
      this.el.querySelector('.scr-table').innerHTML = `<tbody><tr><td><div class="error-box">${esc(e.message)}</div></td></tr></tbody>`;
      return;
    } finally {
      this.loadingTf = null;
    }
    this.renderSectors();
    this.render();
  }

  async reloadQuietly(tf) {
    try {
      const res = await api(`/api/screener?market=${encodeURIComponent(this.cfg.key)}&scope=all&tf=${tf.slice(4)}`);
      if (res.status === 'ready' && this.filters.tf === tf) { this.snapshot = res; this.renderSectors(); this.render(); }
    } catch { /* keep current table */ }
    this.retry = setTimeout(() => { if (this.filters.tf === tf) this.reloadQuietly(tf); }, 5 * 60_000);
  }
}
