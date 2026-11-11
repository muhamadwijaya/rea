// Shared helpers: DOM, formatting, API, tiny event bus.

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
export const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ESC[c]);

export const bus = new EventTarget();
export const emit = (type, detail) => bus.dispatchEvent(new CustomEvent(type, { detail }));
export const on = (type, fn) => bus.addEventListener(type, (e) => fn(e.detail));

export class ApiError extends Error {
  constructor(message, status, body) { super(message); this.status = status; this.body = body; }
}

export async function api(path, { signal } = {}) {
  let res;
  try {
    res = await fetch(path, { signal, headers: { Accept: 'application/json' } });
  } catch (err) {
    if (err.name === 'AbortError') throw err;
    throw new ApiError('Gateway tidak dapat dihubungi. Pastikan start.bat masih berjalan.', 0, null);
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(body?.message || `HTTP ${res.status}`, res.status, body);
  return body;
}

export function decimalsFor(price, hint) {
  if (Number.isInteger(hint)) return hint;
  const a = Math.abs(price ?? 0);
  if (a >= 1000) return 2;
  if (a >= 100) return 3;
  if (a >= 1) return 4;
  return 5;
}

export function fmtPrice(v, decimals) {
  if (v === null || v === undefined || Number.isNaN(v)) return '–';
  const d = decimalsFor(v, decimals);
  return Number(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
}

export function fmtPct(v, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return '–';
  return `${v > 0 ? '+' : ''}${Number(v).toFixed(digits)}%`;
}

export const fmtScore = (v) => (v === null || v === undefined ? '–' : `${v > 0 ? '+' : ''}${Math.round(v)}`);
export const signClass = (v) => (v > 0 ? 'up' : v < 0 ? 'down' : 'flat');

export function dirClass(direction) {
  if (!direction) return 'neu';
  if (direction.includes('bull')) return 'bull';
  if (direction.includes('bear')) return 'bear';
  return 'neu';
}
export const dirColor = (direction) => ({ bull: 'var(--bull)', bear: 'var(--bear)', neu: 'var(--neu)' })[dirClass(direction)];
export const dirHex = (direction) => ({ bull: '#2fd28c', bear: '#ff4d5e', neu: '#f2c94c' })[dirClass(direction)];
export const arrow = (direction) => ({ bull: '▲', bear: '▼', neu: '◆' })[dirClass(direction)];

export function badge(direction, label) {
  return `<span class="badge ${esc(direction || 'neutral')}">${esc(label || 'SIDEWAYS')}</span>`;
}

export function trendText(trend) {
  if (trend === 'bullish') return '<span class="trend up">▲ BULL</span>';
  if (trend === 'bearish') return '<span class="trend down">▼ BEAR</span>';
  return '<span class="trend flat">◆ N/A</span>';
}

export function timeAgo(input) {
  const t = typeof input === 'number' ? input * 1000 : new Date(input).getTime();
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 45) return 'baru saja';
  if (s < 3600) return `${Math.round(s / 60)} mnt`;
  if (s < 86400) return `${Math.round(s / 3600)} jam`;
  return `${Math.round(s / 86400)} hr`;
}

export function fmtTime(input, withDate = false) {
  const d = typeof input === 'number' ? new Date(input * 1000) : new Date(input);
  const opts = withDate
    ? { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false }
    : { hour: '2-digit', minute: '2-digit', hour12: false };
  return d.toLocaleString('id-ID', opts).replace('.', ':');
}

export function sparkline(values, color = '#6b9bff', w = 120, h = 32, fill = true) {
  const v = (values || []).filter((x) => Number.isFinite(x));
  if (v.length < 2) return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><line x1="0" y1="${h / 2}" x2="${w}" y2="${h / 2}" stroke="${color}" stroke-opacity=".5"/></svg>`;
  const min = Math.min(...v), max = Math.max(...v), span = max - min || 1;
  const pts = v.map((x, i) => [(i / (v.length - 1)) * w, h - 2 - ((x - min) / span) * (h - 4)]);
  const d = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join('');
  const area = fill ? `<path d="${d}L${w},${h}L0,${h}Z" fill="${color}" fill-opacity=".12"/>` : '';
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">${area}<path d="${d}" fill="none" stroke="${color}" stroke-width="1.3" vector-effect="non-scaling-stroke"/></svg>`;
}

export function scoreBar(score) {
  const s = Math.max(-100, Math.min(100, score || 0));
  const width = Math.abs(s) / 2;
  const left = s >= 0 ? 50 : 50 - width;
  return `<span class="scorebar"><i style="left:${left}%;width:${width}%;background:${dirHex(s > 0 ? 'bull' : s < 0 ? 'bear' : '')}"></i></span>`;
}

export function divBar(score, max = 100) {
  const s = Math.max(-max, Math.min(max, score || 0));
  const width = (Math.abs(s) / max) * 50;
  const left = s >= 0 ? 50 : 50 - width;
  const color = s > 0 ? 'var(--bull)' : s < 0 ? 'var(--bear)' : 'var(--neu)';
  return `<div class="divbar"><i style="left:${left}%;width:${width}%;background:${color}"></i></div>`;
}

export function toast(title, body, onClick) {
  const zone = $('#toasts');
  const el = document.createElement('div');
  el.className = 'toast';
  el.innerHTML = `<b>${esc(title)}</b><span class="muted">${esc(body)}</span>`;
  el.addEventListener('click', () => { onClick?.(); el.remove(); });
  zone.prepend(el);
  while (zone.children.length > 4) zone.lastChild.remove();
  setTimeout(() => el.remove(), 9000);
}

export function segmented(root, onChange) {
  root.addEventListener('click', (e) => {
    const b = e.target.closest('button[data-v]');
    if (!b) return;
    root.querySelectorAll('button').forEach((x) => x.classList.toggle('active', x === b));
    onChange(b.dataset.v);
  });
  return {
    get value() { return root.querySelector('button.active')?.dataset.v; },
    set(v) { root.querySelectorAll('button').forEach((x) => x.classList.toggle('active', x.dataset.v === v)); },
  };
}

export const CLASS_LABEL = { forex: 'FX', us: 'US', idx: 'IDX', index: 'IDXS', commodity: 'KOM', crypto: 'KRIPTO' };
