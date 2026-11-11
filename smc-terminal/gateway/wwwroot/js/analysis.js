// Analisa SMC: SMC chart (engine) + TradingView live chart + bias, MTF, trade plan, narrative and symbol news.
import { $, api, esc, badge, dirHex, dirClass, fmtPct, fmtPrice, fmtScore, fmtTime, segmented, timeAgo, trendText, arrow, divBar, CLASS_LABEL } from './util.js';
import { SmcChart } from './smc-chart.js';
import { advancedChart, technicalGauge } from './tv.js';

const LAYERS = [
  ['swing', 'Struktur swing'], ['internal', 'Struktur internal'], ['pivots', 'HH/HL/LH/LL'], ['ob', 'Order block'],
  ['fvg', 'FVG'], ['eq', 'EQH/EQL'], ['liquidity', 'Likuiditas'], ['zones', 'Premium/Discount'], ['ema', 'EMA 50/200'], ['plan', 'Trade plan'],
];
const LEGEND = [
  ['#2fd28c', 'OB/BOS bull'], ['#ff4d5e', 'OB/BOS bear'], ['#38d3f5', 'FVG bull'], ['#ff9f43', 'FVG bear'], ['#6b9bff', 'EMA50'], ['#f2c94c', 'EMA200 · likuiditas'],
];

let chart = null;
let current = { symbol: 'EURUSD', tf: '1h' };
let data = null;
let view = 'smc';
let tvMounted = '';
let controller = null;
let refreshTimer = null;
let tfSeg;

function ensureChart() {
  if (chart) return chart;
  try {
    chart = new SmcChart($('#smc-chart'), $('#smc-shell'));
  } catch (e) {
    $('#smc-chart').innerHTML = `<div class="error-box" style="margin:20px">${esc(e.message)}</div>`;
  }
  return chart;
}

function renderToggles() {
  $('#an-toggles').innerHTML = LAYERS.map(([k, label]) =>
    `<label class="check"><input type="checkbox" data-layer="${k}" checked> ${label}</label>`).join('') +
    `<span class="grow"></span>${LEGEND.map(([c, l]) => `<span class="check"><span style="width:10px;height:10px;background:${c};display:inline-block;border-radius:1px"></span>${l}</span>`).join('')}`;
}

function setView(v) {
  view = v;
  const main = $('#an-main');
  main.classList.toggle('view-split', v === 'split');
  $('#smc-shell').classList.toggle('hidden', v === 'tv');
  $('#tv-live').classList.toggle('hidden', v === 'smc');
  $('#an-toggles').classList.toggle('hidden', v === 'tv');
  if (v !== 'smc') mountTv();
  chart?.requestDraw();
}

function mountTv() {
  if (!data || view === 'smc') return;
  const key = `${data.instrument.tv}|${current.tf}`;
  if (tvMounted === key) return;
  tvMounted = key;
  advancedChart($('#tv-live'), data.instrument.tv, current.tf);
}

function gauge(score, direction) {
  const s = Math.max(-100, Math.min(100, score));
  const angle = ((s + 100) / 200) * Math.PI;
  const x = 60 - Math.cos(angle) * 44, y = 58 - Math.sin(angle) * 44;
  return `<svg width="120" height="68" viewBox="0 0 120 68">
    <defs><linearGradient id="gg" x1="0" x2="1"><stop offset="0" stop-color="#ff4d5e"/><stop offset=".5" stop-color="#f2c94c"/><stop offset="1" stop-color="#2fd28c"/></linearGradient></defs>
    <path d="M10 58 A50 50 0 0 1 110 58" fill="none" stroke="url(#gg)" stroke-width="7" stroke-opacity=".85"/>
    <line x1="60" y1="58" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" stroke="#e6ebf3" stroke-width="2"/>
    <circle cx="60" cy="58" r="4" fill="${dirHex(direction)}"/>
    <text x="60" y="44" text-anchor="middle" fill="#e6ebf3" font-size="13" font-weight="800" font-family="JetBrains Mono">${fmtScore(score)}</text>
  </svg>`;
}

