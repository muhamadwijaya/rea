// Berita view of one market page: realtime headlines for that market only (SignalR), sentiment summary,
// sources and the market's TradingView news timeline.
import { api, esc, on, dirClass, fmtTime, segmented, timeAgo } from './util.js';
import { timeline } from './tv.js';
import { sentCard } from './overview.js';

const CAT_LABEL = { forex: 'FOREX', us: 'SAHAM AS', idx: 'SAHAM IDX', commodity: 'KOMODITAS', macro: 'MAKRO', crypto: 'KRIPTO' };
const ASSET_LABEL = { US_EQ: 'SAHAM AS', IDX: 'IDX', CRYPTO: 'KRIPTO', GOLD: 'EMAS', OIL: 'MINYAK' };
const PAGE = 60;

export class NewsView {
  constructor(el, cfg) {
    this.el = el;
    this.cfg = cfg;
    this.filters = { category: 'all', sentiment: 'all', q: '' };
    this.items = [];
    this.offset = 0;
    el.innerHTML = `
      <div class="sent-strip sent-bars"></div>
      <div class="toolbar">
        ${cfg.newsCats ? `<div class="chips news-cats">${cfg.newsCats.map(([k, l]) => `<button class="chip ${k === 'all' ? 'active' : ''}" data-v="${k}">${l}</button>`).join('')}</div>` : ''}
        <div class="seg news-sent"><button data-v="all" class="active">SEMUA</button><button data-v="bullish">BULLISH</button><button data-v="bearish">BEARISH</button><button data-v="neutral">NETRAL</button></div>
        <input class="input news-q" placeholder="cari berita…">
        <span class="grow"></span><span class="time sent-updated"></span>
      </div>
      <div class="split-2">
        <div class="panel col-panel">
          <div class="panel-head"><span class="diamond">◆</span> BERITA ${esc(cfg.nav)} REALTIME <span class="sub news-sub"></span><span class="right live-label">LIVE</span></div>
          <div class="scroll news-scroll">
            <div class="news-list"></div>
            <div style="padding:12px; text-align:center"><button class="btn news-more">MUAT LEBIH BANYAK</button></div>
          </div>
        </div>
        <div class="panel tabbed">
          <div class="ptabs"><button data-t="tv" class="active">TRADINGVIEW NEWS</button><button data-t="src">SUMBER BERITA <span class="src-sum"></span></button></div>
          <div class="pane active" data-t="tv"><div class="tv-fill tv-timeline"></div></div>
          <div class="pane" data-t="src"><div class="panel-body news-sources"></div></div>
        </div>
      </div>`;
    const $ = (s) => el.querySelector(s);
    $('.news-cats')?.addEventListener('click', (e) => {
      const b = e.target.closest('[data-v]');
      if (!b) return;
      el.querySelectorAll('.news-cats .chip').forEach((x) => x.classList.toggle('active', x === b));
      this.filters.category = b.dataset.v;
      this.load();
    });
    segmented($('.news-sent'), (v) => { this.filters.sentiment = v; this.load(); });
    let timer;
    $('.news-q').addEventListener('input', (e) => {
      clearTimeout(timer);
      timer = setTimeout(() => { this.filters.q = e.target.value.trim(); this.load(); }, 300);
    });
    $('.news-more').addEventListener('click', () => { this.offset += PAGE; this.load(false); });
    $('.news-list').addEventListener('click', (e) => {
      const b = e.target.closest('[data-speak]');
      const item = b && this.items.find((i) => i.id === b.dataset.speak);
      if (item) window.SMC.voice?.readNow(item);
    });
    timeline($('.tv-timeline'), cfg.timeline);
    on('news', (items) => this.onRealtime(items));
    on('newsReset', () => this.load());
    this.load();
    this.loadSentiment();
    this.loadSources();
    setInterval(() => {
      if (!this.visible() || document.hidden) return;
      this.loadSentiment();
      this.loadSources();
      this.renderList();
    }, 60_000);
  }

  visible() { return this.el.classList.contains('active') && this.el.closest('.tab-panel')?.classList.contains('active'); }

  query() {
    const p = new URLSearchParams({ market: this.cfg.key, limit: PAGE, offset: this.offset });
    if (this.filters.category !== 'all') p.set('category', this.filters.category);
    if (this.filters.sentiment !== 'all') p.set('sentiment', this.filters.sentiment);
    if (this.filters.q) p.set('q', this.filters.q);
    return `/api/news?${p}`;
  }

  matches(i) {
    if (!(i.markets || []).includes(this.cfg.key)) return false;
    if (this.filters.category !== 'all' && i.category !== this.filters.category) return false;
    if (this.filters.sentiment !== 'all' && i.sentimentLabel !== this.filters.sentiment) return false;
    if (this.filters.q && !`${i.title} ${i.summary}`.toLowerCase().includes(this.filters.q.toLowerCase())) return false;
    return true;
  }

