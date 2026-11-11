// Market orbs: one dandelion per market. Every needle is one asset of the full universe
// (length = size of today's move, colour = direction). Top movers are labelled with
// open → last and %, hovering shows any asset, and headlines tagged to an asset float next
// to its needle for 3 minutes. Each orb tallies the sentiment of the last hour of tagged news.
import { esc, fmtPrice, fmtPct } from './util.js';
import { MARKETS, MARKET_KEYS } from './markets.js';

const TAG_MS = 3 * 60 * 1000;
const TALLY_MS = 60 * 60 * 1000;
const COLORS = { up: '#2fd28c', down: '#ff4d5e', flat: '#f2c94c' };

const tone = (v) => (v > 0.05 ? 'up' : v < -0.05 ? 'down' : 'flat');
const sentimentOf = (item, sign) => {
  const s = (item.sentiment ?? 0) * (sign || 1);
  return s > 0.15 ? 'up' : s < -0.15 ? 'down' : 'flat';
};

export class Orbs {
  constructor(stage) {
    this.stage = stage;
    this.canvas = stage.querySelector('canvas');
    this.ctx = this.canvas.getContext('2d');
    this.tip = stage.querySelector('.orb-tip');
    this.heads = stage.querySelector('.orb-heads');
    this.quotes = new Map();
    this.byMarket = Object.fromEntries(MARKET_KEYS.map((k) => [k, []]));
    this.tags = [];
    this.news = []; // {at, market, tone}
    this.ripples = [];
    this.tips = [];
    this.rot = 0;
    this.running = false;
    this.canvas.addEventListener('mousemove', (e) => this.hover(e));
    this.canvas.addEventListener('mouseleave', () => { this.tip.style.display = 'none'; this.hovered = null; });
    this.canvas.addEventListener('click', () => { if (this.hovered) window.SMC.openAnalysis(this.hovered.symbol); });
    setInterval(() => this.renderHeads(), 5000);
  }

  setQuotes(rows) {
    for (const r of rows) this.quotes.set(r.symbol, { ...this.quotes.get(r.symbol), ...r });
    this.regroup();
  }

  /** Compact realtime update: [symbol, last, open, changePct, openChangePct]. */
  applyCompact(rows) {
    for (const [symbol, last, open, changePct, openChangePct] of rows) {
      const q = this.quotes.get(symbol);
      if (q) Object.assign(q, { last, open, changePct, openChangePct });
    }
    this.regroup();
  }

  regroup() {
    for (const k of MARKET_KEYS) this.byMarket[k] = [];
    for (const q of this.quotes.values()) this.byMarket[q.market]?.push(q);
    for (const k of MARKET_KEYS) this.byMarket[k].sort((a, b) => (a.symbol < b.symbol ? -1 : 1));
    this.renderHeads();
  }

  /** News items (with engine symbol tags). `initial` = history load: only very recent ones float. */
  addNews(items, initial = false) {
    const now = Date.now();
    for (const item of items) {
      const at = initial ? new Date(item.published).getTime() : now;
      for (const tag of item.symbols || []) {
        if (!MARKETS[tag.market]) continue;
        const t = sentimentOf(item, tag.sign);
        this.news.push({ at, market: tag.market, tone: t, id: item.id });
        if (now - at < TAG_MS && this.quotes.has(tag.symbol)) {
          this.tags.push({ at, symbol: tag.symbol, market: tag.market, tone: t, title: item.title, source: item.source });
          if (!initial) this.ripples.push({ at: now, market: tag.market, tone: t });
        }
      }
    }
    this.news = this.news.filter((n) => now - n.at < TALLY_MS);
    this.renderHeads();
  }