function renderSide(d) {
  const inst = d.instrument;
  const dec = inst.decimals;
  const b = d.bias;
  const fmt = (v) => fmtPrice(v, dec);
  const mtf = d.mtf;
  const plan = d.plan;
  const z = d.smc.zones;
  const liq = d.smc.liquidity;
  const activeOb = d.smc.orderBlocks.filter((o) => o.active);
  const nearestOb = (dir) => activeOb.filter((o) => o.direction === dir)
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

  $('#an-side').innerHTML = `
    <div class="panel"><div class="panel-body">
      <div class="an-head"><span class="s">${esc(inst.symbol)}</span><span class="cls">${CLASS_LABEL[inst.assetClass] || ''}</span><span class="p num">${fmt(d.price)}</span></div>
      <div class="muted" style="font-size:11.5px">${esc(inst.name)} · TF ${esc(d.tfLabel)} · bar terakhir ${fmtTime(d.asOf, true)} · <a href="https://www.tradingview.com/symbols/${esc(inst.tv.replace(':', '-'))}/" target="_blank" rel="noopener noreferrer">${esc(inst.tv)} ↗</a></div>
    </div></div>

    <div class="panel"><div class="panel-head"><span class="diamond">◆</span> BIAS ${esc(d.tfLabel)}<span class="right">${badge(b.direction, b.label)}</span></div>
      <div class="panel-body">
        <div class="gauge-wrap">${gauge(b.score, b.direction)}<div><div class="gauge-label" style="color:${dirHex(b.direction)}">${arrow(b.direction)} ${esc(b.label)}</div><div class="muted" style="font-size:11px">skor −100 (turun kuat) … +100 (naik kuat)</div></div></div>
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

    <div class="panel"><div class="panel-head"><span class="diamond">◆</span> TEKNIKAL TRADINGVIEW<span class="right sub">realtime</span></div><div style="height:420px" id="tv-gauge"></div></div>

    <div class="panel"><div class="panel-head"><span class="diamond">◆</span> BERITA ${esc(inst.symbol)}<span class="right live-label">LIVE</span></div><div class="panel-body" id="sym-news"><div class="loading">Memuat berita</div></div></div>
    <div class="note">${esc(d.source)}. Chart SMC diperbarui tiap 60 detik; gunakan mode TRADINGVIEW LIVE / SPLIT untuk harga tick realtime.</div>`;

  technicalGauge($('#tv-gauge'), inst.tv, current.tf);
  loadSymbolNews(inst.symbol);
}

