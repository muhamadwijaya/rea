// Kalender ekonomi: ForexFactory weekly feed (via gateway) + TradingView economic calendar widget.
import { $, api, esc, on, fmtTime, segmented } from './util.js';
import { economicCalendar } from './tv.js';

let events = [];
let impact = 'medium';
let currencies = new Set();
let countdownTimer;

const rank = (i) => ({ High: 3, Medium: 2, Low: 1 })[i] || 0;

function render() {
  const now = Date.now();
  const min = impact === 'high' ? 3 : impact === 'medium' ? 2 : 0;
  const list = events.filter((e) => rank(e.impact) >= min && (!currencies.size || currencies.has(e.currency)));
  const next = events.find((e) => new Date(e.date).getTime() > now && e.impact === 'High');
  const nextAny = list.find((e) => new Date(e.date).getTime() > now);
  let html = '';
  let day = '';
  for (const e of list) {
    const d = new Date(e.date);
    const label = d.toLocaleDateString('id-ID', { weekday: 'long', day: 'numeric', month: 'long' }).toUpperCase();
    if (label !== day) { day = label; html += `<div class="cal-day">${esc(label)}</div>`; }
    const past = d.getTime() < now;
    html += `<div class="cal-row ${past ? 'past' : ''} ${nextAny === e ? 'next' : ''}">
      <span class="num">${e.impact === 'Holiday' ? 'Libur' : fmtTime(e.date)}</span>
      <span class="ccy">${esc(e.currency)}</span>
      <span class="impact ${esc(e.impact)}">${esc(e.impact.toUpperCase())}</span>
      <span>${esc(e.title)}</span>
      <span class="fc dim">f: <b class="num" style="color:var(--text-2)">${esc(e.forecast || '–')}</b></span>
      <span class="pv dim">p: <span class="num">${esc(e.previous || '–')}</span></span>
    </div>`;
  }
  $('#cal-list').innerHTML = html || '<div class="empty">Tidak ada event untuk filter ini.</div>';
  renderCountdown(next);
}

function renderCountdown(next) {
  clearInterval(countdownTimer);
  const el = $('#cal-next');
  if (!next) { el.innerHTML = '<span class="muted">Tidak ada event high-impact tersisa minggu ini.</span>'; return; }
  const draw = () => {
    const ms = new Date(next.date).getTime() - Date.now();
    if (ms <= 0) { el.innerHTML = `<span class="tag bear">RILIS</span> <b>${esc(next.currency)} ${esc(next.title)}</b>`; return; }
    const h = Math.floor(ms / 3.6e6), m = Math.floor((ms % 3.6e6) / 6e4), s = Math.floor((ms % 6e4) / 1000);
    el.innerHTML = `<div><div class="eyebrow">EVENT HIGH-IMPACT BERIKUTNYA</div><div class="big num">${h}j ${String(m).padStart(2, '0')}m ${String(s).padStart(2, '0')}d</div></div>
      <div><div style="font-size:16px;font-weight:800">${esc(next.currency)} · ${esc(next.title)}</div>
      <div class="muted">${fmtTime(next.date, true)} · forecast ${esc(next.forecast || '–')} · sebelumnya ${esc(next.previous || '–')}</div></div>`;
  };
  draw();
  countdownTimer = setInterval(draw, 1000);
}

async function load() {
  try {
    const res = await api('/api/calendar');
    events = res.events;
    $('#cal-updated').textContent = res.error ? `gagal: ${res.error}` : res.updated ? `update ${fmtTime(res.updated)}` : '';
    const all = [...new Set(events.map((e) => e.currency))].sort();
    $('#cal-ccy').innerHTML = all.map((c) => `<button class="chip ${currencies.has(c) ? 'active' : ''}" data-c="${esc(c)}">${esc(c)}</button>`).join('');
    render();
  } catch (e) {
    $('#cal-list').innerHTML = `<div class="panel-body"><div class="error-box">${esc(e.message)}</div></div>`;
  }
}

export default {
  init() {
    segmented($('#cal-impact'), (v) => { impact = v; render(); });
    $('#cal-ccy').addEventListener('click', (e) => {
      const b = e.target.closest('[data-c]');
      if (!b) return;
      const c = b.dataset.c;
      if (currencies.has(c)) currencies.delete(c); else currencies.add(c);
      b.classList.toggle('active');
      render();
    });
    economicCalendar($('#tv-events'));
    on('calendar', load);
    load();
    setInterval(() => { if ($('#tab-kalender').classList.contains('active')) render(); }, 60_000);
  },
};
