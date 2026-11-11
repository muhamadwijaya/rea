// FUNDAMENTAL & AI view of one market page: economic releases with actual vs forecast, the rule model's
// call and the real 60-minute reaction, measured accuracy, combined asset outlook, and the AI panel.
import { api, esc, on, badge, dirHex, fmtScore, fmtTime, timeAgo, arrow, fmtBig } from './util.js';
import { rowsOf } from './markets.js';
import { mountAiPanel } from './ai-panel.js';

const VERDICT = {
  confirmed: '<span class="tag bull">✔ TERKONFIRMASI</span>',
  opposite: '<span class="tag bear">✘ BERLAWANAN</span>',
  flat: '<span class="tag ghost">– DATAR</span>',
};

export class FundamentalView {
  constructor(el, cfg, store) {
    this.el = el;
    this.cfg = cfg;
    this.store = store;
    this.data = null;
    el.innerHTML = `
      <div class="split-2">
        <div class="panel tabbed">
          <div class="ptabs"><button data-t="rilis" class="active">RILIS DATA EKONOMI</button><button data-t="outlook">OUTLOOK ASET (SMC + FUNDAMENTAL + BERITA)</button><button data-t="akurasi">AKURASI MODEL</button></div>
          <div class="pane active" data-t="rilis"><div class="table-wrap fd-events"><div class="loading">Menganalisa rilis data</div></div></div>
          <div class="pane" data-t="outlook"><div class="table-wrap fd-outlook"><div class="loading">Menghitung outlook</div></div></div>
          <div class="pane" data-t="akurasi"><div class="panel-body fd-acc"></div></div>
        </div>
        <div class="panel col-panel">
          <div class="panel-head"><span class="diamond">◆</span> ANALISA AI <span class="right sub">OpenRouter gratis (rotasi otomatis) · Claude · API lain</span></div>
          <div class="panel-body fd-ai" style="overflow:auto;flex:1"></div>
        </div>
      </div>`;
    this.ai = mountAiPanel(el.querySelector('.fd-ai'), { initial: cfg.defaultSymbol });
    el.querySelector('.pane[data-t="outlook"]').addEventListener('tabshow', () => this.loadOutlook());
    el.addEventListener('click', (e) => {
      const ai = e.target.closest('[data-ai]');
      if (ai) { this.ai.setSymbol(ai.dataset.ai); return; }
      const row = e.target.closest('tr[data-sym]');
      if (row) window.SMC.go(cfg.key, 'analisa', row.dataset.sym);
    });
    on('fundamental', () => this.load());
    this.load();
  }

  currencies() {
    return this.cfg.key === 'forex' ? null : this.cfg.key === 'saham-idx' ? ['USD', 'IDR', 'CNY'] : ['USD'];
  }

  async load() {
    try {
      this.data = await api('/api/fundamental');
    } catch (e) {
      this.el.querySelector('.fd-events').innerHTML = `<div class="panel-body"><div class="error-box">${esc(e.message)}</div></div>`;
      return;
    }
    this.renderEvents();
    this.renderAccuracy();
  }

  renderEvents() {
    const wanted = this.currencies();
    const events = (this.data.events || []).filter((e) => (!wanted || wanted.includes(e.currency)) && e.impact !== 'Low');
    const released = events.filter((e) => e.status === 'released').reverse();
    const upcoming = events.filter((e) => e.status === 'upcoming');
    const row = (e) => {
      const p = e.prediction;
      const r = e.reaction;
      const call = p ? `<span style="color:${dirHex(p.direction > 0 ? 'bull' : 'bear')}">${arrow(p.direction > 0 ? 'bull' : 'bear')} ${esc(e.currency)} ${p.direction > 0 ? 'menguat' : 'melemah'}</span><small>${esc(p.basis)}</small>`
        : e.scenario ? `<small>${esc(e.scenario.above)}<br>${esc(e.scenario.below)}</small>`
          : e.kind === 'speech' ? '<small class="dim">pidato — tanpa angka</small>' : '<small class="dim">belum ada headline hasil rilis</small>';
      const assets = p?.assets?.length ? `<small>${p.assets.slice(0, 5).map((a) => `<span style="color:${dirHex(a.direction > 0 ? 'bull' : 'bear')}">${esc(a.symbol)}${a.direction > 0 ? '▲' : '▼'}</span>`).join(' ')}</small>` : '';
      const evidence = (e.evidence || []).map((x) => `• ${x.title}`).join('\n');
      return `<tr title="${esc(evidence)}">
        <td class="nowrap">${fmtTime(e.date, true)}</td><td><span class="ccy">${esc(e.currency)}</span></td>
        <td><b>${esc(e.title)}</b> <span class="impact ${esc(e.impact)}">${esc(e.impact.toUpperCase())}</span>${e.evidence?.length ? `<small>${e.evidence.length} headline sumber (arahkan kursor)</small>` : ''}</td>
        <td class="r num">${esc(e.forecast || '–')}</td><td class="r num dim">${esc(e.previous || '–')}</td>
        <td class="r num">${e.actual != null ? `<b>${fmtBig(e.actual)}</b>` : '–'}</td>
        <td class="fd-call">${call}${assets}</td>
        <td class="r num">${r ? `<span class="${r.currencyMovePct > 0 ? 'up' : r.currencyMovePct < 0 ? 'down' : ''}">${e.currency} ${r.currencyMovePct > 0 ? '+' : ''}${r.currencyMovePct.toFixed(2)}%</span><small>${esc(r.symbol)} ${r.minutes} mnt</small>` : '–'}</td>
        <td>${e.verdict ? VERDICT[e.verdict] : ''}</td></tr>`;
    };
    this.el.querySelector('.fd-events').innerHTML = `
      <table class="data fd-table"><thead><tr><th>WAKTU</th><th>CCY</th><th>EVENT</th><th class="r">FORECAST</th><th class="r">PREV</th><th class="r">ACTUAL</th><th>PREDIKSI MODEL</th><th class="r">REAKSI NYATA</th><th>HASIL</th></tr></thead>
      <tbody>${upcoming.length ? `<tr class="fd-sep"><td colspan="9">AKAN RILIS · ${upcoming.length} event</td></tr>${upcoming.map(row).join('')}` : ''}
      ${released.length ? `<tr class="fd-sep"><td colspan="9">SUDAH RILIS · ${released.length} event</td></tr>${released.map(row).join('')}` : ''}
      ${!events.length ? '<tr><td colspan="9" class="empty">Belum ada event medium/high-impact minggu ini untuk pasar ini.</td></tr>' : ''}</tbody></table>`;
  }

