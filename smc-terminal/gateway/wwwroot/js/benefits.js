// DIVIDEN & BENEFIT tab of a market page: upcoming dividends (announced or projected), halvings, central-bank
// rate decisions and benefit / corporate-action headlines, each with a hype read (volume, news, price move).
import { api, esc, fmtPct, fmtPrice } from './util.js';

const STATUS = { dijadwalkan: ['DIJADWALKAN', 'bull'], perkiraan: ['PERKIRAAN', 'neu'], berita: ['BERITA', 'ghost'] };
const FILTERS = [['all', 'SEMUA'], ['upcoming', 'MENDATANG'], ['dijadwalkan', 'DIJADWALKAN'], ['perkiraan', 'PERKIRAAN'], ['berita', 'DARI BERITA']];
const REFRESH_MS = 2 * 60 * 1000;

const when = (d) => (d == null ? '' : d === 0 ? 'hari ini' : d > 0 ? `H-${d}` : `${-d} hari lalu`);

function detail(r) {
  const d = r.detail || {};
  if (r.kind === 'DIVIDEN') {
    return `${d.amountEstimate != null ? `± ${fmtPrice(d.amountEstimate)} /lembar` : ''}${d.yieldPct != null ? ` · yield 12 bln ${fmtPct(d.yieldPct, 2)}` : ''}${d.perYear ? ` · ${d.perYear}×/thn` : ''}
      ${d.payDate ? `<small class="dim">bayar ${esc(d.payDate)}</small>` : ''}${d.lastExDate ? `<small class="dim">terakhir ${esc(d.lastExDate)} (${fmtPrice(d.lastAmount)})</small>` : ''}`;
  }
  if (r.kind === 'HALVING') return `blok ${Number(d.height).toLocaleString('id-ID')} → ${Number(d.target).toLocaleString('id-ID')}<small class="dim">${Number(d.blocksLeft).toLocaleString('id-ID')} blok lagi · imbalan penambang dibagi dua</small>`;
  if (r.status === 'berita') return d.link ? `<a href="${esc(d.link)}" target="_blank" rel="noopener noreferrer">${esc(d.title)} ↗</a>` : esc(d.title || '');
  return `${esc(d.title || '')}${d.forecast || d.previous ? `<small class="dim">forecast ${esc(d.forecast || '–')} · sebelumnya ${esc(d.previous || '–')}</small>` : ''}`;
}

function hypeHtml(h) {
  if (!h) return '–';
  const cls = h.label === 'TINGGI' ? 'up' : h.label === 'SEDANG' ? 'flat' : 'dim';
  return `<div class="bn-hype"><i><b style="width:${h.score}%"></b></i><span class="${cls}">${h.label}</span></div>
    <small class="dim">${h.volumeRatio != null ? `vol ${h.volumeRatio}× · ` : ''}${h.news7d} berita${h.return5dPct != null ? ` · 5h ${fmtPct(h.return5dPct, 1)}` : ''}</small>`;
}

export class BenefitsView {
  constructor(el, cfg) {
    this.el = el;
    this.cfg = cfg;
    this.filter = 'upcoming';
    this.kind = '';
    this.q = '';
    this.data = null;
    el.innerHTML = `
      <div class="toolbar">
        <div class="seg bn-filter">${FILTERS.map(([k, l]) => `<button data-v="${k}" class="${k === this.filter ? 'active' : ''}">${l}</button>`).join('')}</div>
        <select class="input bn-kind" style="width:auto"><option value="">Semua jenis</option></select>
        <input class="input bn-q" placeholder="cari aset…" style="width:170px">
        <span class="grow"></span><span class="time bn-updated"></span>
      </div>
      <div class="bn-kpis"></div>
      <div class="split-2 bn-split">
        <div class="panel col-panel">
          <div class="panel-head"><span class="diamond">◆</span> JADWAL DIVIDEN & BENEFIT · ${esc(cfg.nav)} <span class="sub">tanggal ex-dividen / event · urut terdekat</span></div>
          <div class="list-head bn-cols"><span>TANGGAL</span><span>ASET</span><span>JENIS</span><span>DETAIL</span><span>STATUS</span><span>HYPE</span></div>
          <div class="scroll bn-list"><div class="loading">Memuat jadwal</div></div>
        </div>
        <div class="panel col-panel">
          <div class="panel-head"><span class="diamond">◆</span> HYPE TERTINGGI <span class="sub">perhatian pasar pada event mendatang</span></div>
          <div class="scroll bn-hot"></div>
          <div class="note bn-note" style="padding:8px 12px"></div>
        </div>
      </div>`;
    const $ = (s) => el.querySelector(s);
    $('.bn-filter').addEventListener('click', (e) => {
      const b = e.target.closest('button[data-v]');
      if (!b) return;
      this.filter = b.dataset.v;
      el.querySelectorAll('.bn-filter button').forEach((x) => x.classList.toggle('active', x === b));
      this.render();
    });
    $('.bn-kind').addEventListener('change', (e) => { this.kind = e.target.value; this.render(); });
    $('.bn-q').addEventListener('input', (e) => { this.q = e.target.value.trim().toUpperCase(); this.render(); });
    el.addEventListener('click', (e) => {
      const row = e.target.closest('[data-sym]');
      if (row && !e.target.closest('a')) window.SMC.openAnalysis(row.dataset.sym);
    });
    setInterval(() => { if (el.classList.contains('active')) this.load(); }, REFRESH_MS);
  }

