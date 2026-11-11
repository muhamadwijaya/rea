// SMC chart: TradingView Lightweight Charts (candles + EMA) with a canvas overlay that
// draws structure (BOS/CHoCH), order blocks, FVG, equal highs/lows, liquidity and
// premium/discount zones exactly as returned by the Python engine.

const C = {
  bull: '#2fd28c', bear: '#ff4d5e', neu: '#f2c94c', blue: '#6b9bff', cyan: '#38d3f5', orange: '#ff9f43',
  text: '#c3cbd8', dim: '#56627a', grid: 'rgba(28, 38, 55, .55)', bg: '#0a0e16',
};

const rgba = (hex, a) => {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
};

// Lightweight Charts renders in UTC; shift timestamps so the axis shows local time.
const localTime = (t) => t - new Date(t * 1000).getTimezoneOffset() * 60;

export const DEFAULT_LAYERS = {
  swing: true, internal: true, ob: true, fvg: true, eq: true, zones: true, liquidity: true, ema: true, plan: true, pivots: true,
};

export class SmcChart {
  constructor(host, shell) {
    this.host = host;
    this.shell = shell;
    this.layers = { ...DEFAULT_LAYERS };
    this.data = null;
    this.priceLines = [];
    const LW = window.LightweightCharts;
    if (!LW) throw new Error('Library Lightweight Charts gagal dimuat (cek koneksi internet).');
    this.chart = LW.createChart(host, {
      autoSize: true,
      layout: { background: { type: 'solid', color: C.bg }, textColor: '#8592a8', fontFamily: 'JetBrains Mono, monospace', fontSize: 11 },
      grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
      rightPriceScale: { borderColor: '#1c2637', scaleMargins: { top: 0.08, bottom: 0.08 } },
      timeScale: { borderColor: '#1c2637', timeVisible: true, secondsVisible: false, rightOffset: 18, barSpacing: 7 },
      crosshair: { mode: LW.CrosshairMode.Normal, vertLine: { color: '#3b4a66' }, horzLine: { color: '#3b4a66' } },
    });
    this.candles = this.chart.addCandlestickSeries({
      upColor: C.bull, downColor: C.bear, borderVisible: false, wickUpColor: C.bull, wickDownColor: C.bear,
    });
    const line = { lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false };
    this.ema50 = this.chart.addLineSeries({ ...line, color: rgba(C.blue, 0.85) });
    this.ema200 = this.chart.addLineSeries({ ...line, color: rgba(C.neu, 0.75) });

    this.canvas = document.createElement('canvas');
    this.canvas.className = 'overlay';
    shell.appendChild(this.canvas);
    this.ctx = this.canvas.getContext('2d');

    const redraw = () => this.requestDraw();
    this.chart.timeScale().subscribeVisibleLogicalRangeChange(redraw);
    this.chart.subscribeCrosshairMove(redraw);
    new ResizeObserver(redraw).observe(host);
    for (const ev of ['pointermove', 'wheel', 'pointerup', 'dblclick']) host.addEventListener(ev, redraw, { passive: true });
  }

  setLayers(layers) {
    this.layers = { ...this.layers, ...layers };
    this.ema50.applyOptions({ visible: this.layers.ema });
    this.ema200.applyOptions({ visible: this.layers.ema });
    this.applyPlanLines();
    this.requestDraw();
  }

  setData(data, { keepRange = false } = {}) {
    const range = keepRange ? this.chart.timeScale().getVisibleLogicalRange() : null;
    this.data = data;
    this.decimals = data.instrument.decimals;
    this.index = new Map(data.candles.map((c, i) => [c.t, i]));
    const precision = Math.min(Math.max(this.decimals ?? 2, 0), 8);
    this.candles.applyOptions({ priceFormat: { type: 'price', precision, minMove: 1 / 10 ** precision } });
    this.candles.setData(data.candles.map((c) => ({ time: localTime(c.t), open: c.o, high: c.h, low: c.l, close: c.c })));
    this.ema50.setData((data.ema?.ema50 || []).map((p) => ({ time: localTime(p.t), value: p.v })));
    this.ema200.setData((data.ema?.ema200 || []).map((p) => ({ time: localTime(p.t), value: p.v })));
    this.applyPlanLines();
    if (range) this.chart.timeScale().setVisibleLogicalRange(range);
    else this.chart.timeScale().setVisibleLogicalRange({ from: data.candles.length - 160, to: data.candles.length + 12 });
    this.requestDraw();
  }

