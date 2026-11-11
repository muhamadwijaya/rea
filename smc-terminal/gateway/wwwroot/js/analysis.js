// Analisa SMC view of one market page: market watchlist + SMC chart / TradingView live + bias, MTF,
// trade plan, narrative, key levels and symbol news.
import { api, esc, on, badge, dirHex, dirClass, fmtPct, fmtPrice, fmtScore, fmtTime, segmented, timeAgo, trendText, arrow, divBar, CLASS_LABEL } from './util.js';
import { SmcChart } from './smc-chart.js';
import { advancedChart, technicalGauge } from './tv.js';
import { rowsOf } from './markets.js';

const LAYERS = [
  ['swing', 'Struktur swing'], ['internal', 'Internal'], ['pivots', 'HH/HL/LH/LL'], ['ob', 'Order block'],
  ['fvg', 'FVG'], ['eq', 'EQH/EQL'], ['liquidity', 'Likuiditas'], ['zones', 'Premium/Discount'], ['ema', 'EMA 50/200'], ['plan', 'Trade plan'],
];
const LEGEND = [
  ['#2fd28c', 'bull'], ['#ff4d5e', 'bear'], ['#38d3f5', 'FVG bull'], ['#ff9f43', 'FVG bear'], ['#6b9bff', 'EMA50'], ['#f2c94c', 'EMA200 · likuiditas'],
];

function gauge(score, direction, id) {
  const s = Math.max(-100, Math.min(100, score));
  const angle = ((s + 100) / 200) * Math.PI;
  const x = 60 - Math.cos(angle) * 44, y = 58 - Math.sin(angle) * 44;
  return `<svg width="120" height="68" viewBox="0 0 120 68">
    <defs><linearGradient id="gg-${id}" x1="0" x2="1"><stop offset="0" stop-color="#ff4d5e"/><stop offset=".5" stop-color="#f2c94c"/><stop offset="1" stop-color="#2fd28c"/></linearGradient></defs>
    <path d="M10 58 A50 50 0 0 1 110 58" fill="none" stroke="url(#gg-${id})" stroke-width="7" stroke-opacity=".85"/>
    <line x1="60" y1="58" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" stroke="#e6ebf3" stroke-width="2"/>
    <circle cx="60" cy="58" r="4" fill="${dirHex(direction)}"/>
    <text x="60" y="44" text-anchor="middle" fill="#e6ebf3" font-size="13" font-weight="800" font-family="JetBrains Mono">${fmtScore(score)}</text>
  </svg>`;
}

export class AnalysisView {
  constructor(el, cfg, store) {
    this.el = el;
    this.cfg = cfg;
    this.store = store;
    this.current = { symbol: cfg.defaultSymbol, tf: cfg.defaultTf };
    this.data = null;
    this.view = 'smc';
    this.tvMounted = '';
    this.chart = null;
    this.controller = null;
    this.render();
    on('screener', () => this.renderWatch());
    setInterval(() => {
      if (this.visible() && this.data && !document.hidden) this.load(this.current.symbol, this.current.tf, { silent: true });
    }, 60_000);
  }

  $(sel) { return this.el.querySelector(sel); }
  visible() { return this.el.classList.contains('active') && this.el.closest('.tab-panel')?.classList.contains('active'); }

