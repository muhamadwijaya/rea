// Arah Market view of one market page. Everything is split over tabs so nothing needs a page scroll:
// RINGKASAN · MATA UANG (forex) · PENGGERAK & STRUKTUR · INSTRUMEN · EVENT · PETA PASAR.
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
    <div class="v" style="color:${color}">${c.score > 0 ? '+' : ''}${(c.score * 100).toFixed(0)} <span class="dim" style="font-size:10.5px;font-weight:400">${total} berita · ▲${c.bullish} ▼${c.bearish}</span></div>
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
    const tabs = [
      ['ringkasan', 'RINGKASAN'],
      ...(cfg.strength ? [['fx', 'KEKUATAN MATA UANG']] : []),
      ['gerak', 'PENGGERAK & STRUKTUR'],
      ['instrumen', 'INSTRUMEN KUNCI'],
      ['event', 'EVENT HIGH-IMPACT'],
      ['peta', 'PETA PASAR (TRADINGVIEW)'],
    ];
    el.innerHTML = `
      <div class="panel tabbed fill">
        <div class="ptabs">${tabs.map(([k, l], i) => `<button data-t="${k}" class="${i ? '' : 'active'}">${l}</button>`).join('')}</div>
        ${tabs.filter(([k]) => k !== 'peta').map(([k], i) => `<div class="pane ${i ? '' : 'active'}" data-t="${k}"><div class="panel-body"><div class="loading">Menghitung arah market</div></div></div>`).join('')}
        <div class="pane" data-t="peta">
          ${cfg.widgets.length > 1 ? `<div class="toolbar" style="padding:8px 10px;border-bottom:1px solid var(--line)"><div class="seg ov-widgets">${cfg.widgets.map(([k, l], i) => `<button data-v="${k}" class="${i ? '' : 'active'}">${l}</button>`).join('')}</div></div>` : ''}
          <div class="tv-fill ov-widget"></div>
        </div>
      </div>`;
    const seg = el.querySelector('.ov-widgets');
    if (seg) segmented(seg, (v) => widgetMap(el.querySelector('.ov-widget'), v));
    el.querySelector('.pane[data-t="peta"]').addEventListener('tabshow', () => {
      if (!this.widgetMounted) { this.widgetMounted = true; widgetMap(el.querySelector('.ov-widget'), cfg.widgets[0][0]); }
    });
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

  pane(key, html) {
    const body = this.el.querySelector(`.pane[data-t="${key}"] .panel-body`);
    if (body) body.innerHTML = html;
  }

  async loadSentiment() {
    try {
      this.sentiment = await api(`/api/news/sentiment?hours=12&market=${encodeURIComponent(this.cfg.key)}`);
    } catch { /* optional */ }
    this.render();
  }

  render() {
    const d = this.store.direction;
    const rows = rowsOf(this.store.screener, this.cfg.key).filter((r) => !r.error);
    if (!rows.length && !d) return;
    this.renderSummary(d, rows);
    if (this.cfg.strength) this.renderStrength(d);
    this.renderMovers(rows);
    this.renderInstruments(d);
    this.renderEvents(d);
  }

  renderSummary(d, rows) {
    const bull = rows.filter((r) => r.direction?.includes('bullish')).length;
    const bear = rows.filter((r) => r.direction?.includes('bearish')).length;
    const avg = rows.length ? rows.reduce((s, r) => s + r.score, 0) / rows.length : 0;
    const discount = rows.filter((r) => r.zone?.area === 'discount').length;
    const [avgDir, avgLabel] = directionOfScore(avg);
    const outlooks = (d?.outlooks || []).filter((o) => this.cfg.outlooks.includes(o.key))
      .sort((a, b) => this.cfg.outlooks.indexOf(a.key) - this.cfg.outlooks.indexOf(b.key));
    const reg = d?.regime;
    const sent = this.sentiment;
    this.pane('ringkasan', `
      <div class="ov-grid">
        <div class="panel regime-big">
          <div class="eyebrow">ARAH ${esc(this.cfg.nav)} · BREADTH SMC 4H</div>
          <div class="r" style="color:${dirHex(avgDir)}">${arrow(avgDir)} ${esc(avgLabel)}</div>
          <div class="h">Skor rata-rata ${fmtScore(avg)} dari ${rows.length} instrumen · <span class="up">▲${bull} bullish</span> · <span class="down">▼${bear} bearish</span> · ${discount} di zona discount</div>
          ${reg ? `<div class="t">Regime global <b style="color:${regimeColor(reg.regime)}">${esc(reg.regime)}</b> (skor ${fmtScore(reg.score)}) — ${esc(reg.text)}</div>` : ''}
          ${d ? `<div class="t">Diperbarui ${d.updatedAt ? timeAgo(d.updatedAt) : '–'} · ${esc(d.method)}</div>` : ''}
        </div>
        <div class="panel">
          <div class="panel-head"><span class="diamond">◆</span> SENTIMEN BERITA · 12 JAM <span class="right sub">${sent?.total ?? 0} headline</span></div>
          <div class="panel-body"><div class="sent-bars">${sent ? [sentCard({ ...sent.overall, key: 'TOTAL' }), ...sent.assets.slice(0, 5).map((a) => sentCard({ ...a, key: ASSET_LABEL[a.key] || a.key }))].join('') : '<div class="empty">memuat…</div>'}</div></div>
        </div>
      </div>
      ${outlooks.length ? `<div class="section-title" style="margin-top:14px"><h2>Ke Mana Arah ${esc(this.cfg.title)}?</h2><span class="sub">bias SMC harian + 4H · klik kartu untuk analisa</span></div>
      <div class="outlooks">${outlooks.map((o) => `
        <div class="panel outlook" data-sym="${esc(o.symbols[0])}">
          <div class="glow" style="background: radial-gradient(circle at 90% 0%, ${dirHex(o.direction)}22, transparent 60%)"></div>
          <div class="t"><span>${esc(o.title.toUpperCase())}</span><span class="tag ${dirClass(o.direction)}">KEYAKINAN ${esc(o.confidence.toUpperCase())}</span></div>
          <div class="arrow" style="color:${dirHex(o.direction)}">${arrow(o.direction)}</div>
          <div class="l" style="color:${dirHex(o.direction)}">${esc(o.label)} <span class="muted" style="font-size:11px;font-weight:400">skor ${fmtScore(o.score)}</span></div>
          <ul>${o.drivers.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>
        </div>`).join('')}</div>` : ''}`);
  }

  renderStrength(d) {
    const strength = d?.currencyStrength || [];
    if (!strength.length) return;
    const maxAbs = Math.max(30, ...strength.map((s) => Math.abs(s.score)));
    const reg = d.regime;
    this.pane('fx', `
      <div class="ov-grid">
        <div class="panel"><div class="panel-head"><span class="diamond">◆</span> KEKUATAN MATA UANG<span class="sub">rata-rata skor SMC semua pair G8</span></div>
          <div class="panel-body">
            ${strength.map((s) => `<div class="strength-row"><b>${esc(s.currency)}</b>${divBar(s.score, maxAbs)}<span class="num" style="text-align:right;color:${s.score > 0 ? 'var(--bull)' : 'var(--bear)'}">${fmtScore(s.score)}</span><span class="num dim" style="text-align:right">${fmtPct(s.changePct)}</span></div>`).join('')}
            ${d.bestPair ? `<div class="note" style="font-size:12px">Divergensi terbesar: <b class="accent">${esc(d.bestPair.pair)}</b> cenderung <b style="color:${dirHex(d.bestPair.direction)}">${esc(d.bestPair.direction.toUpperCase())}</b> (selisih ${fmtScore(d.bestPair.spread)}). <a data-sym="${esc(d.bestPair.pair)}" style="cursor:pointer">Analisa →</a></div>` : ''}
          </div></div>
        <div class="panel"><div class="panel-head"><span class="diamond">◆</span> PENDORONG RISK-ON / OFF<span class="right" style="color:${regimeColor(reg.regime)}">${esc(reg.regime)}</span></div>
          <div class="panel-body">${reg.contributions.map((c) => `
            <div class="strength-row" style="grid-template-columns: 70px 1fr 50px"><b>${esc(c.name)}</b>${divBar(c.contribution, 20)}<span class="num ${c.contribution > 0 ? 'up' : c.contribution < 0 ? 'down' : ''}" style="text-align:right">${fmtScore(c.contribution)}</span></div>`).join('')}
            <div class="note">Kontribusi positif = mendukung risk-on. VIX, JPY, CHF dan emas dihitung terbalik (safe haven).</div>
          </div></div>
      </div>`);
  }

  renderMovers(rows) {
    const movers = [...rows].filter((r) => r.changePct != null).sort((a, b) => b.changePct - a.changePct);
    const gainers = movers.slice(0, 6), losers = movers.slice(-6).reverse();
    const events = rows.filter((r) => r.lastEvent && r.lastEvent.barsAgo <= 6).sort((a, b) => b.lastEvent.t - a.lastEvent.t).slice(0, 12);
    const mover = (r) => `<div class="strength-row" style="grid-template-columns: 1fr 90px 70px" data-sym="${esc(r.instrument.symbol)}">
      <span style="cursor:pointer"><b>${esc(r.instrument.symbol)}</b> <span class="dim">${esc(r.label)}</span></span>
      <span class="num" style="text-align:right">${fmtPrice(r.price, r.instrument.decimals)}</span>
      <span class="num ${r.changePct > 0 ? 'up' : r.changePct < 0 ? 'down' : ''}" style="text-align:right">${fmtPct(r.changePct)}</span></div>`;
    this.pane('gerak', `
      <div class="ov-grid">
        <div class="panel"><div class="panel-head"><span class="diamond">◆</span> PENGGERAK HARI INI <span class="sub">naik terbesar · turun terbesar</span></div>
          <div class="panel-body two-col"><div>${gainers.map(mover).join('') || '<div class="empty">–</div>'}</div><div>${losers.map(mover).join('') || '<div class="empty">–</div>'}</div></div></div>
        <div class="panel"><div class="panel-head"><span class="diamond">◆</span> BREAK STRUKTUR TERBARU · 4H</div>
          <div class="panel-body">${events.length ? events.map((r) => `
            <div class="strength-row" style="grid-template-columns: 46px 80px 1fr" data-sym="${esc(r.instrument.symbol)}"><span class="time">${fmtTime(r.lastEvent.t)}</span>
            <span class="tag ${dirClass(r.lastEvent.direction)}" style="text-align:center">${r.lastEvent.scope === 'internal' ? 'i' : ''}${esc(r.lastEvent.kind)} ${arrow(r.lastEvent.direction)}</span>
            <span style="cursor:pointer"><b>${esc(r.instrument.symbol)}</b> <span class="dim">@ ${fmtPrice(r.lastEvent.level, r.instrument.decimals)} · ${r.lastEvent.barsAgo} bar lalu</span></span></div>`).join('') : '<div class="empty">Belum ada break struktur baru.</div>'}</div></div>
      </div>`);
  }

  renderInstruments(d) {
    const instruments = (d?.instruments || []).filter((i) => i.market === this.cfg.key);
    this.pane('instrumen', instruments.length ? `
      <table class="data"><thead><tr><th>SIMBOL</th><th class="r">HARGA</th><th class="r">1D</th><th class="r">5D</th><th>1D SMC</th><th>4H SMC</th><th class="r">SKOR</th><th>ARAH</th><th>KEYAKINAN</th></tr></thead><tbody>
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
      </tbody></table>
      <div class="note">Skor = 55% bias SMC harian + 45% bias SMC 4H. Klik baris untuk analisa lengkap.</div>` : '<div class="empty">Instrumen kunci pasar ini belum tersedia di model arah market.</div>');
  }

  renderEvents(d) {
    const list = (d?.events || []).filter((e) => !this.cfg.eventCurrencies || this.cfg.eventCurrencies.includes(e.currency));
    this.pane('event', `${list.length ? list.map((e) => `
      <div class="strength-row" style="grid-template-columns: 130px 52px 1fr"><span class="time">${fmtTime(e.date, true)}</span><span class="ccy">${esc(e.currency)}</span><span>${esc(e.title)} <span class="dim">· forecast ${esc(e.forecast || '-')} · sebelumnya ${esc(e.previous || '-')}</span></span></div>`).join('') : '<div class="empty">Tidak ada event high-impact yang relevan 48 jam ke depan.</div>'}
      <div class="note">${this.cfg.eventCurrencies ? `Filter mata uang: ${esc(this.cfg.eventCurrencies.join(', '))}. ` : ''}Volatilitas biasanya melonjak saat rilis — struktur SMC bisa ditembus palsu (liquidity sweep). Kalender lengkap ada di halaman KALENDER.</div>`);
  }

  show() { this.loadSentiment(); }
}
