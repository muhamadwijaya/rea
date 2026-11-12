// Virtual trading ("AI training"): the PLAN card of one asset (signal timestamps, auto-cancel rules, virtual
// position, track record, SMC upgrades) and the AI TRAINING page (overall accuracy, equity, leaderboard,
// journal, upgrade log, live chart of virtual trades). Data: /api/training/asset, /summary and /chart.
import { api, esc, fmtPrice } from './util.js';
import { SmcChart } from './smc-chart.js';
import { accountInputs, accountLine, accountStats, effectiveSpec, fmtMoney, tradeMoney } from './account.js';

const STATUS = {
  pending: ['MENUNGGU ENTRY', 'neu'], open: ['POSISI TERBUKA', 'bull'], win: ['PROFIT ✔', 'bull'], loss: ['LOSS ✘', 'bear'],
  timeout: ['DITUTUP WAKTU', 'ghost'], cancelled: ['BATAL OTOMATIS', 'ghost'],
};
const MARKET_NAME = { forex: 'FOREX', 'saham-as': 'SAHAM AS', 'saham-idx': 'SAHAM IDX', kripto: 'KRIPTO' };
const SOURCE = { asset: ['UPGRADE ASET', 'bull'], group: ['PARAMETER GRUP', 'info'], default: ['DEFAULT', 'ghost'] };
const CONFLUENCE = { sweep: 'wajib sweep likuiditas', fvg: 'wajib FVG', both: 'wajib sweep + FVG' };
const SCOPE = { both: 'swing + internal', swing: 'swing', internal: 'internal' };
const ruleText = (p) => `pivot ${p.swing}/${p.internal} · semua BOS/CHoCH (tanpa skip) · area FVG (tanpa FVG: order block) entry ${p.entry}
  · SL ATR band ${p.sl_atr}× · TP1 puncak BOS/CHoCH (50%, lalu SL ke entry) · TP2 +${p.ext}× leg${p.hold > 0 ? ` · tutup paksa ${p.hold} bar` : ' · tanpa batas waktu'}
  · order kedaluwarsa ${p.expiry} bar`;
// settings of earlier versions still appear in the upgrade log
const legacyText = (p) => `versi lama · pivot ${p.swing}/${p.internal} · ${SCOPE[p.scope] || p.scope} ${p.kinds === 'CHoCH' ? 'CHoCH saja' : 'BOS+CHoCH'} · entry ${p.entry} · SL ${p.sl_atr}×ATR · ${p.target === 'liq' ? 'TP likuiditas' : `TP ${p.rr}R`}${p.manage === 'partial' ? ' · TP1 50% di +1R lalu SL ke entry' : ''}${CONFLUENCE[p.confluence] ? ` · ${CONFLUENCE[p.confluence]}` : ''}${p.pd_filter ? ' · premium/discount' : ''}${p.trend_filter ? ' · searah tren' : ''}`;
const paramText = (p) => (p.target === 'tp12' && p.zone === 'fvg' ? ruleText(p) : legacyText(p));
// Precision level reached by a setting (lower bound of its proven win rate); thresholds come from the engine.
function levelTag(modes, qualified, low) {
  if (!qualified) return '<span class="tag ghost">BAYANGAN</span>';
  const hit = [...(modes || [])].sort((a, b) => b.low - a.low).find((m) => (low ?? 0) >= m.low);
  return `<span class="tag bull">${esc(hit?.label || 'NORMAL')}</span>`;
}
const modeText = (m) => (m.mtf ? 'EXPERT · MTF' : `${m.label} ≥${Math.round(m.low * 100)}%`);
// EXPERT: a proven setting whose trade the two higher timeframes agree with (alignment stamped when it appeared)
const expertTag = (s) => (s?.qualified && s.align != null && s.align >= 0.5 ? ' <span class="tag info">EXPERT</span>' : '');
const arrowOf = (d) => (d > 0 ? '<b class="up">▲ naik</b>' : d < 0 ? '<b class="down">▼ turun</b>' : '–');
const BUCKETS = [['aligned', 'TF lebih tinggi searah'], ['mixed', 'campuran'], ['against', 'TF lebih tinggi berlawanan'], ['unknown', 'tanpa data TF tinggi']];

