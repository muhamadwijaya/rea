// LAPORAN tab: company profile, valuation, analysts, earnings, financial statements (annual / quarterly)
// and shareholders, from /api/company (Yahoo Finance). Used by the analysis side panel.
import { api, esc, fmtPct, fmtPrice, fmtBig } from './util.js';

const ROW_LABEL = {
  'Total Revenue': 'Pendapatan', 'Gross Profit': 'Laba kotor', 'Operating Income': 'Laba operasi', EBITDA: 'EBITDA',
  'Net Income': 'Laba bersih', 'Diluted EPS': 'EPS dilusi', 'Total Assets': 'Total aset',
  'Total Liabilities Net Minority Interest': 'Total liabilitas', 'Stockholders Equity': 'Ekuitas', 'Total Debt': 'Total utang',
  'Cash And Cash Equivalents': 'Kas & setara kas', 'Ordinary Shares Number': 'Jumlah saham', 'Operating Cash Flow': 'Arus kas operasi',
  'Capital Expenditure': 'Belanja modal', 'Free Cash Flow': 'Arus kas bebas', 'Cash Dividends Paid': 'Dividen dibayar',
  'Repurchase Of Capital Stock': 'Buyback saham',
};
const SECTIONS = [['ringkasan', 'RINGKASAN'], ['income', 'LABA RUGI'], ['balance', 'NERACA'], ['cashflow', 'ARUS KAS'], ['holders', 'PEMEGANG SAHAM']];
const REC = { strong_buy: 'STRONG BUY', buy: 'BUY', hold: 'HOLD', underperform: 'UNDERPERFORM', sell: 'SELL', none: '–' };

const pct = (v) => (v == null ? '–' : fmtPct(v * 100, 1));
const num = (v) => (v == null ? '–' : Math.abs(v) >= 1000 ? fmtBig(v) : fmtPrice(v, 2));
const periodLabel = (iso, quarterly) => {
  if (!iso) return '–';
  const d = new Date(iso);
  return quarterly ? `Q${Math.floor(d.getUTCMonth() / 3) + 1} ${String(d.getUTCFullYear()).slice(2)}` : String(d.getUTCFullYear());
};

function kv(rows) {
  return `<div class="co-kv">${rows.filter(([, v]) => v != null && v !== '–').map(([k, v, cls]) => `<div><span>${k}</span><b class="${cls || ''}">${v}</b></div>`).join('')}</div>`;
}

