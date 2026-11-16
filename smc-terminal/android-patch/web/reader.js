// In-app news reader (Android). Article links open in a reader page inside the app instead of an outside browser:
// the host (ReaderHooks) shows the page, takes its readable text once it has rendered and hands it back here, where
// the voice assistant reads it aloud and has the AI analyse exactly that text. Without the host (desktop) links keep
// their normal behaviour.
import { on } from './util.js';

const byLink = new Map();  // link → news item, from every list this app has shown
const byId = new Map();
const pending = new Map();  // url → page opened automatically, waiting for its text
const host = () => (window.SharxReader && typeof window.SharxReader.open === 'function' ? window.SharxReader : null);
const outside = (url) => {
  try {
    const u = new URL(url, location.href);
    return (u.protocol === 'http:' || u.protocol === 'https:') && u.origin !== location.origin;
  } catch { return false; }
};

export const readerAvailable = () => !!host();

export function rememberNews(items) {
  for (const i of items || []) {
    if (!i?.link) continue;
    byLink.set(i.link, i);
    if (i.id) byId.set(i.id, i);
  }
  if (byLink.size > 3000) for (const k of [...byLink.keys()].slice(0, byLink.size - 2000)) byLink.delete(k);
  if (byId.size > 3000) for (const k of [...byId.keys()].slice(0, byId.size - 2000)) byId.delete(k);
}

export const newsByLink = (url) => byLink.get(url) || null;
export const newsById = (id) => byId.get(id) || null;

/** Opens `url` in the in-app reader (with its news item when known); false when there is no reader (desktop).
 *  `auto`: opened by the assistant: the page closes itself once its text is taken (a touch makes it manual). */
export function openInApp(url, item = null, { auto = false } = {}) {
  const reader = host();
  if (!reader || !outside(url)) return false;
  const it = item || byLink.get(url) || null;
  if (it) rememberNews([{ ...it, link: url }]);
  reader.open(JSON.stringify({
    url, id: it?.id || null, title: it?.title || '', source: it?.source || '', lang: it?.lang || '',
    symbol: it?.symbols?.[0]?.symbol || null, auto,
  }));
  return true;
}

/** Opens the page automatically and resolves with its text event ({text, title, finalUrl, …}), or null when the
 *  page gave no text (verification, too slow, closed, no reader). The page closes itself after its text is taken. */
export function extractPage(url, item = null, { timeoutMs = 45000 } = {}) {
  if (!host() || !outside(url)) return Promise.resolve(null);
  return new Promise((resolve) => {
    const entry = {
      finish(value) {
        clearTimeout(entry.timer);
        if (pending.get(url) === entry) pending.delete(url);
        resolve(value);
      },
    };
    entry.timer = setTimeout(() => entry.finish(null), timeoutMs);
    pending.get(url)?.finish(null);
    pending.set(url, entry);
    if (!openInApp(url, item, { auto: true })) entry.finish(null);
  });
}

/** One line in the reader's status bar (what the assistant is doing with the page). */
export function readerStatus(text) {
  try { host()?.status?.(String(text)); } catch { /* reader closed */ }
}

export function initReader({ onEvent, openTv }) {
  on('news', rememberNews);
  // Outside links of the app page open in the reader. Capture phase: before the views' own handlers. Headlines
  // without a target run the assistant (their handler calls readNow, which opens the reader with the item).
  document.addEventListener('click', (e) => {
    if (!host() || e.defaultPrevented) return;
    const a = e.target.closest?.('a[href]');
    if (!a || !outside(a.href)) return;
    const holder = a.closest('[data-news-id]');
    if (!a.target && holder) return;
    e.preventDefault();
    e.stopPropagation();
    openInApp(a.href, byLink.get(a.href) || (holder && byId.get(holder.dataset.newsId)) || null);
  }, true);
  const openWindow = window.open.bind(window);
  window.open = (url, target, features) => (url && outside(String(url)) && openInApp(String(url)) ? null : openWindow(url, target, features));
  // events of the host: the page's text was taken, or the reader buttons asked to read / analyse it again
  window.__sharxReaderEvent = (evt) => {
    const waiting = evt?.url ? pending.get(evt.url) : null;
    if (waiting && ['text', 'failed', 'closed'].includes(evt.type)) {
      waiting.finish(evt.type === 'text' ? evt : null);  // a page opened automatically: its job takes the text
      return;
    }
    try { onEvent?.(evt); } catch (err) { console.warn('reader event', err); }
  };
  // a TradingView symbol (ticker tape, widget links) opens the app's chart of that asset
  window.__sharxOpenTv = (symbol) => {
    try { openTv?.(symbol); } catch (err) { console.warn('tradingview symbol', err); }
  };
}