  show() { this.load(); }

  async load() {
    try {
      this.data = await api(`/api/benefits?market=${this.cfg.key}`);
      const kinds = [...new Set(this.data.rows.map((r) => r.kind))].sort();
      const sel = this.el.querySelector('.bn-kind');
      sel.innerHTML = `<option value="">Semua jenis</option>${kinds.map((k) => `<option ${k === this.kind ? 'selected' : ''}>${esc(k)}</option>`).join('')}`;
      this.render();
    } catch (e) {
      this.el.querySelector('.bn-list').innerHTML = `<div class="error-box">${esc(e.message)}</div>`;
    }
  }

  rows() {
    return (this.data?.rows || []).filter((r) => (this.filter === 'all' ? true
      : this.filter === 'upcoming' ? r.daysTo != null && r.daysTo >= 0 && r.status !== 'berita'
        : r.status === this.filter))
      .filter((r) => !this.kind || r.kind === this.kind)
      .filter((r) => !this.q || r.symbol.includes(this.q) || r.name.toUpperCase().includes(this.q));
  }

  render() {
    const d = this.data;
    if (!d) return;
    const rows = this.rows();
    const upcoming = d.rows.filter((r) => r.daysTo != null && r.daysTo >= 0 && r.status !== 'berita');
    const yields = upcoming.filter((r) => r.kind === 'DIVIDEN' && r.detail?.yieldPct != null).map((r) => r.detail.yieldPct);
    const scan = d.calendarScan || {};
    this.el.querySelector('.bn-kpis').innerHTML = [
      ['EVENT 7 HARI', upcoming.filter((r) => r.daysTo <= 7).length], ['EVENT 30 HARI', upcoming.filter((r) => r.daysTo <= 30).length],
      ['DIJADWALKAN', upcoming.filter((r) => r.status === 'dijadwalkan').length], ['HEADLINE BENEFIT 7 HARI', d.rows.filter((r) => r.status === 'berita').length],
      ['HYPE TINGGI', d.rows.filter((r) => r.hype?.label === 'TINGGI').length],
      ['RATA-RATA YIELD', yields.length ? fmtPct(yields.reduce((a, b) => a + b, 0) / yields.length, 2) : '–'],
    ].map(([l, v]) => `<div><span>${l}</span><b>${v}</b></div>`).join('');
    this.el.querySelector('.bn-list').innerHTML = rows.length ? rows.map((r) => {
      const [st, cls] = STATUS[r.status] || ['', 'ghost'];
      return `<div class="bn-row" data-sym="${esc(r.symbol)}">
        <span><b class="num">${esc(r.date || '–')}</b><small class="${r.daysTo >= 0 && r.daysTo <= 7 ? 'up' : 'dim'}">${when(r.daysTo)}</small></span>
        <span><b>${esc(r.symbol)}</b><small class="dim">${esc(r.name)}</small></span>
        <span><span class="chip">${esc(r.kind)}</span></span>
        <span class="bn-detail">${detail(r)}<small class="dim">${esc(r.source || '')}</small></span>
        <span><span class="tag ${cls}">${st}</span></span>
        <span>${hypeHtml(r.hype)}</span></div>`;
    }).join('') : `<div class="empty">Belum ada event${this.filter !== 'all' ? ' untuk filter ini' : ''}. ${scan.total ? `Kalender dividen sedang dipindai (${scan.done}/${scan.total}).` : 'Riwayat dividen terkumpul setelah pemindaian harian seluruh aset (±2–4 menit setelah aplikasi dibuka).'}</div>`;
    const hot = upcoming.filter((r) => r.hype).sort((a, b) => b.hype.score - a.hype.score).slice(0, 14);
    this.el.querySelector('.bn-hot').innerHTML = hot.length ? hot.map((r) => `<div class="bn-hot-row" data-sym="${esc(r.symbol)}">
      <span><b>${esc(r.symbol)}</b><small class="dim">${esc(r.kind)} · ${esc(r.date || '')} (${when(r.daysTo)})</small></span>${hypeHtml(r.hype)}</div>`).join('')
      : '<div class="empty">Belum ada event mendatang.</div>';
    this.el.querySelector('.bn-note').textContent = `Dividen: tanggal dari kalender emiten Yahoo bila sudah diumumkan, selain itu PERKIRAAN dari ritme pembayaran sebelumnya (jumlah = dividen terakhir). Kalender dipindai ${scan.payers ?? '–'} emiten pembayar dividen${scan.total ? ` (${scan.done}/${scan.total} pada putaran ini)` : ''}. Hype = volume 5 hari vs 20 hari + jumlah berita terkait 7 hari + kenaikan harga 5 hari.`;
    this.el.querySelector('.bn-updated').textContent = `diperbarui ${new Date(d.generatedAt * 1000).toLocaleTimeString('id-ID')}`;
  }
}
