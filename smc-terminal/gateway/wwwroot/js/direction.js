// Arah Market: risk regime, outlook per asset class, currency strength, instrument matrix, news sentiment, events.
import { $, esc, on, badge, dirHex, dirClass, fmtPct, fmtPrice, fmtScore, fmtTime, segmented, arrow, divBar, timeAgo } from './util.js';
import { heatmap } from './tv.js';

const NEWS_ASSET = { usd: 'USD', us_equities: 'US_EQ', idx: 'IDX', gold: 'GOLD', oil: 'OIL', crypto: 'CRYPTO', yields: 'USD' };
const CAT_NAME = { forex: 'Forex', us: 'Saham AS', idx: 'Saham IDX', commodity: 'Komoditas', macro: 'Makro', crypto: 'Kripto' };

let store;

function regimeColor(r) {
  return r === 'RISK-ON' ? 'var(--bull)' : r === 'RISK-OFF' ? 'var(--bear)' : 'var(--neu)';
}

function newsFor(d, key) {
  const asset = NEWS_ASSET[key];
  return d.news?.assets?.find((a) => a.key === asset);
}

function render(d) {
  const el = $('#dir-content');
  if (!d) return;
  const reg = d.regime;
  const strength = d.currencyStrength || [];
  const maxAbs = Math.max(30, ...strength.map((s) => Math.abs(s.score)));
  el.innerHTML = `
    <div class="dir-hero">
      <div class="panel regime-big">
        <div>
          <div class="eyebrow">REGIME PASAR</div>
          <div class="r" style="color:${regimeColor(reg.regime)}">${esc(reg.regime)}</div>
          <div class="h">${esc(d.headline)}</div>
          <div class="t">${esc(reg.text)}</div>
          <div class="t">Skor risk ${fmtScore(reg.score)} · diperbarui ${d.updatedAt ? timeAgo(d.updatedAt) : '–'} · ${esc(d.method)}</div>
        </div>
      </div>
      <div class="panel">
        <div class="panel-head"><span class="diamond">◆</span> PENDORONG RISK-ON / OFF</div>
        <div class="panel-body">${reg.contributions.map((c) => `
          <div class="strength-row" style="grid-template-columns: 70px 1fr 50px"><b>${esc(c.name)}</b>${divBar(c.contribution, 20)}<span class="num ${c.contribution > 0 ? 'up' : c.contribution < 0 ? 'down' : ''}" style="text-align:right">${fmtScore(c.contribution)}</span></div>`).join('')}
          <div class="note">Kontribusi positif = mendukung risk-on. VIX, JPY, CHF dan emas dihitung terbalik (safe haven).</div>
        </div>
      </div>
    </div>

    <div class="section-title"><h2>Ke Mana Arah Market?</h2><span class="sub">bias SMC harian + 4H · klik kartu untuk analisa detail</span></div>
    <div class="outlooks">${d.outlooks.map((o) => {
      const n = newsFor(d, o.key);
      const c = dirClass(o.direction);
      return `<div class="panel outlook" data-sym="${esc(o.symbols[0])}">
        <div class="glow" style="background: radial-gradient(circle at 90% 0%, ${dirHex(o.direction)}22, transparent 60%)"></div>
        <div class="t"><span>${esc(o.title.toUpperCase())}</span><span class="tag ${c}">${esc(o.confidence.toUpperCase())}</span></div>
        <div class="arrow" style="color:${dirHex(o.direction)}">${arrow(o.direction)}</div>
        <div class="l" style="color:${dirHex(o.direction)}">${esc(o.label)}</div>
        <div class="muted" style="font-size:11px">skor ${fmtScore(o.score)}${n ? ` · berita ${n.count} (${n.score > 0.1 ? '<span class="up">positif</span>' : n.score < -0.1 ? '<span class="down">negatif</span>' : 'netral'})` : ''}</div>
        <ul>${o.drivers.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>
      </div>`;
    }).join('')}</div>

    <div class="two" style="margin-top:14px">
      <div class="panel">
        <div class="panel-head"><span class="diamond">◆</span> KEKUATAN MATA UANG<span class="sub">rata-rata skor SMC semua pair G8</span></div>
        <div class="panel-body">
          ${strength.map((s) => `<div class="strength-row"><b>${esc(s.currency)}</b>${divBar(s.score, maxAbs)}<span class="num" style="text-align:right;color:${s.score > 0 ? 'var(--bull)' : 'var(--bear)'}">${fmtScore(s.score)}</span><span class="num dim" style="text-align:right">${fmtPct(s.changePct)}</span></div>`).join('')}
          ${d.bestPair ? `<div class="note" style="font-size:12px">Divergensi terbesar: <b class="accent">${esc(d.bestPair.pair)}</b> cenderung <b style="color:${dirHex(d.bestPair.direction)}">${esc(d.bestPair.direction.toUpperCase())}</b> (selisih kekuatan ${fmtScore(d.bestPair.spread)}). <a data-sym="${esc(d.bestPair.pair)}" style="cursor:pointer">Analisa →</a></div>` : ''}
        </div>
      </div>
      <div class="panel">
        <div class="panel-head"><span class="diamond">◆</span> EVENT HIGH-IMPACT · 48 JAM</div>
        <div class="panel-body">${(d.events || []).length ? d.events.map((e) => `
          <div class="strength-row" style="grid-template-columns: 110px 44px 1fr"><span class="time">${fmtTime(e.date, true)}</span><span class="ccy">${esc(e.currency)}</span><span>${esc(e.title)} <span class="dim">· f ${esc(e.forecast || '-')} · p ${esc(e.previous || '-')}</span></span></div>`).join('') : '<div class="empty">Tidak ada event high-impact 48 jam ke depan.</div>'}
          <div class="note">Volatilitas biasanya melonjak saat rilis — struktur SMC bisa ditembus palsu (liquidity sweep).</div>
        </div>
      </div>
    </div>

    <div class="panel" style="margin-top:14px">
      <div class="panel-head"><span class="diamond">◆</span> MATRIKS INSTRUMEN KUNCI<span class="right sub">klik baris untuk analisa</span></div>
      <div class="table-wrap"><table class="data"><thead><tr><th>SIMBOL</th><th class="r">HARGA</th><th class="r">1D</th><th class="r">5D</th><th>1D SMC</th><th>4H SMC</th><th class="r">SKOR</th><th>ARAH</th><th>KEYAKINAN</th></tr></thead><tbody>
        ${d.instruments.map((i) => `<tr data-sym="${esc(i.symbol)}">
          <td><span class="sym">${esc(i.symbol)}<small>${esc(i.name)}</small></span></td>
          <td class="r num">${fmtPrice(i.price, i.decimals)}</td>
          <td class="r num ${i.changePct > 0 ? 'up' : i.changePct < 0 ? 'down' : ''}">${fmtPct(i.changePct)}</td>
          <td class="r num ${i.change5dPct > 0 ? 'up' : i.change5dPct < 0 ? 'down' : ''}">${fmtPct(i.change5dPct)}</td>
          <td>${badge(i.d1 ? analysisDir(i.d1.score) : 'neutral', i.d1?.label)}</td>
          <td>${i.h4 ? badge(analysisDir(i.h4.score), i.h4.label) : '<span class="dim">–</span>'}</td>
          <td class="r num" style="color:${dirHex(i.direction)}">${fmtScore(i.score)}</td>
          <td>${badge(i.direction, i.label)}</td>
          <td>${i.aligned ? '<span class="up">✔ searah</span>' : '<span class="dim">campuran</span>'} · ${esc(i.confidence)}</td>
        </tr>`).join('')}
      </tbody></table></div>
    </div>

    <div class="panel" style="margin-top:14px">
      <div class="panel-head"><span class="diamond">◆</span> SENTIMEN BERITA PER KATEGORI · 12 JAM<span class="right sub">${d.news?.total ?? 0} headline</span></div>
      <div class="panel-body"><div class="sent-bars">${(d.news?.categories || []).map(sentCard).join('')}</div></div>
    </div>`;
}

function analysisDir(score) {
  if (score >= 50) return 'strong_bullish';
  if (score >= 20) return 'bullish';
  if (score > -20) return 'neutral';
  if (score > -50) return 'bearish';
  return 'strong_bearish';
}

export function sentCard(c) {
  const total = c.count || 0;
  const pct = (n) => (total ? (n / total) * 100 : 0);
  const color = c.score > 0.08 ? 'var(--bull)' : c.score < -0.08 ? 'var(--bear)' : 'var(--neu)';
  return `<div class="sent-card"><div class="t">${esc((CAT_NAME[c.key] || c.key).toUpperCase())}</div>
    <div class="v" style="color:${color}">${c.score > 0 ? '+' : ''}${(c.score * 100).toFixed(0)}</div>
    <div class="dim" style="font-size:10.5px">${total} berita · ▲${c.bullish} ▼${c.bearish}</div>
    <div class="stack"><i style="width:${pct(c.bullish)}%;background:var(--bull)"></i><i style="width:${pct(c.neutral)}%;background:var(--line-2)"></i><i style="width:${pct(c.bearish)}%;background:var(--bear)"></i></div></div>`;
}

export default {
  init(s) {
    store = s;
    on('direction', render);
    on('directionError', (e) => { $('#dir-content').innerHTML = `<div class="panel"><div class="panel-body"><div class="error-box">${esc(e.message)}</div></div></div>`; });
    $('#tab-arah').addEventListener('click', (e) => {
      const t = e.target.closest('[data-sym]');
      if (t) window.SMC.openAnalysis(t.dataset.sym, '4h');
    });
    segmented($('#heat-tab'), (v) => heatmap($('#tv-heat'), v));
    heatmap($('#tv-heat'), 'fx');
    if (store.direction) render(store.direction);
  },
};