  applyPlanLines() {
    for (const pl of this.priceLines) this.candles.removePriceLine(pl);
    this.priceLines = [];
    const plan = this.data?.plan;
    if (!this.layers.plan || !plan || !plan.entry) return;
    const add = (price, color, title, style = 2) => this.priceLines.push(this.candles.createPriceLine({
      price, color, title, lineWidth: 1, lineStyle: style, axisLabelVisible: true,
    }));
    add(plan.entry, C.blue, 'ENTRY');
    add(plan.stop, C.bear, 'SL');
    (plan.targets || []).forEach((tp, i) => add(tp, C.bull, `TP${i + 1}`));
  }

  requestDraw() {
    if (this.pending) return;
    this.pending = requestAnimationFrame(() => { this.pending = null; this.draw(); });
  }

  // --------------------------------------------------------------- drawing
  draw() {
    const { canvas, ctx } = this;
    const w = this.host.clientWidth, h = this.host.clientHeight;
    const dpr = window.devicePixelRatio || 1;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    if (!this.data) return;

    const ts = this.chart.timeScale();
    const paneW = ts.width();
    const paneH = h - (typeof ts.height === 'function' ? ts.height() : 28);
    ctx.save();
    ctx.beginPath();
    ctx.rect(0, 0, paneW, paneH);
    ctx.clip();

    const smc = this.data.smc;
    const X = (t) => {
      const i = this.index.get(t);
      return i === undefined ? null : ts.logicalToCoordinate(i);
    };
    const Y = (p) => this.candles.priceToCoordinate(p);
    const right = paneW;
    ctx.font = '10px "JetBrains Mono", monospace';
    ctx.textBaseline = 'middle';

    if (this.layers.zones && smc.zones) this.drawZones(smc.zones, X, Y, right);
    if (this.layers.ob) for (const ob of smc.orderBlocks) this.drawBox(ob, X, Y, right, 'ob');
    if (this.layers.fvg) for (const g of smc.fvgs) this.drawBox(g, X, Y, right, 'fvg');
    if (this.layers.liquidity) this.drawLiquidity(smc.liquidity, X, Y, right);
    if (this.layers.eq) for (const e of smc.equalLevels) this.drawEqual(e, X, Y);
    for (const b of smc.breaks) {
      if ((b.scope === 'swing' && this.layers.swing) || (b.scope === 'internal' && this.layers.internal)) this.drawBreak(b, X, Y);
    }
    if (this.layers.pivots) for (const p of smc.pivots) this.drawPivot(p, X, Y);
    ctx.restore();
  }