/** Context of the PLAN card: multi-timeframe direction and evidence, 1-minute trigger, news, calendar. */
function contextHtml(d) {
  const cons = d.consensus;
  const mtf = d.mtf?.buckets || {};
  const trig = d.trigger1m;
  const when = (t) => new Date(t * 1000).toLocaleString('id-ID', { weekday: 'short', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
  const rows = [];
  if (cons?.up != null) {
    rows.push(`<div><span>MULTI TIMEFRAME</span><b class="${cons.dominant === 'naik' ? 'up' : cons.dominant === 'turun' ? 'down' : 'flat'}">▲ ${cons.up}% · ▼ ${cons.down}%</b>
      ${cons.tfs.map((t) => `<span class="chip ${t.trend > 0 ? 'up' : t.trend < 0 ? 'down' : ''}">${esc(t.tf.toUpperCase())} ${t.trend > 0 ? '▲' : t.trend < 0 ? '▼' : '–'}</span>`).join('')}</div>`);
  }
  if (mtf.aligned?.trades || mtf.mixed?.trades || mtf.against?.trades) {
    rows.push(`<div><span>BUKTI MTF ASET INI</span>${BUCKETS.filter(([k]) => mtf[k]?.trades).map(([k, l]) => `${l}: <b>${pct(mtf[k].winRate)}</b> <span class="dim">(${mtf[k].trades})</span>`).join(' · ')}</div>`);
  }
  if (trig) {
    rows.push(`<div><span>PEMICU 1M</span>${trig.pattern ? `<b class="${trig.direction === 'bullish' ? 'up' : 'down'}">${esc(trig.pattern)}</b> ${trig.barsAgo} mnt lalu ·
      di aset ini searah dalam ${trig.horizonMin} mnt: <b>${pct(trig.hitRate)}</b> <span class="dim">dari ${trig.n} kejadian</span>`
      : `<span class="dim">${esc(trig.unavailable || 'belum ada pola candlestick baru')}</span>`}</div>`);
  }
  if (d.news) rows.push(`<div><span>BERITA 24 JAM</span><b class="${d.news.score > 0.15 ? 'up' : d.news.score < -0.15 ? 'down' : 'flat'}">${d.news.score > 0 ? '+' : ''}${d.news.score}</b> <span class="dim">dari ${d.news.n} berita bertag</span></div>`);
  if (d.calendar?.length) {
    rows.push(`<div><span>KALENDER EKONOMI</span><ul class="tr-cal">${d.calendar.map((e) => `<li><b>${esc(e.currency)}</b> ${esc(e.title)} <span class="dim">${when(e.ts)} · ${esc(e.impact || '')}</span>
      · forecast ${esc(e.forecast ?? '–')} <span class="dim">(sebelumnya ${esc(e.previous ?? '–')})</span> · di atas forecast → ${arrowOf(e.ifAbove)} · di bawah → ${arrowOf(e.ifBelow)}</li>`).join('')}</ul></div>`);
  }
  return rows.length ? `<div class="tr-ctx">${rows.join('')}</div>` : '';
}

const PRECISION_KEY = 'sharx.precision';
const loadPrecision = () => { try { return localStorage.getItem(PRECISION_KEY) || 'normal'; } catch { return 'normal'; } };
const savePrecision = (v) => { try { localStorage.setItem(PRECISION_KEY, v); } catch { /* per-viewer convenience only */ } };

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

// ICT checklist of a setup (engine: training.ict_checks): what an ICT trader looks for before calling it a setup
const ICT_CHECKS = [['sweep', 'sweep likuiditas'], ['displacement', 'displacement'], ['fvg', 'FVG'], ['zone', 'discount/premium'],
  ['htf', 'searah TF besar'], ['killzone', 'killzone']];
const ICT_GRADE = { 'A+': 'bull', A: 'bull', B: 'info', C: 'ghost' };
const ictTag = (ict) => (ict?.grade ? `<span class="tag ${ICT_GRADE[ict.grade]}" title="Grade ICT: A+ = semua syarat, A = sweep + displacement + FVG + discount/premium">ICT ${esc(ict.grade)}</span>` : '');
const ictChecks = (ict) => (ict?.grade ? `<div class="chips tr-ict">${ICT_CHECKS.filter(([k]) => ict[k] != null)
  .map(([k, name]) => `<span class="chip ${ict[k] ? 'up' : 'dim'}">${ict[k] ? '✓' : '✗'} ${name}</span>`).join('')}</div>` : '');
// results per ICT grade (all bars of the chart / the session): does following ICT more strictly win more often?
const gradeLine = (g) => (g ? `<span class="tr-grades">ICT: ${['ICT', 'A+', 'A', 'B', 'C'].map((k) => `${k === 'ICT' ? 'A+/A' : k} <b>${pct(g[k]?.winRate)}</b>
  <span class="dim">(${g[k]?.trades ?? 0}, ${rr(g[k]?.expectancyR)})</span>`).join(' · ')}</span>` : '');

function signalCard(s, rules, decimals, modes) {
  const [label, cls] = STATUS[s.status] || [s.status, 'ghost'];
  const f = (v) => fmtPrice(v, decimals);
  const risk = Math.abs(s.entry - s.stop);
  return `<div class="tr-signal">
    <div class="tr-sig-top">${dirTag(s.direction)} <span class="tag ${cls}">${label}</span>${s.qualified != null ? ` ${levelTag(modes, s.qualified, s.quality ?? 0.5)}${expertTag(s)}` : ''} ${ictTag(s.ict)} <span class="dim">${esc(s.kind || '')} ${esc(s.scope || '')} · ${esc(s.tf)}</span></div>
    ${ictChecks(s.ict)}
    <div class="tr-times">
      <div><span>SINYAL MUNCUL</span><b>${dt(s.created_t)}</b></div>
      <div><span>TERDETEKSI SISTEM</span><b>${s.first_seen ? dt(s.first_seen) : 'riwayat (backtest)'}</b></div>
      <div><span>KEDALUWARSA</span><b>${dt(s.expiry_t)}</b></div>
      <div><span>ENTRY TERPICU</span><b>${dt(s.opened_t)}</b></div>
      <div><span>SELESAI</span><b>${dt(s.closed_t)}</b></div>
    </div>
    <div class="chips"><span class="chip">entry ${f(s.entry)}</span><span class="chip">SL ${f(s.stop)}</span>
      ${s.tp1 != null ? `<span class="chip" title="Puncak BOS/CHoCH: 50% ditutup, SL pindah ke entry">TP1 ${f(s.tp1)} · RR ${risk ? (Math.abs(s.tp1 - s.entry) / risk).toFixed(2) : '–'}</span>` : ''}
      <span class="chip">${s.tp1 != null ? 'TP2' : 'TP'} ${f(s.target)}</span><span class="chip">RR ${risk ? (Math.abs(s.target - s.entry) / risk).toFixed(2) : '–'}</span>
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
        const ev = d.evidence || {};
        const [srcLabel, srcCls] = SOURCE[d.source] || SOURCE.default;
        el.innerHTML = `
          <h5>SINYAL VIRTUAL TRADING · ${esc(tf.toUpperCase())} · SMC v${d.version}</h5>
          <div class="tr-sig-top">${levelTag(d.modes, ev.qualified, ev.winRateLow)} <span class="tag ${srcCls}">${srcLabel}</span>
            <span class="dim">bukti ${pct(ev.winRate)} dari ${ev.trades ?? 0} trade (batas bawah ${pct(ev.winRateLow)})</span></div>
          ${contextHtml(d)}
          ${ev.qualified ? '' : '<div class="note">Belum terkualifikasi: sinyal tetap dicatat sebagai trade bayangan untuk belajar, tetapi tidak dihitung sebagai trade AI sampai buktinya cukup (batas bawah win rate ≥ 50% dengan ekspektasi positif).</div>'}
          ${current ? signalCard({ ...current, tf }, d.cancelRules, decimals, d.modes) : '<div class="empty">Belum ada sinyal untuk aset & timeframe ini.</div>'}
          ${others.length ? `<h5>SINYAL SEBELUMNYA</h5><table class="co-table"><thead><tr><th>Muncul</th><th>Arah</th><th>Status</th><th>Hasil</th></tr></thead><tbody>
            ${others.map((s) => `<tr><td>${dt(s.created_t)}</td><td>${s.direction > 0 ? 'BUY' : 'SELL'}</td><td>${esc((STATUS[s.status] || [s.status])[0])}</td><td class="${s.r > 0 ? 'up' : s.r < 0 ? 'down' : ''}">${rr(s.r)}</td></tr>`).join('')}</tbody></table>` : ''}
          <h5>AKURASI ASET INI</h5>
          <div class="tr-stats">${statBlock('LIVE TERKUALIFIKASI', d.live)}${statBlock('BACKTEST OUT-OF-SAMPLE', bt)}</div>
          ${progress(d.live?.trades ? d.live.winRate : bt?.winRate, d.target, d.goal)}
          ${d.liveAll?.trades ? `<div class="note">Termasuk trade bayangan: ${pct(d.liveAll.winRate)} dari ${d.liveAll.trades} trade.</div>` : ''}
          ${d.upgrades?.length ? `<h5>UPGRADE SMC</h5>${d.upgrades.map((u) => `<div class="note">v${u.from} → v${u.to} · ${dt(u.created)} · win rate out-of-sample ${pct(u.before?.winRate)} (${u.before?.trades ?? 0} trade) → <b class="up">${pct(u.after?.winRate)}</b> (${u.after?.trades} trade)</div>`).join('')}` : ''}
          <div class="note">Parameter aktif (${srcLabel.toLowerCase()}): ${esc(paramText(d.params))}.</div>`;
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

// ------------------------------------------------------------------ live training chart

const TF_SECONDS = { '1m': 60, '5m': 300, '10m': 600, '15m': 900, '1h': 3600, '4h': 14400, '1d': 86400, '1w': 604800 };
const SMC_ON = { swing: true, internal: true, pivots: false, ob: false, fvg: false, eq: false, liquidity: false, zones: false, ema: false, plan: false };
const SMC_OFF = Object.fromEntries(Object.keys(SMC_ON).map((k) => [k, false]));
const CHART_KEY = 'sharx.trainingChart';
const loadChartPrefs = () => { try { return JSON.parse(localStorage.getItem(CHART_KEY)) || {}; } catch { return {}; } };
const saveChartPrefs = (v) => { try { localStorage.setItem(CHART_KEY, JSON.stringify(v)); } catch { /* per-viewer convenience only */ } };
const REPLAY_BASE = 3;  // candles per second at ×1
const SPEEDS = [1, 2, 3, 4, 5, 6, 7];
const dateText = (t, tf) => new Date(t * 1000).toLocaleString('id-ID', TF_SECONDS[tf] >= 86400
  ? { day: '2-digit', month: 'short', year: 'numeric' } : { day: '2-digit', month: 'short', year: '2-digit', hour: '2-digit', minute: '2-digit' });

// A trade as it looked at time T of a replay (null = its signal has not appeared yet).
/** Closed trades get their money label for the chart (`spec` from account.effectiveSpec). */
function moneyLabels(trades, spec, lot) {
  if (!spec) return trades;
  return trades.map((t) => {
    const m = tradeMoney(t, spec, lot);
    return m == null ? t : { ...t, money: fmtMoney(m, spec.account, true) };
  });
}

function tradeAt(s, T) {
  if (s.created_t > T) return null;
  if (s.closed_t != null && s.closed_t <= T) return s;
  if (s.status === 'cancelled' || !s.opened_t || s.opened_t > T) return { ...s, status: 'pending', opened_t: null, closed_t: null, r: null, exit: null, tp1_t: null, money: null };
  return { ...s, status: 'open', closed_t: null, r: null, exit: null, money: null, tp1_t: s.tp1_t != null && s.tp1_t <= T ? s.tp1_t : null };
}
const clock = (d) => d.toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).replace(/\./g, ':');

/** LIVE CHART tab: candles of one training asset with its virtual trades, merged with live 1-minute bars. */
function mountTrainingChart(el) {
  el.innerHTML = `
    <div class="tr-live-bar">
      <select class="input tr-live-pick" title="Aset & timeframe yang dilatih"></select>
      <button class="tr-follow" title="Pindah otomatis ke aset yang sedang dilatih di TRAINING LAB, atau ke aktivitas trade virtual terbaru">IKUTI TRAINING</button>
      <button class="tr-smc" title="Tampilkan struktur SMC (BOS/CHoCH swing & internal)">SMC</button>
      <button class="tr-oos" title="Perbesar ke seluruh periode data uji (out-of-sample)">DATA UJI</button>
      <span class="tr-replay-ctl">
        <button class="tr-play" title="Putar ulang data uji (yang tidak dipakai melatih) candle demi candle">▶ REPLAY</button>
        <span class="tr-speed" title="Kecepatan replay: ×1 = ${REPLAY_BASE} candle per detik">${SPEEDS.map((v) => `<button data-speed="${v}">×${v}</button>`).join('')}</span>
        <button class="tr-end hidden" title="Langsung ke akhir replay">⏭</button>
        <button class="tr-stop hidden" title="Hentikan replay, kembali ke chart live">■ LIVE</button>
      </span>
      <span class="tr-acct-host"></span>
      <span class="tr-live-info"></span>
    </div>
    <div class="chart-shell tr-live-chart"><div class="chart-host"></div><div class="chart-live"></div><div class="chart-replay hidden"></div></div>
    <div class="tr-live-foot"></div>`;
  const $ = (q) => el.querySelector(q);
  const prefs = { follow: false, smc: true, speed: 4, ...loadChartPrefs() };
  let chart = null, current = null, summary = null, chartData = null, liveState = null, busy = false, seq = 0;
  let replay = null;  // { full, i, start, playing, acc, last, raf }
  const visible = () => el.offsetParent !== null && !document.hidden;
  const save = () => saveChartPrefs({ ...prefs, symbol: current?.symbol, tf: current?.tf });
  // virtual account: balance & lot → money per trade (the trades themselves do not change)
  const acct = accountInputs(() => refreshMoney());
  $('.tr-acct-host').append(acct.el);
  const spec = () => effectiveSpec(chartData?.contract);
  const withMoney = (trades) => moneyLabels(trades, spec(), acct.lot());
  const refreshMoney = () => {
    if (replay) showReplay();
    else if (chartData && chart) { chart.setTrades({ ...chartData, trades: withMoney(chartData.trades) }); render(); }
  };
  const syncButtons = () => {
    $('.tr-follow').classList.toggle('active', prefs.follow);
    $('.tr-smc').classList.toggle('active', prefs.smc);
    el.querySelectorAll('.tr-speed button').forEach((b) => b.classList.toggle('active', +b.dataset.speed === prefs.speed));
    $('.tr-play').textContent = !replay ? '▶ REPLAY' : replay.playing ? '⏸ JEDA' : replay.i >= replay.full.candles.length ? '↺ ULANG' : '▶ LANJUT';
    $('.tr-play').classList.toggle('active', !!replay?.playing);
    $('.tr-end').classList.toggle('hidden', !replay);
    $('.tr-stop').classList.toggle('hidden', !replay);
    $('.chart-replay').classList.toggle('hidden', !replay);
    $('.chart-live').classList.toggle('hidden', !!replay);
  };

  // ---------------------------------------------------------------- replay of the out-of-sample period
  function startReplay() {
    const full = chartData;
    if (!full?.candles?.length || !chart) return;
    const start = Math.max(1, full.candles.findIndex((c) => c.t >= full.split));
    replay = { full, i: start, start, playing: true, acc: 0, last: performance.now(), raf: 0 };
    chart.setData({ ...full, candles: full.candles.slice(0, start).map((c) => ({ ...c })) });
    showReplay();
    replay.raf = requestAnimationFrame(tick);
    syncButtons();
  }

  function tick(now) {
    if (!replay) return;
    const dt = Math.min(0.25, (now - replay.last) / 1000);
    replay.last = now;
    if (replay.playing) {
      replay.acc += dt * REPLAY_BASE * prefs.speed;
      const n = Math.floor(replay.acc);
      if (n) {
        replay.acc -= n;
        try {
          advance(n);
        } catch (e) {
          replay.playing = false;  // keep what is shown; the error is visible instead of a frozen replay
          syncButtons();
          $('.chart-replay').insertAdjacentHTML('beforeend', `<div class="down">${esc(e.message)}</div>`);
        }
      }
    }
    if (replay) replay.raf = requestAnimationFrame(tick);
  }

  function advance(n) {
    const { full } = replay;
    for (let k = 0; k < n && replay.i < full.candles.length; k++) chart.appendCandle({ ...full.candles[replay.i++] });
    if (replay.i >= full.candles.length) replay.playing = false;
    showReplay();
    if (!replay.playing) syncButtons();
  }

  function showReplay() {
    const { full, i, start } = replay;
    const T = full.candles[i - 1].t;
    chart.until = T;
    chart.setTrades({ ...full, trades: withMoney(full.trades.map((s) => tradeAt(s, T)).filter(Boolean)) });
    const from = full.candles[start].t;
    const done = full.trades.filter((s) => s.created_t >= from && s.closed_t != null && s.closed_t <= T && ['win', 'loss', 'timeout'].includes(s.status));
    const wins = done.filter((s) => s.r > 0).length;
    const total = done.reduce((a, s) => a + (s.r || 0), 0);
    const active = full.trades.filter((s) => { const v = tradeAt(s, T); return v && s.status !== 'cancelled' && (v.status === 'open' || v.status === 'pending'); }).length;
    const progressPct = ((i - start) / Math.max(1, full.candles.length - start)) * 100;
    $('.chart-replay').innerHTML = `
      <div class="rp-head"><b>REPLAY ×${prefs.speed}</b><span>${esc(dateText(T, full.tf))}</span><span class="dim">${i >= full.candles.length ? 'SELESAI' : `${Math.round(progressPct)}%`}</span></div>
      <div class="rp-bar"><i style="width:${progressPct.toFixed(1)}%"></i></div>
      <div class="rp-stats"><span>selesai <b>${done.length}</b></span><span class="up">✔ ${wins}</span><span class="down">✘ ${done.length - wins}</span>
        <span>win <b class="${done.length ? (wins / done.length >= 0.5 ? 'up' : 'down') : ''}">${done.length ? pct(wins / done.length) : '–'}</b></span>
        <span class="${total > 0 ? 'up' : total < 0 ? 'down' : ''}">${total > 0 ? '+' : ''}${total.toFixed(2)}R</span><span class="dim">aktif ${active}</span></div>
      ${spec() ? `<div class="rp-note">${accountLine(accountStats(done, spec(), acct.balance(), acct.lot()), spec(), acct.lot())}</div>` : ''}
      <div class="dim rp-note">data uji: tidak dipakai saat melatih · hasil = backtest yang diputar ulang, bukan trade live</div>`;
  }

  function stopReplay(reload = true) {
    if (!replay) return;
    cancelAnimationFrame(replay.raf);
    replay = null;
    if (chart) chart.until = null;
    syncButtons();
    if (reload) load(false);
  }

  function options() {
    const board = summary?.leaderboard || [];
    const opt = (b) => `<option value="${esc(b.symbol)}|${esc(b.tf)}">${esc(b.symbol)} · ${esc(b.tf.toUpperCase())} · ${esc(MARKET_NAME[b.market] || b.market)}</option>`;
    const proven = board.filter((b) => b.qualified), shadow = board.filter((b) => !b.qualified);
    let html = (proven.length ? `<optgroup label="LOLOS MODE PRESISI">${proven.map(opt).join('')}</optgroup>` : '')
      + (shadow.length ? `<optgroup label="BAYANGAN (BELUM TERBUKTI)">${shadow.map(opt).join('')}</optgroup>` : '');
    if (current && !board.some((b) => b.symbol === current.symbol && b.tf === current.tf)) {
      html = `<option value="${esc(current.symbol)}|${esc(current.tf)}">${esc(current.symbol)} · ${esc(current.tf.toUpperCase())}</option>${html}`;
    }
    const pick = $('.tr-live-pick');
    pick.innerHTML = html || '<option value="">belum ada aset terlatih</option>';
    if (current) pick.value = `${current.symbol}|${current.tf}`;
  }

  function followTarget() {
    const lab = summary?.lab;
    if (lab?.state === 'running') {  // the lab's asset; while it downloads or trains a whole group, stay where we are
      return lab.current?.symbol ? { symbol: lab.current.symbol, tf: lab.current.tf, bars: lab.bars } : null;
    }
    const a = summary?.activity?.[0];
    if (a) return { symbol: a.symbol, tf: a.tf };
    const t = summary?.progress?.lastTuned;
    return t ? { symbol: t.symbol, tf: t.tf } : null;
  }

  async function open(symbol, tf, bars) {
    stopReplay(false);
    current = { symbol, tf, bars };
    save();
    options();
    await load(false);
  }

  async function load(keepRange) {
    if (!current) return;
    const my = ++seq;
    const { symbol, tf } = current;
    if (!chart) {
      chart = new SmcChart($('.chart-host'), $('.tr-live-chart'));
      chart.setLayers(prefs.smc ? SMC_ON : SMC_OFF);
    }
    try {
      const t = await api(`/api/training/chart?symbol=${encodeURIComponent(symbol)}&tf=${tf}${current.bars ? `&bars=${current.bars}` : ''}`);
      if (my !== seq || replay) return;
      chartData = t;
      chart.setData({ ...t, candles: t.candles.map((c) => ({ ...c })) }, { keepRange });
      acct.market(t.instrument?.market);
      chart.setTrades({ ...t, trades: withMoney(t.trades) });
      if (!keepRange) liveState = null;
      render();
      pollLive();
    } catch (e) {
      if (my === seq) $('.tr-live-foot').innerHTML = `<span class="down">${esc(e.message)}</span>`;
    }
  }

  async function pollLive() {
    if (!chart?.data || !current || busy || replay || !visible()) return;
    const { symbol, tf } = current;
    busy = true;
    try {
      const res = await api(`/api/live?symbol=${encodeURIComponent(symbol)}`);
      if (current?.symbol !== symbol || current?.tf !== tf || replay) return;  // a replay may have started meanwhile
      chart.live(res.bars, TF_SECONDS[tf]);
      liveState = { asOf: res.asOf, received: Date.now(), error: null };
    } catch (e) {
      liveState = { ...(liveState || {}), error: e.message };
    } finally {
      busy = false;
    }
    renderClock();
  }

  function renderClock() {
    const box = $('.chart-live');
    if (!box || !visible()) return;
    const now = new Date();
    const age = liveState?.asOf ? now.getTime() / 1000 - liveState.asOf : null;
    const fresh = age != null && age < 5 * 60;
    box.innerHTML = `${liveState?.error ? '<span class="down">● LIVE TERPUTUS</span>'
      : !liveState ? '<span class="dim">● menghubungkan…</span>'
      : fresh ? '<span class="live-on">● LIVE</span>'
      : `<span class="flat">● PASAR TUTUP</span> <span class="dim">data terakhir ${clock(new Date(liveState.asOf * 1000)).slice(0, 5)}</span>`}<b class="num">${clock(now)}</b>`;
  }

  function render() {
    const t = chartData;
    if (!t) return;
    const ev = t.evidence || {};
    const [srcLabel, srcCls] = SOURCE[t.source] || SOURCE.default;
    $('.tr-live-info').innerHTML = `${levelTag(t.modes, ev.qualified, ev.winRateLow)} <span class="tag ${srcCls}">${srcLabel} v${t.version}</span>
      <span class="dim">${esc(paramText(t.params))}</span>`;
    const live = t.trades.filter((x) => x.live);
    const open = t.trades.filter((x) => x.status === 'open' || x.status === 'pending');
    const oos = t.outOfSample || {};
    $('.tr-live-foot').innerHTML = `
      <span>DATA UJI (OOS): <b class="${oos.winRate >= 0.5 ? 'up' : oos.winRate != null ? 'down' : ''}">${pct(oos.winRate)}</b> dari ${oos.trades ?? 0} trade · ekspektasi ${rr(oos.expectancyR)}</span>
      <span>LIVE: <b>${pct(t.live?.winRate)}</b> dari ${t.live?.trades ?? 0} selesai · ${live.length} sinyal tercatat</span>
      <span>AKTIF: ${open.length}</span>
      ${gradeLine(t.grades)}
      ${spec() ? accountLine(accountStats(t.trades, spec(), acct.balance(), acct.lot()), spec(), acct.lot()) : ''}
      <span class="tr-legend"><i class="lg-tp"></i>zona TP <i class="lg-sl"></i>zona SL <i class="lg-1r"></i>TP1 puncak / TP2 <i class="lg-live"></i>LIVE <i class="lg-is"></i>data latih (redup)</span>`;
  }

  $('.tr-live-pick').addEventListener('change', (e) => {
    const [symbol, tf] = e.target.value.split('|');
    if (!symbol) return;
    prefs.follow = false;
    syncButtons();
    open(symbol, tf);
  });
  $('.tr-follow').addEventListener('click', () => {
    prefs.follow = !prefs.follow;
    syncButtons();
    save();
    const target = prefs.follow && followTarget();
    if (target && (target.symbol !== current?.symbol || target.tf !== current?.tf)) open(target.symbol, target.tf, target.bars);
  });
  $('.tr-oos').addEventListener('click', () => {
    const candles = chart?.data?.candles;
    if (!candles?.length || !chartData) return;
    const from = Math.max(0, candles.findIndex((c) => c.t >= chartData.split));
    chart.chart.timeScale().setVisibleLogicalRange({ from: from - 3, to: candles.length + 8 });
  });
  $('.tr-smc').addEventListener('click', () => {
    prefs.smc = !prefs.smc;
    syncButtons();
    save();
    chart?.setLayers(prefs.smc ? SMC_ON : SMC_OFF);
  });
  syncButtons();
  setInterval(pollLive, 3000);
  setInterval(renderClock, 1000);
  setInterval(() => { if (visible() && !replay) load(true); }, 30_000);
  // following a running training lab: check its current asset every few seconds
  setInterval(async () => {
    if (!prefs.follow || replay || !visible()) return;
    try {
      const lab = await api('/api/training/lab');
      summary = { ...summary, lab };
      const c = lab.current;
      if (lab.state === 'running' && c?.symbol && (c.symbol !== current?.symbol || c.tf !== current?.tf)) open(c.symbol, c.tf, lab.bars);
    } catch { /* the next tick retries */ }
  }, 5000);
  $('.tr-play').addEventListener('click', () => {
    if (!replay) { startReplay(); return; }
    if (replay.i >= replay.full.candles.length) { stopReplay(false); startReplay(); return; }
    replay.playing = !replay.playing;
    replay.last = performance.now();
    syncButtons();
  });
  $('.tr-end').addEventListener('click', () => { if (replay) advance(replay.full.candles.length); });
  $('.tr-stop').addEventListener('click', () => stopReplay());
  $('.tr-speed').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-speed]');
    if (!b) return;
    prefs.speed = +b.dataset.speed;
    save();
    syncButtons();
    if (replay) showReplay();
  });

  return {
    /** Called with every AI TRAINING summary while the tab is shown. */
    show(d) {
      summary = d;
      if (replay) return;  // a running replay is not interrupted by refreshes or the follow mode
      const target = prefs.follow ? followTarget() : null;
      if (target && (target.symbol !== current?.symbol || target.tf !== current?.tf)) { open(target.symbol, target.tf, target.bars); return; }
      if (!current) {
        const first = prefs.symbol ? { symbol: prefs.symbol, tf: prefs.tf } : d.leaderboard?.[0];
        if (first) { open(first.symbol, first.tf); return; }
      }
      options();
      if (chart && !chart.data) load(false);
    },
  };
}

// ------------------------------------------------------------------ training lab

const LAB_MARKETS = [['forex', 'FOREX'], ['saham-as', 'SAHAM AS'], ['saham-idx', 'SAHAM IDX'], ['kripto', 'KRIPTO']];
const LAB_TFS = ['5m', '15m', '1h', '4h', '1d', '1w'];
const LAB_HISTORY = { '5m': '±60 hari', '15m': '±60 hari', '1h': '±730 hari', '4h': '±730 hari', '1d': 'seluruh riwayat', '1w': 'seluruh riwayat' };
const LAB_KEY = 'sharx.lab';
const STATE_TEXT = { idle: ['SIAP', 'ghost'], running: ['BERJALAN', 'bull'], done: ['SELESAI', 'bull'], stopped: ['DIHENTIKAN', 'neu'], error: ['GAGAL', 'bear'] };
const duration = (sec) => { sec = Math.max(0, Math.round(sec)); return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`; };

/** TRAINING LAB tab: a batch over chosen markets × timeframes, or one asset trained forward live on its chart. */
// What one learning step did: data too thin, setting kept, or a new setting (with its internal test before → after)
const learnText = (l) => (!l ? '<span class="dim">default (belum belajar)</span>'
  : !l.after?.trades ? '<span class="dim">data belum cukup untuk belajar</span>'
    : `${l.changed ? '<span class="up">setting baru</span>' : 'setting tetap'} <span class="dim">(uji internal ${pct(l.before?.winRate)} → ${pct(l.after?.winRate)})</span>`);
// An exam (or any result) on few trades is not evidence: 3 / 3 says little about 99 %
const thin = (n) => (n < 30 ? ` <span class="flat">· sampel kecil (${n} trade), belum bukti</span>` : '');
const savedText = (sv) => (!sv ? '' : `trade disimpan ke kamus AI · setting aset ${sv.upgrade ? `<span class="up">di-upgrade ke v${sv.version}</span>` : sv.version ? `tetap v${sv.version}` : 'tetap default'}`);

function mountTrainingLab(el) {
  let prefs = { markets: ['forex'], tfs: ['5m'], bars: 5000, asset: '', speed: 4, improve: true };
  try { prefs = { ...prefs, ...JSON.parse(localStorage.getItem(LAB_KEY) || '{}') }; } catch { /* per-viewer convenience only */ }
  const save = () => { try { localStorage.setItem(LAB_KEY, JSON.stringify(prefs)); } catch { /* ignore */ } };
  el.innerHTML = `
    <div class="lab-ctl">
      <div class="lab-row"><span>PASAR</span><div class="lab-set" data-set="markets">${LAB_MARKETS.map(([k, l]) => `<button data-v="${k}">${l}</button>`).join('')}<button data-all="markets">SEMUA</button></div></div>
      <div class="lab-row"><span>ASET</span><select class="input lab-asset"></select><span class="dim lab-asset-note"></span></div>
      <div class="lab-row"><span>TIMEFRAME</span><div class="lab-set" data-set="tfs">${LAB_TFS.map((t) => `<button data-v="${t}">${t.toUpperCase()}</button>`).join('')}<button data-all="tfs">SEMUA</button></div></div>
      <div class="lab-row"><span>CANDLE</span><input class="input lab-bars" type="number" min="300" max="20000" step="100" value="${prefs.bars}">
        <span class="dim lab-hint"></span></div>
      <div class="lab-row lab-improve-row"><span></span><label class="check" title="Selama belum mencapai target, training maju diulang dengan pencarian SMC yang makin luas (54 → 900 kombinasi) dan hasil terbaik dipakai; berhenti bila target tercapai atau pencarian lebih luas tidak membantu lagi. 20% candle terakhir dikunci untuk ujian akhir.">
        <input type="checkbox" class="lab-improve"> TERUS PERBAIKI SAMPAI TARGET 99% — ulang dengan pencarian SMC makin luas, lalu ujian akhir di 20% candle terakhir</label></div>
      <div class="lab-row"><span>AKUN</span><span class="lab-acct-host"></span><span class="dim">uang per trade = R × jarak stop × lot × ukuran kontrak</span></div>
      <div class="lab-row"><button class="btn-primary lab-start">▶ MULAI TRAINING</button><button class="btn danger lab-stop">■ STOP</button><span class="lab-state"></span></div>
      <div class="tr-progress lab-progress"><i></i><span></span></div>
      <div class="lab-current"></div>
    </div>
    <div class="lab-content">
      <div class="lab-batch">
        <div class="lab-results"></div>
        <div class="lab-mtf"></div>
        <div class="note">Training batch berjalan di engine secepat CPU (ribuan candle per detik) untuk semua aset pasar & timeframe terpilih; setiap trade
          disimpan di kamus trade bersama kondisi TF lebih tinggi saat itu. Pilih satu aset di daftar ASET untuk melihat training-nya live di chart.
          Tujuan 99% tetap arah perbaikan, bukan jaminan.</div>
      </div>
      <div class="lab-live hidden">
        <div class="lab-live-bar">
          <button class="lab-play">⏸ JEDA</button>
          <span class="tr-speed">${SPEEDS.map((v) => `<button data-speed="${v}">×${v}</button>`).join('')}</span>
          <button class="lab-end" title="Lompat ke candle terakhir yang sudah dilatih">⏭</button>
          <span class="lab-live-info"></span>
        </div>
        <div class="chart-shell lab-chart"><div class="chart-host"></div><div class="chart-replay"></div></div>
        <div class="lab-learn"></div>
      </div>
    </div>`;
  const $ = (q) => el.querySelector(q);
  // virtual account of the live training chart (shared with LIVE CHART through account.js)
  const acct = accountInputs(() => { if (play) drawPlay(); });
  $('.lab-acct-host').append(acct.el);
  const labSpec = () => effectiveSpec(sess?.contract);
  let status = null, summary = null, timer = null, assets = new Map();
  // live session player
  let sess = null, chart = null, play = null;

  const single = () => Boolean(prefs.asset);
  const renderSets = () => {
    if (single() && prefs.tfs.length > 1) prefs.tfs = [prefs.tfs[0]];
    for (const set of el.querySelectorAll('.lab-set')) {
      const chosen = prefs[set.dataset.set];
      set.querySelectorAll('button[data-v]').forEach((b) => b.classList.toggle('active', chosen.includes(b.dataset.v)));
      set.querySelector('[data-all="tfs"]')?.classList.toggle('hidden', single());
    }
    $('.lab-hint').textContent = `riwayat tersedia: ${prefs.tfs.map((t) => `${t.toUpperCase()} ${LAB_HISTORY[t]}`).join(' · ') || '–'} (bila riwayat lebih pendek, dipakai seluruhnya)`;
    $('.lab-asset-note').textContent = single() ? 'training live 1 aset × 1 timeframe, tampil di chart' : 'semua aset pasar terpilih (batch, tanpa chart)';
    $('.lab-improve-row').classList.toggle('hidden', !single());
    $('.lab-improve').checked = prefs.improve;
    if (prefs.markets.length === 1) acct.market(prefs.markets[0]);  // balance currency & lot of the chosen market
  };

  async function loadAssets() {
    for (const m of prefs.markets) {
      if (assets.has(m)) continue;
      try {
        const r = await api(`/api/instruments?market=${m}&scope=all`);
        assets.set(m, [...r.instruments].sort((a, b) => (b.core ? 1 : 0) - (a.core ? 1 : 0) || a.symbol.localeCompare(b.symbol)));
      } catch { assets.set(m, []); }
    }
    const pick = $('.lab-asset');
    const groups = prefs.markets.map((m) => `<optgroup label="${esc(MARKET_NAME[m] || m)}">${(assets.get(m) || []).map((i) =>
      `<option value="${esc(i.symbol)}">${esc(i.symbol)} — ${esc(i.name || '')}</option>`).join('')}</optgroup>`).join('');
    pick.innerHTML = `<option value="">SEMUA ASET PASAR TERPILIH (training batch)</option>${groups}`;
    const known = prefs.markets.some((m) => (assets.get(m) || []).some((i) => i.symbol === prefs.asset));
    if (!known) prefs.asset = '';
    pick.value = prefs.asset;
    renderSets();
  }

  // ------------------------------------------------------------------ batch
  function renderBatch() {
    const st = status || { state: 'idle' };
    $('.lab-results').innerHTML = st.groups?.length ? `<table class="co-table"><thead><tr><th>Pasar</th><th>TF</th><th>Aset</th><th>Candle</th><th>Trade uji</th><th>Win</th><th>Total R</th><th>Upgrade</th></tr></thead><tbody>
      ${st.groups.map((g) => `<tr><td>${esc(MARKET_NAME[g.market] || g.market)}</td><td>${esc(g.tf.toUpperCase())}</td><td>${g.assets}</td><td>${g.bars}</td><td>${g.trades}</td>
        <td class="${(g.winRate ?? (g.trades ? g.wins / g.trades : null)) >= 0.5 ? 'up' : ''}">${pct(g.winRate ?? (g.trades ? g.wins / g.trades : null))}</td><td>${rr(g.totalR)}</td><td>${g.upgrades}</td></tr>`).join('')}</tbody></table>` : '';
    const mtf = summary?.mtf;
    $('.lab-mtf').innerHTML = mtf?.all?.trades ? `<h5>MULTI TIMEFRAME — KAMUS TRADE (DATA UJI, ${mtf.all.trades} TRADE)</h5>
      <table class="co-table"><thead><tr><th>Kondisi saat sinyal</th><th>Trade</th><th>Win</th><th>Ekspektasi</th></tr></thead><tbody>
      ${BUCKETS.filter(([k]) => mtf.buckets[k]?.trades).map(([k, l]) => `<tr><td>${l}</td><td>${mtf.buckets[k].trades}</td><td class="${mtf.buckets[k].winRate >= 0.5 ? 'up' : 'down'}">${pct(mtf.buckets[k].winRate)}</td><td>${rr(mtf.buckets[k].expectancyR)}</td></tr>`).join('')}</tbody></table>
      <table class="co-table"><thead><tr><th>TF</th><th>Semua</th><th>TF tinggi searah</th><th>Berlawanan</th></tr></thead><tbody>
      ${Object.entries(mtf.tfs).map(([tf, bk]) => `<tr><td>${esc(tf.toUpperCase())}</td>${['all', 'aligned', 'against'].map((k) => {
        const m = k === 'all' ? Object.values(bk).reduce((a, x) => ({ trades: a.trades + x.trades, wins: a.wins + x.wins }), { trades: 0, wins: 0 }) : bk[k];
        return `<td>${m?.trades ? `${pct(m.wins / m.trades)} <span class="dim">(${m.trades})</span>` : '–'}</td>`;
      }).join('')}</tr>`).join('')}</tbody></table>` : '';
  }

  // ------------------------------------------------------------------ status line (batch or live)
  function render() {
    const live = Boolean(sess && sess.state !== 'idle' && !$('.lab-live').classList.contains('hidden'));
    const st = live ? sess : (status || { state: 'idle' });
    const [text, cls] = STATE_TEXT[st.state === 'loading' ? 'running' : st.state] || STATE_TEXT.idle;
    const running = (status?.state === 'running') || sess?.state === 'loading' || sess?.state === 'running';
    $('.lab-state').innerHTML = `<span class="tag ${cls}">${live ? 'LIVE · ' : ''}${text}</span>${st.error ? ` <span class="down">${esc(st.error)}</span>` : ''}`;
    $('.lab-start').disabled = running;
    $('.lab-stop').disabled = !running;
    const elapsed = st.startedAt ? (st.finishedAt || Date.now() / 1000) - st.startedAt : 0;
    if (live) {
      const pctDone = st.total ? Math.round((100 * (st.computedTo || 0)) / st.total) : 0;
      $('.lab-progress i').style.width = `${pctDone}%`;
      $('.lab-progress span').textContent = `AI sudah belajar & trading ${st.computedTo || 0} / ${st.total || st.bars} candle · ${pctDone}% · ${duration(elapsed)}`;
      const lastRound = st.rounds?.[st.rounds.length - 1];
      $('.lab-current').innerHTML = `${esc(st.symbol || '')} · ${esc((st.tf || '').toUpperCase())}${st.stage ? ` · ${esc(st.stage)}` : ''}
        ${st.improve && lastRound ? ` · putaran ${lastRound.round}/${st.maxRounds || lastRound.round}` : ''}${st.exam ? ` · <b>ujian akhir: ${pct(st.exam.stats.winRate)}</b> dari ${st.exam.stats.trades} trade (${rr(st.exam.stats.totalR)})${thin(st.exam.stats.trades)}` : ''}
        · trade selesai ${st.stats?.trades ?? 0} · menang <b>${pct(st.stats?.winRate)}</b> · ${rr(st.stats?.totalR)}${st.saved ? ` · ${savedText(st.saved)}` : ''}`;
    } else {
      const pctDone = st.progressPct ?? 0;
      $('.lab-progress i').style.width = `${pctDone}%`;
      $('.lab-progress span').textContent = st.total ? `${st.done} / ${st.total} aset × TF · ${pctDone}% · ${duration(elapsed)}${st.skipped ? ` · ${st.skipped} tanpa cukup data` : ''}` : 'belum ada training';
      const cur = st.current;
      $('.lab-current').innerHTML = cur ? `Sedang: <b>${esc(cur.symbol || MARKET_NAME[cur.market] || cur.market)}</b> · ${esc(cur.tf.toUpperCase())} · ${esc(cur.stage)}${cur.bars ? ` · ${cur.bars} candle` : ''}`
        : st.total ? `Trade data uji: <b>${st.trades}</b> · menang <b class="${st.trades && st.wins / st.trades >= 0.5 ? 'up' : 'down'}">${st.trades ? pct(st.wins / st.trades) : '–'}</b>
          · total ${rr(st.totalR)} · upgrade SMC ${st.upgrades}` : '';
      renderBatch();
    }
  }

  async function poll() {
    try { status = await api('/api/training/lab'); } catch (e) { status = { ...(status || {}), error: e.message }; }
    try { sess = await api('/api/training/session'); } catch { /* engine warming */ }
    if (single() && sess?.state && sess.state !== 'idle' && sess.symbol === prefs.asset && sess.tf === prefs.tfs[0]) showLive(true);  // e.g. after a reload
    render();
    if (sess && sess.state !== 'idle' && !$('.lab-live').classList.contains('hidden')) await feedPlayer();
    clearTimeout(timer);
    const busy = status?.state === 'running' || sess?.state === 'loading' || sess?.state === 'running';
    if (busy && el.offsetParent !== null) timer = setTimeout(poll, 1000);
  }

  // ------------------------------------------------------------------ live training chart
  function showLive(on) {
    $('.lab-live').classList.toggle('hidden', !on);
    $('.lab-batch').classList.toggle('hidden', on);
  }

  async function feedPlayer() {
    if (!chart) {
      chart = new SmcChart($('.lab-chart .chart-host'), $('.lab-chart'));
      chart.setLayers({ ...SMC_ON, swing: true, internal: true });  // the rule trades swing and internal BOS / CHoCH
    }
    if (!play && (sess.state === 'running' || sess.state === 'done' || sess.state === 'stopped') && sess.total) {
      const data = await api('/api/training/session/candles');
      if (!data.candles?.length) return;
      const startAt = Math.min(60, data.candles.length);
      play = { data, i: startAt, playing: true, acc: 0, last: performance.now(), raf: 0, key: `${sess.symbol}|${sess.tf}|${sess.startedAt}` };
      chart.setData({ ...data, candles: data.candles.slice(0, startAt).map((c) => ({ ...c })) });
      play.raf = requestAnimationFrame(tick);
      syncPlay();
    }
    if (play) drawPlay();
  }

  function tick(now) {
    if (!play) return;
    const dt = Math.min(0.25, (now - play.last) / 1000);
    play.last = now;
    const limit = Math.min(play.data.candles.length, sess?.computedTo || 0);
    if (play.playing && play.i < limit) {
      play.acc += dt * REPLAY_BASE * prefs.speed;
      const n = Math.floor(play.acc);
      if (n) {
        play.acc -= n;
        for (let k = 0; k < n && play.i < limit; k++) chart.appendCandle({ ...play.data.candles[play.i++] });
        drawPlay();
      }
    } else play.acc = 0;
    if (play) play.raf = requestAnimationFrame(tick);
  }

  function drawPlay() {
    const { data, i } = play;
    const T = data.candles[i - 1].t;
    const trades = (sess?.trades || []);
    chart.until = T;
    acct.market(sess?.instrument?.market);
    chart.setTrades({ split: sess?.exam?.fromT ?? null, trades: moneyLabels(trades.map((t) => tradeAt(t, T)).filter(Boolean), labSpec(), acct.lot()) });
    const done = trades.filter((t) => t.closed_t != null && t.closed_t <= T && ['win', 'loss', 'timeout'].includes(t.status));
    const wins = done.filter((t) => t.r > 0).length;
    const total = done.reduce((a, t) => a + (t.r || 0), 0);
    const segs = (sess?.segments || []).filter((sg) => sg.fromT <= T);
    const seg = segs[segs.length - 1];
    const learned = segs.filter((sg) => sg.learn).pop()?.learn;
    const waiting = sess && (sess.state === 'running' || sess.state === 'loading') && i >= (sess.computedTo || 0);
    const finished = i >= data.candles.length;
    $('.lab-chart .chart-replay').innerHTML = `
      <div class="rp-head"><b>TRAINING LIVE ×${prefs.speed}</b><span>${esc(sess?.symbol || '')} ${esc((sess?.tf || '').toUpperCase())}</span>
        <span>${esc(dateText(T, sess?.tf))}</span><span class="dim">candle ${i} / ${data.candles.length}${seg ? ` · AI v${seg.version}` : ''}</span></div>
      <div class="rp-bar"><i style="width:${((100 * i) / data.candles.length).toFixed(1)}%"></i></div>
      <div class="rp-stats"><span>selesai <b>${done.length}</b></span><span class="up">✔ ${wins}</span><span class="down">✘ ${done.length - wins}</span>
        <span>win <b class="${done.length ? (wins / done.length >= 0.5 ? 'up' : 'down') : ''}">${done.length ? pct(wins / done.length) : '–'}</b></span>
        <span class="${total > 0 ? 'up' : total < 0 ? 'down' : ''}">${total > 0 ? '+' : ''}${total.toFixed(2)}R</span></div>
      ${sess?.grades ? `<div class="rp-note">${gradeLine(sess.grades)}</div>` : ''}
      ${labSpec() ? `<div class="rp-note">${accountLine(accountStats(done, labSpec(), acct.balance(), acct.lot()), labSpec(), acct.lot())}</div>` : ''}
      ${sess?.improve && sess.rounds?.length ? `<div class="rp-note">Putaran ${sess.rounds[sess.rounds.length - 1].round} · terbaik putaran
        ${sess.rounds.find((r) => r.best)?.round ?? '–'} (${pct(sess.rounds.find((r) => r.best)?.winRate)})${sess.exam ? ` · <b>UJIAN AKHIR ${pct(sess.exam.stats.winRate)}</b> dari ${sess.exam.stats.trades} trade${thin(sess.exam.stats.trades)}` : ''}
        · target ${Math.round((sess.goal || 0.99) * 100)}% ${sess.reached ? '<span class="up">tercapai</span>' : '<span class="flat">belum tercapai</span>'}</div>` : ''}
      <div class="rp-note ${waiting ? 'flat' : 'dim'}">${waiting ? (sess?.improve && sess.rounds?.length ? 'AI memperbaiki dengan pencarian lebih luas…' : 'AI sedang belajar stretch berikutnya…')
        : finished && sess?.state === 'done' ? `SELESAI · ${savedText(sess.saved)}`
          : learned ? `Belajar terakhir di candle ${learned.bar}: ${learnText(learned)}`
            : 'Fase awal: AI belum belajar (setting default)'}</div>`;
    const hint = sess?.hint ? `<div class="note flat">${esc(sess.hint)}</div>` : '';
    const rounds = sess?.improve && sess.rounds?.length ? `${hint}<table class="co-table"><thead><tr><th>Putaran</th><th>Pencarian SMC</th><th>Trade</th><th>Win</th><th>Batas bawah</th><th>Ekspektasi</th><th></th></tr></thead><tbody>
      ${sess.rounds.map((r) => `<tr class="${r.best ? 'sel' : ''}"><td>${r.round}</td><td>${r.combos.toLocaleString('id-ID')} kombinasi</td><td>${r.trades}</td><td>${pct(r.winRate)}</td><td>${pct(r.winRateLow)}</td><td>${rr(r.expectancyR)}</td><td>${r.best ? '<span class="tag bull">TERBAIK</span>' : '<span class="dim">tidak lebih baik</span>'}</td></tr>`).join('')}
      ${sess.exam ? `<tr><td><b>UJIAN AKHIR</b></td><td class="dim">candle ${sess.exam.from}–${sess.total} (tidak pernah dipakai belajar)</td><td>${sess.exam.stats.trades}</td><td><b class="${sess.exam.stats.winRate >= 0.5 ? 'up' : 'down'}">${pct(sess.exam.stats.winRate)}</b></td><td></td><td>${rr(sess.exam.stats.expectancyR)}</td><td>${thin(sess.exam.stats.trades)}</td></tr>` : ''}
      </tbody></table>` : '';
    $('.lab-learn').innerHTML = rounds + ((sess?.segments || []).length ? `<table class="co-table"><thead><tr><th>Candle</th><th>Setting</th><th>Belajar dari candle sebelumnya</th><th>Trade</th><th>Win</th><th>R</th></tr></thead><tbody>
      ${sess.segments.map((sg) => `<tr class="${seg && sg.index === seg.index ? 'sel' : ''}"><td>${sg.from}–${sg.to}</td><td>v${sg.version}</td>
        <td>${learnText(sg.learn)}</td>
        <td>${sg.stats.trades}</td><td>${pct(sg.stats.winRate)}</td><td>${rr(sg.stats.totalR)}</td></tr>`).join('')}</tbody></table>` : '');
    $('.lab-live-info').textContent = sess ? `${sess.symbol} · ${sess.tf?.toUpperCase()} · ${sess.total || sess.bars} candle` : '';
  }

  function syncPlay() {
    el.querySelectorAll('.lab-live .tr-speed button').forEach((b) => b.classList.toggle('active', +b.dataset.speed === prefs.speed));
    $('.lab-play').textContent = play?.playing ? '⏸ JEDA' : '▶ LANJUT';
  }

  function resetPlayer() {
    if (play) cancelAnimationFrame(play.raf);
    play = null;
    if (chart) chart.until = null;
  }

  // ------------------------------------------------------------------ events
  el.addEventListener('click', async (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.dataset.speed) { prefs.speed = +b.dataset.speed; save(); syncPlay(); if (play) drawPlay(); return; }
    if (b.classList.contains('lab-play')) { if (play) { play.playing = !play.playing; play.last = performance.now(); } syncPlay(); return; }
    if (b.classList.contains('lab-end')) {
      if (play) { const limit = Math.min(play.data.candles.length, sess?.computedTo || 0); while (play.i < limit) chart.appendCandle({ ...play.data.candles[play.i++] }); drawPlay(); }
      return;
    }
    const set = b.closest('.lab-set')?.dataset.set;
    if (set && b.dataset.v) {
      const list = prefs[set];
      if (set === 'tfs' && single()) prefs.tfs = [b.dataset.v];
      else prefs[set] = list.includes(b.dataset.v) ? list.filter((x) => x !== b.dataset.v) : [...list, b.dataset.v];
      if (set === 'markets') { save(); loadAssets(); return; }
      if (set === 'tfs' && single()) { save(); renderSets(); showLive(false); poll(); return; }
    } else if (b.dataset.all) {
      const all = b.dataset.all === 'markets' ? LAB_MARKETS.map(([k]) => k) : LAB_TFS;
      prefs[b.dataset.all] = prefs[b.dataset.all].length === all.length ? [] : [...all];
      if (b.dataset.all === 'markets') { save(); loadAssets(); return; }
    } else if (b.classList.contains('lab-start')) {
      prefs.bars = Number($('.lab-bars').value) || 5000;
      save();
      try {
        if (single()) {
          resetPlayer();
          showLive(true);
          sess = await api('/api/training/session/start', { json: { symbol: prefs.asset, tf: prefs.tfs[0] || '5m', bars: prefs.bars, improve: prefs.improve } });
        } else {
          showLive(false);
          status = await api('/api/training/lab/start', { json: { markets: prefs.markets, tfs: prefs.tfs, bars: prefs.bars } });
        }
      } catch (err) {
        $('.lab-state').innerHTML = `<span class="down">${esc(err.message)}</span>`;
        return;
      }
      render();
      poll();
      return;
    } else if (b.classList.contains('lab-stop')) {
      try {
        if (sess?.state === 'loading' || sess?.state === 'running') sess = await api('/api/training/session/stop', { method: 'POST' });
        else status = await api('/api/training/lab/stop', { method: 'POST' });
      } catch { /* the next poll shows it */ }
      poll();
      return;
    }
    save();
    renderSets();
  });
  // the live chart belongs to the session's own asset: another asset shows the batch view until it is started
  $('.lab-asset').addEventListener('change', (e) => { prefs.asset = e.target.value; save(); renderSets(); showLive(false); poll(); });
  $('.lab-bars').addEventListener('change', (e) => { prefs.bars = Number(e.target.value) || 5000; save(); });
  $('.lab-improve').addEventListener('change', (e) => { prefs.improve = e.target.checked; save(); });
  loadAssets();
  syncPlay();
  return {
    show(d) {
      summary = d;
      status = d.lab || status;
      render();
      poll();
    },
  };
}

/** The AI TRAINING page. */
export const trainingPage = {
  init() {
    this.el = document.getElementById('tab-training');
    this.tab = 'board';
    this.precision = loadPrecision();
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
          <div class="ptabs tr-tabs"><button data-v="board" class="active">PAPAN AKURASI ASET</button><button data-v="groups">GRUP PASAR</button><button data-v="journal">JURNAL SINYAL</button><button data-v="upgrades">LOG UPGRADE SMC</button><button data-v="live">LIVE CHART</button><button data-v="lab">TRAINING LAB</button></div>
          <div class="scroll tr-body"></div>
          <div class="tr-live hidden"></div>
          <div class="scroll tr-lab hidden"></div>
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
      const mode = e.target.closest('button[data-mode]');
      if (mode) { this.precision = mode.dataset.mode; savePrecision(this.precision); this.render(); return; }
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
    const modes = d.modes || [];
    const mode = modes.find((m) => m.key === this.precision) || modes[0];
    const live = mode.live, bt = mode.backtest;
    this.el.querySelector('.tr-kpis').innerHTML = [
      [`WIN RATE LIVE · ${mode.label}`, pct(live.winRate), live.winRate >= d.target ? 'up' : ''],
      [`WIN RATE BACKTEST · ${mode.label}`, pct(bt.winRate), bt.winRate >= d.target ? 'up' : ''],
      ['SEMUA PASANGAN (BACKTEST)', pct(d.backtestAll.winRate), ''],
      ['TRADE LIVE SELESAI', `${live.trades} / ${d.liveAll.trades}`, ''],
      ['EKSPEKTASI BACKTEST', rr(bt.expectancyR), bt.expectancyR > 0 ? 'up' : bt.expectancyR < 0 ? 'down' : ''],
      ['PASANGAN LOLOS', `${bt.pairs} / ${d.evaluatedPairs}`, 'up'],
      ['UPGRADE SMC', d.upgradesTotal, 'up'],
    ].map(([l, v, c]) => `<div><span>${l}</span><b class="${c}">${v}</b></div>`).join('');
    const markets = Object.entries(d.markets || {});
    const sources = Object.entries(d.sources || {});
    this.el.querySelector('.tr-overview').innerHTML = `
      <div class="tr-modes"><span>MODE PRESISI</span>${modes.map((m) => `<button data-mode="${m.key}" class="${m === mode ? 'active' : ''}">${esc(modeText(m))}</button>`).join('')}</div>
      <h5>LIVE · ${esc(mode.label)} (${live.trades} selesai; semua termasuk bayangan: ${pct(d.liveAll.winRate)} dari ${d.liveAll.trades})</h5>${progress(live.winRate, d.target, d.goal)}
      <h5>BACKTEST OUT-OF-SAMPLE · ${esc(mode.label)} (${bt.trades} trade dari ${bt.pairs} pasangan; semua pasangan: ${pct(d.backtestAll.winRate)} dari ${d.backtestAll.trades})</h5>${progress(bt.winRate, d.target, d.goal)}
      <table class="co-table"><thead><tr><th>Mode presisi</th><th>Pasangan</th><th>Win OOS</th><th>Trade</th><th>Ekspektasi</th><th>Live win</th><th>Trade</th></tr></thead><tbody>
        ${modes.map((m) => `<tr class="${m === mode ? 'sel' : ''}"><td>${esc(m.label)} <span class="dim">${m.mtf ? 'MTF searah' : `≥${Math.round(m.low * 100)}%`}</span></td><td>${m.backtest.pairs}</td><td>${pct(m.backtest.winRate)}</td><td>${m.backtest.trades}</td><td>${rr(m.backtest.expectancyR)}</td><td>${pct(m.live.winRate)}</td><td>${m.live.trades}</td></tr>`).join('')}</tbody></table>
      ${sources.length ? `<table class="co-table"><thead><tr><th>Asal parameter</th><th>Pasangan</th><th>Win OOS</th><th>Trade</th><th>Ekspektasi</th></tr></thead><tbody>
        ${sources.map(([k, v]) => `<tr><td><span class="tag ${(SOURCE[k] || SOURCE.default)[1]}">${(SOURCE[k] || SOURCE.default)[0]}</span></td><td>${v.pairs}</td><td>${pct(v.winRate)}</td><td>${v.trades}</td><td>${rr(v.expectancyR)}</td></tr>`).join('')}</tbody></table>` : ''}
      ${markets.length ? `<table class="co-table"><thead><tr><th>Pasar</th><th>Live terkualifikasi</th><th>Trade</th><th>Backtest terkualifikasi</th><th>Semua</th></tr></thead><tbody>
        ${markets.map(([m, v]) => `<tr><td>${esc(MARKET_NAME[m] || m)}</td><td>${pct(v.live.winRate)}</td><td>${v.live.trades}</td><td>${pct(v.backtest.winRate)} <span class="dim">(${v.backtest.trades})</span></td><td>${pct(v.backtestAll.winRate)} <span class="dim">(${v.backtestAll.trades})</span></td></tr>`).join('')}</tbody></table>` : ''}
      <div class="note">Mode lebih ketat = win rate lebih tinggi, sinyal lebih jarang. Status presisi dicap saat sinyal muncul (sebelum hasilnya
        diketahui); bukti ke depan = trade live. Tujuan 99% adalah arah, bukan jaminan — angka di sini selalu terukur.
        ${d.progress?.tuning ? `<br>Sedang melatih: <b>${esc(d.progress.tuning)}</b>` : ''}</div>`;
    equityChart(this.el.querySelector('.tr-equity canvas'), d.equity || []);
    this.el.querySelector('.tr-updated').textContent = `diperbarui ${new Date(d.generatedAt * 1000).toLocaleTimeString('id-ID')}`;
    this.renderBody();
  },
  renderBody() {
    const d = this.data;
    if (!d) return;
    const body = this.el.querySelector('.tr-body');
    const liveEl = this.el.querySelector('.tr-live');
    const labEl = this.el.querySelector('.tr-lab');
    body.classList.toggle('hidden', this.tab === 'live' || this.tab === 'lab');
    liveEl.classList.toggle('hidden', this.tab !== 'live');
    labEl.classList.toggle('hidden', this.tab !== 'lab');
    if (this.tab === 'lab') {
      this.lab ||= mountTrainingLab(labEl);
      this.lab.show(d);
      return;
    }
    if (this.tab === 'live') {
      this.liveChart ||= mountTrainingChart(liveEl);
      this.liveChart.show(d);
      return;
    }
    if (this.tab === 'board') {
      body.innerHTML = d.leaderboard.length ? `<table class="co-table tr-table"><thead><tr><th>Aset</th><th>TF</th><th>Status</th><th>Backtest win</th><th>Trade</th><th>Ekspektasi</th><th>Live win</th><th>Trade</th></tr></thead><tbody>
        ${d.leaderboard.map((b) => `<tr data-sym="${esc(b.symbol)}" data-tf="${esc(b.tf)}"><td><b>${esc(b.symbol)}</b><small class="dim">${esc(MARKET_NAME[b.market] || '')}</small></td><td>${esc(b.tf.toUpperCase())}</td>
          <td title="Bukti parameter aktif: ${pct(b.evidence.winRate)} dari ${b.evidence.trades} trade out-of-sample">${levelTag(d.modes, b.qualified, b.evidence.winRateLow)}<small class="dim">${(SOURCE[b.source] || SOURCE.default)[0].toLowerCase()} v${b.version} · bukti ${pct(b.evidence.winRate)}/${b.evidence.trades}${b.level ? ` · pencarian L${b.level}` : ''}</small></td>
          <td class="${b.backtest.winRate >= d.target ? 'up' : ''}">${pct(b.backtest.winRate)}</td><td>${b.backtest.trades ?? 0}</td><td>${rr(b.backtest.expectancyR)}</td>
          <td class="${b.live.winRate >= d.target ? 'up' : ''}">${pct(b.live.winRate)}</td><td>${b.live.trades}</td></tr>`).join('')}</tbody></table>`
        : '<div class="empty">Pelatihan dimulai ±2 menit setelah aplikasi dibuka, setelah data harga terkumpul.</div>';
    } else if (this.tab === 'groups') {
      body.innerHTML = d.groups.length ? `<table class="co-table tr-table"><thead><tr><th>Grup</th><th>Aset</th><th>Win data uji</th><th>Trade</th><th>Ekspektasi</th><th>Win validasi</th><th>Parameter</th></tr></thead><tbody>
        ${d.groups.map((g) => `<tr><td><b>${esc(MARKET_NAME[g.market] || g.market)}</b><small class="dim">${esc(g.tf.toUpperCase())} · v${g.version}</small></td><td>${g.pairs ?? '–'}</td>
          <td class="${(g.test?.winRate || 0) >= d.target ? 'up' : ''}">${pct(g.test?.winRate)}</td><td>${g.test?.trades ?? 0}</td><td>${rr(g.test?.expectancyR)}</td>
          <td>${pct(g.valid?.winRate)} <span class="dim">(${g.valid?.trades ?? 0})</span></td><td><small>${esc(paramText(g.params))}</small></td></tr>`).join('')}</tbody></table>
        <div class="note">Parameter grup dipelajari dari gabungan aset satu pasar & timeframe: diurutkan di 60% awal riwayat, dipilih di 20% berikutnya (validasi), dan dilaporkan di 20% terakhir (data uji yang tidak ikut memilih).</div>`
        : '<div class="empty">Pelatihan grup berjalan otomatis setelah data harga terkumpul.</div>';
    } else if (this.tab === 'journal') {
      body.innerHTML = d.recent.length ? `<table class="co-table tr-table"><thead><tr><th>Muncul</th><th>Aset</th><th>Arah</th><th>Entry / SL / TP</th><th>Status</th><th>Hasil</th></tr></thead><tbody>
        ${d.recent.map((s) => `<tr data-sym="${esc(s.symbol)}" data-tf="${esc(s.tf)}"><td>${dt(s.created_t)}<small class="dim">terdeteksi ${dt(s.first_seen)}</small></td>
          <td><b>${esc(s.symbol)}</b><small class="dim">${esc(s.tf.toUpperCase())}</small></td><td>${s.direction > 0 ? 'BUY' : 'SELL'}</td>
          <td>${fmtPrice(s.entry)} / ${fmtPrice(s.stop)} / ${fmtPrice(s.target)}</td><td>${esc((STATUS[s.status] || [s.status])[0])}<small class="dim">${esc(s.reason || '')}</small></td>
          <td class="${s.r > 0 ? 'up' : s.r < 0 ? 'down' : ''}">${rr(s.r)}<small class="dim">${s.qualified ? `trade AI${s.quality >= 0.8 ? ' · sangat ketat' : s.quality >= 0.65 ? ' · ketat' : ''}` : 'bayangan'}</small></td></tr>`).join('')}</tbody></table>`
        : '<div class="empty">Belum ada sinyal live. Sinyal baru dicatat sejak aset mulai dipantau.</div>';
    } else {
      body.innerHTML = d.upgrades.length ? `<table class="co-table tr-table"><thead><tr><th>Waktu</th><th>Aset</th><th>Versi</th><th>Win OOS sebelum → sesudah</th><th>Trade</th><th>Parameter baru</th></tr></thead><tbody>
        ${d.upgrades.map((u) => `<tr data-sym="${esc(u.symbol)}" data-tf="${esc(u.tf)}"><td>${dt(u.created)}</td><td><b>${esc(u.symbol)}</b><small class="dim">${esc(u.tf.toUpperCase())}</small></td>
          <td>v${u.from} → v${u.to}</td><td>${pct(u.before?.winRate)} <small class="dim">(${u.before?.trades ?? 0} trade${(u.before?.trades ?? 0) < 5 ? ', bukti kurang' : ''})</small> → <b class="up">${pct(u.after?.winRate)}</b></td><td>${u.after?.trades ?? 0}</td>
          <td><small>${esc(paramText(u.params))}</small></td></tr>`).join('')}</tbody></table>`
        : '<div class="empty">Belum ada upgrade: parameter baru hanya dipakai bila terbukti lebih akurat di data out-of-sample.</div>';
    }
  },
};