function summaryHtml(d) {
  const v = d.valuation || {}, g = d.profitabilityGrowth || {}, a = d.analysts || {}, c = d.crypto || {};
  const earned = (d.earningsReported || []).map((e) => `<tr><td>${esc(e.date || '')}</td><td class="num">${e.epsEstimate ?? '–'}</td><td class="num">${e.reportedEps ?? '–'}</td>
    <td class="num ${e.surprisePct > 0 ? 'up' : e.surprisePct < 0 ? 'down' : ''}">${e.surprisePct == null ? '–' : fmtPct(e.surprisePct, 1)}</td></tr>`).join('');
  const next = d.nextEarnings;
  return `
    ${d.summary ? `<p class="co-summary">${esc(d.summary)}</p>` : ''}
    ${d.assetClass === 'crypto' ? `<h5>KRIPTO</h5>${kv([['Kapitalisasi', num(c.marketCap)], ['Suplai beredar', num(c.circulatingSupply)], ['Volume 24 jam', num(c.volume24Hr)]])}` : `
    <h5>VALUASI</h5>${kv([['Kapitalisasi', num(v.marketCap)], ['P/E', num(v.trailingPE)], ['Forward P/E', num(v.forwardPE)], ['P/B', num(v.priceToBook)],
      ['EV/EBITDA', num(v.enterpriseToEbitda)], ['Dividend yield', v.dividendYield == null ? '–' : fmtPct(v.dividendYield, 2)], ['Payout', pct(v.payoutRatio)], ['Beta', num(v.beta)]])}
    <h5>PROFITABILITAS & PERTUMBUHAN</h5>${kv([['Margin bersih', pct(g.profitMargins)], ['Margin operasi', pct(g.operatingMargins)], ['ROE', pct(g.returnOnEquity)],
      ['Pertumbuhan pendapatan', pct(g.revenueGrowth), g.revenueGrowth > 0 ? 'up' : 'down'], ['Pertumbuhan laba', pct(g.earningsGrowth), g.earningsGrowth > 0 ? 'up' : 'down'],
      ['Utang/ekuitas', num(g.debtToEquity)], ['Arus kas bebas', num(g.freeCashflow)], ['Total kas', num(g.totalCash)]])}
    <h5>ANALIS</h5>${kv([['Rekomendasi', REC[a.recommendationKey] || esc(a.recommendationKey || '–')], ['Jumlah analis', a.numberOfAnalystOpinions],
      ['Target rata-rata', num(a.targetMeanPrice)], ['Target tinggi / rendah', a.targetHighPrice ? `${num(a.targetHighPrice)} / ${num(a.targetLowPrice)}` : '–'],
      ['Potensi ke target', a.upsideToMeanTargetPct == null ? '–' : fmtPct(a.upsideToMeanTargetPct, 1), a.upsideToMeanTargetPct > 0 ? 'up' : 'down'],
      ['EPS trailing → forward', a.trailingEps ? `${a.trailingEps} → ${a.forwardEps ?? '–'}` : '–']])}
    ${earned ? `<h5>KEJUTAN LABA</h5><table class="co-table"><thead><tr><th>Tanggal</th><th>Estimasi</th><th>Aktual</th><th>Kejutan</th></tr></thead><tbody>${earned}</tbody></table>` : ''}
    ${next ? `<div class="note">Laporan berikutnya: <b>${esc(next.date || '–')}</b>${next.epsConsensus != null ? ` · konsensus EPS ${next.epsConsensus}` : next.epsEstimate != null ? ` · estimasi EPS ${next.epsEstimate}` : ''}${next.revenueConsensus ? ` · pendapatan ${num(next.revenueConsensus)}` : ''}</div>` : ''}`}`;
}

function statementHtml(st, quarterly) {
  const s = st?.[quarterly ? 'quarterly' : 'annual'];
  if (!s) return '<div class="empty">Laporan tidak tersedia.</div>';
  const head = s.periods.map((p) => `<th>${periodLabel(p, quarterly)}</th>`).join('');
  const body = Object.entries(s.rows).map(([row, vals]) => `<tr><td>${esc(ROW_LABEL[row] || row)}</td>${vals.map((v, i) => {
    const prev = vals[i + 1];
    const g = v != null && prev ? ((v - prev) / Math.abs(prev)) * 100 : null;
    return `<td class="num">${row === 'Diluted EPS' ? (v ?? '–') : num(v)}${g != null && Math.abs(g) < 1000 ? `<small class="${g > 0 ? 'up' : 'down'}">${fmtPct(g, 0)}</small>` : ''}</td>`;
  }).join('')}</tr>`).join('');
  return `<table class="co-table"><thead><tr><th></th>${head}</tr></thead><tbody>${body}</tbody></table>
    <div class="note">Angka dalam mata uang laporan; persen kecil = perubahan vs periode sebelumnya.</div>`;
}

