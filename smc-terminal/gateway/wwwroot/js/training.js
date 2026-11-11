// Virtual trading ("AI training"): the PLAN card of one asset (signal timestamps, auto-cancel rules, virtual
// position, track record, SMC upgrades) and the AI TRAINING page (overall accuracy, equity, leaderboard,
// journal, upgrade log). Data: /api/training/asset and /api/training/summary.
import { api, esc, fmtPrice } from './util.js';

const STATUS = {
  pending: ['MENUNGGU ENTRY', 'neu'], open: ['POSISI TERBUKA', 'bull'], win: ['PROFIT ✔', 'bull'], loss: ['LOSS ✘', 'bear'],
  timeout: ['DITUTUP WAKTU', 'ghost'], cancelled: ['BATAL OTOMATIS', 'ghost'],
};
const MARKET_NAME = { forex: 'FOREX', 'saham-as': 'SAHAM AS', 'saham-idx': 'SAHAM IDX', kripto: 'KRIPTO' };

const dt = (t) => (t ? new Date(t * 1000).toLocaleString('id-ID', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) : '–');
const pct = (v) => (v == null ? '–' : `${(v * 100).toFixed(1)}%`);
const rr = (v) => (v == null ? '–' : `${v > 0 ? '+' : ''}${v.toFixed(2)}R`);
const dirTag = (d) => (d > 0 ? '<span class="tag bull">BUY</span>' : '<span class="tag bear">SELL</span>');

function progress(rate, target, goal) {
  const v = rate == null ? 0 : rate * 100;
  return `<div class="tr-progress" title="Win rate terukur vs target ${target * 100}% dan tujuan ${goal * 100}%">
    <i style="width:${Math.min(100, v)}%"></i><em style="left:${target * 100}%"></em><em class="goal" style="left:${goal * 100}%"></em>
    <span>${rate == null ? 'belum ada trade selesai' : `${v.toFixed(1)}%`} · target ${target * 100}% · tujuan ${goal * 100}%</span></div>`;
}

function statBlock(title, m) {
  return `<div class="tr-stat"><h5>${title}</h5>
    <b class="${m?.winRate >= 0.5 ? 'up' : m?.winRate != null ? 'down' : ''}">${pct(m?.winRate)}</b>
    <span>${m?.trades ?? 0} trade · ${m?.wins ?? 0} menang${m?.expectancyR != null ? ` · ekspektasi ${rr(m.expectancyR)}` : ''}</span></div>`;
}

function signalCard(s, rules, decimals) {
  const [label, cls] = STATUS[s.status] || [s.status, 'ghost'];
  const f = (v) => fmtPrice(v, decimals);
  const risk = Math.abs(s.entry - s.stop);
  return `<div class="tr-signal">
    <div class="tr-sig-top">${dirTag(s.direction)} <span class="tag ${cls}">${label}</span> <span class="dim">${esc(s.kind || '')} ${esc(s.scope || '')} · ${esc(s.tf)}</span></div>
    <div class="tr-times">
      <div><span>SINYAL MUNCUL</span><b>${dt(s.created_t)}</b></div>
      <div><span>TERDETEKSI SISTEM</span><b>${s.first_seen ? dt(s.first_seen) : 'riwayat (backtest)'}</b></div>
      <div><span>KEDALUWARSA</span><b>${dt(s.expiry_t)}</b></div>
      <div><span>ENTRY TERPICU</span><b>${dt(s.opened_t)}</b></div>
      <div><span>SELESAI</span><b>${dt(s.closed_t)}</b></div>
    </div>
    <div class="chips"><span class="chip">entry ${f(s.entry)}</span><span class="chip">SL ${f(s.stop)}</span>
      <span class="chip">TP ${f(s.target)}</span><span class="chip">RR ${risk ? (Math.abs(s.target - s.entry) / risk).toFixed(2) : '–'}</span>
      ${s.r != null ? `<span class="chip ${s.r > 0 ? 'up' : 'down'}">hasil ${rr(s.r)}</span>` : ''}</div>
    ${s.reason ? `<div class="note">Keterangan: ${esc(s.reason)}</div>` : ''}
    ${s.status === 'pending' && rules?.length ? `<h5>BATAL OTOMATIS BILA</h5><ul class="tr-rules">${rules.map((r) => `<li>${esc(r)}</li>`).join('')}</ul>` : ''}
  </div>`;
}

