// Arah Market view of one market page: breadth, outlooks, regime, currency strength (forex),
// key instruments, movers, structure events, high-impact events, news sentiment and TradingView maps.
import { api, esc, on, badge, dirHex, dirClass, fmtPct, fmtPrice, fmtScore, fmtTime, segmented, arrow, divBar, timeAgo } from './util.js';
import { widgetMap } from './tv.js';
import { rowsOf } from './markets.js';

const CAT_NAME = { forex: 'Forex', us: 'Saham AS', idx: 'Saham IDX', commodity: 'Komoditas', macro: 'Makro', crypto: 'Kripto' };
const ASSET_LABEL = { US_EQ: 'Saham AS', IDX: 'IDX', CRYPTO: 'Kripto', GOLD: 'Emas', OIL: 'Minyak' };

export function sentCard(c) {
  const total = c.count || 0;
  const pct = (n) => (total ? (n / total) * 100 : 0);
  const color = c.score > 0.08 ? 'var(--bull)' : c.score < -0.08 ? 'var(--bear)' : 'var(--neu)';
  return `<div class="sent-card"><div class="t">${esc((CAT_NAME[c.key] || c.key).toUpperCase())}</div>
    <div class="v" style="color:${color}">${c.score > 0 ? '+' : ''}${(c.score * 100).toFixed(0)}</div>
    <div class="dim" style="font-size:10.5px">${total} berita · ▲${c.bullish} ▼${c.bearish}</div>
    <div class="stack"><i style="width:${pct(c.bullish)}%;background:var(--bull)"></i><i style="width:${pct(c.neutral)}%;background:var(--line-2)"></i><i style="width:${pct(c.bearish)}%;background:var(--bear)"></i></div></div>`;
}

const regimeColor = (r) => (r === 'RISK-ON' ? 'var(--bull)' : r === 'RISK-OFF' ? 'var(--bear)' : 'var(--neu)');

function directionOfScore(score) {
  if (score >= 50) return ['strong_bullish', 'NAIK KUAT'];
  if (score >= 20) return ['bullish', 'NAIK'];
  if (score > -20) return ['neutral', 'SIDEWAYS'];
  if (score > -50) return ['bearish', 'TURUN'];
  return ['strong_bearish', 'TURUN KUAT'];
}

export class OverviewView {
  constructor(el, cfg, store) {
    this.el = el;
    this.cfg = cfg;
    this.store = store;
    this.sentiment = null;
    el.innerHTML = `
      <div class="ov-content"><div class="loading">Menghitung arah market</div></div>
      <div class="section-title"><h2>Peta Pasar Realtime · ${esc(cfg.nav)}</h2><span class="sub">widget TradingView</span></div>
      ${cfg.widgets.length > 1 ? `<div class="toolbar"><div class="seg ov-widgets">${cfg.widgets.map(([k, l], i) => `<button data-v="${k}" class="${i ? '' : 'active'}">${l}</button>`).join('')}</div></div>` : ''}
      <div class="panel tv-box ov-widget"></div>`;
    const seg = el.querySelector('.ov-widgets');
    if (seg) segmented(seg, (v) => widgetMap(el.querySelector('.ov-widget'), v));
    widgetMap(el.querySelector('.ov-widget'), cfg.widgets[0][0]);
    el.addEventListener('click', (e) => {
      const t = e.target.closest('[data-sym]');
      if (t) window.SMC.openAnalysis(t.dataset.sym, t.dataset.tf || '4h');
    });
    const rerender = () => { if (this.visible()) this.render(); };
    on('direction', rerender);
    on('screener', rerender);
    this.loadSentiment();
  }

  visible() { return this.el.classList.contains('active') && this.el.closest('.tab-panel')?.classList.contains('active'); }

  async loadSentiment() {
    try {
      this.sentiment = await api(`/api/news/sentiment?hours=12&market=${encodeURIComponent(this.cfg.key)}`);
    } catch { /* optional */ }
    this.render();
  }