  itemHtml(i, fresh = false) {
    const c = dirClass(i.sentimentLabel);
    const score = i.sentiment == null ? '' : ` ${i.sentiment > 0 ? '+' : ''}${(i.sentiment * 100).toFixed(0)}`;
    return `<article class="news-item ${fresh ? 'fresh' : ''}">
      <div class="when"><b>${fmtTime(i.published)}</b>${timeAgo(i.published)}</div>
      <div>
        <h4><button class="va-speak" data-speak="${esc(i.id)}" title="Bacakan berita lengkap + analisa AI">🔊</button><a href="${esc(i.link)}" target="_blank" rel="noopener noreferrer">${esc(i.title)}</a></h4>
        ${i.summary ? `<p>${esc(i.summary)}</p>` : ''}
        <div class="meta">
          <span class="tag ${c}" title="${esc((i.terms || []).join(', '))}">${esc((i.sentimentLabel || 'neutral').toUpperCase())}${score}</span>
          <span class="tag ghost">${esc(CAT_LABEL[i.category] || i.category)}</span>
          ${(i.assets || []).map((a) => `<span class="tag info">${esc(ASSET_LABEL[a] || a)}</span>`).join('')}
          <span>${esc(i.source)}${i.source !== i.feed ? ` · via ${esc(i.feed)}` : ''}</span>
        </div>
      </div>
    </article>`;
  }

  renderList() {
    this.el.querySelector('.news-list').innerHTML = this.items.length
      ? this.items.map((i) => this.itemHtml(i)).join('')
      : '<div class="empty">Tidak ada berita untuk filter ini.</div>';
  }

  async load(reset = true) {
    const $ = (s) => this.el.querySelector(s);
    if (reset) {
      this.offset = 0;
      this.items = [];
      $('.news-list').innerHTML = '<div class="loading">Memuat berita</div>';
    }
    try {
      const res = await api(this.query());
      this.items = reset ? res.items : [...this.items, ...res.items];
      $('.news-sub').textContent = `· update ${res.updated ? timeAgo(res.updated) : '–'}`;
      $('.news-more').classList.toggle('hidden', res.items.length < PAGE);
      this.renderList();
    } catch (e) {
      $('.news-list').innerHTML = `<div class="panel-body"><div class="error-box">${esc(e.message)}</div></div>`;
    }
  }

  async loadSentiment() {
    try {
      const s = await api(`/api/news/sentiment?hours=12&market=${encodeURIComponent(this.cfg.key)}`);
      const cards = [sentCard({ ...s.overall, key: 'TOTAL' })];
      if (s.categories.length > 1) cards.push(...s.categories.map(sentCard));
      cards.push(...s.assets.slice(0, 6).map((a) => sentCard({ ...a, key: ASSET_LABEL[a.key] || a.key })));
      this.el.querySelector('.sent-bars').innerHTML = cards.join('');
      this.el.querySelector('.sent-updated').textContent = `${s.total} headline bertag sentimen`;
    } catch { /* keep previous */ }
  }

  async loadSources() {
    try {
      const list = await api(`/api/news/sources?market=${encodeURIComponent(this.cfg.key)}`);
      const ok = list.filter((s) => s.lastOk && !s.lastError).length;
      this.el.querySelector('.src-sum').textContent = `· ${ok}/${list.length}`;
      this.el.querySelector('.news-sources').innerHTML = list.map((s) => `
        <div class="src-row" title="${esc(s.lastError || '')}"><span class="dot ${s.lastError ? 'bad' : s.lastOk ? 'ok' : ''}"></span>
        <span>${esc(s.name)} <span class="dim">· ${esc(s.host)}</span></span>
        <span class="dim">${s.lastError ? 'gagal' : `${s.lastItemCount} item`}</span></div>`).join('');
    } catch { /* ignore */ }
  }

  onRealtime(newItems) {
    const fresh = newItems.filter((i) => this.matches(i)).filter((i) => !this.items.some((x) => x.id === i.id));
    if (!fresh.length) return;
    // New headlines go in immediately, newest first. A reader scrolled further down keeps their place:
    // the scroll offset grows by the inserted height.
    const list = this.el.querySelector('.news-list');
    const scroller = this.el.querySelector('.news-scroll');
    const before = list.scrollHeight;
    const keep = scroller.scrollTop > 0;
    this.items = [...fresh, ...this.items];
    list.querySelector('.empty')?.remove();
    list.insertAdjacentHTML('afterbegin', fresh.map((i) => this.itemHtml(i, true)).join(''));
    if (keep) scroller.scrollTop += list.scrollHeight - before;
  }

  show() { this.loadSentiment(); }
}