  render() {
    this.el.innerHTML = `
      <div class="an-wrap">
        <aside class="panel an-watch">
          <div class="panel-head"><span class="diamond">◆</span> WATCHLIST <span class="right live-label">LIVE</span></div>
          <form class="watch-form" autocomplete="off"><input class="input an-input" list="symbol-list" placeholder="simbol lain…"><button class="btn" type="submit">MUAT</button></form>
          <div class="watch-list"></div>
        </aside>
        <div class="an-center">
          <div class="toolbar">
            <div class="seg an-tf"><button data-v="15m">15M</button><button data-v="1h">1H</button><button data-v="4h">4H</button><button data-v="1d">1D</button><button data-v="1w">1W</button></div>
            <div class="seg an-view"><button data-v="smc" class="active">CHART SMC</button><button data-v="tv">TRADINGVIEW LIVE</button><button data-v="split">SPLIT</button></div>
            <span class="grow"></span><span class="time an-updated"></span>
          </div>
          <div class="toolbar an-toggles">${LAYERS.map(([k, label]) => `<label class="check"><input type="checkbox" data-layer="${k}" checked> ${label}</label>`).join('')}</div>
          <div class="grid an-main">
            <div class="panel chart-shell"><div class="chart-host"></div><div class="chart-legend"></div></div>
            <div class="panel tv-live hidden"></div>
          </div>
          <div class="legend-row">${LEGEND.map(([c, l]) => `<span class="check"><span class="sw" style="background:${c}"></span>${l}</span>`).join('')}</div>
        </div>
        <aside class="side an-side"><div class="panel"><div class="loading">Memuat analisa</div></div></aside>
      </div>`;
    this.tfSeg = segmented(this.$('.an-tf'), (v) => this.load(this.current.symbol, v));
    this.tfSeg.set(this.current.tf);
    segmented(this.$('.an-view'), (v) => this.setView(v));
    this.$('.watch-form').addEventListener('submit', (e) => {
      e.preventDefault();
      const v = this.$('.an-input').value.trim();
      if (v) this.load(v, this.current.tf);
    });
    this.$('.watch-list').addEventListener('click', (e) => {
      const row = e.target.closest('[data-sym]');
      if (row) this.load(row.dataset.sym, this.current.tf);
    });
    this.$('.an-toggles').addEventListener('change', (e) => {
      const k = e.target.dataset.layer;
      if (k) this.chart?.setLayers({ [k]: e.target.checked });
    });
    this.$('.an-side').addEventListener('click', (e) => {
      const row = e.target.closest('.mtf-row[data-tf]');
      if (row) this.load(this.current.symbol, row.dataset.tf);
    });
    this.renderWatch();
  }

  renderWatch() {
    const rows = rowsOf(this.store.screener, this.cfg.key);
    const active = this.data?.instrument.symbol || this.current.symbol;
    this.$('.watch-list').innerHTML = rows.length ? rows.map((r) => {
      const d = r.instrument.decimals;
      return `<div class="watch-row ${r.instrument.symbol === active ? 'active' : ''}" data-sym="${esc(r.instrument.symbol)}" title="${esc(r.instrument.name)}">
        <span class="w-sym"><span class="w-dot" style="background:${r.error ? 'var(--dim)' : dirHex(r.direction)}"></span>${esc(r.instrument.symbol)}</span>
        <span class="w-px num">${r.error ? '–' : fmtPrice(r.price, d)}</span>
        <span class="w-ch num ${r.changePct > 0 ? 'up' : r.changePct < 0 ? 'down' : ''}">${fmtPct(r.changePct)}</span>
      </div>`;
    }).join('') : '<div class="empty">Menunggu data screener…</div>';
  }

  ensureChart() {
    if (this.chart) return this.chart;
    try {
      this.chart = new SmcChart(this.$('.chart-host'), this.$('.chart-shell'));
    } catch (e) {
      this.$('.chart-host').innerHTML = `<div class="error-box" style="margin:20px">${esc(e.message)}</div>`;
    }
    return this.chart;
  }

  setView(v) {
    this.view = v;
    this.$('.an-main').classList.toggle('view-split', v === 'split');
    this.$('.chart-shell').classList.toggle('hidden', v === 'tv');
    this.$('.tv-live').classList.toggle('hidden', v === 'smc');
    this.$('.an-toggles').classList.toggle('hidden', v === 'tv');
    if (v !== 'smc') this.mountTv();
    this.chart?.requestDraw();
  }

  mountTv() {
    if (!this.data || this.view === 'smc') return;
    const key = `${this.data.instrument.tv}|${this.current.tf}`;
    if (this.tvMounted === key) return;
    this.tvMounted = key;
    advancedChart(this.$('.tv-live'), this.data.instrument.tv, this.current.tf);
  }

