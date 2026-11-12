// Asset map: every asset of one market as a square, grouped in blocks by sector / category (stock
// sectors, forex majors/crosses/exotics/commodities, crypto categories). Colour = today's move or the
// 1D SMC bias; markers show fresh news, news in the last hour and a new 1D CHoCH. The side panel has
// live counts and ranked lists; hovering a row finds the asset in the map, clicking opens the analysis.
// Data comes from the orbs (quotes + tagged news) and the full-universe 1D SMC scan.
import { api, esc, fmtPrice, fmtPct } from './util.js';
import { MARKETS, MARKET_KEYS } from './markets.js';

const NEWS_HOT_MS = 3 * 60 * 1000;
const NEWS_MS = 60 * 60 * 1000;
const SMC_REFRESH_MS = 5 * 60 * 1000;
const LISTS = [['up', 'TERKUAT'], ['down', 'TERLEMAH'], ['news', 'RAMAI BERITA'], ['choch', 'CHoCH BARU 1D']];

function remember(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    localStorage.setItem(key, value);
  } catch { /* storage unavailable: per-session only */ }
  return null;
}

const level = (v, scale) => {
  if (v == null || Number.isNaN(v)) return 'na';
  const x = v / scale;
  if (x >= 0.66) return 'u3';
  if (x >= 0.25) return 'u2';
  if (x > 0.03) return 'u1';
  if (x <= -0.66) return 'd3';
  if (x <= -0.25) return 'd2';
  if (x < -0.03) return 'd1';
  return 'f0';
};

export class AssetMap {
  constructor(el, orbs, store) {
    this.el = el;
    this.orbs = orbs;
    this.store = store;
    this.market = MARKET_KEYS.includes(remember('smc.map.market')) ? remember('smc.map.market') : 'saham-idx';
    this.mode = remember('smc.map.mode') === 'smc' ? 'smc' : 'change';
    this.list = 'up';
    this.smc = {}; // market -> {at, rows: Map(symbol -> row)}
    this.signature = '';
    this.pending = false;
    this.visible = false;
    el.innerHTML = `
      <div class="am-main">
        <div class="am-top">
          <div class="seg am-market">${MARKET_KEYS.map((k) => `<button data-m="${k}">${esc(MARKETS[k].nav)}</button>`).join('')}</div>
          <div class="seg am-mode"><button data-mode="change">% HARI INI</button><button data-mode="smc">BIAS SMC 1D</button></div>
        </div>
        <div class="am-kpis"></div>
        <div class="am-grid"></div>
        <div class="am-legend"></div>
        <div class="orb-tip am-tip"></div>
      </div>
      <div class="am-side">
        <div class="am-side-head"><span class="diamond">◆</span> <b class="am-side-title"></b><span class="right live-txt">● LIVE</span></div>
        <div class="am-stats"></div>
        <div class="am-tabs">${LISTS.map(([k, l]) => `<button data-l="${k}">${l}</button>`).join('')}</div>
        <div class="am-list-head"><span>ASET</span><span>GERAK</span><span class="r">HARGA</span><span class="r">%</span></div>
        <div class="am-list"></div>
        <div class="am-foot">Arahkan ke baris untuk menemukannya di peta · klik untuk analisa</div>
      </div>`;
    this.grid = el.querySelector('.am-grid');
    this.tip = el.querySelector('.am-tip');
    el.querySelector('.am-market').addEventListener('click', (e) => {
      const b = e.target.closest('button[data-m]');
      if (b) this.setMarket(b.dataset.m);
    });
    el.querySelector('.am-mode').addEventListener('click', (e) => {
      const b = e.target.closest('button[data-mode]');
      if (!b) return;
      this.mode = b.dataset.mode;
      remember('smc.map.mode', this.mode);
      this.paint();
    });
    el.querySelector('.am-tabs').addEventListener('click', (e) => {
      const b = e.target.closest('button[data-l]');
      if (!b) return;
      this.list = b.dataset.l;
      this.renderSide();
    });
    this.grid.addEventListener('mouseover', (e) => this.hoverSquare(e));
    this.grid.addEventListener('mouseleave', () => { this.tip.style.display = 'none'; });
    this.grid.addEventListener('click', (e) => {
      const sq = e.target.closest('[data-s]');
      if (sq) window.SMC.openAnalysis(sq.dataset.s);
    });
    const list = el.querySelector('.am-list');
    list.addEventListener('mouseover', (e) => this.highlight(e.target.closest('[data-s]')?.dataset.s));
    list.addEventListener('mouseleave', () => this.highlight(null));
    list.addEventListener('click', (e) => {
      const row = e.target.closest('[data-s]');
      if (row) window.SMC.openAnalysis(row.dataset.s);
    });
    orbs.subscribe(() => this.schedule());
    new ResizeObserver(() => { if (this.visible) this.fit(); }).observe(this.grid);
    setInterval(() => { if (this.visible) { this.paint(); this.loadSmc(); } }, 20_000);
  }

