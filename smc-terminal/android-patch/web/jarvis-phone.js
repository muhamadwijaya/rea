// JARVIS on the phone (Android): SHARX's voice assistant, running in the app without the PC.
// The page talks to the app's engine over /api/jarvis/live and plays the 24 kHz replies. On the phone the engine
// records the microphone itself (Android AudioRecord); without that bridge (desktop tests) this page streams the
// WebView microphone as 16 kHz PCM. The engine (app/jarvis_phone.py) talks to Gemini Live with the key kept in the
// Android keystore, runs the tools, and hands PC work to JARVIS on the paired PC.
import { esc, on, toast } from './util.js';
import { extractPage } from './reader.js';

const KEY = /^[A-HJKMNP-Z2-9]{8}$/;
const ZOOMS = [0.75, 1, 1.25, 1.5, 1.75, 2, 2.5, 3];
const STATE_LABEL = {
  idle: 'SIAP', connecting: 'MENYAMBUNG…', reconnecting: 'MENYAMBUNG ULANG…', listening: 'MENDENGARKAN', thinking: 'BERPIKIR',
  speaking: 'BERBICARA', muted: 'MIC MATI', nokey: 'BUTUH API KEY', error: 'GAGAL',
};
const TOOL_LABEL = {
  save_memory: 'menyimpan memori', recall_memory: 'membuka memori', get_datetime: 'cek waktu', weather_report: 'cuaca',
  read_webpage: 'membaca halaman', reminder: 'pengingat', list_reminders: 'daftar pengingat', market_analysis: 'analisa SMC',
  market_quote: 'harga', market_news: 'berita pasar', economic_calendar: 'kalender ekonomi', open_chart: 'buka chart',
  phone_control: 'kontrol HP', open_app: 'buka aplikasi', open_link: 'buka tautan', phone_compose: 'siapkan pesan/panggilan',
  open_map: 'peta', pc_command: 'perintah ke PC', pc_status: 'cek PC', shutdown_jarvis: 'selesai',
  analyze_asset: 'analisa lengkap', look_at_chart: 'melihat chart', read_news: 'membaca berita', open_page: 'buka halaman',
  read_screen: 'membaca layar', market_context: 'arah market & kalender', set_trading_plan: 'rencana JARVIS',
  open_tab: 'buka tab',
  market_overview: 'arah market', screener: 'screener', alerts: 'alert', company_report: 'laporan perusahaan',
  dividends: 'dividen & benefit', training_stats: 'hasil training',
};

let root = null;
let status = null;
let poll = null;
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};
const q = (name) => root.querySelector(`[data-p="${name}"]`);