  async load(symbol, tf, { silent = false } = {}) {
    this.current = { symbol, tf };
    this.tfSeg.set(tf);
    this.$('.an-input').value = '';
    this.controller?.abort();
    this.controller = new AbortController();
    if (!silent) {
      this.$('.an-side').innerHTML = '<div class="panel"><div class="loading">Menganalisa struktur pasar</div></div>';
      this.$('.an-updated').textContent = '';
    }
    try {
      const d = await api(`/api/analyze?symbol=${encodeURIComponent(symbol)}&tf=${tf}`, { signal: this.controller.signal });
      const sameView = silent && this.data && this.data.instrument.symbol === d.instrument.symbol && this.data.tf === d.tf;
      this.data = d;
      this.current.symbol = d.instrument.symbol;
      this.ensureChart()?.setData(d, { keepRange: sameView });
      this.renderLegend(d);
      if (!silent || this.$('.an-side .badge')?.textContent !== d.bias.label) this.renderSide(d);
      else {
        const p = this.$('.an-head .p');
        if (p) p.textContent = fmtPrice(d.price, d.instrument.decimals);
      }
      this.renderWatch();
      this.mountTv();
      this.$('.an-updated').textContent = `analisa ${fmtTime(d.generatedAt)} · data ${timeAgo(d.fetchedAt)}`;
      const hash = `#${this.cfg.key}/analisa/${encodeURIComponent(d.instrument.symbol)}/${tf}`;
      if (this.visible() && location.hash !== hash) history.replaceState(null, '', hash);
    } catch (e) {
      if (e.name === 'AbortError') return;
      if (!silent) {
        this.$('.an-side').innerHTML = `<div class="panel"><div class="panel-body"><div class="error-box"><b>Analisa gagal.</b><br>${esc(e.message)}</div>
          <div class="note">Contoh simbol: EURUSD, XAUUSD, AAPL, NVDA, BBCA, TLKM, IHSG, BTCUSD, SOLUSD, IDX:ANTM, NYSE:KO.</div></div></div>`;
      }
    }
  }

  renderLegend(d) {
    const last = d.candles[d.candles.length - 1];
    const prev = d.candles[d.candles.length - 2];
    const chg = prev ? ((last.c / prev.c) - 1) * 100 : null;
    const dec = d.instrument.decimals;
    this.$('.chart-legend').innerHTML = `<span><b>${esc(d.instrument.symbol)}</b> · ${esc(d.tfLabel)}</span>
      <span>O <span class="num">${fmtPrice(last.o, dec)}</span> H <span class="num">${fmtPrice(last.h, dec)}</span> L <span class="num">${fmtPrice(last.l, dec)}</span> C <span class="num">${fmtPrice(last.c, dec)}</span> <span class="${chg > 0 ? 'up' : chg < 0 ? 'down' : ''}">${fmtPct(chg)}</span></span>
      <span>swing ${trendText(d.smc.swingTrend)} · internal ${trendText(d.smc.internalTrend)}</span>`;
  }

