// Virtual trading ("AI training"): the PLAN card of one asset (signal timestamps, auto-cancel rules, virtual
// position, track record, SMC upgrades) and the AI TRAINING page (overall accuracy, equity, leaderboard,
// journal, upgrade log, live chart of virtual trades). Data: /api/training/asset, /summary and /chart.
import { api, esc, fmtPrice } from './util.js';
import { SmcChart } from './smc-chart.js';

const STATUS = {
  pending: ['MENUNGGU ENTRY', 'neu'], open: ['POSISI TERBUKA', 'bull'], win: ['PROFIT ✔', 'bull'], loss: ['LOSS ✘', 'bear'],
  timeout: ['DITUTUP WAKTU', 'ghost'], cancelled: ['BATAL OTOMATIS', 'ghost'],
};
const MARKET_NAME = { forex: 'FOREX', 'saham-as': 'SAHAM AS', 'saham-idx': 'SAHAM IDX', kripto: 'KRIPTO' };
const SOURCE = { asset: ['UPGRADE ASET', 'bull'], group: ['PARAMETER GRUP', 'info'], default: ['DEFAULT', 'ghost'] };
const CONFLUENCE = { sweep: 'wajib sweep likuiditas', fvg: 'wajib FVG', both: 'wajib sweep + FVG' };
const paramText = (p) => `pivot ${p.swing}/${p.internal} · ${p.scope} ${p.kinds} · entry ${p.entry} · SL ${p.sl_atr}×ATR · ${p.target === 'liq' ? 'TP likuiditas' : `TP ${p.rr}R`}${p.manage === 'partial' ? ' · TP1 50% di +1R lalu SL ke entry' : ''}${CONFLUENCE[p.confluence] ? ` · ${CONFLUENCE[p.confluence]}` : ''}${p.pd_filter ? ' · premium/discount' : ''}${p.trend_filter ? ' · searah tren' : ''}`;
// Precision level reached by a setting (lower bound of its proven win rate); thresholds come from the engine.
function levelTag(modes, qualified, low) {
  if (!qualified) return '<span class="tag ghost">BAYANGAN</span>';
  const hit = [...(modes || [])].sort((a, b) => b.low - a.low).find((m) => (low ?? 0) >= m.low);
  return `<span class="tag bull">${esc(hit?.label || 'NORMAL')}</span>`;
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

function signalCard(s, rules, decimals, modes) {
  const [label, cls] = STATUS[s.status] || [s.status, 'ghost'];
  const f = (v) => fmtPrice(v, decimals);
  const risk = Math.abs(s.entry - s.stop);
  return `<div class="tr-signal">
    <div class="tr-sig-top">${dirTag(s.direction)} <span class="tag ${cls}">${label}</span>${s.qualified != null ? ` ${levelTag(modes, s.qualified, s.quality ?? 0.5)}` : ''} <span class="dim">${esc(s.kind || '')} ${esc(s.scope || '')} · ${esc(s.tf)}</span></div>
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
        const ev = d.evidence || {};
        const [srcLabel, srcCls] = SOURCE[d.source] || SOURCE.default;
        el.innerHTML = `
          <h5>SINYAL VIRTUAL TRADING · ${esc(tf.toUpperCase())} · SMC v${d.version}</h5>
          <div class="tr-sig-top">${levelTag(d.modes, ev.qualified, ev.winRateLow)} <span class="tag ${srcCls}">${srcLabel}</span>
            <span class="dim">bukti ${pct(ev.winRate)} dari ${ev.trades ?? 0} trade (batas bawah ${pct(ev.winRateLow)})</span></div>
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
function tradeAt(s, T) {
  if (s.created_t > T) return null;
  if (s.closed_t != null && s.closed_t <= T) return s;
  if (s.status === 'cancelled' || !s.opened_t || s.opened_t > T) return { ...s, status: 'pending', opened_t: null, closed_t: null, r: null };
  return { ...s, status: 'open', closed_t: null, r: null };
}
const clock = (d) => d.toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).replace(/\./g, ':');

/** LIVE CHART tab: candles of one training asset with its virtual trades, merged with live 1-minute bars. */
function mountTrainingChart(el) {
  el.innerHTML = `
    <div class="tr-live-bar">
      <select class="input tr-live-pick" title="Aset & timeframe yang dilatih"></select>
      <button class="tr-follow" title="Pindah otomatis ke aset dengan aktivitas trade virtual terbaru (sinyal, entry, hasil)">IKUTI AKTIVITAS</button>
      <button class="tr-smc" title="Tampilkan struktur SMC (BOS/CHoCH swing & internal)">SMC</button>
      <button class="tr-oos" title="Perbesar ke seluruh periode data uji (out-of-sample)">DATA UJI</button>
      <span class="tr-replay-ctl">
        <button class="tr-play" title="Putar ulang data uji (yang tidak dipakai melatih) candle demi candle">▶ REPLAY</button>
        <span class="tr-speed" title="Kecepatan replay: ×1 = ${REPLAY_BASE} candle per detik">${SPEEDS.map((v) => `<button data-speed="${v}">×${v}</button>`).join('')}</span>
        <button class="tr-end hidden" title="Langsung ke akhir replay">⏭</button>
        <button class="tr-stop hidden" title="Hentikan replay, kembali ke chart live">■ LIVE</button>
      </span>
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
    chart.setTrades({ ...full, trades: full.trades.map((s) => tradeAt(s, T)).filter(Boolean) });
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
    const a = summary?.activity?.[0];
    if (a) return { symbol: a.symbol, tf: a.tf };
    const t = summary?.progress?.lastTuned;
    return t ? { symbol: t.symbol, tf: t.tf } : null;
  }

  async function open(symbol, tf) {
    stopReplay(false);
    current = { symbol, tf };
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
      const t = await api(`/api/training/chart?symbol=${encodeURIComponent(symbol)}&tf=${tf}`);
      if (my !== seq || replay) return;
      chartData = t;
      chart.setData({ ...t, candles: t.candles.map((c) => ({ ...c })) }, { keepRange });
      chart.setTrades(t);
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
      <span class="tr-legend"><i class="lg-tp"></i>zona TP <i class="lg-sl"></i>zona SL <i class="lg-1r"></i>TP1 +1R <i class="lg-live"></i>LIVE <i class="lg-is"></i>data latih (redup)</span>`;
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
    if (target && (target.symbol !== current?.symbol || target.tf !== current?.tf)) open(target.symbol, target.tf);
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
      if (target && (target.symbol !== current?.symbol || target.tf !== current?.tf)) { open(target.symbol, target.tf); return; }
      if (!current) {
        const first = prefs.symbol ? { symbol: prefs.symbol, tf: prefs.tf } : d.leaderboard?.[0];
        if (first) { open(first.symbol, first.tf); return; }
      }
      options();
      if (chart && !chart.data) load(false);
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
          <div class="ptabs tr-tabs"><button data-v="board" class="active">PAPAN AKURASI ASET</button><button data-v="groups">GRUP PASAR</button><button data-v="journal">JURNAL SINYAL</button><button data-v="upgrades">LOG UPGRADE SMC</button><button data-v="live">LIVE CHART</button></div>
          <div class="scroll tr-body"></div>
          <div class="tr-live hidden"></div>
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
      <div class="tr-modes"><span>MODE PRESISI</span>${modes.map((m) => `<button data-mode="${m.key}" class="${m === mode ? 'active' : ''}">${esc(m.label)} ≥${Math.round(m.low * 100)}%</button>`).join('')}</div>
      <h5>LIVE · ${esc(mode.label)} (${live.trades} selesai; semua termasuk bayangan: ${pct(d.liveAll.winRate)} dari ${d.liveAll.trades})</h5>${progress(live.winRate, d.target, d.goal)}
      <h5>BACKTEST OUT-OF-SAMPLE · ${esc(mode.label)} (${bt.trades} trade dari ${bt.pairs} pasangan; semua pasangan: ${pct(d.backtestAll.winRate)} dari ${d.backtestAll.trades})</h5>${progress(bt.winRate, d.target, d.goal)}
      <table class="co-table"><thead><tr><th>Mode presisi</th><th>Pasangan</th><th>Win OOS</th><th>Trade</th><th>Ekspektasi</th><th>Live win</th><th>Trade</th></tr></thead><tbody>
        ${modes.map((m) => `<tr class="${m === mode ? 'sel' : ''}"><td>${esc(m.label)} <span class="dim">≥${Math.round(m.low * 100)}%</span></td><td>${m.backtest.pairs}</td><td>${pct(m.backtest.winRate)}</td><td>${m.backtest.trades}</td><td>${rr(m.backtest.expectancyR)}</td><td>${pct(m.live.winRate)}</td><td>${m.live.trades}</td></tr>`).join('')}</tbody></table>
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
    body.classList.toggle('hidden', this.tab === 'live');
    liveEl.classList.toggle('hidden', this.tab !== 'live');
    if (this.tab === 'live') {
      this.liveChart ||= mountTrainingChart(liveEl);
      this.liveChart.show(d);
      return;
    }
    if (this.tab === 'board') {
      body.innerHTML = d.leaderboard.length ? `<table class="co-table tr-table"><thead><tr><th>Aset</th><th>TF</th><th>Status</th><th>Backtest win</th><th>Trade</th><th>Ekspektasi</th><th>Live win</th><th>Trade</th></tr></thead><tbody>
        ${d.leaderboard.map((b) => `<tr data-sym="${esc(b.symbol)}" data-tf="${esc(b.tf)}"><td><b>${esc(b.symbol)}</b><small class="dim">${esc(MARKET_NAME[b.market] || '')}</small></td><td>${esc(b.tf.toUpperCase())}</td>
          <td title="Bukti parameter aktif: ${pct(b.evidence.winRate)} dari ${b.evidence.trades} trade out-of-sample">${levelTag(d.modes, b.qualified, b.evidence.winRateLow)}<small class="dim">${(SOURCE[b.source] || SOURCE.default)[0].toLowerCase()} v${b.version} · bukti ${pct(b.evidence.winRate)}/${b.evidence.trades}</small></td>
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

