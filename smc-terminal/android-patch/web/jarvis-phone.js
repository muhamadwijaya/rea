// JARVIS on the phone (Android): the voice assistant core of Mark LVII by FatihMakes
// (https://github.com/FatihMakes/Mark-LVII, CC BY-NC 4.0, non-commercial use only) running in SHARX without the PC.
// The page captures the microphone as 16 kHz PCM, streams it to the app's engine over /api/jarvis/live and plays the
// 24 kHz replies. The engine (app/jarvis_phone.py) talks to Gemini Live with the key kept in the Android keystore, runs
// the tools, and hands PC work to JARVIS on the paired PC.
import { esc, on, toast } from './util.js';

const SOURCE = 'https://github.com/FatihMakes/Mark-LVII';
const LICENSE = 'https://creativecommons.org/licenses/by-nc/4.0/';
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
    this.sources.add(src);
    src.onended = () => {
      this.sources.delete(src);
      if (!this.sources.size) this.lastEnd = performance.now();
    };
  }

  flush() {
    for (const s of this.sources) { try { s.stop(); } catch { /* already ended */ } }
    this.sources.clear();
    this.at = 0;
    this.lastEnd = performance.now();
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

  get active() { return !!this.ws; },

  setState(state) {
    this.state = state;
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

  async start({ mic = this.micWanted } = {}) {
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
    await this.player.ctx.resume().catch(() => {});
    const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/jarvis/live`);
    ws.binaryType = 'arraybuffer';
    this.ws = ws;
    // the news assistant gives way while JARVIS has the conversation: its job goes back to the queue, its voice stops
    try {
      const news = window.SMC?.voice;
      news?.tasks?.hold?.();
      news?.speaker?.stop();
      news?.hideCard?.();
    } catch { /* the news assistant is not loaded */ }
    this.setState('connecting');
    return new Promise((resolve) => {
      let settled = false;
      const settle = (v) => { if (!settled) { settled = true; resolve(v); } };
      ws.onopen = () => ws.send(JSON.stringify({ type: 'start' }));
      ws.onmessage = (e) => {
        if (typeof e.data !== 'string') {
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
    // speaker mode: the microphone pauses while JARVIS talks (it would hear itself); headset mode lets you cut in
    if (!status?.settings?.duplex && this.player?.echoWindow) return;
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
    this.send({ type: 'text', text: clean, replyTo });
    this.setState('thinking');
  },

  setMic(on) {
    this.micWanted = on;
    this.micOn = on;
    if (on && this.active) this.startMic();
    if (!on) {
      this.mic?.stop();
      this.mic = null;
      this.send({ type: 'audio_end' });
    }
    if (this.active) this.setState(on ? 'listening' : 'muted');
    else this.setState(this.state);
  },

  interrupt() {
    if (!this.player?.speaking) return;
    this.player.flush();
    this.setState(this.micOn ? 'listening' : 'muted');
  },

  onMessage(m, settle) {
    switch (m.type) {
      case 'state':
        if (m.state === 'thinking' || m.state === 'connecting' || m.state === 'reconnecting') this.setState(m.state);
        break;
      case 'audio_format':
        if (this.player) this.player.rate = m.rate;
        break;
      case 'transcript':
        streamLine(m.role === 'user' ? 'user' : 'model', m.text);
        break;
      case 'interrupted':
        this.player?.flush();
        endLine('model');
        if (this.active) this.setState(this.micOn ? 'listening' : 'muted');
        break;
      case 'turn_complete':
        endLine('user');
        endLine('model');
        this.afterSpeech(() => {
          if (this.active && this.state !== 'connecting') this.setState(this.micOn ? 'listening' : 'muted');
          if (this.ending || this.forPc) this.stop();
        });
        break;
      case 'tool':
        toolLine(m);
        break;
      case 'client_tool':
        this.clientTool(m);
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
        await window.SMC?.openAnalysis?.(symbol, m.args?.timeframe || '1h');
        result = { result: 'chart shown on the phone screen', symbol };
      } else if (m.name === 'shutdown_jarvis') {
        this.ending = true;
        result = { result: 'conversation ends after this reply' };
      } else result = { result: `unknown client tool ${m.name}` };
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
    endLine('user');
    endLine('model');
    this.setState(state);
    try { window.SMC?.voice?.tasks?.drain?.(); } catch { /* the news assistant is not loaded */ }
  },
};

// The news assistant asks this before its next job and before speaking (analysis-queue.js, voice-audio.js): it waits
// while a JARVIS conversation runs, or while the user is typing in the JARVIS tab.
const TYPING = 'textarea, select, input:not([type]), input[type="text"], input[type="password"], input[type="search"]';
window.__sharxJarvis = {
  busy: () => session.active || (!!root?.contains(document.activeElement) && !!document.activeElement.matches?.(TYPING)),
};

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
    const arg = m.args?.symbol || m.args?.city || m.args?.name || m.args?.command || m.args?.url || m.args?.query || m.args?.action || '';
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
        <div class="panel-head"><span class="diamond">◆</span> JARVIS · MARK LVII<span class="right"><span class="jv-chip" data-p="chip">SIAP</span></span></div>
        <div class="jv-body">
          <div class="jp-core">
            <canvas class="jp-orb" data-p="orb" width="320" height="320" title="Ketuk untuk memotong JARVIS"></canvas>
            <div class="jp-state" data-p="state">SIAP</div>
            <div class="jp-controls">
              <button class="btn-primary" data-p="toggle">▶ MULAI</button>
              <button class="btn" data-p="mic">🎙 MIC ON</button>
            </div>
          </div>
          <p class="jv-credit">Inti asisten <a href="${SOURCE}" target="_blank" rel="noopener">Mark LVII</a> oleh FatihMakes
            (model Gemini Live, persona, memori) jalan di HP ini, tanpa PC. Lisensi
            <a href="${LICENSE}" target="_blank" rel="noopener">CC BY-NC 4.0</a>: hanya non-komersial.</p>
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
                (sama seperti Mark LVII di PC). Key disimpan terenkripsi di HP dan tidak dikirim ke halaman.</li>
              <li><b>▶ MULAI</b> lalu bicara. Atau ketik perintah di bawah percakapan. Ketuk orb untuk memotong JARVIS.</li>
              <li>JARVIS di HP bisa: percakapan dan memori seperti Mark LVII, cuaca, pengingat, membaca halaman web,
                pencarian Google, analisa SMC, harga, berita, kalender ekonomi, buka chart SHARX, volume/senter/baterai,
                buka aplikasi atau tautan, siapkan panggilan/SMS/WhatsApp/email (Anda yang menekan kirim), peta.</li>
              <li><b>Kontrol PC</b> (opsional): jalankan JARVIS di PC (SHARX desktop → tab JARVIS), isi alamat PC dan kode
                <b>Remote Control</b> dari HUD PC → PASANGKAN. Lalu minta, mis. "suruh PC buka Chrome": JARVIS HP meneruskan
                ke PC dan membacakan jawabannya. Tanpa PC, JARVIS HP tetap jalan.</li>
              <li><b>PC mengontrol HP</b> (opsional): centang "Izinkan PC mengontrol HP ini". Di SHARX desktop → tab JARVIS →
                KONTROL HP, isi alamat HP dan kode yang tampil di sini. Setelah itu JARVIS di PC bisa menyuruh HP (mis.
                "suruh HP pasang pengingat jam 8"), selama aplikasi SHARX terbuka di HP.</li>
              <li>Selama JARVIS aktif, asisten berita menunda bacaannya.</li>
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
        duplex: q('duplex').checked, googleSearch: q('gsearch').checked,
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

/** The PC's own Mark LVII panel; it signs in with its own Remote Control key (keys are single-use). */
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
  build();
  render();
  session.setState(status?.settings?.configured ? 'idle' : 'nokey');
  refresh();
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