  renderSide(d) {
    const inst = d.instrument;
    const fmt = (v) => fmtPrice(v, inst.decimals);
    const b = d.bias;
    const mtf = d.mtf;
    const plan = d.plan;
    const z = d.smc.zones;
    const liq = d.smc.liquidity;
    const nearestOb = (dir) => d.smc.orderBlocks.filter((o) => o.active && o.direction === dir)
      .sort((a, c) => Math.abs((a.top + a.bottom) / 2 - d.price) - Math.abs((c.top + c.bottom) / 2 - d.price))[0];
    const obBull = nearestOb('bullish'), obBear = nearestOb('bearish');

    const planHtml = !plan
      ? '<div class="note">Bias netral — tidak ada skenario. Tunggu CHoCH/BOS yang jelas.</div>'
      : plan.status === 'wait'
        ? `<div class="note">${esc(plan.note)}</div>`
        : `<div style="display:flex;gap:8px;align-items:center"><span class="tag ${dirClass(plan.direction)}">${plan.direction === 'bullish' ? 'BUY' : 'SELL'} · ${plan.status === 'active' ? 'HARGA DI ZONA' : 'MENUNGGU'}</span><span class="muted">${esc(plan.source)}</span></div>
          <div class="plan-grid">
            <div><div class="k">ZONA</div><div class="v">${fmt(plan.zone.bottom)} – ${fmt(plan.zone.top)}</div></div>
            <div><div class="k">ENTRY</div><div class="v accent">${fmt(plan.entry)}</div></div>
            <div><div class="k">STOP / INVALIDASI</div><div class="v down">${fmt(plan.stop)}</div></div>
            <div><div class="k">TARGET</div><div class="v up">${plan.targets.map(fmt).join(' · ') || '–'}</div></div>
            <div><div class="k">RISK : REWARD</div><div class="v">${plan.rr.map((r) => `1:${r}`).join(' · ') || '–'}</div></div>
            <div><div class="k">ATR</div><div class="v">${fmt(d.indicators.atr)}</div></div>
          </div><div class="note">${esc(plan.note)}</div>`;

    this.$('.an-side').innerHTML = `
      <div class="panel"><div class="panel-body">
        <div class="an-head"><span class="s">${esc(inst.symbol)}</span><span class="cls">${CLASS_LABEL[inst.assetClass] || ''}</span><span class="p num">${fmt(d.price)}</span></div>
        <div class="muted" style="font-size:11.5px">${esc(inst.name)} · TF ${esc(d.tfLabel)} · bar terakhir ${fmtTime(d.asOf, true)} · ${esc(inst.tv)}</div>
      </div></div>

      <div class="panel"><div class="panel-head"><span class="diamond">◆</span> BIAS ${esc(d.tfLabel)}<span class="right">${badge(b.direction, b.label)}</span></div>
        <div class="panel-body">
          <div class="gauge-wrap">${gauge(b.score, b.direction, this.cfg.key)}<div><div class="gauge-label" style="color:${dirHex(b.direction)}">${arrow(b.direction)} ${esc(b.label)}</div><div class="muted" style="font-size:11px">skor −100 (turun kuat) … +100 (naik kuat)</div></div></div>
          ${b.components.map((c) => `<div class="comp"><span class="n">${esc(c.name)}<small>${esc(c.note)}</small></span>${divBar(c.value, c.max)}<span class="v" style="color:${c.value > 0 ? 'var(--bull)' : c.value < 0 ? 'var(--bear)' : 'var(--dim)'}">${fmtScore(c.value)}</span></div>`).join('')}
        </div></div>

      ${mtf ? `<div class="panel"><div class="panel-head"><span class="diamond">◆</span> MULTI-TIMEFRAME<span class="right">${badge(mtf.direction, mtf.label)}</span></div>
        <div class="panel-body">
          ${mtf.timeframes.map((t) => t.error ? `<div class="mtf-row"><b>${esc(t.tf.toUpperCase())}</b><span class="down" style="grid-column: span 3">${esc(t.error)}</span></div>` : `
            <div class="mtf-row" data-tf="${esc(t.tf)}" style="cursor:pointer"><b>${esc(t.tf.toUpperCase())}</b><span>${trendText(t.swingTrend)} <span class="dim">swing</span></span><span>${trendText(t.internalTrend)} <span class="dim">int</span></span><span class="num" style="text-align:right;color:${dirHex(t.direction)}">${fmtScore(t.score)}</span></div>`).join('')}
          <div class="note">${mtf.aligned ? '✔ ' : '⚠ '}${esc(mtf.note)} Skor gabungan ${fmtScore(mtf.score)} (bobot 1D 45% · 4H 35% · 1H 20%).</div>
        </div></div>` : ''}

      <div class="panel"><div class="panel-head"><span class="diamond">◆</span> SKENARIO TRADING<span class="right sub">otomatis · bukan saran</span></div><div class="panel-body">${planHtml}</div></div>

      <div class="panel"><div class="panel-head"><span class="diamond">◆</span> NARASI ANALISA</div><div class="panel-body"><ul class="narr">${d.narrative.map((n) => `<li>${esc(n)}</li>`).join('')}</ul></div></div>

      <div class="panel"><div class="panel-head"><span class="diamond">◆</span> LEVEL KUNCI</div><div class="panel-body levels">
        ${(liq.above || []).slice(0, 2).reverse().map((l) => `<div><span class="flat">BSL · ${esc(l.kind)}</span><b class="num">${fmt(l.price)}</b></div>`).join('')}
        ${z ? `<div><span class="down">Range high (${z.strong === 'high' ? 'strong' : 'weak'})</span><b class="num">${fmt(z.top)}</b></div>
               <div><span class="muted">Equilibrium</span><b class="num">${fmt(z.equilibrium)}</b></div>
               <div><span class="up">Range low (${z.strong === 'low' ? 'strong' : 'weak'})</span><b class="num">${fmt(z.bottom)}</b></div>` : ''}
        ${obBear ? `<div><span class="down">OB bearish ${esc(obBear.scope)}</span><b class="num">${fmt(obBear.bottom)} – ${fmt(obBear.top)}</b></div>` : ''}
        ${obBull ? `<div><span class="up">OB bullish ${esc(obBull.scope)}</span><b class="num">${fmt(obBull.bottom)} – ${fmt(obBull.top)}</b></div>` : ''}
        ${(liq.below || []).slice(0, 2).map((l) => `<div><span class="flat">SSL · ${esc(l.kind)}</span><b class="num">${fmt(l.price)}</b></div>`).join('')}
        <div><span class="muted">EMA50 / EMA200</span><b class="num">${fmt(d.indicators.ema50)} / ${fmt(d.indicators.ema200)}</b></div>
        <div><span class="muted">RSI(14)</span><b class="num">${d.indicators.rsi?.toFixed(1) ?? '–'}</b></div>
      </div></div>

      <div class="panel"><div class="panel-head"><span class="diamond">◆</span> TEKNIKAL TRADINGVIEW<span class="right sub">realtime</span></div><div style="height:420px" class="tv-gauge"></div></div>

      <div class="panel"><div class="panel-head"><span class="diamond">◆</span> BERITA ${esc(inst.symbol)}<span class="right live-label">LIVE</span></div><div class="panel-body sym-news"><div class="loading">Memuat berita</div></div></div>
      <div class="note">${esc(d.source)}. Chart SMC diperbarui tiap 60 detik; mode TRADINGVIEW LIVE / SPLIT untuk harga tick realtime.</div>`;

    technicalGauge(this.$('.tv-gauge'), inst.tv, this.current.tf);
    this.loadSymbolNews(inst.symbol);
  }

  async loadSymbolNews(symbol) {
    const target = () => this.$('.sym-news');
    try {
      const res = await api(`/api/symbol-news?symbol=${encodeURIComponent(symbol)}`);
      if (!target()) return;
      target().innerHTML = res.items.length ? res.items.slice(0, 12).map((n) => `
        <div class="news-mini"><div class="m"><span class="tag ${dirClass(n.sentimentLabel)}">${esc((n.sentimentLabel || 'neutral').toUpperCase())}</span>${esc(n.source)} · ${timeAgo(n.published)}</div>
        <a href="${esc(n.link)}" target="_blank" rel="noopener noreferrer">${esc(n.title)}</a></div>`).join('') : '<div class="empty">Tidak ada berita terbaru.</div>';
    } catch (e) {
      if (target()) target().innerHTML = `<div class="empty">${esc(e.message)}</div>`;
    }
  }

  show(params = []) {
    const symbol = params[0] || this.current.symbol;
    const tf = params[1] || this.current.tf;
    if (!this.data || symbol.toUpperCase() !== this.data.instrument.symbol || tf !== this.data.tf) this.load(symbol, tf);
    else this.chart?.requestDraw();
  }
}
