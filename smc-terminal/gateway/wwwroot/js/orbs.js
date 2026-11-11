// Market orbs: one 3D dandelion sphere per market. Every filament is one asset of the full universe,
// spread evenly over a sphere (Fibonacci lattice); length = size of today's move, colour = direction
// (gold = up, blue = down). The spheres rotate slowly with perspective and depth shading and can be
// dragged. Top movers facing the viewer are labelled with open → last and %, hovering shows any asset,
// and headlines tagged to an asset float next to its filament tip for 3 minutes. Each orb tallies the
// sentiment of the last hour of tagged news.
import { esc, fmtPrice, fmtPct } from './util.js';
import { MARKETS, MARKET_KEYS } from './markets.js';

const TAG_MS = 3 * 60 * 1000;
const TALLY_MS = 60 * 60 * 1000;
const COLORS = { up: '#f5c04e', down: '#55acff', flat: '#9aa7bd' };
const RGB = { up: '245,192,78', down: '85,172,255', flat: '154,167,189' };
const GOLDEN = Math.PI * (3 - Math.sqrt(5));
const CAMERA = 4; // camera distance in sphere radii (perspective strength)
const DEPTH_BUCKETS = 7;
const SPIN = 0.00011; // radians per ms of automatic rotation
const TONES = ['flat', 'down', 'up'];

