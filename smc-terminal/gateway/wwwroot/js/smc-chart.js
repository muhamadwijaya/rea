// SMC chart: TradingView Lightweight Charts (candles + EMA) with a canvas overlay that
// draws structure (BOS/CHoCH), order blocks, FVG, equal highs/lows, liquidity and
// premium/discount zones exactly as returned by the Python engine.

const C = {
  bull: '#2fd28c', bear: '#ff4d5e', neu: '#f2c94c', blue: '#6b9bff', cyan: '#38d3f5', orange: '#ff9f43',
  text: '#c9d6e6', dim: '#5d7896', grid: 'rgba(21, 76, 124, .35)', bg: '#001b33',
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
      rightPriceScale: { borderColor: '#14306a', scaleMargins: { top: 0.08, bottom: 0.08 } },
      timeScale: { borderColor: '#14306a', timeVisible: true, secondsVisible: false, rightOffset: 18, barSpacing: 7 },
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
    this.visibleArea = 0;
    new ResizeObserver(() => {
      const area = host.clientWidth * host.clientHeight;
      // Coming back from display:none (TradingView-only view, hidden tab): rebuild the pane.
      if (!this.visibleArea && area && this.data) this.reveal();
      this.visibleArea = area;
      this.requestDraw();
    }).observe(host);
    for (const ev of ['pointermove', 'wheel', 'pointerup', 'dblclick']) host.addEventListener(ev, redraw, { passive: true });
  }

  setLayers(layers) {
    this.layers = { ...this.layers, ...layers };
    this.ema50.applyOptions({ visible: this.layers.ema });
    this.ema200.applyOptions({ visible: this.layers.ema });
    this.applyPlanLines();
    this.requestDraw();
  }

  /**
   * Re-applies the current data. Data set while the chart was hidden (zero size) can leave the
   * pane blank in some WebView2 builds; re-setting it forces a full layout and price-scale fit.
   */
  reveal() {
    if (!this.data || !this.host.clientWidth || !this.host.clientHeight) return;
    this.setData(this.data, { keepRange: !this.setWhileHidden });
  }

  setData(data, { keepRange = false } = {}) {
    this.setWhileHidden = !this.host.clientWidth || !this.host.clientHeight;
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
    this.candles.priceScale().applyOptions({ autoScale: true });
    if (range) this.chart.timeScale().setVisibleLogicalRange(range);
    else this.chart.timeScale().setVisibleLogicalRange({ from: data.candles.length - 160, to: data.candles.length + 12 });
    this.requestDraw();
  }

  /**
   * Merge today's 1-minute bars into the last candle of the shown timeframe (`tfSec` seconds), relative
   * to that candle's own start so the alignment always matches the engine's bars. New periods append
   * a candle. Returns the last candle (UTC seconds) or null.
   */
  live(bars, tfSec) {
    const candles = this.data?.candles;
    if (!candles?.length || !bars?.length) return null;
    let last = candles[candles.length - 1];
    for (const b of bars) {
      if (b.t < last.t) continue;
      if (b.t < last.t + tfSec) {
        last.h = Math.max(last.h, b.h); last.l = Math.min(last.l, b.l); last.c = b.c;
      } else {
        const k = Math.floor((b.t - last.t) / tfSec);
        last = { t: last.t + k * tfSec, o: b.o, h: b.h, l: b.l, c: b.c };
        candles.push(last);
        this.index.set(last.t, candles.length - 1);
      }
      this.candles.update({ time: localTime(last.t), open: last.o, high: last.h, low: last.l, close: last.c });
    }
    return last;
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
      if (this.until != null && b.toT > this.until) continue;  // replay: structure not formed yet
      if ((b.scope === 'swing' && this.layers.swing) || (b.scope === 'internal' && this.layers.internal)) this.drawBreak(b, X, Y);
    }
    if (this.layers.pivots) for (const p of smc.pivots) this.drawPivot(p, X, Y);
    if (this.trades) this.drawTrades(this.trades, Y, right, paneH);
    ctx.restore();
  }

  /** Replay: add the next candle (the chart scrolls with it while the last bar is in view). */
  appendCandle(c) {
    const candles = this.data.candles;
    candles.push(c);
    this.index.set(c.t, candles.length - 1);
    this.candles.update({ time: localTime(c.t), open: c.o, high: c.h, low: c.l, close: c.c });
  }

  /** Virtual-trading trades drawn as position boxes (AI TRAINING live chart); null removes them. */
  setTrades(trades) {
    this.trades = trades;
    this.requestDraw();
  }

  // x of any timestamp: its candle, the candle it falls in (gaps such as weekends), or extrapolated past the last one
  timeX(t) {
    const candles = this.data?.candles;
    if (!candles?.length || t == null) return null;
    const ts = this.chart.timeScale();
    const i = this.index.get(t);
    if (i !== undefined) return ts.logicalToCoordinate(i);
    const n = candles.length;
    if (t > candles[n - 1].t) {
      const step = n > 1 ? Math.max(1, candles[n - 1].t - candles[n - 2].t) : 60;
      return ts.logicalToCoordinate(n - 1 + (t - candles[n - 1].t) / step);
    }
    if (t < candles[0].t) return null;
    let lo = 0, hi = n - 1;
    while (lo < hi) {
      const m = (lo + hi + 1) >> 1;
      if (candles[m].t <= t) lo = m; else hi = m - 1;
    }
    return ts.logicalToCoordinate(lo);
  }

  drawTrades(tr, Y, right, paneH) {
    const { ctx } = this;
    const X = (t) => this.timeX(t);
    const xs = X(tr.split);
    if (xs !== null && xs > 0 && xs < right) {
      ctx.fillStyle = 'rgba(0, 10, 22, .32)';
      ctx.fillRect(0, 0, xs, paneH);
      ctx.strokeStyle = rgba(C.neu, 0.7); ctx.setLineDash([6, 4]); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(xs, 0); ctx.lineTo(xs, paneH); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = rgba(C.neu, 0.95);
      ctx.textAlign = 'left'; ctx.fillText('DATA UJI (OUT-OF-SAMPLE) →', xs + 6, 12);
      ctx.textAlign = 'right'; ctx.fillText('← DATA LATIH', xs - 6, 12);
    }
    const lastT = this.data.candles[this.data.candles.length - 1].t;
    const labels = [];  // result labels already drawn: skip one that would overlap
    const free = (x, y, w) => !labels.some((b) => x < b.x + b.w && b.x < x + w && Math.abs(y - b.y) < 11);
    const ts = this.chart.timeScale();
    const wide = (ts.logicalToCoordinate(1) ?? 0) - (ts.logicalToCoordinate(0) ?? 0) >= 4;  // room for area labels
    for (const s of tr.trades) {
      this.drawArea(s, X, Y, right, lastT, labels, free, wide);  // every BOS / CHoCH: its buy or sell area
      if (s.status === 'cancelled') continue;  // never filled: the area only
      const pending = s.status === 'pending';
      const x1 = X(pending ? s.created_t : s.opened_t);
      let x2 = pending ? X(Math.min(s.expiry_t, lastT)) : s.closed_t ? X(s.closed_t) : X(lastT);
      if (x1 === null || x2 === null || x2 < 0 || x1 > right) continue;
      x2 = Math.max(x2, x1 + 5) + (s.closed_t ? 0 : 6);
      const yE = Y(s.entry), yS = Y(s.stop), yT = Y(s.target);
      if ([yE, yS, yT].some((v) => v === null)) continue;
      const faint = s.sample === 'is' ? 0.45 : 1;
      if (pending) {
        const end = Math.min(right, Math.max(x2, x1 + 60));  // a signal of the last candle still shows its levels
        ctx.setLineDash([5, 4]); ctx.lineWidth = 1.2;
        const y1 = s.tp1 != null ? Y(s.tp1) : null;
        const lines = [[yE, C.blue, `${s.direction > 0 ? 'BUY' : 'SELL'} LIMIT${s.live ? ' · LIVE' : ''}`], [yS, C.bear, 'SL'],
          ...(y1 !== null ? [[y1, C.bull, 'TP1 puncak BOS/CHoCH'], [yT, C.bull, 'TP2']] : [[yT, C.bull, 'TP']])];
        for (const [y, c, tag] of lines) {
          ctx.strokeStyle = rgba(c, 0.9); ctx.beginPath(); ctx.moveTo(x1, y); ctx.lineTo(end, y); ctx.stroke();
          const fits = end + 6 + tag.length * 6.2 < right;
          ctx.textAlign = fits ? 'left' : 'right'; ctx.fillStyle = rgba(c, 1);
          ctx.fillText(tag, fits ? end + 4 : x1 - 4, y);
        }
        ctx.setLineDash([]);
        continue;
      }
      ctx.fillStyle = rgba(C.bull, 0.16 * faint); ctx.fillRect(x1, Math.min(yE, yT), x2 - x1, Math.abs(yT - yE));
      ctx.fillStyle = rgba(C.bear, 0.16 * faint); ctx.fillRect(x1, Math.min(yE, yS), x2 - x1, Math.abs(yS - yE));
      const color = s.status === 'open' ? C.blue : s.r > 0 ? C.bull : s.r < 0 ? C.bear : C.neu;
      const top = Math.min(yE, yS, yT), bottom = Math.max(yE, yS, yT);
      ctx.strokeStyle = s.live ? '#99ff00' : rgba(color, 0.75 * faint);
      ctx.lineWidth = s.live ? 1.6 : 1;
      ctx.strokeRect(x1 + 0.5, top + 0.5, x2 - x1 - 1, bottom - top - 1);
      ctx.strokeStyle = rgba(C.text, 0.55 * faint); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x1, yE); ctx.lineTo(x2, yE); ctx.stroke();
      if (s.tp1 != null) {  // TP1 at the BOS / CHoCH extreme: half closes here, the rest runs to TP2
        const y1 = Y(s.tp1);
        if (y1 !== null) {
          ctx.setLineDash([4, 3]); ctx.strokeStyle = rgba(C.bull, 0.9 * faint);
          ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y1); ctx.stroke(); ctx.setLineDash([]);
          if (x2 - x1 > 34) {
            ctx.textAlign = 'left'; ctx.fillStyle = rgba(C.bull, 0.9 * faint);
            ctx.fillText('TP1', x1 + 3, y1 + (s.direction > 0 ? 6 : -6));
            ctx.fillText('TP2', x1 + 3, yT + (s.direction > 0 ? 6 : -6));
          }
        }
      } else if (s.manage === 'partial') {
        const y1r = Y(s.entry + (s.entry - s.stop));
        if (y1r !== null && Math.abs(yT - yE) > Math.abs(y1r - yE) + 1) {
          ctx.setLineDash([2, 3]); ctx.strokeStyle = rgba(C.bull, 0.8 * faint);
          ctx.beginPath(); ctx.moveTo(x1, y1r); ctx.lineTo(x2, y1r); ctx.stroke(); ctx.setLineDash([]);
        }
      }
      ctx.fillStyle = rgba(s.direction > 0 ? C.bull : C.bear, faint);
      ctx.beginPath();
      if (s.direction > 0) { ctx.moveTo(x1, yE + 1); ctx.lineTo(x1 - 4, yE + 8); ctx.lineTo(x1 + 4, yE + 8); }
      else { ctx.moveTo(x1, yE - 1); ctx.lineTo(x1 - 4, yE - 8); ctx.lineTo(x1 + 4, yE - 8); }
      ctx.fill();
      const label = s.status === 'open' ? 'OPEN' : `${s.r > 0 ? '+' : ''}${(s.r ?? 0).toFixed(2)}R ${s.r > 0 ? '✔' : '✘'}`;
      const text = s.live ? `${label} LIVE` : label;
      const lw = text.length * 6.2, ly = top - 7 < 8 ? bottom + 8 : top - 7;
      const lx = x2 + 3 + lw < right ? x2 + 3 : Math.max(0, x1 - 3 - lw);  // keep clear of the price axis
      if (!s.live && !free(lx, ly, lw)) continue;
      labels.push({ x: lx, y: ly, w: lw });
      ctx.font = 'bold 10px "JetBrains Mono", monospace';
      ctx.textAlign = 'left'; ctx.fillStyle = s.live ? '#99ff00' : rgba(color, faint);
      ctx.fillText(text, lx, ly);
      ctx.font = '10px "JetBrains Mono", monospace';
    }
  }

  /** The buy (green) or sell (red) area of a BOS / CHoCH — its FVG or order block — from the break until the entry
   *  fills or the setup ends, with TP1 / TP2 while it waits. */
  drawArea(s, X, Y, right, lastT, labels, free, wide) {
    if (s.zone_top == null || s.zone_bottom == null) return;
    const { ctx } = this;
    const buy = s.direction > 0, color = buy ? C.bull : C.bear;
    const end = s.opened_t ?? s.closed_t ?? Math.min(s.expiry_t, lastT);
    const x1 = X(s.created_t);
    let x2 = X(end);
    const y1 = Y(s.zone_top), y2 = Y(s.zone_bottom);
    if (x1 === null || x2 === null || y1 === null || y2 === null || x2 < 0 || x1 > right) return;
    x2 = Math.min(right, Math.max(x2, x1 + 6));
    const waiting = !s.opened_t;  // no entry (yet): the targets belong to the area
    const ict = s.ict?.grade === 'A+' || s.ict?.grade === 'A';  // the full ICT model: drawn stronger, always labelled
    ctx.fillStyle = rgba(color, ict ? 0.26 : 0.18); ctx.fillRect(x1, y1, x2 - x1, y2 - y1);
    ctx.strokeStyle = rgba(color, ict ? 1 : 0.7); ctx.lineWidth = ict ? 1.6 : 1;
    ctx.strokeRect(x1 + 0.5, y1 + 0.5, x2 - x1 - 1, y2 - y1 - 1);
    if (waiting && s.status !== 'pending') {  // a past area that never filled: where its targets were
      ctx.setLineDash([3, 3]); ctx.strokeStyle = rgba(C.bull, 0.55);
      for (const v of [s.tp1, s.target]) {
        const y = v == null ? null : Y(v);
        if (y !== null) { ctx.beginPath(); ctx.moveTo(x1, y); ctx.lineTo(x2, y); ctx.stroke(); }
      }
      ctx.setLineDash([]);
    }
    if (!wide && !ict && s.status !== 'pending') return;
    const text = `${buy ? 'BUY AREA' : 'SELL AREA'}${s.ict?.grade ? ` · ICT ${s.ict.grade}` : ''}`;
    const w = text.length * 5.6, y = buy ? Math.max(y1, y2) + 8 : Math.min(y1, y2) - 8;
    if (!free(x1, y, w)) return;
    labels.push({ x: x1, y, w });
    ctx.font = 'bold 9px "JetBrains Mono", monospace';
    ctx.textAlign = 'left'; ctx.fillStyle = rgba(color, 0.95); ctx.fillText(text, x1, y);
    ctx.font = '10px "JetBrains Mono", monospace';
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