  renderAccuracy() {
    const a = this.data.accuracy || {};
    const judged = (this.data.events || []).filter((e) => e.verdict);
    const cur = this.data.currencies || {};
    this.el.querySelector('.fd-acc').innerHTML = `
      <div class="acc-hero"><div><div class="eyebrow">AKURASI TERUKUR MODEL RILIS DATA</div>
        <div class="acc-big">${a.pct == null ? '–' : `${a.pct}%`}</div>
        <div class="muted">${a.confirmed ?? 0} terkonfirmasi · ${a.opposite ?? 0} berlawanan · ${a.flat ?? 0} datar (dari ${a.judged ?? 0} rilis yang bisa dinilai)</div></div>
        <div class="acc-cur">${Object.entries(cur).map(([c, v]) => `<div><span class="ccy">${esc(c)}</span> <b style="color:${dirHex(v.score > 0 ? 'bull' : v.score < 0 ? 'bear' : '')}">${fmtScore(v.score)}</b> <span class="dim">${v.events} rilis</span></div>`).join('') || '<span class="dim">belum ada skor mata uang</span>'}</div></div>
      <div class="note" style="font-size:12px">${esc(this.data.method || '')}</div>
      <div class="note">Angka ini dihitung dari data nyata minggu ini, bukan klaim. Sampel kecil = belum bermakna secara statistik. Tidak ada model yang bisa menjamin akurasi 99% di pasar keuangan; gunakan bersama manajemen risiko.</div>
      ${judged.map((e) => `<div class="strength-row" style="grid-template-columns: 120px 48px 1fr auto"><span class="time">${fmtTime(e.date, true)}</span><span class="ccy">${esc(e.currency)}</span><span>${esc(e.title)}</span>${VERDICT[e.verdict]}</div>`).join('')}`;
  }

  async loadOutlook() {
    const symbols = rowsOf(this.store.screener, this.cfg.key).map((r) => r.instrument.symbol).slice(0, 40);
    const box = this.el.querySelector('.fd-outlook');
    if (!symbols.length) { box.innerHTML = '<div class="empty">Menunggu data screener…</div>'; return; }
    box.innerHTML = '<div class="loading">Menghitung outlook gabungan (SMC 1D/4H/1H + fundamental + berita)</div>';
    try {
      const res = await api(`/api/outlook?symbols=${encodeURIComponent(symbols.join(','))}`);
      const rows = res.rows.filter((r) => !r.error).sort((a, b) => Math.abs(b.score) - Math.abs(a.score));
      const cell = (v) => (v == null ? '<td class="r dim">–</td>' : `<td class="r num" style="color:${dirHex(v > 0 ? 'bull' : v < 0 ? 'bear' : '')}">${fmtScore(v)}</td>`);
      box.innerHTML = `<table class="data"><thead><tr><th>ASET</th><th class="r">SMC</th><th class="r">FUNDAMENTAL</th><th class="r">BERITA</th><th class="r">GABUNGAN</th><th>ARAH</th><th>KEYAKINAN</th><th>CATATAN</th><th></th></tr></thead><tbody>
        ${rows.map((r) => `<tr data-sym="${esc(r.instrument.symbol)}"><td><span class="sym">${esc(r.instrument.symbol)}<small>${esc(r.instrument.name)}</small></span></td>
          ${cell(r.components.smc)}${cell(r.components.fundamental)}${cell(r.components.news)}
          <td class="r num"><b style="color:${dirHex(r.direction)}">${fmtScore(r.score)}</b></td><td>${badge(r.direction, r.label)}</td>
          <td><span class="tag ${r.confidence === 'tinggi' ? 'bull' : r.confidence === 'sedang' ? 'neu' : 'ghost'}">${esc(r.confidence.toUpperCase())}</span></td>
          <td class="fd-notes">${esc(r.notes.join(' · '))}</td><td><button class="btn" data-ai="${esc(r.instrument.symbol)}">AI ›</button></td></tr>`).join('')}
      </tbody></table>
      <div class="note" style="padding:0 12px 12px">Bobot: SMC 40% · fundamental 35% · berita 25% (dibagi ulang bila komponen tidak tersedia). Diperbarui ${timeAgo(res.generatedAt)}.</div>`;
    } catch (e) {
      box.innerHTML = `<div class="panel-body"><div class="error-box">${esc(e.message)}</div></div>`;
    }
  }

  show() { this.load(); }
}