async function loadSymbolNews(symbol) {
  try {
    const res = await api(`/api/symbol-news?symbol=${encodeURIComponent(symbol)}`);
    const el = $('#sym-news');
    if (!el) return;
    el.innerHTML = res.items.length ? res.items.slice(0, 12).map((n) => `
      <div class="news-mini"><div class="m"><span class="tag ${dirClass(n.sentimentLabel)}">${esc((n.sentimentLabel || 'neutral').toUpperCase())}</span>${esc(n.source)} · ${timeAgo(n.published)}</div>
      <a href="${esc(n.link)}" target="_blank" rel="noopener noreferrer">${esc(n.title)}</a></div>`).join('') : '<div class="empty">Tidak ada berita terbaru.</div>';
  } catch (e) {
    const el = $('#sym-news');
    if (el) el.innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

function renderLegend(d) {
  const last = d.candles[d.candles.length - 1];
  const prev = d.candles[d.candles.length - 2];
  const chg = prev ? ((last.c / prev.c) - 1) * 100 : null;
  const dec = d.instrument.decimals;
  $('#smc-legend').innerHTML = `<span><b>${esc(d.instrument.symbol)}</b> · ${esc(d.tfLabel)}</span>
    <span>O <span class="num">${fmtPrice(last.o, dec)}</span> H <span class="num">${fmtPrice(last.h, dec)}</span> L <span class="num">${fmtPrice(last.l, dec)}</span> C <span class="num">${fmtPrice(last.c, dec)}</span> <span class="${chg > 0 ? 'up' : chg < 0 ? 'down' : ''}">${fmtPct(chg)}</span></span>
    <span>swing ${trendText(d.smc.swingTrend)} · internal ${trendText(d.smc.internalTrend)}</span>`;
}

async function load(symbol, tf, { silent = false } = {}) {
  current = { symbol, tf };
  tfSeg?.set(tf);
  $('#an-input').value = symbol;
  controller?.abort();
  controller = new AbortController();
  if (!silent) {
    $('#an-side').innerHTML = '<div class="panel"><div class="loading">Menganalisa struktur pasar</div></div>';
    $('#an-updated').textContent = '';
  }
  try {
    const d = await api(`/api/analyze?symbol=${encodeURIComponent(symbol)}&tf=${tf}`, { signal: controller.signal });
    const sameView = silent && data && data.instrument.symbol === d.instrument.symbol && data.tf === d.tf;
    data = d;
    current.symbol = d.instrument.symbol;
    $('#an-input').value = d.instrument.symbol;
    ensureChart()?.setData(d, { keepRange: sameView });
    renderLegend(d);
    if (!silent) renderSide(d);
    else refreshSideHeader(d);
    mountTv();
    $('#an-updated').textContent = `analisa ${fmtTime(d.generatedAt)} · data ${timeAgo(d.fetchedAt)}`;
    if (location.hash !== `#analisa/${encodeURIComponent(d.instrument.symbol)}/${tf}`) {
      history.replaceState(null, '', `#analisa/${encodeURIComponent(d.instrument.symbol)}/${tf}`);
    }
  } catch (e) {
    if (e.name === 'AbortError') return;
    if (!silent) {
      $('#an-side').innerHTML = `<div class="panel"><div class="panel-body"><div class="error-box"><b>Analisa gagal.</b><br>${esc(e.message)}</div>
        <div class="note">Contoh simbol valid: EURUSD, GBPJPY, XAUUSD, AAPL, NVDA, BBCA, TLKM, IHSG, BTCUSD, IDX:ANTM, NYSE:KO.</div></div></div>`;
    }
  }
}

function refreshSideHeader(d) {
  // Silent refresh re-renders the whole side panel only when the bias label changed, to keep widgets stable.
  const label = $('#an-side .badge');
  if (!label || label.textContent !== d.bias.label) renderSide(d);
  else {
    const p = $('#an-side .an-head .p');
    if (p) p.textContent = fmtPrice(d.price, d.instrument.decimals);
  }
}

export default {
  init() {
    renderToggles();
    tfSeg = segmented($('#an-tf'), (v) => load(current.symbol, v));
    segmented($('#an-view'), setView);
    $('#an-form').addEventListener('submit', (e) => {
      e.preventDefault();
      const v = $('#an-input').value.trim();
      if (v) load(v, current.tf);
    });
    $('#an-toggles').addEventListener('change', (e) => {
      const k = e.target.dataset.layer;
      if (k) chart?.setLayers({ [k]: e.target.checked });
    });
    $('#an-side').addEventListener('click', (e) => {
      const row = e.target.closest('.mtf-row[data-tf]');
      if (row) load(current.symbol, row.dataset.tf);
    });
    refreshTimer = setInterval(() => {
      if ($('#tab-analisa').classList.contains('active') && data && !document.hidden) load(current.symbol, current.tf, { silent: true });
    }, 60_000);
  },
  show(params) {
    const symbol = params[0] || current.symbol;
    const tf = params[1] || current.tf;
    if (!data || symbol.toUpperCase() !== data.instrument.symbol || tf !== data.tf) load(symbol, tf);
    else chart?.requestDraw();
  },
};
