// App shell: routing between tabs, realtime connection (SignalR), shared store, clock & sessions.
import { $, $$, api, emit, esc, on, dirClass, toast } from './util.js';
import * as tv from './tv.js';
import dashboard from './dashboard.js';
import calendar from './calendar.js';
import method from './method.js';
import { marketPage } from './market.js';
import { MARKET_KEYS, MARKETS } from './markets.js';

export const store = {
  stats: null,
  screener: null,
  direction: null,
  analyses: [],
  alerts: [],
  headlines: [],
  instruments: [],
  quotes: new Map(), // symbol -> full quote row (all assets of the universe)
  recentNews: [],
  connected: false,
};

const modules = {
  dashboard,
  ...Object.fromEntries(MARKET_KEYS.map((k) => [k, marketPage(k)])),
  kalender: calendar,
  metode: method,
};
const initialized = new Set();
let current = null;

// ------------------------------------------------------------------ routing
export function go(tab, ...params) {
  const hash = '#' + [tab, ...params.filter(Boolean)].map(encodeURIComponent).join('/');
  if (location.hash === hash) route();
  else location.hash = hash;
}
// Which market page owns a symbol: the engine's instrument catalogue decides (instrument.market).
async function marketFor(symbol) {
  const key = String(symbol).toUpperCase();
  const known = store.instruments.find((i) => i.symbol === key);
  if (known) return { market: known.market, symbol: known.symbol };
  try {
    const inst = await api(`/api/resolve?q=${encodeURIComponent(symbol)}`);
    return { market: inst.market, symbol: inst.symbol };
  } catch {
    return { market: 'forex', symbol: key };
  }
}

async function openAnalysis(symbol, tf) {
  const { market, symbol: resolved } = await marketFor(symbol);
  go(MARKETS[market] ? market : 'forex', 'analisa', resolved, tf);
}

window.SMC = { go, openAnalysis };