function holdersHtml(h, notes) {
  if (!h) return '<div class="empty">Data pemegang saham tidak tersedia.</div>';
  const b = h.breakdown || {};
  const bar = (label, v) => (v == null ? '' : `<div class="co-bar"><span>${label}</span><i><b style="width:${Math.min(100, v * 100)}%"></b></i><em>${pct(v)}</em></div>`);
  const table = (rows, title) => (rows?.length ? `<h5>${title}</h5><table class="co-table"><thead><tr><th>Pemegang</th><th>Lembar</th><th>%</th><th>Δ</th></tr></thead><tbody>
    ${rows.map((r) => `<tr><td>${esc(r.holder || '')}<small class="dim">${esc(r.date || '')}</small></td><td class="num">${num(r.shares)}</td><td class="num">${pct(r.pct)}</td>
      <td class="num ${r.pctChange > 0 ? 'up' : r.pctChange < 0 ? 'down' : ''}">${r.pctChange == null ? '–' : pct(r.pctChange)}</td></tr>`).join('')}</tbody></table>` : '');
  const insiders = h.insiders?.length ? `<h5>ORANG DALAM</h5><table class="co-table"><thead><tr><th>Nama</th><th>Lembar</th><th>Transaksi terakhir</th></tr></thead><tbody>
    ${h.insiders.map((r) => `<tr><td>${esc(r.name || '')}<small class="dim">${esc(r.position || '')}</small></td><td class="num">${num(r.shares)}</td><td>${esc(r.transaction || '–')}<small class="dim">${esc(r.date || '')}</small></td></tr>`).join('')}</tbody></table>` : '';
  return `${bar('Orang dalam', b.insidersPercentHeld)}${bar('Institusi', b.institutionsPercentHeld)}${bar('Institusi (dari float)', b.institutionsFloatPercentHeld)}
    ${b.institutionsCount ? `<div class="note">${num(b.institutionsCount)} institusi tercatat</div>` : ''}
    ${table(h.institutions, 'INSTITUSI TERBESAR')}${table(h.funds, 'REKSA DANA TERBESAR')}${insiders}
    ${notes?.length ? `<div class="note">${esc(notes.join(' · '))}</div>` : ''}`;
}

/** Mounts the LAPORAN view; call `load(symbol)` whenever the shown asset changes. */
export function mountCompany(el) {
  let data = null;
  let section = 'ringkasan';
  let quarterly = false;
  let loadingFor = '';
  const render = () => {
    if (!data) return;
    if (data.unavailable) { el.innerHTML = `<div class="empty">${esc(data.notes?.[0] || 'Tidak tersedia.')}</div>`; return; }
    const isStatement = ['income', 'balance', 'cashflow'].includes(section);
    el.innerHTML = `<div class="co-head"><b>${esc(data.name || data.symbol)}</b><span class="dim">${esc([data.sector, data.industry, data.currency].filter(Boolean).join(' · '))}</span></div>
      <div class="seg co-sections">${SECTIONS.map(([k, l]) => `<button data-s="${k}" class="${k === section ? 'active' : ''}">${l}</button>`).join('')}</div>
      ${isStatement ? `<div class="seg co-period"><button data-q="0" class="${quarterly ? '' : 'active'}">TAHUNAN</button><button data-q="1" class="${quarterly ? 'active' : ''}">KUARTALAN</button></div>` : ''}
      <div class="co-body">${section === 'ringkasan' ? summaryHtml(data) : section === 'holders' ? holdersHtml(data.holders, data.notes) : statementHtml(data.statements?.[section], quarterly)}</div>
      <div class="note">Sumber: Yahoo Finance · ${data.fetchedAt ? new Date(data.fetchedAt * 1000).toLocaleString('id-ID') : ''}</div>`;
  };
  el.addEventListener('click', (e) => {
    const s = e.target.closest('[data-s]');
    if (s) { section = s.dataset.s; render(); return; }
    const q = e.target.closest('[data-q]');
    if (q) { quarterly = q.dataset.q === '1'; render(); }
  });
  return {
    async load(symbol) {
      if (!symbol || (data && data.symbol === symbol) || loadingFor === symbol) return;
      loadingFor = symbol;
      el.innerHTML = '<div class="loading">Memuat laporan perusahaan</div>';
      try {
        const d = await api(`/api/company?symbol=${encodeURIComponent(symbol)}`);
        if (loadingFor !== symbol) return;
        data = d;
        render();
      } catch (e) {
        el.innerHTML = `<div class="error-box">${esc(e.message)}</div>`;
      } finally {
        if (loadingFor === symbol) loadingFor = '';
      }
    },
  };
}