/** Virtual-trading section of the PLAN tab; call load(symbol, tf, decimals) when the shown chart changes. */
export function mountPlanTraining(el) {
  let key = '';
  return {
    async load(symbol, tf, decimals) {
      const k = `${symbol}|${tf}`;
      if (key === k) return;
      key = k;
      el.innerHTML = '<div class="loading">Memuat sinyal virtual trading</div>';
      try {
        const d = await api(`/api/training/asset?symbol=${encodeURIComponent(symbol)}&tf=${tf}`);
        if (key !== k) return;
        const live = d.journal || [];
        const current = live.find((s) => s.status === 'pending' || s.status === 'open') || live[0] || d.latest?.[0];
        const others = (live.length ? live : d.latest || []).filter((s) => s !== current).slice(0, 6);
        const bt = d.backtest?.outOfSample;
        el.innerHTML = `
          <h5>SINYAL VIRTUAL TRADING · ${esc(tf.toUpperCase())} · SMC v${d.version}</h5>
          ${current ? signalCard({ ...current, tf }, d.cancelRules, decimals) : '<div class="empty">Belum ada sinyal untuk aset & timeframe ini.</div>'}
          ${others.length ? `<h5>SINYAL SEBELUMNYA</h5><table class="co-table"><thead><tr><th>Muncul</th><th>Arah</th><th>Status</th><th>Hasil</th></tr></thead><tbody>
            ${others.map((s) => `<tr><td>${dt(s.created_t)}</td><td>${s.direction > 0 ? 'BUY' : 'SELL'}</td><td>${esc((STATUS[s.status] || [s.status])[0])}</td><td class="${s.r > 0 ? 'up' : s.r < 0 ? 'down' : ''}">${rr(s.r)}</td></tr>`).join('')}</tbody></table>` : ''}
          <h5>AKURASI ASET INI</h5>
          <div class="tr-stats">${statBlock('LIVE (VIRTUAL)', d.live)}${statBlock('BACKTEST OUT-OF-SAMPLE', bt)}</div>
          ${progress(d.live?.trades ? d.live.winRate : bt?.winRate, d.target, d.goal)}
          ${d.upgrades?.length ? `<h5>UPGRADE SMC</h5>${d.upgrades.map((u) => `<div class="note">v${u.from} → v${u.to} · ${dt(u.created)} · win rate out-of-sample ${pct(u.before?.winRate)} (${u.before?.trades ?? 0} trade) → <b class="up">${pct(u.after?.winRate)}</b> (${u.after?.trades} trade)</div>`).join('')}` : ''}
          <div class="note">Parameter aktif: pivot ${d.params.swing}/${d.params.internal} · ${esc(d.params.scope)} ${esc(d.params.kinds)} · entry ${esc(d.params.entry)} · SL ${d.params.sl_atr}×ATR · target ${d.params.target === 'liq' ? 'likuiditas impuls' : `${d.params.rr}R`}${d.params.pd_filter ? ' · filter premium/discount' : ''}${d.params.trend_filter ? ' · searah tren swing' : ''}.</div>`;
      } catch (e) {
        if (key === k) el.innerHTML = `<div class="error-box">${esc(e.message)}</div>`;
      }
    },
    reset() { key = ''; },
  };
}

function equityChart(canvas, points) {
  const w = canvas.clientWidth, h = canvas.clientHeight, dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  if (points.length < 2) {
    ctx.fillStyle = '#5d7896'; ctx.font = '11px JetBrains Mono'; ctx.textAlign = 'center';
    ctx.fillText('kurva ekuitas muncul setelah trade virtual pertama selesai', w / 2, h / 2);
    return;
  }
  const ys = points.map((p) => p[1]);
  const min = Math.min(0, ...ys), max = Math.max(0, ...ys);
  const y = (v) => h - 8 - ((v - min) / (max - min || 1)) * (h - 16);
  const x = (i) => 6 + (i / (points.length - 1)) * (w - 12);
  ctx.strokeStyle = 'rgba(141,164,191,.3)'; ctx.setLineDash([4, 4]);
  ctx.beginPath(); ctx.moveTo(0, y(0)); ctx.lineTo(w, y(0)); ctx.stroke(); ctx.setLineDash([]);
  ctx.strokeStyle = ys.at(-1) >= 0 ? '#99ff00' : '#ff2e55'; ctx.lineWidth = 2;
  ctx.beginPath();
  points.forEach((p, i) => (i ? ctx.lineTo(x(i), y(p[1])) : ctx.moveTo(x(i), y(p[1]))));
  ctx.stroke();
}