  show() {
    this.visible = true;
    this.render();
    this.loadSmc();
  }

  hide() {
    this.visible = false;
    this.tip.style.display = 'none';
  }

  setMarket(m) {
    this.market = m;
    remember('smc.map.market', m);
    this.signature = '';
    this.render();
    this.loadSmc();
  }

  schedule() {
    if (!this.visible || this.pending) return;
    this.pending = true;
    setTimeout(() => { this.pending = false; this.render(); }, 1000);
  }

  async loadSmc(force = false) {
    const m = this.market;
    const cached = this.smc[m];
    if (!force && cached && Date.now() - cached.at < SMC_REFRESH_MS) return;
    try {
      const res = await api(`/api/screener?market=${m}&scope=all&tf=1d`);
      if (res.status === 'warming' || !res.rows) return;
      this.smc[m] = { at: Date.now(), rows: new Map(res.rows.map((r) => [r.symbol, r])) };
      if (m === this.market) this.paint();
    } catch { /* engine warming: the map works without SMC data */ }
  }

  rows() {
    return this.orbs.byMarket[this.market] || [];
  }

  /** Blocks in the engine's order (fixed groups first), then the remaining ones largest first. */
  groups(rows) {
    const by = new Map();
    for (const q of rows) {
      const b = q.block || 'Lainnya';
      if (!by.has(b)) by.set(b, []);
      by.get(b).push(q);
    }
    const order = this.store.blockOrder?.[this.market] || [];
    const rank = (b) => { const i = order.indexOf(b); return i < 0 ? order.length : i; };
    return [...by.entries()]
      .map(([name, items]) => ({ name, items: items.sort((a, b) => (b.changePct ?? -1e9) - (a.changePct ?? -1e9)) }))
      .sort((a, b) => rank(a.name) - rank(b.name) || b.items.length - a.items.length || (a.name < b.name ? -1 : 1));
  }

  render() {
    if (!this.visible) return;
    this.el.querySelectorAll('.am-market button').forEach((b) => b.classList.toggle('active', b.dataset.m === this.market));
    const rows = this.rows();
    const groups = this.groups(rows);
    const signature = `${this.market}|${groups.map((g) => `${g.name}:${g.items.map((q) => q.symbol).join(',')}`).join('|')}`;
    if (signature !== this.signature) {
      this.signature = signature;
      this.grid.innerHTML = rows.length ? groups.map((g) => `
        <div class="am-block" data-b="${esc(g.name)}">
          <div class="am-block-head"><b>${esc(g.name.toUpperCase())}</b> <span class="am-block-avg"></span></div>
          <div class="am-cells">${g.items.map((q) => `<i data-s="${esc(q.symbol)}"></i>`).join('')}</div>
        </div>`).join('') : '<div class="loading">Memuat harga seluruh aset</div>';
      this.cells = new Map([...this.grid.querySelectorAll('[data-s]')].map((c) => [c.dataset.s, c]));
      this.fit();
    }
    this.paint();
  }

