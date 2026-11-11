// App shell: routing between tabs, realtime connection (SignalR), shared store, clock & sessions.
import { $, $$, api, emit, esc, on, dirClass, toast } from './util.js';
import * as tv from './tv.js';
import dashboard from './dashboard.js';
import screener from './screener.js';
import analysis from './analysis.js';
import direction from './direction.js';
import news from './news.js';
import calendar from './calendar.js';
import method from './method.js';

export const store = {
  stats: null,
  screener: null,
  direction: null,
  analyses: [],
  alerts: [],
  headlines: [],
  instruments: [],
  connected: false,
};

const modules = { dashboard, screener, analisa: analysis, arah: direction, berita: news, kalender: calendar, metode: method };
const initialized = new Set();
let current = null;

// ------------------------------------------------------------------ routing
export function go(tab, ...params) {
  const hash = '#' + [tab, ...params.filter(Boolean)].map(encodeURIComponent).join('/');
  if (location.hash === hash) route();
  else location.hash = hash;
}
window.SMC = { go, openAnalysis: (symbol, tf) => go('analisa', symbol, tf) };

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
  if (tab === 'berita') $('#news-dot').classList.add('hidden');
}

document.addEventListener('click', (e) => {
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

function updateLiveLabel() {
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
    if (current !== 'berita') $('#news-dot').classList.remove('hidden');
  });
  conn.on('newsReset', () => { loadHeadlines(); emit('newsReset'); });
  conn.on('screener', (snap) => { store.screener = snap; emit('screener', snap); });
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
    if (a.severity !== 'info') toast(a.title, a.detail, () => a.symbol && a.kind !== 'calendar' && go('analisa', a.symbol));
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
  await Promise.all([refreshStats(), loadScreener(), loadDirection(), loadHeadlines(), loadAnalyses(), loadAlerts()]);
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

// ------------------------------------------------------------------ boot
function boot() {
  tick();
  setInterval(tick, 1000);
  tv.tickerTape($('#tv-tape'));
  route();
  connectRealtime();
  refreshAll();
  loadInstruments();
  // Safety net: periodic refresh even if a realtime message was missed.
  setInterval(() => { refreshStats(); if (!store.connected) refreshAll(); }, 60_000);
  on('needInstruments', loadInstruments);
}

boot();