/** The AI TRAINING page. */
export const trainingPage = {
  init() {
    this.el = document.getElementById('tab-training');
    this.tab = 'board';
    this.el.innerHTML = `
      <div class="tr-kpis"></div>
      <div class="tr-grid">
        <div class="panel col-panel">
          <div class="panel-head"><span class="diamond">◆</span> PROGRES AKURASI <span class="sub">trade virtual live & backtest out-of-sample</span><span class="right time tr-updated"></span></div>
          <div class="panel-body tr-overview"></div>
          <div class="panel-head"><span class="diamond">◆</span> KURVA EKUITAS VIRTUAL <span class="sub">akumulasi R dari trade live yang selesai</span></div>
          <div class="tr-equity"><canvas></canvas></div>
        </div>
        <div class="panel tabbed col-panel">
          <div class="ptabs tr-tabs"><button data-v="board" class="active">PAPAN AKURASI ASET</button><button data-v="journal">JURNAL SINYAL</button><button data-v="upgrades">LOG UPGRADE SMC</button></div>
          <div class="scroll tr-body"></div>
        </div>
      </div>`;
    this.el.querySelector('.tr-tabs').addEventListener('click', (e) => {
      const b = e.target.closest('button[data-v]');
      if (!b) return;
      this.tab = b.dataset.v;
      this.el.querySelectorAll('.tr-tabs button').forEach((x) => x.classList.toggle('active', x === b));
      this.renderBody();
    });
    this.el.addEventListener('click', (e) => {
      const row = e.target.closest('[data-sym]');
      if (row) window.SMC.openAnalysis(row.dataset.sym, row.dataset.tf);
    });
    setInterval(() => { if (this.el.classList.contains('active')) this.load(); }, 30_000);
  },
  show() { this.load(); },
  async load() {
    try {
      this.data = await api('/api/training/summary');
      this.render();
    } catch (e) {
      this.el.querySelector('.tr-overview').innerHTML = `<div class="error-box">${esc(e.message)}</div>`;
    }
  },
  render() {
    const d = this.data;
    const live = d.live, bt = d.backtest;
    this.el.querySelector('.tr-kpis').innerHTML = [
      ['WIN RATE LIVE', pct(live.winRate), live.winRate >= d.target ? 'up' : ''], ['WIN RATE BACKTEST (OOS)', pct(bt.winRate), bt.winRate >= d.target ? 'up' : ''],
      ['TRADE LIVE SELESAI', live.trades, ''], ['EKSPEKTASI LIVE', rr(live.expectancyR), live.expectancyR > 0 ? 'up' : live.expectancyR < 0 ? 'down' : ''],
      ['POSISI / MENUNGGU', `${live.open} / ${live.pending}`, ''], ['UPGRADE SMC', d.upgradesTotal, 'up'],
      ['ASET×TF DIPANTAU / DILATIH', `${d.tracked} / ${d.tuned}`, ''],
    ].map(([l, v, c]) => `<div><span>${l}</span><b class="${c}">${v}</b></div>`).join('');
    const markets = Object.entries(d.markets || {});
    this.el.querySelector('.tr-overview').innerHTML = `
      <h5>LIVE (TRADE VIRTUAL SETELAH SINYAL MUNCUL)</h5>${progress(live.winRate, d.target, d.goal)}
      <h5>BACKTEST OUT-OF-SAMPLE (30% DATA TERAKHIR, PARAMETER AKTIF)</h5>${progress(bt.winRate, d.target, d.goal)}
      ${markets.length ? `<table class="co-table"><thead><tr><th>Pasar</th><th>Live</th><th>Trade</th><th>Backtest</th><th>Trade</th></tr></thead><tbody>
        ${markets.map(([m, v]) => `<tr><td>${esc(MARKET_NAME[m] || m)}</td><td>${pct(v.live.winRate)}</td><td>${v.live.trades}</td><td>${pct(v.backtest.winRate)}</td><td>${v.backtest.trades}</td></tr>`).join('')}</tbody></table>` : ''}
      <div class="note">Sinyal SMC dibuat otomatis, dieksekusi secara virtual (tanpa uang sungguhan) dan dicatat. Bila akurasi suatu aset
        bisa ditingkatkan, parameter SMC-nya di-upgrade otomatis: dipilih dari ${'±'}1.150 kombinasi pada 70% riwayat, lalu harus terbukti
        lebih baik di 30% riwayat terakhir (lower bound win rate) dengan ekspektasi tetap positif dan target minimal 1:1. Tujuan 99%
        ditampilkan sebagai arah perbaikan; angka di sini selalu hasil terukur, bukan janji.
        ${d.progress?.tuning ? `<br>Sedang melatih: <b>${esc(d.progress.tuning)}</b>` : ''}</div>`;
    equityChart(this.el.querySelector('.tr-equity canvas'), d.equity || []);
    this.el.querySelector('.tr-updated').textContent = `diperbarui ${new Date(d.generatedAt * 1000).toLocaleTimeString('id-ID')}`;
    this.renderBody();
  },
  renderBody() {
    const d = this.data;
    if (!d) return;
    const body = this.el.querySelector('.tr-body');
    if (this.tab === 'board') {
      body.innerHTML = d.leaderboard.length ? `<table class="co-table tr-table"><thead><tr><th>Aset</th><th>TF</th><th>v</th><th>Backtest win</th><th>Trade</th><th>Ekspektasi</th><th>Live win</th><th>Trade</th></tr></thead><tbody>
        ${d.leaderboard.map((b) => `<tr data-sym="${esc(b.symbol)}" data-tf="${esc(b.tf)}"><td><b>${esc(b.symbol)}</b><small class="dim">${esc(MARKET_NAME[b.market] || '')}</small></td><td>${esc(b.tf.toUpperCase())}</td><td>${b.version}</td>
          <td class="${b.backtest.winRate >= d.target ? 'up' : ''}">${pct(b.backtest.winRate)}</td><td>${b.backtest.trades ?? 0}</td><td>${rr(b.backtest.expectancyR)}</td>
          <td class="${b.live.winRate >= d.target ? 'up' : ''}">${pct(b.live.winRate)}</td><td>${b.live.trades}</td></tr>`).join('')}</tbody></table>`
        : '<div class="empty">Pelatihan dimulai ±2 menit setelah aplikasi dibuka, setelah data harga terkumpul.</div>';
    } else if (this.tab === 'journal') {
      body.innerHTML = d.recent.length ? `<table class="co-table tr-table"><thead><tr><th>Muncul</th><th>Aset</th><th>Arah</th><th>Entry / SL / TP</th><th>Status</th><th>Hasil</th></tr></thead><tbody>
        ${d.recent.map((s) => `<tr data-sym="${esc(s.symbol)}" data-tf="${esc(s.tf)}"><td>${dt(s.created_t)}<small class="dim">terdeteksi ${dt(s.first_seen)}</small></td>
          <td><b>${esc(s.symbol)}</b><small class="dim">${esc(s.tf.toUpperCase())}</small></td><td>${s.direction > 0 ? 'BUY' : 'SELL'}</td>
          <td>${fmtPrice(s.entry)} / ${fmtPrice(s.stop)} / ${fmtPrice(s.target)}</td><td>${esc((STATUS[s.status] || [s.status])[0])}<small class="dim">${esc(s.reason || '')}</small></td>
          <td class="${s.r > 0 ? 'up' : s.r < 0 ? 'down' : ''}">${rr(s.r)}</td></tr>`).join('')}</tbody></table>`
        : '<div class="empty">Belum ada sinyal live. Sinyal baru dicatat sejak aset mulai dipantau.</div>';
    } else {
      body.innerHTML = d.upgrades.length ? `<table class="co-table tr-table"><thead><tr><th>Waktu</th><th>Aset</th><th>Versi</th><th>Win OOS sebelum → sesudah</th><th>Trade</th><th>Parameter baru</th></tr></thead><tbody>
        ${d.upgrades.map((u) => `<tr data-sym="${esc(u.symbol)}" data-tf="${esc(u.tf)}"><td>${dt(u.created)}</td><td><b>${esc(u.symbol)}</b><small class="dim">${esc(u.tf.toUpperCase())}</small></td>
          <td>v${u.from} → v${u.to}</td><td>${pct(u.before?.winRate)} <small class="dim">(${u.before?.trades ?? 0} trade${(u.before?.trades ?? 0) < 5 ? ', bukti kurang' : ''})</small> → <b class="up">${pct(u.after?.winRate)}</b></td><td>${u.after?.trades ?? 0}</td>
          <td><small>pivot ${u.params.swing}/${u.params.internal} · ${esc(u.params.scope)} · ${esc(u.params.entry)} · ${u.params.target === 'liq' ? 'TP likuiditas' : `TP ${u.params.rr}R`} · SL ${u.params.sl_atr}×ATR</small></td></tr>`).join('')}</tbody></table>`
        : '<div class="empty">Belum ada upgrade: parameter baru hanya dipakai bila terbukti lebih akurat di data out-of-sample.</div>';
    }
  },
};