  /** Largest square size at which every block fits without scrolling. */
  fit() {
    const box = this.grid;
    const blocks = [...box.querySelectorAll('.am-block')];
    if (!blocks.length) return;
    const W = box.clientWidth, H = box.clientHeight;
    for (let cell = 40; cell >= 4; cell--) {
      box.style.setProperty('--cell', `${cell}px`);
      for (const b of blocks) {
        const n = b.querySelector('.am-cells').childElementCount;
        const cols = Math.max(Math.ceil(78 / (cell + 2)), Math.ceil(Math.sqrt(n * 1.7)));
        b.style.setProperty('--cols', Math.min(cols, Math.max(1, Math.floor(W / (cell + 2)) - 1)));
      }
      if (box.scrollHeight <= H + 1 && box.scrollWidth <= W + 1) break;
    }
  }

  newsIndex() {
    const now = Date.now();
    const recent = new Map();
    for (const n of this.orbs.news) {
      if (n.market !== this.market || now - n.at > NEWS_MS || !n.symbol) continue;
      const e = recent.get(n.symbol) || { count: 0, up: 0, down: 0 };
      e.count++;
      if (n.tone === 'up') e.up++; else if (n.tone === 'down') e.down++;
      recent.set(n.symbol, e);
    }
    const hot = new Set(this.orbs.tags.filter((t) => t.market === this.market && now - t.at < NEWS_HOT_MS).map((t) => t.symbol));
    return { recent, hot };
  }

  paint() {
    if (!this.visible || !this.cells) return;
    this.el.querySelectorAll('.am-mode button').forEach((b) => b.classList.toggle('active', b.dataset.mode === this.mode));
    const rows = this.rows();
    const smc = this.smc[this.market]?.rows;
    const useSmc = this.mode === 'smc' && smc;
    const abs = rows.map((q) => Math.abs(q.changePct ?? 0)).sort((a, b) => a - b);
    const scale = Math.max(0.5, abs[Math.floor(abs.length * 0.9)] || 1);
    const { recent, hot } = this.newsIndex();
    for (const q of rows) {
      const cell = this.cells.get(q.symbol);
      if (!cell) continue;
      const s = smc?.get(q.symbol);
      const lvl = useSmc ? level(s?.score ?? null, 60) : level(q.changePct, scale);
      const choch = s?.lastEvent?.kind === 'CHoCH' && s.lastEvent.barsAgo <= 3;
      cell.className = `${lvl}${recent.has(q.symbol) ? ' news' : ''}${hot.has(q.symbol) ? ' hot' : ''}${choch ? ' choch' : ''}${this.highlighted === q.symbol ? ' hl' : ''}`;
    }
    this.groupInfo = new Map();
    for (const g of this.groups(rows)) {
      const vals = g.items.map((q) => q.changePct).filter((v) => v != null);
      const avg = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
      const el = this.grid.querySelector(`.am-block[data-b="${CSS.escape(g.name)}"]`);
      if (!el) continue;
      // shown as the last line of the map's own tooltip (a native title tooltip would cover it)
      this.groupInfo.set(g.name, `${esc(g.name)}: ${g.items.length} aset · <span class="up">▲${vals.filter((v) => v > 0.05).length}</span> <span class="down">▼${vals.filter((v) => v < -0.05).length}</span> · rata-rata <span class="${avg > 0.05 ? 'up' : avg < -0.05 ? 'down' : 'flat'}">${avg == null ? '–' : fmtPct(avg)}</span>`);
      const span = el.querySelector('.am-block-avg');
      span.textContent = avg == null ? '' : fmtPct(avg);
      span.className = `am-block-avg ${avg > 0.05 ? 'up' : avg < -0.05 ? 'down' : 'flat'}`;
    }
    this.renderKpis(rows, recent, smc);
    this.renderLegend(useSmc, smc);
    this.renderSide();
  }