  render() {
    const d = this.store.direction;
    const rows = rowsOf(this.store.screener, this.cfg.key).filter((r) => !r.error);
    const el = this.el.querySelector('.ov-content');
    if (!rows.length && !d) return;

    const bull = rows.filter((r) => r.direction?.includes('bullish')).length;
    const bear = rows.filter((r) => r.direction?.includes('bearish')).length;
    const avg = rows.length ? rows.reduce((s, r) => s + r.score, 0) / rows.length : 0;
    const discount = rows.filter((r) => r.zone?.area === 'discount').length;
    const [avgDir, avgLabel] = directionOfScore(avg);
    const outlooks = (d?.outlooks || []).filter((o) => this.cfg.outlooks.includes(o.key))
      .sort((a, b) => this.cfg.outlooks.indexOf(a.key) - this.cfg.outlooks.indexOf(b.key));
    const instruments = (d?.instruments || []).filter((i) => i.market === this.cfg.key);
    const movers = [...rows].filter((r) => r.changePct != null).sort((a, b) => b.changePct - a.changePct);
    const gainers = movers.slice(0, 5), losers = movers.slice(-5).reverse();
    const events = rows.filter((r) => r.lastEvent && r.lastEvent.barsAgo <= 6).sort((a, b) => b.lastEvent.t - a.lastEvent.t).slice(0, 8);
    const highImpact = (d?.events || []).filter((e) => !this.cfg.eventCurrencies || this.cfg.eventCurrencies.includes(e.currency));
    const reg = d?.regime;
    const strength = d?.currencyStrength || [];
    const maxAbs = Math.max(30, ...strength.map((s) => Math.abs(s.score)));

    el.innerHTML = `
      <div class="dir-hero">
        <div class="panel regime-big">
          <div>
            <div class="eyebrow">ARAH ${esc(this.cfg.nav)} · BREADTH SMC 4H</div>
            <div class="r" style="color:${dirHex(avgDir)}">${arrow(avgDir)} ${esc(avgLabel)}</div>
            <div class="h">Skor rata-rata ${fmtScore(avg)} dari ${rows.length} instrumen · <span class="up">▲${bull} bullish</span> · <span class="down">▼${bear} bearish</span> · ${discount} di zona discount</div>
            ${reg ? `<div class="t">Regime global: <b style="color:${regimeColor(reg.regime)}">${esc(reg.regime)}</b> — ${esc(reg.text)}</div>` : ''}
            ${d ? `<div class="t">Diperbarui ${d.updatedAt ? timeAgo(d.updatedAt) : '–'}. ${esc(d.method)}</div>` : ''}
          </div>
        </div>
        <div class="panel">
          <div class="panel-head"><span class="diamond">◆</span> SENTIMEN BERITA · 12 JAM <span class="right sub">${this.sentiment?.total ?? 0} headline</span></div>
          <div class="panel-body"><div class="sent-bars">${this.sentiment ? [sentCard({ ...this.sentiment.overall, key: 'TOTAL' }), ...this.sentiment.assets.slice(0, 5).map((a) => sentCard({ ...a, key: ASSET_LABEL[a.key] || a.key }))].join('') : '<div class="empty">memuat…</div>'}</div></div>
        </div>
      </div>

      ${outlooks.length ? `<div class="section-title"><h2>Ke Mana Arah ${esc(this.cfg.title)}?</h2><span class="sub">bias SMC harian + 4H · klik kartu untuk analisa</span></div>
      <div class="outlooks">${outlooks.map((o) => `
        <div class="panel outlook" data-sym="${esc(o.symbols[0])}">
          <div class="glow" style="background: radial-gradient(circle at 90% 0%, ${dirHex(o.direction)}22, transparent 60%)"></div>
          <div class="t"><span>${esc(o.title.toUpperCase())}</span><span class="tag ${dirClass(o.direction)}">KEYAKINAN ${esc(o.confidence.toUpperCase())}</span></div>
          <div class="arrow" style="color:${dirHex(o.direction)}">${arrow(o.direction)}</div>
          <div class="l" style="color:${dirHex(o.direction)}">${esc(o.label)}</div>
          <div class="muted" style="font-size:11px">skor ${fmtScore(o.score)}</div>
          <ul>${o.drivers.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>
        </div>`).join('')}</div>` : ''}

      <div class="two" style="margin-top:14px">
        ${this.cfg.strength && strength.length ? `<div class="panel">
          <div class="panel-head"><span class="diamond">◆</span> KEKUATAN MATA UANG<span class="sub">rata-rata skor SMC semua pair G8</span></div>
          <div class="panel-body">
            ${strength.map((s) => `<div class="strength-row"><b>${esc(s.currency)}</b>${divBar(s.score, maxAbs)}<span class="num" style="text-align:right;color:${s.score > 0 ? 'var(--bull)' : 'var(--bear)'}">${fmtScore(s.score)}</span><span class="num dim" style="text-align:right">${fmtPct(s.changePct)}</span></div>`).join('')}
            ${d.bestPair ? `<div class="note" style="font-size:12px">Divergensi terbesar: <b class="accent">${esc(d.bestPair.pair)}</b> cenderung <b style="color:${dirHex(d.bestPair.direction)}">${esc(d.bestPair.direction.toUpperCase())}</b> (selisih ${fmtScore(d.bestPair.spread)}). <a data-sym="${esc(d.bestPair.pair)}" style="cursor:pointer">Analisa →</a></div>` : ''}
          </div></div>` : `<div class="panel">
          <div class="panel-head"><span class="diamond">◆</span> PENGGERAK HARI INI</div>
          <div class="panel-body two-col">
            <div>${gainers.map((r) => this.moverRow(r)).join('') || '<div class="empty">–</div>'}</div>
            <div>${losers.map((r) => this.moverRow(r)).join('') || '<div class="empty">–</div>'}</div>
          </div></div>`}
        <div class="panel">
          <div class="panel-head"><span class="diamond">◆</span> BREAK STRUKTUR TERBARU · 4H</div>
          <div class="panel-body">${events.length ? events.map((r) => `
            <div class="strength-row" style="grid-template-columns: 46px 76px 1fr" data-sym="${esc(r.instrument.symbol)}"><span class="time">${fmtTime(r.lastEvent.t)}</span>
            <span class="tag ${dirClass(r.lastEvent.direction)}" style="text-align:center">${r.lastEvent.scope === 'internal' ? 'i' : ''}${esc(r.lastEvent.kind)} ${arrow(r.lastEvent.direction)}</span>
            <span style="cursor:pointer"><b>${esc(r.instrument.symbol)}</b> <span class="dim">@ ${fmtPrice(r.lastEvent.level, r.instrument.decimals)} · ${r.lastEvent.barsAgo} bar lalu</span></span></div>`).join('') : '<div class="empty">Belum ada break struktur baru.</div>'}
          </div>
        </div>
      </div>

      ${this.cfg.strength ? `<div class="panel" style="margin-top:14px"><div class="panel-head"><span class="diamond">◆</span> PENGGERAK HARI INI</div>
        <div class="panel-body two-col"><div>${gainers.map((r) => this.moverRow(r)).join('')}</div><div>${losers.map((r) => this.moverRow(r)).join('')}</div></div></div>` : ''}

      ${instruments.length ? `<div class="panel" style="margin-top:14px">
        <div class="panel-head"><span class="diamond">◆</span> INSTRUMEN KUNCI<span class="right sub">1D + 4H · klik untuk analisa</span></div>
        <div class="table-wrap"><table class="data"><thead><tr><th>SIMBOL</th><th class="r">HARGA</th><th class="r">1D</th><th class="r">5D</th><th>1D SMC</th><th>4H SMC</th><th class="r">SKOR</th><th>ARAH</th><th>KEYAKINAN</th></tr></thead><tbody>
          ${instruments.map((i) => `<tr data-sym="${esc(i.symbol)}">
            <td><span class="sym">${esc(i.symbol)}<small>${esc(i.name)}</small></span></td>
            <td class="r num">${fmtPrice(i.price, i.decimals)}</td>
            <td class="r num ${i.changePct > 0 ? 'up' : i.changePct < 0 ? 'down' : ''}">${fmtPct(i.changePct)}</td>
            <td class="r num ${i.change5dPct > 0 ? 'up' : i.change5dPct < 0 ? 'down' : ''}">${fmtPct(i.change5dPct)}</td>
            <td>${i.d1 ? badge(directionOfScore(i.d1.score)[0], i.d1.label) : '–'}</td>
            <td>${i.h4 ? badge(directionOfScore(i.h4.score)[0], i.h4.label) : '<span class="dim">–</span>'}</td>
            <td class="r num" style="color:${dirHex(i.direction)}">${fmtScore(i.score)}</td>
            <td>${badge(i.direction, i.label)}</td>
            <td>${i.aligned ? '<span class="up">✔ searah</span>' : '<span class="dim">campuran</span>'} · ${esc(i.confidence)}</td>
          </tr>`).join('')}
        </tbody></table></div></div>` : ''}

      <div class="panel" style="margin-top:14px">
        <div class="panel-head"><span class="diamond">◆</span> EVENT HIGH-IMPACT · 48 JAM${this.cfg.eventCurrencies ? ` · ${esc(this.cfg.eventCurrencies.join(', '))}` : ''}</div>
        <div class="panel-body">${highImpact.length ? highImpact.map((e) => `
          <div class="strength-row" style="grid-template-columns: 120px 48px 1fr"><span class="time">${fmtTime(e.date, true)}</span><span class="ccy">${esc(e.currency)}</span><span>${esc(e.title)} <span class="dim">· forecast ${esc(e.forecast || '-')} · sebelumnya ${esc(e.previous || '-')}</span></span></div>`).join('') : '<div class="empty">Tidak ada event high-impact yang relevan 48 jam ke depan.</div>'}
          <div class="note">Volatilitas biasanya melonjak saat rilis — struktur SMC bisa ditembus palsu (liquidity sweep).</div>
        </div>
      </div>`;
  }

  moverRow(r) {
    return `<div class="strength-row" style="grid-template-columns: 1fr 80px 70px" data-sym="${esc(r.instrument.symbol)}">
      <span style="cursor:pointer"><b>${esc(r.instrument.symbol)}</b> <span class="dim">${esc(r.label)}</span></span>
      <span class="num" style="text-align:right">${fmtPrice(r.price, r.instrument.decimals)}</span>
      <span class="num ${r.changePct > 0 ? 'up' : r.changePct < 0 ? 'down' : ''}" style="text-align:right">${fmtPct(r.changePct)}</span></div>`;
  }

  show() { this.loadSentiment(); }
}