  drawZones(z, X, Y, right) {
    const { ctx } = this;
    const x0 = Math.max(0, Math.min(X(z.topT) ?? 0, X(z.bottomT) ?? 0));
    const yTop = Y(z.top), yBot = Y(z.bottom), yEq = Y(z.equilibrium);
    if ([yTop, yBot, yEq].some((v) => v === null)) return;
    const band = (yBot - yTop) * 0.05;
    ctx.fillStyle = rgba(C.bear, 0.05); ctx.fillRect(x0, yTop, right - x0, yEq - yTop);
    ctx.fillStyle = rgba(C.bull, 0.05); ctx.fillRect(x0, yEq, right - x0, yBot - yEq);
    ctx.fillStyle = rgba(C.bear, 0.16); ctx.fillRect(x0, yTop, right - x0, band);
    ctx.fillStyle = rgba(C.bull, 0.16); ctx.fillRect(x0, yBot - band, right - x0, band);
    ctx.setLineDash([4, 4]); ctx.strokeStyle = rgba('#8592a8', 0.6); ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x0, yEq); ctx.lineTo(right, yEq); ctx.stroke(); ctx.setLineDash([]);
    ctx.textAlign = 'right';
    ctx.fillStyle = rgba(C.bear, 0.9); ctx.fillText(z.strong === 'high' ? 'PREMIUM · STRONG HIGH' : 'PREMIUM · WEAK HIGH', right - 6, yTop + 8);
    ctx.fillStyle = '#8592a8'; ctx.fillText('EQUILIBRIUM', right - 6, yEq - 7);
    ctx.fillStyle = rgba(C.bull, 0.9); ctx.fillText(z.strong === 'low' ? 'DISCOUNT · STRONG LOW' : 'DISCOUNT · WEAK LOW', right - 6, yBot - 8);
  }

  drawBox(o, X, Y, right, kind) {
    const { ctx } = this;
    const bull = o.direction === 'bullish';
    const ended = kind === 'ob' ? o.brokenT : o.filledT;
    const x1 = X(o.t);
    if (x1 === null) return;
    const x2 = ended ? (X(ended) ?? right) : right;
    const y1 = Y(o.top), y2 = Y(o.bottom);
    if (y1 === null || y2 === null || x2 < 0) return;
    const color = kind === 'ob' ? (bull ? C.bull : C.bear) : (bull ? C.cyan : C.orange);
    const internal = o.scope === 'internal';
    const alpha = ended ? 0.05 : kind === 'ob' ? (internal ? 0.09 : 0.16) : 0.12;
    ctx.fillStyle = rgba(color, alpha);
    ctx.fillRect(x1, y1, x2 - x1, y2 - y1);
    ctx.strokeStyle = rgba(color, ended ? 0.25 : 0.6);
    ctx.lineWidth = 1;
    if (ended) ctx.setLineDash([3, 3]);
    ctx.strokeRect(x1 + 0.5, y1 + 0.5, x2 - x1 - 1, y2 - y1 - 1);
    ctx.setLineDash([]);
    if (!ended || x2 - x1 > 40) {
      ctx.textAlign = 'left';
      ctx.fillStyle = rgba(color, ended ? 0.5 : 0.95);
      const label = kind === 'ob' ? `${internal ? 'iOB' : 'OB'} ${bull ? '▲' : '▼'}${o.tappedT && !ended ? ' · tapped' : ''}` : `FVG ${bull ? '▲' : '▼'}${o.fill ? ` ${Math.round(o.fill * 100)}%` : ''}`;
      ctx.fillText(label, x1 + 4, (y1 + y2) / 2);
    }
  }

  drawBreak(b, X, Y) {
    const { ctx } = this;
    const x1 = X(b.fromT), x2 = X(b.toT), y = Y(b.level);
    if (x1 === null || x2 === null || y === null) return;
    const color = b.direction === 'bullish' ? C.bull : C.bear;
    const swing = b.scope === 'swing';
    ctx.strokeStyle = rgba(color, swing ? 0.95 : 0.6);
    ctx.lineWidth = swing ? 1.4 : 1;
    ctx.setLineDash(swing ? [] : [4, 3]);
    ctx.beginPath(); ctx.moveTo(x1, y); ctx.lineTo(x2, y); ctx.stroke();
    ctx.setLineDash([]);
    ctx.textAlign = 'center';
    ctx.fillStyle = rgba(color, swing ? 1 : 0.75);
    ctx.font = swing ? 'bold 10px "JetBrains Mono", monospace' : '9px "JetBrains Mono", monospace';
    const label = swing ? b.kind : `i${b.kind}`;
    ctx.fillText(label, (x1 + x2) / 2, b.direction === 'bullish' ? y - 7 : y + 8);
    ctx.font = '10px "JetBrains Mono", monospace';
  }

  drawPivot(p, X, Y) {
    if (p.scope !== 'swing' || !p.label) return;
    const x = X(p.t), y = Y(p.price);
    if (x === null || y === null) return;
    const { ctx } = this;
    const up = p.label === 'HH' || p.label === 'HL';
    ctx.textAlign = 'center';
    ctx.fillStyle = up ? rgba(C.bull, 0.85) : rgba(C.bear, 0.85);
    ctx.fillText(p.label, x, p.kind === 'high' ? y - 12 : y + 12);
  }

  drawEqual(e, X, Y) {
    const x1 = X(e.fromT), x2 = X(e.toT), y = Y(e.price);
    if (x1 === null || x2 === null || y === null) return;
    const { ctx } = this;
    const color = e.kind === 'EQH' ? C.bear : C.bull;
    ctx.strokeStyle = rgba(color, e.sweptT ? 0.35 : 0.8);
    ctx.setLineDash([2, 3]);
    ctx.beginPath(); ctx.moveTo(x1, y); ctx.lineTo(x2, y); ctx.stroke();
    ctx.setLineDash([]);
    ctx.textAlign = 'center';
    ctx.fillStyle = rgba(color, e.sweptT ? 0.5 : 0.95);
    ctx.fillText(e.sweptT ? `${e.kind} ✕` : e.kind, (x1 + x2) / 2, e.kind === 'EQH' ? y - 8 : y + 9);
  }

  drawLiquidity(liq, X, Y, right) {
    const { ctx } = this;
    const draw = (items, color, tag) => items.slice(0, 2).forEach((l) => {
      const x = X(l.t), y = Y(l.price);
      if (x === null || y === null) return;
      ctx.strokeStyle = rgba(color, 0.45);
      ctx.setLineDash([1, 4]);
      ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(right, y); ctx.stroke();
      ctx.setLineDash([]);
      ctx.textAlign = 'right';
      ctx.fillStyle = rgba(color, 0.85);
      ctx.fillText(`${tag} $$$`, right - 6, y + (tag === 'BSL' ? -7 : 8));
    });
    draw(liq.above || [], C.neu, 'BSL');
    draw(liq.below || [], C.neu, 'SSL');
  }
}