  renderKpis(rows, recent, smc) {
    const ch = rows.map((q) => q.changePct).filter((v) => v != null);
    const up = ch.filter((v) => v > 0.05).length, down = ch.filter((v) => v < -0.05).length;
    const choch = smc ? [...smc.values()].filter((s) => s.lastEvent?.kind === 'CHoCH' && s.lastEvent.barsAgo <= 3).length : null;
    this.el.querySelector('.am-kpis').innerHTML = [
      ['ASET', rows.length, ''], ['NAIK', up, 'up'], ['TURUN', down, 'down'],
      ['BERITA 60 MNT', [...recent.values()].reduce((a, e) => a + e.count, 0), 'info'], ['CHoCH BARU 1D', choch ?? '…', 'purple'],
    ].map(([l, v, c]) => `<div><span>${l}</span><b class="${c}">${v}</b></div>`).join('');
  }

  renderLegend(useSmc, smc) {
    const scale = useSmc ? 'bias SMC 1D (skor)' : '% hari ini';
    this.el.querySelector('.am-legend').innerHTML = `
      <span><i class="u3"></i><i class="u2"></i><i class="u1"></i> naik</span>
      <span><i class="f0"></i> datar</span>
      <span><i class="d1"></i><i class="d2"></i><i class="d3"></i> turun</span>
      <span class="dim">· warna = ${scale}${this.mode === 'smc' && !smc ? ' (memuat pemindaian…)' : ''}</span>
      <span><i class="f0 news"></i> berita 60 mnt</span><span><i class="f0 hot"></i> berita baru (3 mnt)</span><span><i class="f0 choch"></i> CHoCH baru ≤3 bar (1D)</span>`;
  }

  renderSide() {
    if (!this.visible) return;
    const rows = this.rows();
    const smc = this.smc[this.market]?.rows;
    const { recent } = this.newsIndex();
    const ch = rows.map((q) => q.changePct).filter((v) => v != null);
    const avg = ch.length ? ch.reduce((a, b) => a + b, 0) / ch.length : null;
    const smcRows = smc ? [...smc.values()] : [];
    const bull = smcRows.filter((s) => s.score >= 25).length, bear = smcRows.filter((s) => s.score <= -25).length;
    this.el.querySelector('.am-side-title').textContent = `SEKARANG · ${MARKETS[this.market].nav}`;
    this.el.querySelector('.am-stats').innerHTML = [
      ['RATA-RATA %', avg == null ? '–' : fmtPct(avg), avg > 0 ? 'up' : avg < 0 ? 'down' : ''],
      ['NAIK ≥ 2%', ch.filter((v) => v >= 2).length, 'up'], ['TURUN ≤ −2%', ch.filter((v) => v <= -2).length, 'down'],
      ['BIAS SMC BULLISH', smc ? bull : '…', 'up'], ['BIAS SMC BEARISH', smc ? bear : '…', 'down'],
      ['ASET BERBERITA', recent.size, 'info'],
    ].map(([l, v, c]) => `<div><span>${l}</span><b class="${c}">${v}</b></div>`).join('');
    this.el.querySelectorAll('.am-tabs button').forEach((b) => b.classList.toggle('active', b.dataset.l === this.list));
    let items;
    if (this.list === 'up') items = rows.filter((q) => q.changePct != null).sort((a, b) => b.changePct - a.changePct);
    else if (this.list === 'down') items = rows.filter((q) => q.changePct != null).sort((a, b) => a.changePct - b.changePct);
    else if (this.list === 'news') items = rows.filter((q) => recent.has(q.symbol)).sort((a, b) => recent.get(b.symbol).count - recent.get(a.symbol).count);
    else items = smc ? rows.filter((q) => { const s = smc.get(q.symbol); return s?.lastEvent?.kind === 'CHoCH' && s.lastEvent.barsAgo <= 3; }) : [];
    items = items.slice(0, 30);
    const max = Math.max(1, ...items.map((q) => Math.abs(q.changePct ?? 0)));
    const box = this.el.querySelector('.am-list');
    box.innerHTML = items.length ? items.map((q) => {
      const v = q.changePct ?? 0;
      const extra = this.list === 'news' ? `${recent.get(q.symbol).count} berita` : this.list === 'choch' ? `CHoCH ${smc.get(q.symbol).lastEvent.direction === 'bullish' ? '▲' : '▼'} ${smc.get(q.symbol).lastEvent.barsAgo} bar` : esc(q.block || '');
      return `<div class="am-row${this.highlighted === q.symbol ? ' hl' : ''}" data-s="${esc(q.symbol)}">
        <span class="am-sym"><b>${esc(q.symbol)}</b><small>${extra}</small></span>
        <span class="am-bar"><i class="${v >= 0 ? 'up' : 'down'}" style="width:${Math.max(3, (Math.abs(v) / max) * 100)}%"></i></span>
        <span class="r num">${fmtPrice(q.last, q.decimals)}</span>
        <span class="r num ${v > 0 ? 'up' : v < 0 ? 'down' : ''}">${fmtPct(q.changePct)}</span></div>`;
    }).join('') : `<div class="empty">${this.list === 'choch' && !smc ? 'Menunggu pemindaian SMC 1D seluruh aset…' : 'Belum ada aset di daftar ini.'}</div>`;
  }