  layout() {
    const w = this.stage.clientWidth, h = this.stage.clientHeight;
    const cols = w > h * 1.15 ? 2 : 1;
    const rows = Math.ceil(MARKET_KEYS.length / cols);
    const cw = w / cols, ch = h / rows;
    return MARKET_KEYS.map((k, i) => {
      const cx = (i % cols) * cw + cw / 2, cy = Math.floor(i / cols) * ch + ch / 2 + 14;
      return { key: k, x: (i % cols) * cw, y: Math.floor(i / cols) * ch, w: cw, h: ch, cx, cy, r: Math.max(40, Math.min(cw, ch) / 2 - 40) };
    });
  }

  renderHeads() {
    if (!this.heads) return;
    const now = Date.now();
    this.heads.innerHTML = this.layout().map((o) => {
      const rows = this.byMarket[o.key];
      const ch = rows.map((q) => q.changePct).filter((v) => v != null);
      const up = ch.filter((v) => v > 0.05).length, down = ch.filter((v) => v < -0.05).length;
      const avg = ch.length ? ch.reduce((s, v) => s + v, 0) / ch.length : null;
      const news = this.news.filter((n) => n.market === o.key && now - n.at < TALLY_MS);
      const nUp = news.filter((n) => n.tone === 'up').length, nDown = news.filter((n) => n.tone === 'down').length;
      const nFlat = news.length - nUp - nDown;
      const lean = nUp > nDown * 1.2 && nUp >= 2 ? ['up', 'CONDONG NAIK'] : nDown > nUp * 1.2 && nDown >= 2 ? ['down', 'CONDONG TURUN'] : ['flat', news.length ? 'NETRAL / CAMPURAN' : 'BELUM ADA BERITA'];
      return `<div class="orb-head" style="left:${o.x + 10}px;top:${o.y + 6}px;max-width:${o.w - 20}px">
        <b data-go="${o.key}">${esc(MARKETS[o.key].nav)}</b> <span class="dim">${rows.length} aset</span>
        · <span class="up">▲${up}</span> <span class="down">▼${down}</span> · rata-rata <b class="${avg > 0 ? 'up' : avg < 0 ? 'down' : ''}">${avg == null ? '–' : fmtPct(avg)}</b>
        <div class="orb-news">BERITA 60 MNT <span class="up">▲${nUp}</span> <span class="flat">◆${nFlat}</span> <span class="down">▼${nDown}</span> · <b style="color:${COLORS[lean[0]]}">${lean[1]}</b></div>
      </div>`;
    }).join('');
  }

  start() {
    if (this.running) return;
    this.running = true;
    let last = 0;
    const frame = (t) => {
      if (!this.stage.isConnected || !this.stage.closest('.tab-panel')?.classList.contains('active')) { this.running = false; return; }
      if (t - last > 33) { last = t; this.draw(); }
      requestAnimationFrame(frame);
    };
    requestAnimationFrame(frame);
  }

  draw() {
    const { canvas, ctx } = this;
    const w = this.stage.clientWidth, h = this.stage.clientHeight, dpr = window.devicePixelRatio || 1;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
      canvas.style.width = `${w}px`; canvas.style.height = `${h}px`;
      this.renderHeads();
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const now = Date.now();
    this.rot += 0.0006;
    this.tags = this.tags.filter((g) => now - g.at < TAG_MS);
    this.ripples = this.ripples.filter((r) => now - r.at < 2400);
    this.tips = [];
    for (const o of this.layout()) this.drawOrb(o, now);
  }