function route() {
  const [name = 'dashboard', ...params] = location.hash.replace(/^#/, '').split('/').map(decodeURIComponent);
  const tab = modules[name] ? name : 'dashboard';
  $$('.tab-btn').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
  $$('.tab-panel').forEach((p) => p.classList.toggle('active', p.id === `tab-${tab}`));
  if (!initialized.has(tab)) {
    initialized.add(tab);
    modules[tab].init?.(store);
  }
  modules[tab].show?.(params, store);
  if (current !== tab) window.scrollTo({ top: 0 });
  current = tab;
  $(`.tab-btn[data-tab="${tab}"] .badge-dot`)?.remove();
}

function markNewNews(items) {
  const markets = new Set(items.flatMap((i) => i.markets || []));
  for (const m of markets) {
    const btn = $(`.tab-btn[data-tab="${m}"]`);
    if (btn && m !== current && !btn.querySelector('.badge-dot')) btn.insertAdjacentHTML('beforeend', '<span class="badge-dot" title="berita baru"></span>');
  }
}

// Generic panel tabs: <div class="tabbed"><div class="ptabs"><button data-t>…</div><div class="pane" data-t>…</div></div>.
// The newly shown pane receives a 'tabshow' event so views can mount heavy widgets lazily.
document.addEventListener('click', (e) => {
  const btn = e.target.closest('.ptabs button[data-t]');
  if (!btn) return;
  const box = btn.closest('.tabbed');
  if (!box) return;
  box.querySelectorAll(':scope > .ptabs button').forEach((b) => b.classList.toggle('active', b === btn));
  box.querySelectorAll(':scope > .pane').forEach((p) => {
    const on = p.dataset.t === btn.dataset.t;
    p.classList.toggle('active', on);
    if (on) p.dispatchEvent(new CustomEvent('tabshow'));
  });
});

document.addEventListener('click', (e) => {
  if (e.target.closest('[data-focus-search]')) {
    go('dashboard');
    setTimeout(() => $('#hero-input')?.focus(), 50);
    return;
  }
  const t = e.target.closest('[data-tab],[data-go]');
  if (!t) return;
  e.preventDefault();
  go(t.dataset.tab || t.dataset.go);
});
window.addEventListener('hashchange', route);

// ------------------------------------------------------------------ realtime
function setLive(state, label) {
  const dot = $('#live-dot');
  dot.className = `live-dot ${state}`;
  $('#live-label').textContent = label;
}

function statusLine(s) {
  if (!s) return;
  const parts = [
    s.engineOnline ? 'engine OK' : `engine: ${s.engineError || 'offline'}`,
    s.screenerAt ? `screener ${new Date(s.screenerAt).toLocaleTimeString('id-ID', { hour12: false })}` : 'screener …',
    `${s.instruments} instrumen`,
    `${s.newsTotal} berita · ${s.feedsOk}/${s.feedsTotal} sumber`,
    `${s.alerts24h} alert 24j`,
  ];
  $('#status-left').textContent = `SMC Terminal · ${parts.join(' · ')}`;
}

function updateLiveLabel() {
  statusLine(store.stats);
  if (!store.connected) return setLive('off', 'OFFLINE');
  if (store.stats && !store.stats.engineOnline) return setLive('off', 'ENGINE OFF');
  setLive('on', 'LIVE');
}

async function connectRealtime() {
  if (!window.signalR) {
    setLive('off', 'POLLING');
    setInterval(refreshAll, 60_000);
    return;
  }
  const conn = new signalR.HubConnectionBuilder()
    .withUrl('/hub/market')
    .withAutomaticReconnect([0, 2000, 5000, 10000, 20000, 30000])
    .configureLogging(signalR.LogLevel.Warning)
    .build();

  conn.on('news', (items) => {
    pushHeadlines(items);
    emit('news', items);
    markNewNews(items);
  });
  conn.on('newsReset', () => { loadHeadlines(); emit('newsReset'); });
  conn.on('screener', (snap) => { store.screener = snap; emit('screener', snap); });
  conn.on('quotes', (msg) => {
    let missing = false;
    for (const [symbol, last, open, changePct, openChangePct] of msg.rows) {
      const q = store.quotes.get(symbol);
      if (q) Object.assign(q, { last, open, changePct, openChangePct });
      else missing = true;
    }
    emit('quotes', msg);
    if (missing) loadQuotes(); // new symbols in the sweep: fetch their names/decimals once
  });
  conn.on('fundamental', (msg) => emit('fundamental', msg));
  conn.on('direction', async () => { await loadDirection(); });
  conn.on('stats', (s) => { store.stats = s; updateLiveLabel(); emit('stats', s); });
  conn.on('calendar', () => emit('calendar'));
  conn.on('engine', (s) => {
    if (!s.online) toast('Engine Python offline', s.error || 'Analisa tidak tersedia sementara.');
    refreshStats();
  });
  conn.on('analysis', (rec) => {
    store.analyses = [rec, ...store.analyses.filter((a) => !(a.symbol === rec.symbol && a.tf === rec.tf))].slice(0, 20);
    emit('analyses', store.analyses);
  });
  conn.on('alert', (a) => {
    store.alerts = [a, ...store.alerts].slice(0, 100);
    emit('alert', a);
    if (a.severity !== 'info') toast(a.title, a.detail, () => a.symbol && a.kind !== 'calendar' && a.kind !== 'news' && openAnalysis(a.symbol));
  });
  conn.onreconnecting(() => { store.connected = false; setLive('', 'RECONNECT'); });
  conn.onreconnected(() => { store.connected = true; updateLiveLabel(); refreshAll(); });
  conn.onclose(() => { store.connected = false; updateLiveLabel(); setTimeout(connectRealtime, 5000); });

  try {
    await conn.start();
    store.connected = true;
  } catch {
    store.connected = false;
    setTimeout(connectRealtime, 5000);
  }
  updateLiveLabel();
}

// ------------------------------------------------------------------ data loading
async function refreshStats() {
  try { store.stats = await api('/api/stats'); emit('stats', store.stats); } catch { /* shown by label */ }
  updateLiveLabel();
}

async function loadScreener() {
  try { store.screener = await api('/api/screener'); emit('screener', store.screener); } catch (e) { emit('screenerError', e); }
}

async function loadDirection() {
  try { store.direction = await api('/api/direction'); emit('direction', store.direction); } catch (e) { emit('directionError', e); }
}

async function loadHeadlines() {
  try {
    const res = await api('/api/news?limit=40');
    store.headlines = res.items;
    renderMarquee();
  } catch { /* marquee keeps old content */ }
}

function pushHeadlines(items) {
  const ids = new Set(store.headlines.map((h) => h.id));
  store.headlines = [...items.filter((i) => !ids.has(i.id)), ...store.headlines].slice(0, 40);
  renderMarquee();
}

function renderMarquee() {
  const items = store.headlines.slice(0, 30);
  if (!items.length) return;
  const tagOf = (i) => {
    const c = dirClass(i.sentimentLabel);
    return `<span class="tag ${c}">${c === 'bull' ? 'BULL' : c === 'bear' ? 'BEAR' : 'NEWS'}</span>`;
  };
  const html = items.map((i) => `<span class="feed-item">${tagOf(i)}<span class="src">${esc(i.source)}</span>·<a href="${esc(i.link)}" target="_blank" rel="noopener noreferrer">${esc(i.title)}</a></span>`).join('');
  const track = $('#marquee');
  track.style.setProperty('--dur', `${Math.max(60, items.length * 9)}s`);
  track.innerHTML = html + html;
}

let quotesLoading = false;
async function loadQuotes() {
  if (quotesLoading) return;
  quotesLoading = true;
  try {
    const res = await api('/api/quotes?market=all');
    for (const r of res.rows) store.quotes.set(r.symbol, { ...store.quotes.get(r.symbol), ...r });
    if (res.blockOrder) store.blockOrder = res.blockOrder;
    emit('quotesFull', res.rows);
  } catch { /* engine still sweeping */ } finally {
    quotesLoading = false;
  }
}

async function loadRecentNews() {
  try {
    const res = await api('/api/news?sinceMinutes=60&limit=500');
    store.recentNews = res.items;
    emit('recentNews', res.items);
  } catch { /* ignore */ }
}

async function loadAnalyses() {
  try { store.analyses = await api('/api/analyses'); emit('analyses', store.analyses); } catch { /* ignore */ }
}

async function loadAlerts() {
  try { store.alerts = await api('/api/alerts?limit=60'); emit('alerts', store.alerts); } catch { /* ignore */ }
}

async function loadInstruments() {
  try {
    const res = await api('/api/instruments');
    store.instruments = res.instruments;
    $('#symbol-list').innerHTML = res.instruments.map((i) => `<option value="${esc(i.symbol)}">${esc(i.name)}</option>`).join('');
    emit('instruments', res.instruments);
  } catch { /* engine offline: retried by refreshAll */ }
}

async function refreshAll() {
  await Promise.all([refreshStats(), loadScreener(), loadDirection(), loadHeadlines(), loadAnalyses(), loadAlerts(), loadQuotes(), loadRecentNews()]);
  if (!store.instruments.length) loadInstruments();
}

// ------------------------------------------------------------------ clock & sessions
const SESSIONS = [
  { k: 'SYD', open: 21, close: 6 },
  { k: 'TKY', open: 0, close: 9 },
  { k: 'LDN', open: 7, close: 16 },
  { k: 'NY', open: 12, close: 21 },
  { k: 'IDX', open: 2, close: 9, weekdaysOnly: true },
];

function tick() {
  const now = new Date();
  $('#clock').textContent = now.toLocaleTimeString('id-ID', { hour12: false }).replace(/\./g, ':');
  const h = now.getUTCHours() + now.getUTCMinutes() / 60;
  const day = now.getUTCDay();
  const weekend = day === 6 || (day === 0 && h < 21) || (day === 5 && h >= 21);
  $('#sessions').innerHTML = SESSIONS.map((s) => {
    const inside = s.open < s.close ? h >= s.open && h < s.close : h >= s.open || h < s.close;
    const open = inside && !weekend && !(s.weekdaysOnly && (day === 0 || day === 6));
    return `<span class="session ${open ? 'open' : ''}" title="Sesi ${s.k} (UTC ${s.open}:00–${s.close}:00)">${s.k}</span>`;
  }).join('');
}

// ------------------------------------------------------------------ boot screen
const BOOT_STEPS = [
  ['gateway', 'Gateway C# & server lokal'],
  ['realtime', 'Koneksi realtime'],
  ['engine', 'Engine analisa Python'],
  ['screener', 'Data harga & screener SMC'],
  ['direction', 'Model arah market'],
  ['news', 'Berita realtime'],
  ['calendar', 'Kalender ekonomi'],
];

function bootState(h) {
  const sup = h?.engine?.supervisor;
  const st = {};
  st.gateway = h ? ['ok'] : ['run', 'menghubungkan…'];
  st.realtime = store.connected ? ['ok'] : window.signalR ? ['run'] : ['warn', 'mode polling'];
  if (h?.engine?.online) st.engine = ['ok'];
  else if (sup?.failure) st.engine = ['fail', sup.failure];
  else st.engine = ['run', sup?.managed ? `status: ${sup.state}` : 'menunggu engine…'];
  st.screener = h?.screenerAt ? ['ok'] : ['run', 'memindai seluruh watchlist (pertama kali ±20–40 dtk)'];
  st.direction = h?.directionAt ? ['ok'] : ['run'];
  st.news = h?.news?.total > 0 ? ['ok', `${h.news.total} headline`] : ['run', 'mengambil 28 sumber RSS…'];
  st.calendar = h?.calendar?.updated ? ['ok'] : h?.calendar?.error ? ['warn', 'gagal dimuat, dicoba lagi nanti'] : ['run'];
  return st;
}

function runBoot() {
  const el = $('#boot');
  const started = Date.now();
  let finished = false;
  const finish = () => {
    if (finished) return;
    finished = true;
    $('#boot-bar').style.width = '100%';
    $('#boot-msg').textContent = 'Siap.';
    refreshAll();
    setTimeout(() => el.classList.add('done'), 350);
    setTimeout(() => el.remove(), 1000);
  };
  $('#boot-skip').addEventListener('click', finish);
  const poll = async () => {
    if (finished) return;
    let h = null;
    try { h = await api('/api/health'); } catch { /* gateway not up yet */ }
    const st = bootState(h);
    const done = BOOT_STEPS.filter(([k]) => st[k][0] === 'ok' || st[k][0] === 'warn').length;
    const firstRun = BOOT_STEPS.find(([k]) => st[k][0] === 'run');
    $('#boot-steps').innerHTML = BOOT_STEPS.map(([k, label]) => {
      const [state, detail] = st[k];
      const cls = state === 'run' && firstRun?.[0] !== k ? '' : state;
      return `<li class="${cls}"><span class="st"></span><span>${esc(label)}${detail ? ` <small>· ${esc(detail)}</small>` : ''}</span><small>${state === 'ok' ? 'OK' : state === 'fail' ? 'GAGAL' : state === 'warn' ? '!' : ''}</small></li>`;
    }).join('');
    $('#boot-bar').style.width = `${Math.round((done / BOOT_STEPS.length) * 100)}%`;
    const secs = Math.round((Date.now() - started) / 1000);
    $('#boot-msg').textContent = firstRun ? `${firstRun[1]}… (${secs} dtk)` : 'Siap.';
    if (done === BOOT_STEPS.length) return finish();
    if (st.engine[0] === 'fail' || secs > 75) $('#boot-skip').classList.remove('hidden');
    setTimeout(poll, 700);
  };
  poll();
}

// ------------------------------------------------------------------ desktop window integration (WebView2)
function desktopIntegration() {
  const host = window.chrome?.webview;
  const send = (msg) => host?.postMessage(msg);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'F11' && host) { e.preventDefault(); send('window:toggle-fullscreen'); }
  });
  if (!host) return;
  $('#winctl').classList.remove('hidden');
  host.addEventListener('message', (e) => {
    if (e.data === 'fullscreen:1') { $('#win-fs').textContent = '❐'; $('#win-fs').title = 'Mode jendela (F11)'; }
    if (e.data === 'fullscreen:0') { $('#win-fs').textContent = '⛶'; $('#win-fs').title = 'Layar penuh (F11)'; }
    if (typeof e.data === 'string' && e.data.startsWith('zoom:')) $('#zoom-label').textContent = `${Math.round(parseFloat(e.data.slice(5)) * 100)}%`;
  });
  $('#winctl').addEventListener('click', (e) => {
    const z = e.target.closest('[data-zoom]');
    if (z) { send(`zoom:${z.dataset.zoom}`); return; }
    const b = e.target.closest('[data-win]');
    if (!b) return;
    if (b.dataset.win === 'close') confirmExit().then((ok) => ok && send('window:close'));
    else send(`window:${b.dataset.win}`);
  });
  send('ui:ready');
}

function confirmExit() {
  return new Promise((resolve) => {
    const modal = $('#modal');
    $('#modal-text').innerHTML = '<b>Keluar dari SMC Terminal?</b><div class="note">Engine analisa dan pengambilan berita akan dihentikan.</div>';
    modal.classList.remove('hidden');
    const done = (ok) => { modal.classList.add('hidden'); modal.onclick = null; resolve(ok); };
    modal.onclick = (e) => {
      const a = e.target.closest('[data-modal]');
      if (a) done(a.dataset.modal === 'ok');
      else if (e.target === modal) done(false);
    };
  });
}

// ------------------------------------------------------------------ boot
function boot() {
  runBoot();
  desktopIntegration();
  tick();
  setInterval(tick, 1000);
  tv.tickerTape($('#tv-tape'));
  route();
  connectRealtime();
  refreshAll();
  loadInstruments();
  // Safety net: periodic refresh even if a realtime message was missed.
  setInterval(() => { refreshStats(); if (!store.connected) refreshAll(); }, 60_000);
  setInterval(loadQuotes, 5 * 60_000); // full rows (names, sessions) — realtime prices arrive via SignalR
  on('needInstruments', loadInstruments);
}

boot();
