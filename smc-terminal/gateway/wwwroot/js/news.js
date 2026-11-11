// Berita: realtime headlines (SignalR) with category/sentiment/search filters, sentiment summary, sources, TradingView news.
import { $, api, esc, on, dirClass, fmtTime, segmented, timeAgo } from './util.js';
import { timeline } from './tv.js';
import { sentCard } from './direction.js';

const CATS = [['all', 'SEMUA'], ['forex', 'FOREX'], ['us', 'SAHAM AS'], ['idx', 'SAHAM IDX'], ['commodity', 'KOMODITAS'], ['macro', 'MAKRO & BANK SENTRAL'], ['crypto', 'KRIPTO']];
const CAT_LABEL = Object.fromEntries(CATS);
const PAGE = 60;

let filters = { category: 'all', sentiment: 'all', q: '' };
let items = [];
let pending = [];
let offset = 0;
let searchTimer;

function query(extra = '') {
  const p = new URLSearchParams({ limit: PAGE, offset });
  if (filters.category !== 'all') p.set('category', filters.category);
  if (filters.sentiment !== 'all') p.set('sentiment', filters.sentiment);
  if (filters.q) p.set('q', filters.q);
  return `/api/news?${p}${extra}`;
}

function matches(i) {
  if (filters.category !== 'all' && i.category !== filters.category) return false;
  if (filters.sentiment !== 'all' && i.sentimentLabel !== filters.sentiment) return false;
  if (filters.q && !`${i.title} ${i.summary}`.toLowerCase().includes(filters.q.toLowerCase())) return false;
  return true;
}

function itemHtml(i, fresh = false) {
  const c = dirClass(i.sentimentLabel);
  const score = i.sentiment == null ? '' : ` ${i.sentiment > 0 ? '+' : ''}${(i.sentiment * 100).toFixed(0)}`;
  return `<article class="news-item ${fresh ? 'fresh' : ''}">
    <div class="when"><b>${fmtTime(i.published)}</b>${timeAgo(i.published)}</div>
    <div>
      <h4><a href="${esc(i.link)}" target="_blank" rel="noopener noreferrer">${esc(i.title)}</a></h4>
      ${i.summary ? `<p>${esc(i.summary)}</p>` : ''}
      <div class="meta">
        <span class="tag ${c}" title="${esc((i.terms || []).join(', '))}">${esc((i.sentimentLabel || 'neutral').toUpperCase())}${score}</span>
        <span class="tag ghost">${esc(CAT_LABEL[i.category] || i.category)}</span>
        ${(i.assets || []).map((a) => `<span class="tag info">${esc(a.replace('_', ' '))}</span>`).join('')}
        <span>${esc(i.source)}${i.source !== i.feed ? ` · via ${esc(i.feed)}` : ''}</span>
      </div>
    </div>
  </article>`;
}

function render() {
  $('#news-list').innerHTML = items.length ? items.map((i) => itemHtml(i)).join('') : '<div class="empty">Tidak ada berita untuk filter ini.</div>';
}

async function load(reset = true) {
  if (reset) { offset = 0; items = []; pending = []; $('#news-new').classList.add('hidden'); $('#news-list').innerHTML = '<div class="loading">Memuat berita</div>'; }
  try {
    const res = await api(query());
    items = reset ? res.items : [...items, ...res.items];
    $('#news-sub').textContent = `· ${res.total} di memori · update ${res.updated ? timeAgo(res.updated) : '–'}`;
    $('#news-more').classList.toggle('hidden', res.items.length < PAGE);
    render();
  } catch (e) {
    $('#news-list').innerHTML = `<div class="panel-body"><div class="error-box">${esc(e.message)}</div></div>`;
  }
}

async function loadSentiment() {
  try {
    const s = await api('/api/news/sentiment?hours=12');
    $('#sent-bars').innerHTML = [sentCard({ ...s.overall, key: 'SEMUA' }), ...s.categories.map(sentCard)].join('');
    $('#sent-updated').textContent = `${s.total} headline bertag sentimen`;
  } catch { /* keep previous */ }
}

async function loadSources() {
  try {
    const list = await api('/api/news/sources');
    const ok = list.filter((s) => s.lastOk && !s.lastError).length;
    $('#src-sum').textContent = `${ok}/${list.length} aktif`;
    $('#news-sources').innerHTML = list.map((s) => `
      <div class="src-row" title="${esc(s.lastError || '')}"><span class="dot ${s.lastError ? 'bad' : s.lastOk ? 'ok' : ''}"></span>
      <span>${esc(s.name)} <span class="dim">· ${esc(s.host)}</span></span>
      <span class="dim">${s.lastError ? 'gagal' : `${s.lastItemCount} item`}</span></div>`).join('');
  } catch { /* ignore */ }
}

function onRealtime(newItems) {
  const fresh = newItems.filter(matches).filter((i) => !items.some((x) => x.id === i.id));
  if (!fresh.length) return;
  const nearTop = window.scrollY < 300 || !$('#tab-berita').classList.contains('active');
  if (nearTop) {
    items = [...fresh, ...items];
    $('#news-list').insertAdjacentHTML('afterbegin', fresh.map((i) => itemHtml(i, true)).join(''));
    $('#news-list .empty')?.remove();
  } else {
    pending = [...fresh, ...pending];
    const banner = $('#news-new');
    banner.querySelector('span').textContent = pending.length;
    banner.classList.remove('hidden');
  }
}

export default {
  init() {
    $('#news-cats').innerHTML = CATS.map(([k, l]) => `<button class="chip ${k === 'all' ? 'active' : ''}" data-v="${k}">${l}</button>`).join('');
    $('#news-cats').addEventListener('click', (e) => {
      const b = e.target.closest('[data-v]');
      if (!b) return;
      $('#news-cats').querySelectorAll('.chip').forEach((x) => x.classList.toggle('active', x === b));
      filters.category = b.dataset.v;
      load();
    });
    segmented($('#news-sent'), (v) => { filters.sentiment = v; load(); });
    $('#news-q').addEventListener('input', (e) => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => { filters.q = e.target.value.trim(); load(); }, 300);
    });
    $('#news-more').addEventListener('click', () => { offset += PAGE; load(false); });
    $('#news-new').addEventListener('click', () => {
      items = [...pending, ...items];
      pending = [];
      $('#news-new').classList.add('hidden');
      render();
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
    $('#tl-market').addEventListener('change', (e) => timeline($('#tv-timeline'), e.target.value));
    timeline($('#tv-timeline'), 'forex');
    on('news', onRealtime);
    on('newsReset', () => load());
    load();
    loadSentiment();
    loadSources();
    setInterval(() => { if ($('#tab-berita').classList.contains('active')) { loadSentiment(); loadSources(); } }, 60_000);
    // Refresh relative times without refetching.
    setInterval(() => { if ($('#tab-berita').classList.contains('active') && !document.hidden) render(); }, 60_000);
  },
  show() { loadSentiment(); },
};