/** Small additive glow sprite per tone, drawn at every filament tip. */
function glowSprite(rgb) {
  const c = document.createElement('canvas');
  c.width = c.height = 32;
  const g = c.getContext('2d');
  const grad = g.createRadialGradient(16, 16, 0, 16, 16, 16);
  grad.addColorStop(0, 'rgba(255,255,255,1)');
  grad.addColorStop(0.18, `rgba(${rgb},0.95)`);
  grad.addColorStop(0.45, `rgba(${rgb},0.28)`);
  grad.addColorStop(1, `rgba(${rgb},0)`);
  g.fillStyle = grad;
  g.fillRect(0, 0, 32, 32);
  return c;
}

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
    this.views = {};
    this.sprites = Object.fromEntries(TONES.map((t) => [t, glowSprite(RGB[t])]));
    this.lastFrame = 0;
    this.running = false;
    this.drag = null;
    this.canvas.addEventListener('pointerdown', (e) => this.pointerDown(e));
    this.canvas.addEventListener('pointermove', (e) => this.pointerMove(e));
    this.canvas.addEventListener('pointerup', (e) => this.pointerUp(e));
    this.canvas.addEventListener('pointercancel', () => { this.drag = null; });
    this.canvas.addEventListener('mouseleave', () => { this.tip.style.display = 'none'; this.hovered = null; });
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

  view(key) {
    if (!this.views[key]) this.views[key] = { yaw: MARKET_KEYS.indexOf(key) * 1.3, pitch: -0.38, held: 0 };
    return this.views[key];
  }

  local(e) {
    const rect = this.canvas.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top, rect };
  }

  pointerDown(e) {
    const { x, y } = this.local(e);
    const o = this.layout().find((c) => x >= c.x && x < c.x + c.w && y >= c.y && y < c.y + c.h);
    if (!o) return;
    const v = this.view(o.key);
    this.drag = { key: o.key, x: e.clientX, y: e.clientY, yaw: v.yaw, pitch: v.pitch, moved: false };
    this.canvas.setPointerCapture?.(e.pointerId);
  }

  pointerMove(e) {
    const d = this.drag;
    if (!d) { this.hover(e); return; }
    const dx = e.clientX - d.x, dy = e.clientY - d.y;
    if (!d.moved && Math.hypot(dx, dy) < 4) return;
    d.moved = true;
    const v = this.view(d.key);
    v.yaw = d.yaw + dx * 0.008;
    v.pitch = Math.max(-1.35, Math.min(1.35, d.pitch + dy * 0.008));
    v.held = Date.now();
    this.tip.style.display = 'none';
    this.canvas.style.cursor = 'grabbing';
  }

  pointerUp(e) {
    const d = this.drag;
    this.drag = null;
    this.canvas.style.cursor = 'default';
    if (d && !d.moved) {
      this.hover(e);
      if (this.hovered) window.SMC.openAnalysis(this.hovered.symbol);
    }
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
      return { key: k, x: (i % cols) * cw, y: Math.floor(i / cols) * ch, w: cw, h: ch, cx, cy, r: Math.max(40, (Math.min(cw, ch) / 2 - 34) / 1.04) };
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
    const dt = this.lastFrame ? Math.min(100, now - this.lastFrame) : 0;
    this.lastFrame = now;
    for (const k of MARKET_KEYS) {
      const v = this.view(k);
      if (!this.drag && now - v.held > 3000) v.yaw += dt * SPIN;
    }
    this.tags = this.tags.filter((g) => now - g.at < TAG_MS);
    this.ripples = this.ripples.filter((r) => now - r.at < 2400);
    this.tips = [];
    for (const o of this.layout()) this.drawOrb(o, now);
  }

  drawOrb(o, now) {
    const { ctx } = this;
    const rows = this.byMarket[o.key];
    const { cx, cy, r } = o;
    const v = this.view(o.key);
    const cyw = Math.cos(v.yaw), syw = Math.sin(v.yaw), cp = Math.cos(v.pitch), sp = Math.sin(v.pitch);
    // rotate (yaw around Y, then pitch around X) and project with perspective; z > 0 faces the viewer
    const project = (x, y, z) => {
      const x1 = x * cyw + z * syw, z1 = z * cyw - x * syw;
      const y2 = y * cp - z1 * sp, z2 = y * sp + z1 * cp;
      const k = CAMERA / (CAMERA - z2);
      return [cx + x1 * r * k, cy + y2 * r * k, z2];
    };
    // atmosphere + wireframe shell give the sphere its volume
    const halo = ctx.createRadialGradient(cx, cy, r * 0.05, cx, cy, r * 1.12);
    halo.addColorStop(0, 'rgba(120,150,255,0.10)');
    halo.addColorStop(0.55, 'rgba(60,90,170,0.05)');
    halo.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = halo;
    ctx.beginPath(); ctx.arc(cx, cy, r * 1.12, 0, Math.PI * 2); ctx.fill();
    ctx.lineWidth = 0.7;
    for (const [axis, tilt] of [['y', 0], ['x', 0], ['x', Math.PI / 2], ['y', 0.62], ['y', -0.62]]) {
      ctx.beginPath();
      for (let j = 0; j <= 64; j++) {
        const a = (j / 64) * Math.PI * 2;
        const ring = axis === 'y' ? [Math.cos(a) * Math.cos(tilt), Math.sin(tilt), Math.sin(a) * Math.cos(tilt)]
          : [Math.sin(tilt) * Math.cos(a), Math.sin(a), Math.cos(tilt) * Math.cos(a)];
        const [px, py] = project(ring[0], ring[1], ring[2]);
        if (j) ctx.lineTo(px, py); else ctx.moveTo(px, py);
      }
      ctx.strokeStyle = 'rgba(110,140,210,0.10)'; ctx.stroke();
    }
    for (const rp of this.ripples.filter((x) => x.market === o.key)) {
      const p = (now - rp.at) / 2400;
      ctx.beginPath(); ctx.arc(cx, cy, r * 0.15 + p * r, 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(${RGB[rp.tone]},${((1 - p) * 0.6).toFixed(3)})`; ctx.lineWidth = 1.2; ctx.stroke();
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
    // filaments grouped by tone and depth so a few thousand assets cost a few dozen strokes
    const groups = TONES.map(() => Array.from({ length: DEPTH_BUCKETS }, () => ({ inner: new Path2D(), outer: new Path2D(), tips: [] })));
    rows.forEach((q, i) => {
      const dy = 1 - (2 * (i + 0.5)) / n, ring = Math.sqrt(1 - dy * dy), th = i * GOLDEN;
      const dx = Math.cos(th) * ring, dz = Math.sin(th) * ring;
      const strength = Math.min(1, Math.abs(q.changePct ?? 0) / scale);
      const L = 0.3 + 0.7 * strength;
      const [x0, y0] = project(dx * 0.1, dy * 0.1, dz * 0.1);
      const [xm, ym] = project(dx * L * 0.55, dy * L * 0.55, dz * L * 0.55);
      const [xt, yt, zt] = project(dx * L, dy * L, dz * L);
      const depth = (zt / L + 1) / 2; // 0 = far side, 1 = facing the viewer
      const t = tone(q.changePct ?? 0);
      const g = groups[TONES.indexOf(t)][Math.min(DEPTH_BUCKETS - 1, Math.floor(depth * DEPTH_BUCKETS))];
      g.inner.moveTo(x0, y0); g.inner.lineTo(xm, ym);
      g.outer.moveTo(xm, ym); g.outer.lineTo(xt, yt);
      g.tips.push([xt, yt, q.core]);
      pos.set(q.symbol, { x: xt, y: yt, depth, right: xt >= cx, q });
      this.tips.push({ x: xt, y: yt, depth, q });
    });
    const density = n > 400 ? 0.55 : n > 120 ? 0.75 : 1;
    for (let b = 0; b < DEPTH_BUCKETS; b++) {
      const f = (b + 0.5) / DEPTH_BUCKETS;
      const alpha = (0.08 + 0.92 * f * f) * density;
      TONES.forEach((t, ti) => {
        const g = groups[ti][b];
        if (!g.tips.length) return;
        ctx.lineWidth = 0.5 + 0.7 * f;
        ctx.strokeStyle = `rgba(${RGB[t]},${(alpha * 0.28).toFixed(3)})`; ctx.stroke(g.inner);
        ctx.strokeStyle = `rgba(${RGB[t]},${(alpha * 0.75).toFixed(3)})`; ctx.stroke(g.outer);
        ctx.globalCompositeOperation = 'lighter';
        ctx.globalAlpha = Math.min(1, alpha * 1.25);
        const sprite = this.sprites[t];
        for (const [x, y, core] of g.tips) {
          const size = (core ? 11 : 7) * (0.5 + 0.7 * f);
          ctx.drawImage(sprite, x - size / 2, y - size / 2, size, size);
        }
        ctx.globalAlpha = 1;
        ctx.globalCompositeOperation = 'source-over';
      });
    }
    // glowing core, tinted by the market's average move
    const avg = rows.reduce((sum, q) => sum + (q.changePct ?? 0), 0) / n;
    const coreRgb = RGB[tone(avg)];
    const core = ctx.createRadialGradient(cx, cy, 0, cx, cy, r * 0.2);
    core.addColorStop(0, 'rgba(255,250,235,0.95)');
    core.addColorStop(0.35, `rgba(${coreRgb},0.55)`);
    core.addColorStop(1, `rgba(${coreRgb},0)`);
    ctx.globalCompositeOperation = 'lighter';
    ctx.fillStyle = core; ctx.beginPath(); ctx.arc(cx, cy, r * 0.2, 0, Math.PI * 2); ctx.fill();
    ctx.globalCompositeOperation = 'source-over';
    ctx.fillStyle = '#0b1019'; ctx.font = 'bold 11px JetBrains Mono'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(String(n), cx, cy + 0.5);
    // Floating news tags first (they matter most), then mover labels; every box is placed in the
    // first free vertical slot near its tip so nothing overlaps, and flipped to stay inside the orb.
    const placed = [];
    const free = (x, y, w, h) => !placed.some((b) => x < b.x + b.w && x + w > b.x && y < b.y + b.h && y + h > b.y);
    const slot = (x, y, w, h, steps) => {
      for (const dy of steps) {
        const top = Math.max(o.y + 44, Math.min(o.y + o.h - h - 2, y + dy - h / 2));
        if (free(x, top, w, h)) { placed.push({ x, y: top, w, h }); return top + h / 2; }
      }
      return null;
    };
    const side = (p, w, gap) => {
      let right = p.right;
      if (right && p.x + gap + w > o.x + o.w - 4) right = false;
      else if (!right && p.x - gap - w < o.x + 4) right = true;
      return { right, x: Math.max(o.x + 4, Math.min(o.x + o.w - w - 4, right ? p.x + gap : p.x - gap - w)) };
    };
    ctx.textBaseline = 'middle';
    const tags = this.tags.filter((g) => g.market === o.key && pos.has(g.symbol)).sort((a, b) => b.at - a.at).slice(0, 5);
    tags.forEach((g, i) => {
      const p = pos.get(g.symbol);
      const age = (now - g.at) / TAG_MS;
      const fade = Math.max(0, age > 0.85 ? (1 - age) / 0.15 : 1);
      const word = g.tone === 'up' ? '▲ NAIK' : g.tone === 'down' ? '▼ TURUN' : '◆ NETRAL';
      const title = g.title.length > 46 ? `${g.title.slice(0, 45)}…` : g.title;
      const text = `${word} · ${g.symbol} · ${title}`;
      ctx.font = 'bold 9.5px JetBrains Mono';
      const w = Math.min(ctx.measureText(text).width, o.w * 0.7) + 10;
      const { right, x: x0 } = side(p, w, 26);
      const by = slot(x0, p.y - 18, w, 18, [0, -22, 22, -44, 44, -66, 66, -88]);
      if (by === null) return;
      const y = by + Math.sin(now / 600 + i) * 2;
      ctx.globalAlpha = fade;
      ctx.strokeStyle = COLORS[g.tone]; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(right ? x0 : x0 + w, y); ctx.stroke();
      ctx.fillStyle = 'rgba(12,18,30,.94)'; ctx.fillRect(x0, y - 9, w, 18);
      ctx.strokeRect(x0 + 0.5, y - 8.5, w - 1, 17);
      ctx.fillStyle = COLORS[g.tone]; ctx.textAlign = 'left';
      ctx.save(); ctx.beginPath(); ctx.rect(x0, y - 9, w, 18); ctx.clip();
      ctx.fillText(text, x0 + 5, y + 0.5); ctx.restore();
      ctx.lineWidth = 1.3; ctx.globalAlpha = fade * (0.45 + 0.55 * p.depth);
      ctx.beginPath(); ctx.arc(p.x, p.y, 5 + Math.sin(now / 300) * 1.8, 0, Math.PI * 2); ctx.stroke();
      ctx.globalAlpha = 1;
    });
    // labels for the top movers facing the viewer: open → last  %
    const facing = rows.filter((q) => q.changePct != null && pos.get(q.symbol).depth > 0.45).sort((a, b) => b.changePct - a.changePct);
    const k = r > 150 ? 3 : 2;
    ctx.font = '9.5px JetBrains Mono';
    ctx.textAlign = 'left';
    for (const q of [...facing.slice(0, k), ...facing.slice(-k)]) {
      const p = pos.get(q.symbol);
      const text = `${q.symbol} ${fmtPrice(q.open, q.decimals)}→${fmtPrice(q.last, q.decimals)} ${fmtPct(q.changePct)}`;
      const w = ctx.measureText(text).width + 4;
      const { right, x } = side(p, w, 7);
      const y = slot(x, p.y, w, 13, [0, 14, -14, 28, -28]);
      if (y === null) continue;
      ctx.globalAlpha = Math.min(1, 0.35 + p.depth);
      if (Math.abs(y - p.y) > 3) {
        ctx.strokeStyle = `rgba(${RGB[tone(q.changePct)]},0.5)`; ctx.lineWidth = 0.8;
        ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(right ? x : x + w, y); ctx.stroke();
      }
      ctx.fillStyle = 'rgba(8,12,22,.78)'; ctx.fillRect(x, y - 7, w, 13);
      ctx.fillStyle = COLORS[tone(q.changePct)];
      ctx.fillText(text, x + 2, y + 0.5);
    }
    ctx.globalAlpha = 1;
    ctx.textBaseline = 'alphabetic';
  }

  hover(e) {
    const { x, y, rect } = this.local(e);
    let best = null, bestScore = Infinity;
    for (const t of this.tips) {
      const d = Math.hypot(t.x - x, t.y - y);
      const score = d - t.depth * 4; // overlapping tips: the one in front wins
      if (d < 9 && score < bestScore) { best = t; bestScore = score; }
    }
    this.hovered = best?.q || null;
    this.canvas.style.cursor = best ? 'pointer' : 'grab';
    if (!best) { this.tip.style.display = 'none'; return; }
    const q = best.q;
    this.tip.style.display = 'block';
    this.tip.style.left = `${Math.min(x + 14, rect.width - 240)}px`;
    this.tip.style.top = `${Math.min(y + 14, rect.height - 90)}px`;
    this.tip.innerHTML = `<b>${esc(q.symbol)}</b> <span class="dim">${esc(q.name)}</span><br>
      open <span class="num">${fmtPrice(q.open, q.decimals)}</span> → sekarang <b class="num">${fmtPrice(q.last, q.decimals)}</b><br>
      <span class="${q.changePct > 0 ? 'up' : q.changePct < 0 ? 'down' : ''}">${fmtPct(q.changePct)} vs penutupan kemarin</span> ·
      <span class="${q.openChangePct > 0 ? 'up' : q.openChangePct < 0 ? 'down' : ''}">${fmtPct(q.openChangePct)} dari open</span><br>
      <span class="dim">sesi ${esc(q.session || '')} · klik untuk analisa · seret untuk memutar</span>`;
  }
}