  highlight(symbol) {
    if (this.highlighted === symbol) return;
    this.cells?.get(this.highlighted)?.classList.remove('hl');
    this.highlighted = symbol || null;
    const cell = this.cells?.get(symbol);
    cell?.classList.add('hl');
  }

  hoverSquare(e) {
    const sq = e.target.closest('[data-s]');
    if (!sq) { this.tip.style.display = 'none'; return; }
    const q = this.orbs.quotes.get(sq.dataset.s);
    if (!q) return;
    const s = this.smc[this.market]?.rows.get(q.symbol);
    const n = this.newsIndex().recent.get(q.symbol);
    const host = this.el.querySelector('.am-main').getBoundingClientRect();
    const r = sq.getBoundingClientRect();
    this.tip.innerHTML = `<b>${esc(q.symbol)}</b> <span class="dim">${esc(q.name || '')}</span><br>
      <span class="dim">${esc(q.block || '')}</span><br>
      open <span class="num">${fmtPrice(q.open, q.decimals)}</span> → <b class="num">${fmtPrice(q.last, q.decimals)}</b>
      <span class="${q.changePct > 0 ? 'up' : q.changePct < 0 ? 'down' : ''}">${fmtPct(q.changePct)}</span><br>
      ${s ? `SMC 1D: skor <b class="${s.score > 0 ? 'up' : s.score < 0 ? 'down' : ''}">${s.score > 0 ? '+' : ''}${Math.round(s.score)}</b> · swing ${esc(s.swingTrend)}${s.lastEvent ? ` · ${esc(s.lastEvent.kind)} ${s.lastEvent.direction === 'bullish' ? '▲' : '▼'} ${s.lastEvent.barsAgo} bar` : ''}<br>` : ''}
      ${n ? `berita 60 mnt: ${n.count} (▲${n.up} ▼${n.down})<br>` : ''}<span class="dim">klik untuk analisa</span>
      ${this.groupInfo?.get(sq.closest('.am-block')?.dataset.b) ? `<div class="am-tip-group">${this.groupInfo.get(sq.closest('.am-block').dataset.b)}</div>` : ''}`;
    this.tip.style.display = 'block';
    const x = r.left - host.left + r.width + 8, y = r.top - host.top;
    this.tip.style.left = `${Math.min(x, host.width - 270)}px`;
    this.tip.style.top = `${Math.min(Math.max(0, y), host.height - this.tip.offsetHeight - 4)}px`;
  }
}