  drawOrb(o, now) {
    const { ctx } = this;
    const rows = this.byMarket[o.key];
    const { cx, cy, r } = o;
    // guide rings
    ctx.lineWidth = 1;
    for (let k = 1; k <= 3; k++) {
      ctx.beginPath(); ctx.arc(cx, cy, (r * k) / 3, 0, Math.PI * 2);
      ctx.strokeStyle = k === 3 ? 'rgba(38,50,74,.75)' : 'rgba(38,50,74,.35)'; ctx.stroke();
    }
    for (const rp of this.ripples.filter((x) => x.market === o.key)) {
      const p = (now - rp.at) / 2400;
      ctx.beginPath(); ctx.arc(cx, cy, r * 0.15 + p * r, 0, Math.PI * 2);
      ctx.strokeStyle = `${COLORS[rp.tone]}${Math.round((1 - p) * 160).toString(16).padStart(2, '0')}`; ctx.stroke();
    }
    if (!rows.length) {
      ctx.fillStyle = '#56627a'; ctx.font = '11px JetBrains Mono'; ctx.textAlign = 'center';
      ctx.fillText('memuat harga…', cx, cy);
      return;
    }
    const abs = rows.map((q) => Math.abs(q.changePct ?? 0)).sort((a, b) => a - b);
    const scale = Math.max(0.5, abs[Math.floor(abs.length * 0.95)] || 1);
    const n = rows.length;
    const pos = new Map();
    const lines = { up: new Path2D(), down: new Path2D(), flat: new Path2D() };
    const dots = { up: [], down: [], flat: [] };
    rows.forEach((q, i) => {
      const a = this.rot + (i / n) * Math.PI * 2;
      const strength = Math.min(1, Math.abs(q.changePct ?? 0) / scale);
      const L = r * (0.2 + 0.8 * strength);
      const c = Math.cos(a), s = Math.sin(a);
      const t = tone(q.changePct ?? 0);
      lines[t].moveTo(cx + c * r * 0.12, cy + s * r * 0.12);
      lines[t].lineTo(cx + c * L, cy + s * L);
      const tx = cx + c * L, ty = cy + s * L;
      dots[t].push([cx + c * L * 0.55, cy + s * L * 0.55, 1.2], [cx + c * L * 0.8, cy + s * L * 0.8, 1.4], [tx, ty, q.core ? 3 : 2.1]);
      pos.set(q.symbol, { x: tx, y: ty, c, s, q });
      this.tips.push({ x: tx, y: ty, q });
    });
    const alpha = n > 400 ? '30' : n > 120 ? '45' : '70';
    for (const t of ['flat', 'down', 'up']) {
      ctx.strokeStyle = `${COLORS[t]}${alpha}`; ctx.lineWidth = n > 400 ? 0.7 : 1; ctx.stroke(lines[t]);
      ctx.fillStyle = COLORS[t];
      for (const [x, y, rad] of dots[t]) { ctx.globalAlpha = rad > 2 ? 0.95 : 0.55; ctx.fillRect(x - rad / 2, y - rad / 2, rad, rad); }
    }
    ctx.globalAlpha = 1;
    // core
    ctx.beginPath(); ctx.arc(cx, cy, r * 0.11, 0, Math.PI * 2);
    ctx.fillStyle = '#0f1622'; ctx.fill(); ctx.strokeStyle = '#6b9bff'; ctx.stroke();
    ctx.fillStyle = '#e6ebf3'; ctx.font = 'bold 12px JetBrains Mono'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(String(n), cx, cy);
    // labels for top movers: open → last  %
    const movers = [...rows].filter((q) => q.changePct != null).sort((a, b) => b.changePct - a.changePct);
    const k = r > 150 ? 3 : 2;
    const labelled = [...movers.slice(0, k), ...movers.slice(-k)];
    ctx.font = '9.5px JetBrains Mono';
    for (const q of labelled) {
      const p = pos.get(q.symbol);
      if (!p) continue;
      const right = p.c >= 0;
      const x = p.x + (right ? 6 : -6), y = p.y;
      const text = `${q.symbol} ${fmtPrice(q.open, q.decimals)}→${fmtPrice(q.last, q.decimals)} ${fmtPct(q.changePct)}`;
      ctx.textAlign = right ? 'left' : 'right';
      ctx.fillStyle = 'rgba(10,14,22,.75)';
      const tw = ctx.measureText(text).width;
      ctx.fillRect(right ? x - 2 : x - tw - 2, y - 7, tw + 4, 13);
      ctx.fillStyle = COLORS[tone(q.changePct)];
      ctx.fillText(text, x, y);
    }
    // floating news tags (3 minutes), newest first, stacked outward from the needle
    const tags = this.tags.filter((g) => g.market === o.key).sort((a, b) => b.at - a.at).slice(0, 5);
    tags.forEach((g, i) => {
      const p = pos.get(g.symbol);
      if (!p) return;
      const age = (now - g.at) / TAG_MS;
      const fade = age > 0.85 ? (1 - age) / 0.15 : 1;
      const bob = Math.sin(now / 600 + i) * 3;
      const right = p.c >= 0;
      const bx = Math.max(o.x + 6, Math.min(o.x + o.w - 6, p.x + (right ? 26 : -26)));
      const by = Math.max(o.y + 46, Math.min(o.y + o.h - 12, p.y - 18 - i * 22 + bob));
      const word = g.tone === 'up' ? '▲ NAIK' : g.tone === 'down' ? '▼ TURUN' : '◆ NETRAL';
      const title = g.title.length > 46 ? `${g.title.slice(0, 45)}…` : g.title;
      const text = `${word} · ${g.symbol} · ${title}`;
      ctx.globalAlpha = Math.max(0, fade);
      ctx.font = 'bold 9.5px JetBrains Mono';
      const tw = Math.min(ctx.measureText(text).width, o.w * 0.7);
      const x0 = right ? Math.min(bx, o.x + o.w - tw - 14) : Math.max(bx - tw - 10, o.x + 4);
      ctx.strokeStyle = COLORS[g.tone]; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(right ? x0 : x0 + tw + 10, by); ctx.stroke();
      ctx.fillStyle = 'rgba(15,22,34,.94)'; ctx.fillRect(x0, by - 9, tw + 10, 18);
      ctx.strokeRect(x0 + 0.5, by - 8.5, tw + 9, 17);
      ctx.fillStyle = COLORS[g.tone]; ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
      ctx.save(); ctx.beginPath(); ctx.rect(x0, by - 9, tw + 10, 18); ctx.clip();
      ctx.fillText(text, x0 + 5, by + 0.5); ctx.restore();
      ctx.beginPath(); ctx.arc(p.x, p.y, 4 + Math.sin(now / 300) * 1.5, 0, Math.PI * 2); ctx.stroke();
      ctx.globalAlpha = 1;
    });
    ctx.textBaseline = 'alphabetic';
  }