async function call(path, body) {
  let res;
  try {
    res = await fetch(`/api/jarvis/${path}`, {
      method: body === undefined ? 'GET' : 'POST',
      headers: { Accept: 'application/json', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new Error('Layanan aplikasi tidak dapat dihubungi.');
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error(data?.message || `HTTP ${res.status}`);
  return data;
}

// ------------------------------------------------------------------ audio

class Mic {
  async start(onChunk, echoCancellation) {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation, noiseSuppression: true, autoGainControl: true },
    });
    this.ctx = new AudioContext({ sampleRate: 16000 });
    await this.ctx.audioWorklet.addModule('js/jarvis-worklet.js');
    this.source = this.ctx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(this.ctx, 'jarvis-capture');
    this.node.port.onmessage = (e) => onChunk(e.data.pcm, e.data.level);
    const sink = this.ctx.createGain();
    sink.gain.value = 0;  // the worklet only runs while connected to the output; nothing is heard
    this.source.connect(this.node).connect(sink).connect(this.ctx.destination);
    await this.ctx.resume();
  }

  stop() {
    this.stream?.getTracks().forEach((t) => t.stop());
    this.ctx?.close().catch(() => {});
    this.stream = this.ctx = this.node = this.source = null;
  }
}

class Player {
  constructor() {
    this.ctx = new AudioContext({ sampleRate: 24000 });
    this.rate = 24000;
    this.at = 0;
    this.sources = new Set();
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 512;
    this.analyser.connect(this.ctx.destination);
    this.samples = new Uint8Array(this.analyser.fftSize);
    this.lastEnd = 0;
  }

  play(buffer) {
    const pcm = new Int16Array(buffer);
    if (!pcm.length) return;
    const floats = new Float32Array(pcm.length);
    for (let i = 0; i < pcm.length; i++) floats[i] = pcm[i] / 32768;
    const audio = this.ctx.createBuffer(1, floats.length, this.rate);
    audio.copyToChannel(floats, 0);
    const src = this.ctx.createBufferSource();
    src.buffer = audio;
    src.connect(this.analyser);
    this.at = Math.max(this.at, this.ctx.currentTime + 0.06);
    src.start(this.at);
    this.at += audio.duration;
    const idle = !this.sources.size;
    this.sources.add(src);
    if (idle) this.onPlaying?.(true);
    src.onended = () => {
      this.sources.delete(src);
      if (!this.sources.size) {
        this.lastEnd = performance.now();
        this.onPlaying?.(false);
      }
    };
  }

  flush() {
    const busy = this.sources.size > 0;
    for (const s of this.sources) { try { s.stop(); } catch { /* already ended */ } }
    this.sources.clear();
    this.at = 0;
    this.lastEnd = performance.now();
    if (busy) this.onPlaying?.(false);
  }

  get speaking() { return this.sources.size > 0; }

  /** True while JARVIS talks and briefly after, so the phone speaker's echo is not sent back as the user's voice. */
  get echoWindow() { return this.speaking || performance.now() - this.lastEnd < 450; }

  level() {
    this.analyser.getByteTimeDomainData(this.samples);
    let sum = 0;
    for (const v of this.samples) { const d = (v - 128) / 128; sum += d * d; }
    return Math.sqrt(sum / this.samples.length);
  }

  close() { this.flush(); this.ctx.close().catch(() => {}); }
}

// ------------------------------------------------------------------ conversation

const session = {
  // micOn: this conversation listens; micWanted: the user's MIC choice for conversations they start (a typed command or
  // a request from the PC starts one without the microphone and leaves the choice alone)
  ws: null, mic: null, player: null, state: 'idle', micOn: true, micWanted: true, micLevel: 0, model: null, ending: false,
  queue: [], bubbles: { user: null, model: null }, forPc: false,
  // working: JARVIS is taking a request, running tools or answering; the news assistant waits until it is done
  working: false, quietUntil: 0, gate: false, gateTimer: null,
  turns: 0,  // answers finished in this conversation
  pausedMic: false,  // the microphone was paused because the app went to the background

  get active() { return !!this.ws; },

  busy() { return this.active && (this.working || !!this.player?.speaking || performance.now() < this.quietUntil); },

  markWorking() {
    const was = this.busy();
    this.working = true;
    if (!was) holdNews();
  },

  markDone() {
    this.working = false;
    this.quietUntil = performance.now() + 2500;  // a short pause before the news assistant talks again
  },

  /** The microphone pauses while JARVIS or the news assistant is talking (it would hear them as the user). */
  updateGate() {
    const gate = !!this.player?.speaking || newsSpeaking();
    if (gate !== this.gate) {
      this.gate = gate;
      this.send({ type: 'playing', on: gate });
    }
  },

  setState(state) {
    this.state = state;
    const nav = document.querySelector('.tab-btn[data-tab="jarvis"]');  // JARVIS is on, seen from any tab
    nav?.classList.toggle('jv-live', this.active);
    nav?.classList.toggle('jv-talk', this.active && (state === 'speaking' || state === 'thinking'));
    const label = q('state');
    if (!label) return;
    label.textContent = STATE_LABEL[state] || state.toUpperCase();
    label.className = `jp-state jp-${state}`;
    chip(STATE_LABEL[state] || state.toUpperCase(), { listening: 'on', speaking: 'on', thinking: 'busy', connecting: 'busy',
      reconnecting: 'busy', error: 'off', nokey: 'off', muted: 'warn' }[state] || '');
    q('toggle').textContent = this.active ? '■ SELESAI' : '▶ MULAI';
    q('toggle').classList.toggle('danger', this.active);
    const mic = this.active ? this.micOn : this.micWanted;
    q('mic').textContent = mic ? '🎙 MIC ON' : '🔇 MIC OFF';
  },

  async start({ mic = this.micWanted, greet = false } = {}) {
    if (this.active) return true;
    if (!status?.settings?.configured) {
      this.setState('nokey');
      q('setup').open = true;
      toast('JARVIS', 'Isi Gemini API key dulu (gratis dari aistudio.google.com/apikey).');
      return false;
    }
    this.micOn = mic;
    this.ending = false;
    this.player = new Player();
    // the engine pauses the phone's microphone while JARVIS talks on the speaker (it would hear itself)
    this.player.onPlaying = () => this.updateGate();
    await this.player.ctx.resume().catch(() => {});
    const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/jarvis/live`);
    ws.binaryType = 'arraybuffer';
    this.ws = ws;
    // the news assistant gives way while the user starts talking: a few seconds now, then whenever JARVIS works
    this.working = false;
    this.quietUntil = performance.now() + 8000;
    holdNews();
    this.gate = false;
    clearInterval(this.gateTimer);
    this.gateTimer = setInterval(() => this.updateGate(), 250);
    this.setState('connecting');
    return new Promise((resolve) => {
      let settled = false;
      const settle = (v) => { if (!settled) { settled = true; resolve(v); } };
      ws.onopen = () => ws.send(JSON.stringify({ type: 'start' }));
      ws.onmessage = (e) => {
        if (typeof e.data !== 'string') {
          this.markWorking();
          this.player?.play(e.data);
          if (this.state !== 'speaking') this.setState('speaking');
          return;
        }
        let m;
        try { m = JSON.parse(e.data); } catch { return; }
        if (m.type === 'ready') {
          this.model = m.model;
          q('model').textContent = `${m.model}${m.resumed ? ' · lanjut' : ''}`;
          this.setState(this.micOn ? 'listening' : 'muted');
          if (this.micOn) this.startMic();
          while (this.queue.length) this.send(this.queue.shift());
          if (greet) this.send({ type: 'app_started' });  // JARVIS says in one sentence that it is ready
          settle(true);
        } else this.onMessage(m, settle);
      };
      ws.onclose = () => {
        if (this.ws === ws) this.stop(false);
        settle(false);
      };
    });
  },

  async startMic() {
    if (status?.nativeMic) {
      this.send({ type: 'mic', on: true });  // recorded by the app itself (see the 'mic' answer)
      return;
    }
    if (this.mic) return;
    const mic = new Mic();
    try {
      await mic.start((pcm, level) => this.onMic(pcm, level), !!status?.settings?.duplex);
      this.mic = mic;
    } catch (err) {
      mic.stop();
      this.micOn = false;
      this.setState('muted');
      toast('Mikrofon', `Tidak bisa dipakai (${err.name || err.message}). Perintah ketik tetap berjalan.`);
    }
  },

  onMic(pcm, level) {
    this.micLevel = level;
    if (!this.ws || this.ws.readyState !== 1 || !this.micOn) return;
    // speaker mode: the microphone pauses while JARVIS talks (it would hear itself); headset mode lets you cut in.
    // The news assistant's voice is never sent: JARVIS would answer it.
    if (newsSpeaking() || (!status?.settings?.duplex && this.player?.echoWindow)) return;
    this.ws.send(pcm);
  },

  send(message) {
    if (this.ws?.readyState === 1) this.ws.send(JSON.stringify(message));
    else this.queue.push(message);
  },

  async say(text) {
    const clean = String(text || '').trim();
    if (!clean) return;
    addLine('user', clean, true);
    if (!this.active && !(await this.start({ mic: false }))) return;
    this.markWorking();
    this.send({ type: 'text', text: clean });
    this.setState('thinking');
  },

  /** A request JARVIS on the PC (or SHARX desktop) sent to this phone; the engine returns the spoken answer to the PC.
   *  A conversation started only for it ends again after the answer. */
  async fromPc(text, replyTo) {
    const clean = String(text || '').trim();
    if (!clean) return;
    addLine('pc', clean, true);
    if (!this.active) {
      if (!(await this.start({ mic: false }))) return;
      this.forPc = true;
    }
    this.markWorking();
    this.send({ type: 'text', text: clean, replyTo });
    this.setState('thinking');
  },

  setMic(on) {
    this.micWanted = on;
    this.micOn = on;
    if (on && this.active) this.startMic();
    if (!on && status?.nativeMic) {
      if (this.active) this.send({ type: 'mic', on: false });
    } else if (!on) {
      this.mic?.stop();
      this.mic = null;
      this.send({ type: 'audio_end' });
    }
    if (this.active) this.setState(on ? 'listening' : 'muted');
    else this.setState(this.state);
  },

  /** The app went to the background or came back: the microphone pauses meanwhile (Android gives a background app
   *  no microphone, and nothing is sent to Gemini for nothing); the user's MIC choice stays. */
  background(hidden) {
    if (!this.active) return;
    const talking = ['thinking', 'speaking'].includes(this.state);
    if (hidden && this.micOn) {
      this.pausedMic = true;
      this.micOn = false;
      if (status?.nativeMic) this.send({ type: 'mic', on: false });
      else {
        this.mic?.stop();
        this.mic = null;
        this.send({ type: 'audio_end' });
      }
      if (!talking) this.setState('muted');
    } else if (!hidden && this.pausedMic) {
      this.pausedMic = false;
      this.micOn = true;
      this.startMic();
      if (!talking) this.setState('listening');
    }
  },

  /** Tapping the orb: JARVIS and the news assistant stop talking, so the user can speak. */
  interrupt() {
    try { window.SMC?.voice?.speaker?.stop(); } catch { /* not loaded */ }
    if (!this.player?.speaking) return;
    this.player.flush();
    this.setState(this.micOn ? 'listening' : 'muted');
  },

  onMessage(m, settle) {
    switch (m.type) {
      case 'mic':
        if (m.error) {
          this.micOn = false;
          toast('Mikrofon', `${m.error} Perintah ketik tetap berjalan.`);
          addLine('note', `Mikrofon: ${m.error}`);
        } else this.micOn = !!m.on;
        if (this.active && !['thinking', 'speaking'].includes(this.state)) this.setState(this.micOn ? 'listening' : 'muted');
        else this.setState(this.state);
        break;
      case 'level':
        this.micLevel = Number(m.value) || 0;
        break;
      case 'note':
        addLine('note', m.text);
        break;
      case 'state':
        if (m.state === 'thinking') this.markWorking();
        if (m.state === 'thinking' || m.state === 'connecting' || m.state === 'reconnecting') this.setState(m.state);
        break;
      case 'audio_format':
        if (this.player) this.player.rate = m.rate;
        break;
      case 'transcript':
        this.markWorking();
        streamLine(m.role === 'user' ? 'user' : 'model', m.text);
        break;
      case 'interrupted':
        this.player?.flush();
        endLine('model');
        if (this.active) this.setState(this.micOn ? 'listening' : 'muted');
        break;
      case 'turn_complete':
        this.turns += 1;
        endLine('user');
        endLine('model');
        this.afterSpeech(() => {
          this.markDone();
          if (this.active && this.state !== 'connecting') this.setState(this.micOn ? 'listening' : 'muted');
          if (this.ending || this.forPc) this.stop();
        });
        break;
      case 'tool':
        if (m.phase === 'start') this.markWorking();
        toolLine(m);
        break;
      case 'client_tool':
        this.clientTool(m);
        break;
      case 'screen_dropped':  // the page was not told to JARVIS (setting off, or JARVIS stayed busy): nothing to wait for
        if (this.working && !this.player?.speaking) this.markDone();
        break;
      case 'error':
        this.setState(m.code === 'no_key' ? 'nokey' : 'error');
        addLine('note', m.message);
        toast('JARVIS', m.message);
        settle?.(false);
        this.stop(false, m.code === 'no_key' ? 'nokey' : 'error');
        break;
      case 'closed':
        addLine('note', 'Sambungan JARVIS terputus.');
        this.stop(false, 'error');
        break;
      default:
    }
  },

  afterSpeech(fn) {
    const started = performance.now();
    const check = () => {
      if (!this.player?.speaking || performance.now() - started > 20000) fn();
      else setTimeout(check, 200);
    };
    check();
  },

  async clientTool(m) {
    let result = { result: 'ok' };
    try {
      if (m.name === 'open_chart') {
        const symbol = String(m.args?.symbol || '').toUpperCase();
        markJarvisNav(symbol);
        await window.SMC?.openAnalysis?.(symbol, m.args?.timeframe || '1h', { fromAssistant: true });
        result = { result: 'chart shown on the phone screen', symbol };
      } else if (m.name === 'shutdown_jarvis') {
        this.ending = true;
        result = { result: 'conversation ends after this reply' };
      } else if (m.name === 'open_page') result = await openPage(m.args || {});
      else if (m.name === 'chart_capture') result = await captureChart(m.args || {});
      else if (m.name === 'reader_extract') result = await readerExtract(m.args || {});
      else if (m.name === 'screen_read') result = screenRead(m.args || {});
      else if (m.name === 'tab_open') result = await openTab(m.args || {});
      else result = { result: `unknown client tool ${m.name}` };
    } catch (err) {
      result = { result: 'failed', detail: String(err.message || err) };
    }
    this.send({ type: 'tool_result', id: m.id, result });
  },

  stop(sendStop = true, state = 'idle') {
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      if (sendStop && ws.readyState === 1) ws.send(JSON.stringify({ type: 'stop' }));
      try { ws.close(); } catch { /* closed */ }
    }
    this.mic?.stop();
    this.mic = null;
    this.player?.close();
    this.player = null;
    this.queue = [];
    this.forPc = false;
    this.pausedMic = false;
    this.working = false;
    this.quietUntil = 0;
    clearInterval(this.gateTimer);
    this.gateTimer = null;
    this.gate = false;
    endLine('user');
    endLine('model');
    this.setState(state);
    try { window.SMC?.voice?.tasks?.drain?.(); } catch { /* the news assistant is not loaded */ }
  },
};

// The news assistant asks this before its next job and before speaking (analysis-queue.js, voice-audio.js): it waits
// while JARVIS works or speaks, and while the user is typing in the JARVIS tab (text not sent yet, or a key in the
// last few seconds; an empty field that kept the focus after sending does not count).
const TYPING = 'textarea, input:not([type]), input[type="text"], input[type="password"], input[type="search"]';
let lastKey = 0;
function typing() {
  const el = document.activeElement;
  if (!root?.contains(el) || !el.matches?.(TYPING)) return false;
  return !!String(el.value || '').trim() || performance.now() - lastKey < 6000;
}
window.__sharxJarvis = { busy: () => session.busy() || typing() };

/** The news assistant gives way: its job in progress goes back to its queue and its voice stops. */
function holdNews() {
  try {
    const news = window.SMC?.voice;
    news?.tasks?.hold?.();
    news?.speaker?.stop();
    news?.hideCard?.();
  } catch { /* the news assistant is not loaded */ }
}

/** The news assistant is speaking (Android voice, server voice or browser voice). */
function newsSpeaking() {
  const speaker = window.SMC?.voice?.speaker;
  if (!speaker) return false;
  return !!(speaker.nativeActive || (speaker.audio && !speaker.audio.paused && !speaker.audio.ended)
    || ('speechSynthesis' in window && window.speechSynthesis.speaking));
}

// ------------------------------------------------------------------ the app, for JARVIS's tools

const MARKET_PAGES = ['forex', 'saham-as', 'saham-idx', 'kripto'];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function openPage({ page = 'dashboard', section, symbol, timeframe } = {}) {
  const tab = page === 'metodologi' ? 'metode' : page;
  markJarvisNav(symbol);
  if (symbol && (!section || section === 'analisa')) {
    // fromAssistant: JARVIS gives the analysis, the news assistant does not start its own on this chart
    await window.SMC.openAnalysis(String(symbol).toUpperCase(), timeframe || '1h', { fromAssistant: true });
  } else if (MARKET_PAGES.includes(tab)) {
    window.SMC.go(tab, section || 'analisa');
  } else {
    window.SMC.go(tab);
  }
  await sleep(300);
  return { result: 'shown on the screen', page: decodeURIComponent(location.hash.slice(1)) };
}

let captureSeq = 0;

/** Waits until the analysis page shows `wanted` (or the asset the page resolved it to) with its data loaded. Stops
 * when the analysis fails, the user leaves the page, or `still()` turns false. */
async function analysisReady(wanted, ms, still = () => true) {
  const until = performance.now() + ms;
  let away = 0;
  while (performance.now() < until) {
    if (!still()) return { result: 'replaced by another chart' };
    const panel = document.querySelector('.tab-panel.active .sub-panel[data-sub="analisa"]');
    if (!panel || !/^#[^/]+\/analisa\//.test(location.hash)) {
      if (++away > 8) return { result: 'the chart page was left before the chart had drawn' };
      await sleep(400);
      continue;
    }
    away = 0;
    const failed = panel.querySelector('.an-card .error-box');
    if (failed) return { result: 'the analysis failed', detail: failed.textContent.trim().slice(0, 200) };
    const resolved = decodeURIComponent(location.hash.split('/')[2] || wanted).toUpperCase();
    const shown = (panel.querySelector('.an-head .s')?.textContent || '').toUpperCase();
    if (shown && shown === resolved && !panel.querySelector('.an-card .loading')) return { panel };
    await sleep(400);
  }
  return { result: 'the chart did not finish loading' };
}

/** Opens the symbol's SMC chart, waits until it has drawn, and returns a JPEG of it (candles + SMC overlay). Stops when
 * the analysis fails, the user leaves the chart page, or JARVIS asks for another chart. */
async function captureChart({ symbol, timeframe = '1h', waitMs = 45000 } = {}) {
  const wanted = String(symbol || '').toUpperCase();
  if (!wanted) return { result: 'no symbol' };
  const seq = ++captureSeq;
  markJarvisNav(wanted);
  await window.SMC.openAnalysis(wanted, timeframe, { fromAssistant: true });
  const until = performance.now() + Math.max(1000, Math.min(120000, Number(waitMs) || 45000));
  let panel = null;
  let shell = null;
  while (performance.now() < until) {
    const ready = await analysisReady(wanted, until - performance.now(), () => seq === captureSeq);
    if (!ready.panel) return ready;
    panel = ready.panel;
    const smcView = panel.querySelector('.an-view [data-v="smc"]');
    if (smcView && !smcView.classList.contains('active')) smcView.click();  // TradingView mode cannot be captured
    shell = panel.querySelector('.chart-shell');
    if (shell && shell.offsetWidth > 200) break;
    shell = null;
    await sleep(400);
  }
  if (!shell) return { result: 'the chart did not finish loading' };
  await sleep(900);  // the SMC overlay is drawn just after the candles
  const box = shell.getBoundingClientRect();
  const scale = Math.min(1, 1024 / box.width);
  const out = document.createElement('canvas');
  out.width = Math.round(box.width * scale);
  out.height = Math.round(box.height * scale);
  const g = out.getContext('2d');
  g.fillStyle = '#001b33';
  g.fillRect(0, 0, out.width, out.height);
  for (const c of shell.querySelectorAll('canvas')) {
    const r = c.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    try { g.drawImage(c, (r.left - box.left) * scale, (r.top - box.top) * scale, r.width * scale, r.height * scale); } catch { /* skip */ }
  }
  const symbolShown = (panel.querySelector('.an-head .s')?.textContent || wanted).trim();
  return { image: out.toDataURL('image/jpeg', 0.8).split(',')[1], width: out.width, height: out.height, symbol: symbolShown };
}

// names JARVIS may use for the analysis side tabs
const TAB_ALIAS = { MULTITIMEFRAME: 'mtf', TIMEFRAME: 'mtf', RENCANA: 'plan', TRADINGPLAN: 'plan', SKENARIO: 'plan',
  NARRATIVE: 'narasi', NARASI: 'narasi', LEVELS: 'level', KEYLEVELS: 'level', REPORT: 'laporan', COMPANY: 'laporan',
  TRADINGVIEW: 'tv', NEWS: 'berita', AIOUTLOOK: 'ai', OUTLOOK: 'ai' };
const norm = (v) => String(v || '').toUpperCase().replace(/[^A-Z0-9]/g, '');

/** A tab of the page on screen, clicked as the user would: the analysis side tabs (BIAS … AI), any other panel's
 * tabs, or a market page's sections. With a symbol, that asset's analysis page opens first. Returns what it shows. */
async function openTab({ tab, symbol, timeframe } = {}) {
  const want = norm(tab);
  if (!want) return { result: 'no tab named' };
  if (symbol) {
    const wanted = String(symbol).toUpperCase();
    markJarvisNav(wanted);
    await window.SMC.openAnalysis(wanted, timeframe || '1h', { fromAssistant: true });
    const ready = await analysisReady(wanted, 30000);
    if (!ready.panel) return ready;
  }
  markJarvisNav(symbol);  // the clicks below are JARVIS's, not pages the user opened
  const page = document.querySelector('.tab-panel.active');
  const shownOnPage = (el) => el.offsetParent !== null;  // not inside a hidden section or pane
  const label = (b) => norm([...b.childNodes].filter((n) => n.nodeType === 3).map((n) => n.nodeValue).join('') || b.textContent);
  const alias = TAB_ALIAS[want];
  const find = (list, key) => list.find((b) => norm(b.dataset[key]) === want || (alias && b.dataset[key] === alias))
    || list.find((b) => label(b) === want) || list.find((b) => label(b).startsWith(want));
  const tabs = [...(page?.querySelectorAll('.tabbed > .ptabs button[data-t]') || [])].filter(shownOnPage);
  const button = find(tabs, 't');
  if (button) {
    button.click();
    const pane = button.closest('.tabbed')?.querySelector(`:scope > .pane[data-t="${button.dataset.t}"]`);
    for (let i = 0; i < 20 && pane?.querySelector('.loading'); i++) await sleep(400);  // e.g. LAPORAN loads when opened
    const name = button.textContent.trim().replace(/\s+/g, ' ');
    return { result: `tab ${name} is open on the screen`, tab: name, page: currentScreen(button.closest('.an-tabs') ? button.dataset.t : null).label,
      shows: button.dataset.t === 'tv' ? '(a TradingView widget: not readable)' : shownText(pane, 1800) || '(empty)' };
  }
  const sections = [...(page?.querySelectorAll('.subtabs [data-sub]') || [])];
  const section = find(sections, 'sub');
  if (section) {
    section.click();
    await sleep(600);
    return { result: `section ${section.textContent.trim()} is open on the screen`, page: currentScreen().label,
      note: 'call read_screen to read it' };
  }
  return { result: 'no such tab on this page', page: currentScreen().label,
    tabs: [...tabs, ...sections].map((b) => b.textContent.trim().replace(/\s+/g, ' ')) };
}

/** An article page opened in the app's reader, its text taken, and the page closed again (Android); null elsewhere. */
async function readerExtract({ url, item, timeoutMs = 40000 } = {}) {
  try {
    const page = await extractPage(url, item?.id ? item : null, { timeoutMs: Math.max(3000, Math.min(60000, Number(timeoutMs) || 40000)) });
    if (!page?.text) return { result: 'no text from the page' };
    return { text: String(page.text).slice(0, 24000), title: page.title, finalUrl: page.finalUrl };
  } catch (err) {
    return { result: 'failed', detail: String(err.message || err) };
  }
}

// ------------------------------------------------------------------ the page the user opens

// While a conversation runs, every page or analysis side tab the user opens is told to JARVIS ([SCREEN] notice in
// app/jarvis_phone.py), which reacts to it. Pages JARVIS opened itself are not told back; the same page is told again
// only after a few minutes.
const PAGE_LABEL = { dashboard: 'Dashboard', forex: 'Forex', 'saham-as': 'Saham AS', 'saham-idx': 'Saham IDX', kripto: 'Kripto',
  training: 'AI Training', kalender: 'Kalender ekonomi', metode: 'Metodologi', jarvis: 'JARVIS' };
const SECTION_LABEL = { analisa: 'Analisa SMC', screener: 'Screener', berita: 'Berita', arah: 'Arah market',
  fundamental: 'Fundamental & AI', benefit: 'Dividen & benefit' };
const SCREEN_WAIT_MS = 1500;      // the user settles on a page
const SCREEN_REPEAT_MS = 180000;  // the same page is told again after this
const JARVIS_NAV_MS = 4000;       // page changes right after JARVIS opened a page are its own
const screenFollow = { timer: null, last: '', lastAt: 0, navAt: 0, navSymbol: '', navUntil: 0, installed: false };

/** JARVIS opens a page or chart itself (open_page, open_chart, chart_capture): that is not a page the user opened. */
function markJarvisNav(symbol) {
  screenFollow.navAt = performance.now();
  screenFollow.navSymbol = String(symbol || '').toUpperCase();
  screenFollow.navUntil = performance.now() + 120000;  // its chart may take long to load the first time
  clearTimeout(screenFollow.timer);
}

/** Which page, section, asset and side tab the user sees. */
function currentScreen(sideTab = null) {
  const [page = 'dashboard', section, symbol, tf] = location.hash.replace(/^#/, '').split('/').map((v) => {
    try { return decodeURIComponent(v); } catch { return v; }
  });
  const market = MARKET_PAGES.includes(page);
  const sec = market ? (section || 'analisa') : null;
  const out = { page, section: sec, symbol: null, tf: null, sideTab: null };
  if (sec === 'analisa') {
    const panel = document.querySelector(`#tab-${page} .sub-panel[data-sub="analisa"]`);
    out.symbol = (symbol || panel?.querySelector('.an-head .s')?.textContent || '').trim().toUpperCase() || null;
    out.tf = (tf || '').toLowerCase() || null;
    out.sideTab = sideTab || panel?.querySelector('.an-tabs .ptabs button.active')?.dataset.t || null;
  }
  out.label = [PAGE_LABEL[page] || page, sec && SECTION_LABEL[sec], out.symbol, out.tf?.toUpperCase(),
    out.sideTab && `tab ${out.sideTab.toUpperCase()}`].filter(Boolean).join(' › ');
  return out;
}

function screenChanged(sideTab = null, { symbol = null } = {}) {
  if (!session.active || status?.settings?.followScreen === false) return;
  const now = performance.now();
  if (now - screenFollow.navAt < JARVIS_NAV_MS) return;
  if (symbol && symbol.toUpperCase() === screenFollow.navSymbol && now < screenFollow.navUntil) return;
  clearTimeout(screenFollow.timer);
  const fire = (tries = 0) => {
    const s = currentScreen(sideTab);
    if (s.page === 'jarvis' || !session.active) return;
    if (s.section === 'analisa' && tries < 40) {  // an analysis still loading: JARVIS reads it once it is there
      const panel = document.querySelector(`#tab-${s.page} .sub-panel[data-sub="analisa"]`);
      if (!panel?.querySelector('.an-head') || panel.querySelector('.an-card .loading')) {
        screenFollow.timer = setTimeout(() => fire(tries + 1), 750);
        return;
      }
    }
    if (s.label === screenFollow.last && performance.now() - screenFollow.lastAt < SCREEN_REPEAT_MS) return;
    screenFollow.last = s.label;
    screenFollow.lastAt = performance.now();
    session.markWorking();  // the news assistant gives way: JARVIS is about to talk about this page
    const turns = session.turns;
    setTimeout(() => {  // JARVIS never took it up (still busy elsewhere): release the news assistant
      if (session.turns === turns && session.working && !session.player?.speaking) session.markDone();
    }, 50000);
    addLine('note', `Dibuka: ${s.label}`);
    session.send({ type: 'screen', label: s.label, page: s.page, section: s.section, symbol: s.symbol, tf: s.tf, sideTab: s.sideTab });
  };
  screenFollow.timer = setTimeout(fire, SCREEN_WAIT_MS);
}

function followScreen() {
  if (screenFollow.installed) return;
  screenFollow.installed = true;
  window.addEventListener('hashchange', () => screenChanged());
  // an asset picked in the watchlist or search: the analysis view shows it without a new page address
  on('analysis-shown', (evt) => screenChanged(null, { symbol: evt?.symbol }));
  document.addEventListener('click', (e) => {  // a side tab of the analysis (BIAS, MTF, PLAN, ...)
    const tab = e.target.closest?.('.an-tabs .ptabs button[data-t]');
    if (tab && !root?.contains(tab)) screenChanged(tab.dataset.t);
  }, true);
}

const PANE_LIMIT = { bias: 900, mtf: 600, plan: 900, narasi: 1000, level: 700, laporan: 1000, berita: 900, ai: 1000 };
const BLOCK = /^(DIV|P|LI|UL|OL|H[1-6]|TR|TABLE|THEAD|TBODY|SECTION|ARTICLE|HEADER|FOOTER|BR|HR|DETAILS|SUMMARY|DL|DT|DD)$/;
const SKIP = /^(SCRIPT|STYLE|SVG|CANVAS|IFRAME|BUTTON|INPUT|SELECT|TEXTAREA|TEMPLATE|NOSCRIPT)$/;

/** The text an element shows, one line per block, also for side tabs that are hidden behind another tab. */
function shownText(el, limit) {
  if (!el) return '';
  let out = '';
  const walk = (n) => {
    if (out.length > limit * 2) return;
    if (n.nodeType === 3) { out += n.nodeValue; return; }
    if (n.nodeType !== 1 || SKIP.test(n.tagName) || n.hasAttribute('data-jv-skip')) return;
    const block = BLOCK.test(n.tagName);
    out += block ? '\n' : ' ';
    for (const c of n.childNodes) walk(c);
    out += block ? '\n' : ' ';
  };
  walk(el);
  return out.replace(/[ \t ]+/g, ' ').replace(/ ?\n ?/g, '\n').split('\n')
    .filter((line) => line && line !== 'Buka sumber ↗').join('\n').slice(0, limit);
}

/** What read_screen gives JARVIS: on an analysis page each side tab's text, elsewhere the open page's text. */
function screenRead({ part = 'all' } = {}) {
  const s = currentScreen();
  const route = decodeURIComponent(location.hash.slice(1)) || 'dashboard';
  if (s.section === 'analisa') {
    const panel = document.querySelector(`#tab-${s.page} .sub-panel[data-sub="analisa"]`);
    if (!panel) return { title: s.label, route, text: '(the analysis is not shown yet)' };
    const panes = {};
    for (const pane of panel.querySelectorAll('.an-tabs .pane[data-t]')) {
      const k = pane.dataset.t;
      if (k === 'tv' || (part === 'tab' && k !== s.sideTab)) continue;  // TV: TradingView's own frame, not readable
      panes[k] = shownText(pane, PANE_LIMIT[k] || 800) || '(empty)';
    }
    return { title: s.label, route, symbol: s.symbol, tf: s.tf, sideTab: s.sideTab, panes,
      header: shownText(panel.querySelector('.an-card'), 300), loading: !!panel.querySelector('.an-card .loading') };
  }
  const tab = document.querySelector('.tab-panel.active');
  const area = s.section ? tab?.querySelector(`.sub-panel[data-sub="${s.section}"]`) : tab;
  return { title: s.label, route, text: shownText(area, 3500) || '(nothing shown yet)' };
}

// ------------------------------------------------------------------ conversation log

function scrollLog() {
  const log = q('log');
  if (log) log.scrollTop = log.scrollHeight;
}

function addLine(role, text, final = false) {
  const log = q('log');
  if (!log) return null;
  log.querySelector('.jp-empty')?.remove();
  const el = document.createElement('div');
  el.className = `jp-msg jp-${role}`;
  if (role === 'model') el.innerHTML = `<b>${esc(status?.settings?.assistantName || 'JARVIS')}</b><span></span>`;
  else if (role === 'user') el.innerHTML = '<b>ANDA</b><span></span>';
  else if (role === 'pc') el.innerHTML = '<b>DARI PC</b><span></span>';
  else el.innerHTML = '<span></span>';
  el.querySelector('span').textContent = text;
  log.append(el);
  while (log.children.length > 200) log.firstChild.remove();
  if (!final && (role === 'user' || role === 'model')) session.bubbles[role] = el;
  scrollLog();
  return el;
}

function streamLine(role, text) {
  if (!text) return;
  const el = session.bubbles[role] || addLine(role, '');
  const span = el.querySelector('span');
  span.textContent = (span.textContent + text).replace(/\s+/g, ' ').trimStart();
  scrollLog();
}

function endLine(role) { session.bubbles[role] = null; }

function toolLine(m) {
  const label = TOOL_LABEL[m.name] || m.name;
  if (m.phase === 'start') {
    const arg = (m.args?.tab && [m.args.tab, m.args.symbol].filter(Boolean).join(' · ')) || m.args?.symbol || m.args?.city || m.args?.name || m.args?.command || m.args?.url || m.args?.query
      || m.args?.action || m.args?.news_id || [m.args?.page, m.args?.section].filter(Boolean).join(' › ') || m.args?.market || '';
    const el = addLine('tool', `⚙ ${label}${arg ? ` · ${String(arg).slice(0, 80)}` : ''}`, true);
    if (el) el.dataset.id = m.id;
  } else {
    const el = q('log')?.querySelector(`.jp-tool[data-id="${CSS.escape(String(m.id))}"]`);
    if (el) {
      el.classList.add(m.ok ? 'ok' : 'fail');
      el.querySelector('span').textContent += ` · ${m.ok ? '✓' : '✗'} ${(m.ms / 1000).toFixed(1)} dtk`;
    }
    if (m.name === 'save_memory' || m.name === 'reminder') refresh();
  }
}

// ------------------------------------------------------------------ orb

function drawOrb() {
  const canvas = q('orb');
  if (!canvas || !root.isConnected) return;
  const visible = root.classList.contains('active');
  if (visible) {
    const ctx = canvas.getContext('2d');
    const w = canvas.width, h = canvas.height, t = performance.now() / 1000;
    const level = session.state === 'speaking' ? (session.player?.level() || 0) * 3
      : session.state === 'listening' ? session.micLevel * 4 : 0;
    const color = { listening: [153, 255, 0], speaking: [56, 211, 245], thinking: [242, 201, 76], connecting: [242, 201, 76],
      reconnecting: [242, 201, 76], error: [255, 77, 94], nokey: [255, 77, 94] }[session.state] || [120, 150, 190];
    ctx.clearRect(0, 0, w, h);
    const base = w * 0.24 * (1 + Math.min(0.35, level) + 0.03 * Math.sin(t * 2));
    for (let i = 4; i >= 0; i--) {
      const r = base + i * w * 0.045 + 4 * Math.sin(t * (1.3 + i * 0.4) + i);
      ctx.beginPath();
      ctx.arc(w / 2, h / 2, r, 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(${color.join(',')},${0.12 + (4 - i) * 0.12})`;
      ctx.lineWidth = i === 0 ? 3 : 1.5;
      ctx.stroke();
    }
    const glow = ctx.createRadialGradient(w / 2, h / 2, 0, w / 2, h / 2, base);
    glow.addColorStop(0, `rgba(${color.join(',')},0.55)`);
    glow.addColorStop(1, `rgba(${color.join(',')},0)`);
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(w / 2, h / 2, base, 0, Math.PI * 2);
    ctx.fill();
  }
  requestAnimationFrame(drawOrb);
}

// ------------------------------------------------------------------ layout

function chip(text, cls) {
  const c = q('chip');
  if (!c) return;
  c.textContent = text;
  c.className = `jv-chip ${cls || ''}`;
}

function build() {
  root.innerHTML = `
    <div class="jv jp ${store.get('sharx.jarvis.side') === 'hidden' ? 'jv-collapsed' : ''}">
      <div class="panel jv-side">
        <div class="panel-head"><span class="diamond">◆</span> JARVIS<span class="right"><span class="jv-chip" data-p="chip">SIAP</span></span></div>
        <div class="jv-body">
          <div class="jp-core">
            <canvas class="jp-orb" data-p="orb" width="320" height="320" title="Ketuk untuk memotong JARVIS"></canvas>
            <div class="jp-state" data-p="state">SIAP</div>
            <div class="jp-controls">
              <button class="btn-primary" data-p="toggle">▶ MULAI</button>
              <button class="btn" data-p="mic">🎙 MIC ON</button>
            </div>
          </div>
          <details class="jp-sec" data-p="setup"><summary>PENGATURAN</summary>
            <form class="jv-form" data-p="keyform" autocomplete="off">
              <label>GEMINI API KEY <input data-p="key" type="password" placeholder="AIza…" spellcheck="false"></label>
              <button class="btn-primary" type="submit">SIMPAN</button>
            </form>
            <div class="jp-keyline" data-p="keyline"></div>
            <div class="jp-grid">
              <label>NAMA ASISTEN <input data-p="aname" maxlength="40"></label>
              <label>PANGGIL SAYA <input data-p="uname" maxlength="40" placeholder="(opsional)"></label>
              <label>SUARA <select data-p="voice"></select></label>
            </div>
            <label class="jp-check"><input type="checkbox" data-p="duplex"> Mode headset: bisa memotong JARVIS dengan suara
              (di speaker HP, mikrofon jeda saat JARVIS bicara)</label>
            <label class="jp-check"><input type="checkbox" data-p="gsearch"> Pencarian Google untuk info terbaru</label>
            <label class="jp-check"><input type="checkbox" data-p="follow"> Tanggapi halaman yang dibuka: JARVIS langsung
              menganalisa tab yang Anda buka selama percakapan berjalan</label>
            <label class="jp-check"><input type="checkbox" data-p="autostart"> Nyalakan JARVIS otomatis setiap aplikasi dibuka
              (memakai kuota Gemini selama menyala; mikrofon jeda saat aplikasi di latar belakang)</label>
            <div class="jp-row"><button class="btn" data-p="saveset">SIMPAN PENGATURAN</button><button class="btn jv-stop" data-p="clearkey">HAPUS KEY</button></div>
          </details>
          <details class="jp-sec" data-p="pcsec"><summary>KONTROL PC <span class="jp-pcchip" data-p="pcchip"></span></summary>
            <form class="jp-grid" data-p="pcform" autocomplete="off">
              <label>ALAMAT PC <input data-p="pcaddr" inputmode="url" placeholder="192.168.1.5:8000" spellcheck="false"></label>
              <label>KODE REMOTE CONTROL <input data-p="pckey" maxlength="12" placeholder="8 karakter dari HUD PC" spellcheck="false" autocapitalize="characters"></label>
              <button class="btn-primary" type="submit">PASANGKAN</button>
            </form>
            <div class="jp-pcinfo" data-p="pcinfo"></div>
            <div class="jp-row"><button class="btn" data-p="pccheck">CEK PC</button><button class="btn jv-stop" data-p="pcunpair">PUTUSKAN</button></div>
            <label class="jp-check"><input type="checkbox" data-p="allowpc"> Izinkan PC mengontrol HP ini (jaringan lokal, port 8010)</label>
            <div class="jp-pcinfo" data-p="lan"></div>
            <div class="jp-row" data-p="lanrow"><button class="btn" data-p="lankey">BUAT KODE BARU</button><button class="btn jv-stop" data-p="lanforget">LUPAKAN SEMUA PC</button></div>
          </details>
          <details class="jp-sec"><summary>MEMORI</summary><ul class="jp-list" data-p="memory"></ul></details>
          <details class="jp-sec"><summary>PENGINGAT</summary><ul class="jp-list" data-p="reminders"></ul></details>
          <details class="jp-sec"><summary>CARA PAKAI</summary>
            <ol class="jv-steps">
              <li>Isi <b>Gemini API key</b> gratis dari <a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener">aistudio.google.com/apikey</a>
                Key disimpan terenkripsi di HP dan tidak dikirim ke halaman.</li>
              <li>JARVIS <b>menyala sendiri</b> setiap aplikasi dibuka (setelah key diisi), lalu langsung mendengarkan. Titik
                hijau di tombol JARVIS berarti JARVIS menyala. <b>■ SELESAI</b> mematikannya; <b>▶ MULAI</b> menyalakan lagi.
                Bicara langsung, atau ketik perintah di bawah percakapan. Ketuk orb untuk memotong JARVIS.</li>
              <li>"Buka tab MTF", "pindah ke NARASI", "lihat LAPORAN BBCA": JARVIS membuka tab itu dan menjelaskan isinya.</li>
              <li><b>"Analisa EURUSD"</b>: JARVIS membuka chart, melihat gambarnya, mengambil data SMC, membaca berita terbaru
                (halaman berita terbuka dan tertutup sendiri), mengecek kalender ekonomi, lalu menyampaikan analisanya.</li>
              <li>JARVIS bisa membuka halaman mana pun di SHARX ("buka screener forex", "buka kalender"), membacakan berita,
                dan memakai arah market, screener, alert, laporan perusahaan, dividen, hasil training, harga, cuaca,
                pengingat, pencarian Google, volume/senter/baterai, aplikasi, tautan dan peta. Panggilan, SMS, WhatsApp dan
                email hanya disiapkan; Anda yang menekan kirim.</li>
              <li><b>Kontrol PC</b> (opsional): jalankan JARVIS di PC (SHARX desktop → tab JARVIS), isi alamat PC dan kode
                <b>Remote Control</b> dari HUD PC → PASANGKAN. Lalu minta, mis. "suruh PC buka Chrome": JARVIS HP meneruskan
                ke PC dan membacakan jawabannya. Tanpa PC, JARVIS HP tetap jalan.</li>
              <li><b>PC mengontrol HP</b> (opsional): centang "Izinkan PC mengontrol HP ini". Di SHARX desktop → tab JARVIS →
                KONTROL HP, isi alamat HP dan kode yang tampil di sini. Setelah itu JARVIS di PC bisa menyuruh HP (mis.
                "suruh HP pasang pengingat jam 8"), selama aplikasi SHARX terbuka di HP.</li>
              <li><b>Ikut layar:</b> selama percakapan berjalan, buka tab apa pun. Di halaman analisa aset, JARVIS membaca
                semua tab samping (BIAS, MTF, PLAN, NARASI, LEVEL, LAPORAN, BERITA, AI), mencocokkannya dengan arah market
                dan kalender ekonomi, lalu memberi rencananya sendiri. Rencana itu tampil di tab PLAN sebagai
                <b>RENCANA JARVIS</b>, di atas skenario aplikasi. Di halaman lain JARVIS menyebut yang menonjol dan
                bertanya aset mana yang mau dianalisa. Bisa dimatikan di PENGATURAN.</li>
              <li>Selama JARVIS bekerja atau bicara, asisten berita berhenti dulu. Setelah JARVIS selesai, suara berita
                jalan lagi. Ketuk orb untuk menghentikan suara JARVIS dan suara berita.</li>
            </ol>
          </details>
        </div>
      </div>
      <div class="panel jv-main">
        <div class="panel-head"><button class="jv-toggle" data-p="side" title="Tampilkan / sembunyikan pengaturan">☰</button>
          <div class="seg jp-views"><button data-view="chat" class="active">PERCAKAPAN</button><button data-view="pc">PANEL PC</button></div>
          <span class="sub" data-p="model"></span>
          <span class="right"><span class="jv-zoom" data-p="zoombox" hidden><button data-p="zoom-out">A−</button><b data-p="zoom">200%</b><button data-p="zoom-in">A+</button></span>
            <button class="btn" data-p="reload" hidden>⟳ MUAT ULANG</button></span></div>
        <div class="jp-chat" data-pane="chat">
          <div class="jp-log" data-p="log"><div class="jp-empty">Percakapan dengan JARVIS tampil di sini.</div></div>
          <form class="jp-say" data-p="say" autocomplete="off">
            <input data-p="text" placeholder="Ketik perintah untuk JARVIS… (mis. analisa XAUUSD 1 jam)" maxlength="2000">
            <button class="btn-primary" type="submit">KIRIM</button>
          </form>
        </div>
        <div class="jv-stage" data-pane="pc" hidden>
          <iframe data-p="frame" title="Panel JARVIS di PC" referrerpolicy="no-referrer"
            sandbox="allow-scripts allow-same-origin allow-forms allow-modals allow-downloads allow-popups allow-popups-to-escape-sandbox"></iframe>
          <div class="jv-empty" data-p="pcempty"></div>
        </div>
      </div>
    </div>`;

  q('toggle').addEventListener('click', () => (session.active ? session.stop() : session.start()));
  q('mic').addEventListener('click', () => session.setMic(!(session.active ? session.micOn : session.micWanted)));
  q('orb').addEventListener('click', () => session.interrupt());
  q('say').addEventListener('submit', (e) => {
    e.preventDefault();
    const text = q('text').value;
    q('text').value = '';
    session.say(text);
  });
  q('keyform').addEventListener('submit', async (e) => {
    e.preventDefault();
    const apiKey = q('key').value.trim();
    if (!apiKey) return;
    try {
      status = await call('settings', { apiKey });
      q('key').value = '';
      render();
      toast('JARVIS', 'Gemini API key tersimpan.');
    } catch (err) { toast('JARVIS', err.message); }
  });
  q('clearkey').addEventListener('click', async () => {
    try { status = await call('settings', { clearKey: true }); render(); toast('JARVIS', 'Gemini API key dihapus.'); } catch (err) { toast('JARVIS', err.message); }
  });
  q('saveset').addEventListener('click', async () => {
    try {
      status = await call('settings', {
        assistantName: q('aname').value, userName: q('uname').value, voice: q('voice').value,
        duplex: q('duplex').checked, googleSearch: q('gsearch').checked, followScreen: q('follow').checked,
        autoStart: q('autostart').checked,
      });
      render();
      toast('JARVIS', session.active ? 'Tersimpan. Berlaku di percakapan berikutnya (SELESAI lalu MULAI).' : 'Pengaturan tersimpan.');
    } catch (err) { toast('JARVIS', err.message); }
  });
  q('pcform').addEventListener('submit', async (e) => {
    e.preventDefault();
    const key = q('pckey').value.trim().toUpperCase().replace(/[\s-]/g, '');
    if (!KEY.test(key)) return toast('Kode tidak valid', 'Kode Remote Control terdiri dari 8 huruf/angka (tanpa O, I, L, 0, 1).');
    q('pcinfo').textContent = 'Memasangkan…';
    try {
      await call('pc/pair', { address: q('pcaddr').value, key });
      q('pckey').value = '';
      toast('JARVIS', 'PC terpasang. JARVIS HP bisa meneruskan perintah ke PC.');
    } catch (err) { toast('Kontrol PC', err.message); }
    refresh();
  });
  q('pccheck').addEventListener('click', async () => {
    try { status.pc = await call('pc'); renderPc(); } catch (err) { toast('Kontrol PC', err.message); }
  });
  q('pcunpair').addEventListener('click', async () => {
    try { await call('pc/unpair', {}); } catch (err) { toast('Kontrol PC', err.message); }
    refresh();
  });
  q('allowpc').addEventListener('change', async (e) => {
    try { status.lan = await call('lan', { action: e.target.checked ? 'enable' : 'disable' }); } catch (err) { toast('Kontrol PC', err.message); }
    renderLan();
  });
  q('lankey').addEventListener('click', async () => {
    try { status.lan = await call('lan', { action: 'key' }); } catch (err) { toast('Kontrol PC', err.message); }
    renderLan();
  });
  q('lanforget').addEventListener('click', async () => {
    try { status.lan = await call('lan', { action: 'forget' }); toast('Kontrol PC', 'Semua PC dilupakan; pasangkan lagi bila perlu.'); } catch (err) { toast('Kontrol PC', err.message); }
    renderLan();
  });
  q('side').addEventListener('click', () => {
    const box = root.querySelector('.jv');
    box.classList.toggle('jv-collapsed');
    store.set('sharx.jarvis.side', box.classList.contains('jv-collapsed') ? 'hidden' : 'shown');
  });
  root.querySelectorAll('.jp-views button').forEach((b) => b.addEventListener('click', () => view(b.dataset.view)));
  q('zoom-out').addEventListener('click', () => zoom(-1));
  q('zoom-in').addEventListener('click', () => zoom(1));
  q('reload').addEventListener('click', () => showPcPanel(true));
  root.addEventListener('click', async (e) => {
    const forget = e.target.closest('[data-forget]');
    if (forget) {
      const [category, key] = JSON.parse(forget.dataset.forget);
      try { renderMemory((await call('memory', { action: 'forget', category, key })).entries); } catch (err) { toast('Memori', err.message); }
    }
    const cancel = e.target.closest('[data-cancel]');
    if (cancel) {
      try { status.reminders = (await call('reminders', { action: 'cancel', id: cancel.dataset.cancel })).reminders; renderReminders(); } catch (err) { toast('Pengingat', err.message); }
    }
  });
  on('jarvis', (evt) => {
    if (evt?.type === 'reminder') {
      toast('Pengingat JARVIS', evt.message);
      addLine('note', `⏰ ${evt.message}`);
      refresh();
    } else if (evt?.type === 'pc_command') {
      session.fromPc(evt.text, evt.replyTo);
    } else if (evt?.type === 'pc_paired') {
      toast('JARVIS', 'Sebuah PC terpasang dan bisa mengontrol HP ini.');
      refresh();
    }
  });
  zoom();
  requestAnimationFrame(drawOrb);
}

function view(name) {
  root.querySelectorAll('.jp-views button').forEach((b) => b.classList.toggle('active', b.dataset.view === name));
  root.querySelectorAll('[data-pane]').forEach((p) => { p.hidden = p.dataset.pane !== name; });
  q('zoombox').hidden = name !== 'pc';
  q('reload').hidden = name !== 'pc';
  if (name === 'pc') showPcPanel(false);
}

function zoom(step = 0) {
  const saved = Number(store.get('sharx.jarvis.zoom.remote'));
  let z = ZOOMS.includes(saved) ? saved : 2;
  z = ZOOMS[Math.max(0, Math.min(ZOOMS.length - 1, ZOOMS.indexOf(z) + step))];
  if (step) store.set('sharx.jarvis.zoom.remote', String(z));
  root.querySelector('.jv-stage').style.setProperty('--jv-zoom', String(z));
  q('zoom').textContent = `${Math.round(z * 100)}%`;
}

/** The PC's own JARVIS panel (Mark LVII's dashboard); it signs in with its own Remote Control key (single-use keys). */
async function showPcPanel(force) {
  const frame = q('frame');
  const stage = root.querySelector('.jv-stage');
  const pc = status?.pc;
  if (!pc?.address) {
    frame.src = 'about:blank';
    stage.classList.remove('jv-live');
    q('pcempty').innerHTML = '<b>PC belum diatur.</b><br>Buka KONTROL PC di kiri: isi alamat PC dan kode Remote Control.';
    return;
  }
  let probe;
  try { probe = await call('probe', { address: pc.address }); } catch (err) { probe = { state: 'offline', detail: err.message }; }
  if (probe.state === 'online') {
    if (force || frame.dataset.src !== probe.url) {
      frame.dataset.src = probe.url;
      frame.src = probe.url;
    }
    stage.classList.add('jv-live');
  } else {
    frame.src = 'about:blank';
    frame.dataset.src = '';
    stage.classList.remove('jv-live');
    q('pcempty').innerHTML = `<b>${esc(probe.detail || 'PC tidak menjawab.')}</b><br>Panel PC butuh JARVIS berjalan di PC, satu Wi-Fi.
      Panel ini meminta kode Remote Control tersendiri (kode hanya sekali pakai).`;
  }
}

// ------------------------------------------------------------------ status

function render() {
  if (!status) return;
  const s = status.settings || {};
  q('keyline').textContent = s.configured ? `Key tersimpan${s.encrypted ? ' (terenkripsi Android Keystore)' : ''}.` : 'Belum ada key.';
  q('aname').value = s.assistantName || 'JARVIS';
  q('uname').value = s.userName || '';
  q('voice').innerHTML = (status.voices || []).map((v) => `<option ${v === s.voice ? 'selected' : ''}>${esc(v)}</option>`).join('');
  q('duplex').checked = !!s.duplex;
  q('gsearch').checked = s.googleSearch !== false;
  q('follow').checked = s.followScreen !== false;
  q('autostart').checked = s.autoStart !== false;
  if (!s.configured) {
    q('setup').open = true;
    if (!session.active) session.setState('nokey');
  } else if (!session.active && session.state === 'nokey') session.setState('idle');
  renderPc();
  renderLan();
  renderReminders();
}

function renderLan() {
  const lan = status?.lan || {};
  q('allowpc').checked = !!lan.enabled;
  q('lanrow').hidden = !lan.running;
  q('lan').innerHTML = !lan.enabled
    ? 'PC tidak bisa mengirim perintah ke HP ini.'
    : lan.error
      ? `<span class="down">${esc(lan.error)}</span>`
      : `Alamat HP untuk PC: <b>${esc(lan.address || '(tidak ada Wi-Fi)')}</b>${lan.key ? ` · kode <b>${esc(lan.key)}</b> (${lan.keyMinutes} menit)` : ' · buat kode baru untuk memasangkan PC'}
         · PC terpasang: ${lan.paired || 0}`;
}

function renderPc() {
  const pc = status?.pc || {};
  q('pcchip').textContent = pc.paired ? (pc.online === false ? '· TIDAK TERSAMBUNG' : '· TERPASANG') : '';
  q('pcchip').className = `jp-pcchip ${pc.paired ? (pc.online === false ? 'off' : 'on') : ''}`;
  if (pc.address && !q('pcaddr').value) q('pcaddr').value = pc.address;
  q('pcinfo').innerHTML = pc.paired
    ? `PC <b>${esc(pc.address)}</b> terpasang${pc.online === false ? ` · <span class="down">${esc(pc.lastError || 'tidak menjawab')}</span>` : ''}.
       Perintah PC lewat suara atau ketik ("suruh PC …").`
    : 'Belum terpasang. Tanpa PC, JARVIS HP tetap jalan.';
  q('pcunpair').hidden = !pc.paired;
}

function renderReminders() {
  const list = status?.reminders || [];
  q('reminders').innerHTML = list.length
    ? list.map((r) => `<li><span>${esc(r.local)} · ${esc(r.message)}</span><button class="jp-x" data-cancel="${esc(r.id)}" title="Batalkan">✕</button></li>`).join('')
    : '<li class="muted">Tidak ada pengingat.</li>';
}

function renderMemory(entries) {
  q('memory').innerHTML = (entries || []).length
    ? entries.map((m) => `<li><span><b>${esc(m.category)}</b> · ${esc(m.key)}: ${esc(m.value)}</span>
        <button class="jp-x" data-forget="${esc(JSON.stringify([m.category, m.key]))}" title="Lupakan">✕</button></li>`).join('')
    : '<li class="muted">Belum ada yang diingat.</li>';
}

async function refresh() {
  try {
    status = await call('status');
    render();
    renderMemory((await call('memory')).entries);
  } catch (err) {
    chip('ERROR', 'off');
    toast('JARVIS', err.message);
  }
}

export function mountPhone(element, first) {
  root = element;
  status = first;
  root.addEventListener('input', () => { lastKey = performance.now(); });
  followScreen();
  document.addEventListener('visibilitychange', () => session.background(document.hidden));
  build();
  render();
  session.setState(status?.settings?.configured ? 'idle' : 'nokey');
  refresh();
}

/** At app start (jarvis.js background): JARVIS starts by itself when its key is set and "start automatically" is on,
 *  and says in one sentence that it is ready. */
export function autoStart() {
  if (session.active || !status?.settings?.configured || status.settings.autoStart === false) return;
  session.start({ greet: true });
}

export function showPhone() {
  clearTimeout(poll);
  const tick = () => {
    if (!root?.classList.contains('active')) return;
    if (!session.active) refresh();
    poll = setTimeout(tick, 15000);
  };
  poll = setTimeout(tick, 15000);
}
