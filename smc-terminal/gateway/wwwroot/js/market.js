// A market page (forex / saham-as / saham-idx / kripto) with its own sub-tabs and state.
import { $, esc, on, dirHex, fmtScore, arrow } from './util.js';
import { MARKETS, rowsOf } from './markets.js';
import { AnalysisView } from './analysis.js';
import { ScreenerView } from './screener.js';
import { NewsView } from './news.js';
import { OverviewView } from './overview.js';

const SUBS = [['analisa', 'ANALISA SMC'], ['screener', 'SCREENER'], ['berita', 'BERITA'], ['arah', 'ARAH MARKET']];
const VIEWS = { analisa: AnalysisView, screener: ScreenerView, berita: NewsView, arah: OverviewView };

export function marketPage(key) {
  const cfg = MARKETS[key];
  let root;
  let store;
  const views = {};
  let current = 'analisa';

  function header() {
    const rows = rowsOf(store.screener, key).filter((r) => !r.error);
    const bull = rows.filter((r) => r.direction?.includes('bullish')).length;
    const bear = rows.filter((r) => r.direction?.includes('bearish')).length;
    const avg = rows.length ? rows.reduce((s, r) => s + r.score, 0) / rows.length : 0;
    const outlook = (store.direction?.outlooks || []).find((o) => o.key === cfg.outlooks[0]);
    const news = store.stats?.newsByMarket?.[key];
    const choch = rows.filter((r) => r.lastEvent?.kind === 'CHoCH' && r.lastEvent.barsAgo <= 6).length;
    root.querySelector('.mkt-stats').innerHTML = `
      ${outlook ? `<div class="mkt-stat"><span>ARAH ${esc(outlook.title.toUpperCase())}</span><b style="color:${dirHex(outlook.direction)}">${arrow(outlook.direction)} ${esc(outlook.label)}</b></div>` : ''}
      <div class="mkt-stat"><span>BREADTH SMC</span><b><span class="up">▲${bull}</span> <span class="down">▼${bear}</span> <span class="dim">/ ${rows.length}</span></b></div>
      <div class="mkt-stat"><span>SKOR RATA-RATA</span><b style="color:${dirHex(avg > 10 ? 'bull' : avg < -10 ? 'bear' : '')}">${fmtScore(avg)}</b></div>
      <div class="mkt-stat"><span>CHoCH BARU</span><b class="flat">${choch}</b></div>
      <div class="mkt-stat"><span>BERITA 24 JAM</span><b class="accent">${news ?? '–'}</b></div>`;
  }

  function activate(sub, params) {
    current = VIEWS[sub] ? sub : 'analisa';
    root.querySelectorAll('.subtab').forEach((b) => b.classList.toggle('active', b.dataset.sub === current));
    root.querySelectorAll('.sub-panel').forEach((p) => p.classList.toggle('active', p.dataset.sub === current));
    if (!views[current]) {
      views[current] = new VIEWS[current](root.querySelector(`.sub-panel[data-sub="${current}"]`), cfg, store);
    }
    views[current].show?.(params);
  }

  return {
    init(s) {
      store = s;
      root = $(`#tab-${key}`);
      root.innerHTML = `
        <div class="mkt-head">
          <div class="mkt-title"><h1>${esc(cfg.title)}</h1><span class="muted">${esc(cfg.desc)}</span></div>
          <nav class="subtabs">${SUBS.map(([k, l]) => `<button class="subtab" data-sub="${k}">${l}</button>`).join('')}</nav>
          <div class="mkt-stats"></div>
        </div>
        ${SUBS.map(([k]) => `<div class="sub-panel" data-sub="${k}"></div>`).join('')}`;
      root.querySelector('.subtabs').addEventListener('click', (e) => {
        const b = e.target.closest('[data-sub]');
        if (b) window.SMC.go(key, b.dataset.sub);
      });
      on('screener', header);
      on('stats', header);
      on('direction', header);
      header();
    },
    show(params) {
      const [sub, ...rest] = params;
      activate(sub || current, rest);
    },
  };
}