  hover(e) {
    const rect = this.canvas.getBoundingClientRect();
    const x = e.clientX - rect.left, y = e.clientY - rect.top;
    let best = null, bestD = 9;
    for (const t of this.tips) {
      const d = Math.hypot(t.x - x, t.y - y);
      if (d < bestD) { best = t; bestD = d; }
    }
    this.hovered = best?.q || null;
    this.canvas.style.cursor = best ? 'pointer' : 'default';
    if (!best) { this.tip.style.display = 'none'; return; }
    const q = best.q;
    this.tip.style.display = 'block';
    this.tip.style.left = `${Math.min(x + 14, rect.width - 240)}px`;
    this.tip.style.top = `${Math.min(y + 14, rect.height - 90)}px`;
    this.tip.innerHTML = `<b>${esc(q.symbol)}</b> <span class="dim">${esc(q.name)}</span><br>
      open <span class="num">${fmtPrice(q.open, q.decimals)}</span> → sekarang <b class="num">${fmtPrice(q.last, q.decimals)}</b><br>
      <span class="${q.changePct > 0 ? 'up' : q.changePct < 0 ? 'down' : ''}">${fmtPct(q.changePct)} vs penutupan kemarin</span> ·
      <span class="${q.openChangePct > 0 ? 'up' : q.openChangePct < 0 ? 'down' : ''}">${fmtPct(q.openChangePct)} dari open</span><br>
      <span class="dim">sesi ${esc(q.session || '')} · klik untuk analisa</span>`;
  }
}
