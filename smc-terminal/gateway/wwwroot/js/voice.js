// News voice assistant. When a headline tagged to an asset arrives, the AI analyses it first: the engine fetches
// the article from its link, the AI judges its impact on the asset (the link is attached to the analysis), and
// every timeframe with momentum in that direction gets its own signal (entry / SL / TP). Only then is it read
// aloud: headline, the article's lead, the AI result and the signals per timeframe; then the assistant asks
// whether to read the whole article. The answer can be spoken ("ya" / "tidak", recognised offline), clicked,
// or typed (Y / N). The 🔊 button of a headline does the same and reads the whole article without asking.
//
// Voices: Supertonic 3 (natural, Indonesian + English in one voice) or Piper, both offline, free and without
// limit, served by the engine; the Windows / browser voices as fallback or by choice. Settings per device.
import { api, esc, on } from './util.js';
import { aiResultHtml } from './ai-panel.js';

const KEY = 'sharx.voice';
const SETTINGS_VERSION = 2;  // v2: the natural Supertonic voice became the default
const DEFAULTS = { enabled: false, scope: 'all', fullOnly: false, listen: true, engine: 'supertonic', voice: 'M1', persona: 'natural', mixed: true, browserVoice: '', rate: 1.0, volume: 1.0, version: SETTINGS_VERSION };
const ENGINE_MODELS = { supertonic: () => ['st3', 'stt'], piper: (persona) => (persona === 'jarvis' ? ['id', 'engb', 'stt'] : ['id', 'en', 'stt']) };
const NATURAL_VOICES = [['M1', 'Pria 1'], ['M2', 'Pria 2'], ['M3', 'Pria 3'], ['M4', 'Pria 4'], ['M5', 'Pria 5'], ['F1', 'Wanita 1'], ['F2', 'Wanita 2'], ['F3', 'Wanita 3'], ['F4', 'Wanita 4'], ['F5', 'Wanita 5']];
const TF_NAME = { '5m': '5 menit', '15m': '15 menit', '1h': '1 jam', '4h': '4 jam', '1d': 'harian' };
const TF_STATUS = { signal: null, against: ['berlawanan', 'down'], no_momentum: ['belum ada momentum', 'dim'], neutral: ['netral', 'dim'], error: ['data tidak tersedia', 'dim'] };
const LEAD_CHARS = 420;
const MAX_QUEUE = 3;
const STALE_MS = 5 * 60 * 1000;
const ANSWER_MS = 8000;
const SCOPES = [['all', 'Semua berita yang terkait aset'], ['core', 'Hanya aset inti (watchlist)'], ['strong', 'Hanya sentimen kuat']];

function loadSettings() {
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch { /* per-session only */ }
  if (saved.version !== SETTINGS_VERSION) {  // earlier versions defaulted to Piper / JARVIS: move to the natural voice once
    saved = { ...saved, engine: saved.engine === 'browser' ? 'browser' : 'supertonic', persona: 'natural', version: SETTINGS_VERSION };
  }
  return { ...DEFAULTS, ...saved };
}

// Prices for the Indonesian voice: no thousands separator, decimal comma ("1,0845" is read "satu koma ...").
const spokenPrice = (v, decimals) => (v == null ? '' : Number(v).toFixed(Math.min(6, Math.max(0, decimals ?? 2))).replace('.', ','));

/** First sentences of a text, up to about `max` characters. */
function lead(text, max) {
  const parts = String(text || '').match(/[^.!?]+[.!?]+\s*|[^.!?]+$/g) || [];
  let out = '';
  for (const p of parts) {
    if (out && (out + p).length > max) break;
    out += p;
  }
  return out.trim();
}
function saveSettings(s) {
  try { localStorage.setItem(KEY, JSON.stringify(s)); } catch { /* per-session only */ }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Sentences grouped into chunks the engine synthesizes quickly; the next chunk is prepared while one plays. */
function chunks(text, max = 260) {
  const parts = String(text || '').replace(/\s+/g, ' ').trim().match(/[^.!?。]+[.!?。]*\s*/g) || [];
  const out = [];
  let cur = '';
  for (const p of parts) {
    if ((cur + p).length > max && cur) { out.push(cur.trim()); cur = ''; }
    if (p.length > max) { for (let i = 0; i < p.length; i += max) out.push(p.slice(i, i + max).trim()); continue; }
    cur += p;
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}

function encodeWav(buffers, rate) {
  const n = buffers.reduce((a, b) => a + b.length, 0);
  const view = new DataView(new ArrayBuffer(44 + n * 2));
  const str = (o, s) => { for (let i = 0; i < s.length; i++) view.setUint8(o + i, s.charCodeAt(i)); };
  str(0, 'RIFF'); view.setUint32(4, 36 + n * 2, true); str(8, 'WAVE'); str(12, 'fmt ');
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  str(36, 'data'); view.setUint32(40, n * 2, true);
  let o = 44;
  for (const b of buffers) for (const v of b) { view.setInt16(o, Math.max(-1, Math.min(1, v)) * 0x7fff, true); o += 2; }
  return new Blob([view], { type: 'audio/wav' });
}

class Speaker {
  constructor(assistant) {
    this.a = assistant;
    this.audio = new Audio();
    this.token = 0;
  }

  stop() {
    this.token++;
    this.audio.pause();
    this.audio.dispatchEvent(new Event('ended'));
    if ('speechSynthesis' in window) speechSynthesis.cancel();
  }

  /** The engine can speak this language: the natural voice, or Piper (also the engine's fallback for it). */
  serverReady(lang) {
    const st = this.a.status;
    const engine = this.a.settings.engine;
    if (engine === 'browser' || !st) return false;
    if (engine === 'supertonic' && st.natural?.ready && st.packages?.supertonic) return true;
    if (!st.packages?.piper) return false;
    return lang === 'en' ? Boolean(st.tts?.engb?.ready || st.tts?.en?.ready) : Boolean(st.tts?.id?.ready);
  }

  async fetchWav(text, lang) {
    const res = await fetch('/api/voice/tts', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, lang, rate: this.a.settings.rate, persona: this.a.settings.persona, mixed: this.a.settings.mixed,
        engine: this.a.settings.engine, voice: this.a.settings.voice }),
    });
    if (!res.ok) throw new Error((await res.json().catch(() => null))?.message || `HTTP ${res.status}`);
    return res.blob();
  }

  play(blob, token) {
    return new Promise((resolve) => {
      const url = URL.createObjectURL(blob);
      const done = () => { this.audio.removeEventListener('ended', done); URL.revokeObjectURL(url); resolve(token === this.token); };
      this.audio.addEventListener('ended', done);
      this.audio.src = url;
      this.audio.volume = this.a.settings.volume;
      this.audio.play().catch(() => done());
    });
  }

  /** Reads `text`; resolves false when interrupted. `onPart(i, n)` reports progress. */
  async say(text, lang = 'id', onPart) {
    const token = this.token;
    const parts = chunks(text);
    if (!parts.length) return true;
    if (!this.serverReady(lang)) return this.browserSay(parts, lang, token, onPart);
    try {
      let next = this.fetchWav(parts[0], lang);
      for (let i = 0; i < parts.length; i++) {
        const blob = await next;
        if (i + 1 < parts.length) next = this.fetchWav(parts[i + 1], lang);
        onPart?.(i + 1, parts.length);
        if (!(await this.play(blob, token))) return false;
      }
      return token === this.token;
    } catch (e) {
      this.a.note(`Suara offline tidak tersedia (${e.message}); memakai suara Windows.`);
      return this.browserSay(parts, lang, token, onPart);
    }
  }

  browserVoice(lang) {
    const voices = ('speechSynthesis' in window) ? speechSynthesis.getVoices() : [];
    const chosen = voices.find((v) => v.name === this.a.settings.browserVoice && v.lang.toLowerCase().startsWith(lang));
    if (chosen) return chosen;
    const same = voices.filter((v) => v.lang.toLowerCase().startsWith(lang));
    return same.find((v) => /natural|neural|online/i.test(v.name)) || same[0] || null;
  }

  async browserSay(parts, lang, token, onPart) {
    if (!('speechSynthesis' in window)) return false;
    const voice = this.browserVoice(lang) || this.browserVoice('id') || null;
    for (let i = 0; i < parts.length; i++) {
      if (token !== this.token) return false;
      onPart?.(i + 1, parts.length);
      await new Promise((resolve) => {
        const u = new SpeechSynthesisUtterance(parts[i]);
        if (voice) { u.voice = voice; u.lang = voice.lang; } else u.lang = lang === 'en' ? 'en-US' : 'id-ID';
        u.rate = this.a.settings.rate;
        u.volume = this.a.settings.volume;
        u.onend = resolve;
        u.onerror = resolve;
        speechSynthesis.speak(u);
      });
    }
    return token === this.token;
  }
}

/** Records one short spoken answer as 16 kHz mono WAV (stops after ~0.9 s of silence). */
async function recordAnswer(maxMs, onLevel) {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
  const ctx = new AudioContext({ sampleRate: 16000 });
  const src = ctx.createMediaStreamSource(stream);
  const proc = ctx.createScriptProcessor(4096, 1, 1);
  const buffers = [];
  const started = performance.now();
  let heard = false;
  let lastLoud = 0;
  return new Promise((resolve) => {
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      proc.disconnect(); src.disconnect();
      stream.getTracks().forEach((t) => t.stop());
      ctx.close();
      resolve(heard ? encodeWav(buffers, ctx.sampleRate) : null);
    };
    proc.onaudioprocess = (e) => {
      const x = e.inputBuffer.getChannelData(0);
      buffers.push(new Float32Array(x));
      let sum = 0;
      for (let i = 0; i < x.length; i++) sum += x[i] * x[i];
      const rms = Math.sqrt(sum / x.length);
      onLevel?.(rms);
      const now = performance.now();
      if (rms > 0.02) { heard = true; lastLoud = now; }
      if ((heard && now - lastLoud > 900) || now - started > maxMs) finish();
    };
    src.connect(proc);
    proc.connect(ctx.destination);
  });
}

export class VoiceAssistant {
  constructor(store) {
    this.store = store;
    this.settings = loadSettings();
    this.status = null;
    this.queue = [];
    this.busy = false;
    this.answer = null;
    this.speaker = new Speaker(this);
    this.buildUi();
    on('news', (items) => this.onNews(items));
    document.addEventListener('keydown', (e) => {
      if (!this.answer || e.target.closest?.('input, textarea, select')) return;
      if (e.key === 'y' || e.key === 'Y') this.answer('yes');
      if (e.key === 'n' || e.key === 'N' || e.key === 'Escape') this.answer('no');
    });
    if ('speechSynthesis' in window) speechSynthesis.onvoiceschanged = () => this.renderSettings();
    this.refreshStatus();
  }

  // ------------------------------------------------------------------ UI

  buildUi() {
    const right = document.querySelector('.top-right');
    right.insertAdjacentHTML('afterbegin', `
      <div class="va-wrap">
        <button class="va-toggle" id="va-toggle" title="Asisten suara berita">🔊</button>
        <div class="va-pop hidden" id="va-pop"></div>
      </div>`);
    document.body.insertAdjacentHTML('beforeend', '<div class="va-card hidden" id="va-card"></div>');
    this.pop = document.getElementById('va-pop');
    this.card = document.getElementById('va-card');
    document.getElementById('va-toggle').addEventListener('click', (e) => {
      e.stopPropagation();
      this.pop.classList.toggle('hidden');
      if (!this.pop.classList.contains('hidden')) { this.refreshStatus(); this.renderSettings(); }
    });
    document.addEventListener('click', (e) => { if (!e.target.closest('.va-wrap')) this.pop.classList.add('hidden'); });
    this.pop.addEventListener('change', (e) => this.onSetting(e));
    this.pop.addEventListener('input', (e) => { if (e.target.type === 'range') this.onSetting(e); });
    this.pop.addEventListener('click', (e) => this.onPopClick(e));
    this.card.addEventListener('click', (e) => {
      const b = e.target.closest('[data-va]');
      if (!b) return;
      if (b.dataset.va === 'yes' || b.dataset.va === 'no') this.answer?.(b.dataset.va);
      if (b.dataset.va === 'stop') this.stop();
      if (b.dataset.va === 'open' && this.current?.link) window.open(this.current.link, '_blank', 'noopener');
      if (b.dataset.va === 'analysis' && this.current?.symbol) window.SMC.openAnalysis(this.current.symbol);
      if (b.dataset.va === 'detail') this.card.querySelector('.va-detail')?.classList.toggle('hidden');
    });
    this.renderToggle();
  }

  renderToggle() {
    const t = document.getElementById('va-toggle');
    t.classList.toggle('on', this.settings.enabled);
    t.textContent = this.settings.enabled ? '🔊' : '🔈';
  }

  renderSettings() {
    const s = this.settings;
    const st = this.status;
    const pkg = st?.packages || {};
    const model = (key, label, info) => {
      const d = info?.download;
      const state = info?.ready ? '<b class="up">✔ siap</b>'
        : d?.status === 'downloading' ? `<span class="flat">mengunduh ${Math.round((d.done / Math.max(1, d.total)) * 100)}%</span>`
          : d?.status === 'error' ? `<span class="down">gagal: ${esc(d.error || '')}</span>` : `<span class="dim">belum (${Math.round((info?.sizeBytes || 0) / 1e6)} MB)</span>`;
      return `<div class="va-model"><span>${label}</span>${state}${!info?.ready && d?.status !== 'downloading' ? `<button class="btn sm" data-dl="${key}">UNDUH</button>` : ''}</div>`;
    };
    const voices = ('speechSynthesis' in window) ? speechSynthesis.getVoices().filter((v) => /^(id|en)/i.test(v.lang)) : [];
    this.pop.innerHTML = `
      <h5>ASISTEN SUARA BERITA</h5>
      <label class="check"><input type="checkbox" data-k="enabled" ${s.enabled ? 'checked' : ''}> Bacakan berita baru yang terkait aset</label>
      <select class="input" data-k="scope">${SCOPES.map(([k, l]) => `<option value="${k}" ${s.scope === k ? 'selected' : ''}>${l}</option>`).join('')}</select>
      <label class="check" title="Berita dari Google News hanya berisi judul untuk pembaca otomatis; sumber langsung (CNBC Indonesia, Kontan, detik, Bloomberg, …) bisa dibaca utuh"><input type="checkbox" data-k="fullOnly" ${s.fullOnly ? 'checked' : ''}> Hanya berita yang isinya bisa dibaca AI (lewati tautan Google News)</label>
      <label class="check"><input type="checkbox" data-k="listen" ${s.listen ? 'checked' : ''}> Jawab dengan suara ("ya" / "tidak") lewat mikrofon</label>
      <h5>SUARA</h5>
      <select class="input" data-k="engine">
        <option value="supertonic" ${s.engine === 'supertonic' ? 'selected' : ''}>Supertonic 3 — suara natural mirip manusia, Indonesia + English satu suara</option>
        <option value="piper" ${s.engine === 'piper' ? 'selected' : ''}>Piper — suara neural ringan</option>
        <option value="browser" ${s.engine === 'browser' ? 'selected' : ''}>Suara Windows / browser</option>
      </select>
      ${s.engine === 'supertonic' ? `<select class="input" data-k="voice">${NATURAL_VOICES.map(([k, l]) => `<option value="${k}" ${s.voice === k ? 'selected' : ''}>Suara ${l} (${k})</option>`).join('')}</select>` : ''}
      ${s.engine !== 'browser' ? `<select class="input" data-k="persona">
        <option value="natural" ${s.persona === 'natural' ? 'selected' : ''}>Natural — suara asli tanpa efek</option>
        <option value="jarvis" ${s.persona === 'jarvis' ? 'selected' : ''}>JARVIS — warna suara asisten AI${s.engine === 'piper' ? ', English aksen British' : ''}</option>
      </select>
      <label class="check"><input type="checkbox" data-k="mixed" ${s.mixed ? 'checked' : ''}> Frasa bahasa Inggris dibacakan dengan pelafalan Inggris</label>
      ${s.engine === 'piper' && s.persona === 'jarvis' && pkg.pyworld === false ? '<div class="note down">Paket pyworld belum terpasang — persona JARVIS memakai suara asli. Jalankan setup.bat lagi.</div>' : ''}` : ''}
      ${s.engine === 'browser' ? `<select class="input" data-k="browserVoice"><option value="">Otomatis</option>${voices.map((v) => `<option ${v.name === s.browserVoice ? 'selected' : ''}>${esc(v.name)}</option>`).join('')}</select>` : ''}
      <label class="va-range">Kecepatan <input type="range" min="0.7" max="1.5" step="0.05" data-k="rate" value="${s.rate}"><b>${Number(s.rate).toFixed(2)}×</b></label>
      <label class="va-range">Volume <input type="range" min="0.1" max="1" step="0.05" data-k="volume" value="${s.volume}"><b>${Math.round(s.volume * 100)}%</b></label>
      <h5>MODEL (SEKALI UNDUH, DISIMPAN DI KOMPUTER INI)</h5>
      ${(s.engine === 'supertonic' && pkg.supertonic === false) || (s.engine === 'piper' && pkg.piper === false) || pkg.whisper === false
        ? '<div class="note down">Paket suara belum terpasang — jalankan setup.bat lagi. Sementara memakai suara Windows.</div>' : ''}
      ${s.engine === 'supertonic' ? model('st3', 'Suara natural Supertonic 3 (ID + EN)', st?.natural) : ''}
      ${s.engine === 'piper' ? `${model('id', 'Suara Indonesia', st?.tts?.id)}${model('engb', 'Suara English British pria (JARVIS)', st?.tts?.engb)}${model('en', 'Suara English natural', st?.tts?.en)}` : ''}
      ${model('stt', 'Pengenal jawaban (Whisper)', st?.stt)}
      <div class="toolbar"><button class="btn" data-act="dl-all">UNDUH YANG DIPERLUKAN</button><button class="btn" data-act="test">TES SUARA</button></div>
      <div class="note">Semua suara diproses offline di komputer ini, gratis dan tanpa batas. Supertonic 3: kode MIT, bobot model
        OpenRAIL-M (dilarang dipakai untuk meniru suara orang sungguhan). Model Piper untuk pemakaian pribadi. Tombol Y / N di keyboard juga bisa menjawab.</div>
      <div class="note va-note"></div>`;
  }

  onSetting(e) {
    const k = e.target.dataset.k;
    if (!k) return;
    const v = e.target.type === 'checkbox' ? e.target.checked : e.target.type === 'range' ? Number(e.target.value) : e.target.value;
    this.settings[k] = v;
    saveSettings(this.settings);
    if (k === 'enabled') { this.renderToggle(); if (v) this.speak('Asisten suara berita aktif.'); else this.stop(); }
    if (e.type === 'change') this.renderSettings();
    else e.target.nextElementSibling.textContent = k === 'rate' ? `${Number(v).toFixed(2)}×` : `${Math.round(v * 100)}%`;
  }

  async onPopClick(e) {
    const dl = e.target.closest('[data-dl]');
    const act = e.target.closest('[data-act]')?.dataset.act;
    if (dl || act === 'dl-all') {
      const items = dl ? [dl.dataset.dl] : (ENGINE_MODELS[this.settings.engine] || ENGINE_MODELS.supertonic)(this.settings.persona);
      try {
        this.status = await api('/api/voice/setup', { json: { items } });
      } catch (err) { this.note(err.message); }
      this.renderSettings();
      this.pollDownloads();
    }
    if (act === 'test') this.speak('Selamat malam. Saya asisten berita SHARX INVEST. Investor masih wait and see menjelang keputusan the Fed, sementara saham Nvidia rebound di premarket. Sentimen naik.');
  }

  async refreshStatus() {
    try { this.status = await api('/api/voice/status'); } catch { this.status = null; }
    if (!this.pop.classList.contains('hidden')) this.renderSettings();
  }

  async pollDownloads() {
    for (let i = 0; i < 600; i++) {
      await sleep(1500);
      await this.refreshStatus();
      const st = this.status;
      const busy = [st?.natural, st?.tts?.id, st?.tts?.engb, st?.tts?.en, st?.stt].some((m) => m?.download?.status === 'downloading');
      if (!busy) return;
    }
  }

  note(text) {
    const n = this.pop.querySelector('.va-note');
    if (n) n.textContent = text;
    this.cardLine(text);
  }

  // ------------------------------------------------------------------ card

  showCard(item, state) {
    this.current = item ? { ...item, symbol: item.symbols?.[0]?.symbol } : this.current;
    const c = this.current;
    this.card.classList.remove('hidden');
    this.card.innerHTML = `
      <div class="va-head"><span class="va-dot"></span><b>ASISTEN BERITA</b><span class="va-state">${esc(state)}</span>
        <button class="va-x" data-va="stop" title="Hentikan">✕</button></div>
      ${c ? `<div class="va-title">${esc(c.title)}</div>
      <div class="va-tags">${(c.symbols || []).slice(0, 4).map((t) => `<span class="chip">${esc(t.symbol)}</span>`).join('')}<span class="dim">${esc(c.source || '')}</span></div>` : ''}
      <div class="va-body"></div>
      <div class="va-actions"></div>`;
  }

  cardState(state) {
    const el = this.card.querySelector('.va-state');
    if (el) el.textContent = state;
  }

  cardLine(html) {
    const body = this.card.querySelector('.va-body');
    if (body) body.innerHTML = html;
  }

  cardActions(html) {
    const el = this.card.querySelector('.va-actions');
    if (el) el.innerHTML = html;
  }

  hideCard() {
    this.card.classList.add('hidden');
    this.current = null;
  }

  // ------------------------------------------------------------------ flow

  speak(text, lang = 'id') {
    return this.speaker.say(text, lang);
  }

  stop() {
    this.queue = [];
    this.speaker.stop();
    this.answer?.('no');
    this.cancelled = true;
    this.hideCard();
  }

  wanted(item) {
    const tags = (item.symbols || []).filter((t) => this.store.quotes?.has(t.symbol));
    if (!tags.length) return false;
    // Google News links only redirect inside a browser: the reader (and so the AI) gets the headline, not the article
    if (this.settings.fullOnly && /^https?:\/\/news\.google\.com\//i.test(item.link || '')) return false;
    if (this.settings.scope === 'core') return tags.some((t) => this.store.quotes.get(t.symbol)?.core);
    if (this.settings.scope === 'strong') return Math.abs(item.sentiment ?? 0) >= 0.5;
    return true;
  }

  onNews(items) {
    if (!this.settings.enabled) return;
    const fresh = items.filter((i) => this.wanted(i));
    if (!fresh.length) return;
    this.queue.push(...fresh.map((item) => ({ item, at: Date.now() })));
    this.queue = this.queue.slice(-MAX_QUEUE); // a burst of headlines: only the newest few are read
    this.run();
  }

  /** From a 🔊 button: read this headline in full and analyse it now, without asking first. */
  readNow(item) {
    this.speaker.stop();
    this.queue = [];
    this.run({ item, at: Date.now(), direct: true });
  }

  async run(direct) {
    if (direct) {
      while (this.busy) { this.cancelled = true; this.speaker.stop(); await sleep(100); }
    } else if (this.busy) return;
    this.busy = true;
    try {
      let job = direct || this.queue.shift();
      while (job) {
        this.cancelled = false;
        if (job.direct || Date.now() - job.at < STALE_MS) await this.handle(job.item, job.direct);
        if (this.cancelled && job.direct) break;
        job = this.queue.shift();
      }
    } finally {
      this.busy = false;
    }
  }

  names(item) {
    return (item.symbols || []).slice(0, 2).map((t) => {
      const q = this.store.quotes?.get(t.symbol);
      return String(q?.name || t.symbol).replace(/\s*\/\s*US Dollar$/i, '').replace(/,?\s*(Tbk\.?|Inc\.?|Corp\.?|Corporation|Ltd\.?|PLC)$/i, '').trim();
    }).join(' dan ');
  }

  tone(item) {
    const s = (item.sentiment ?? 0) * (item.symbols?.[0]?.sign || 1);
    return s > 0.15 ? 'naik' : s < -0.15 ? 'turun' : 'netral';
  }

  /** The news analysis in the card: article (with its link), AI result and the signal of every timeframe. */
  renderNews(item, r) {
    const decimals = this.store.quotes?.get(r?.symbol)?.decimals;
    const art = r?.article || { title: item.title, link: item.link, fullText: false };
    const a = r?.analysis;
    const c = r?.confluence;
    const price = (v) => (v == null ? '–' : Number(v).toLocaleString('en-US', { maximumFractionDigits: Math.min(6, decimals ?? 4) }));
    const rows = (r?.timeframes || []).map((t) => {
      const st = TF_STATUS[t.status];
      const s = t.setup;
      const side = t.direction === 'bullish' ? '<span class="tag bull">BUY</span>' : '<span class="tag bear">SELL</span>';
      const cell = t.status === 'signal'
        ? (s?.entry != null ? `${side} <span class="num">${price(s.entry)}</span> · SL <span class="num">${price(s.stop)}</span> · TP <span class="num">${price(s.target)}</span>${s.rr ? ` <span class="dim">RR ${s.rr}</span>` : ''}`
          : `${side} <span class="dim">tunggu zona entry</span>`)
        : `<span class="${st?.[1] || 'dim'}">${esc(st?.[0] || t.status)}</span>`;
      return `<tr><td><b>${esc(t.tf.toUpperCase())}</b></td><td class="${t.smcScore > 0 ? 'up' : t.smcScore < 0 ? 'down' : ''}">${t.smcScore ?? '–'}</td><td>${cell}</td>
        <td>${t.status === 'signal' ? `<span class="tag ${s?.level ? 'bull' : 'ghost'}">${esc(s?.level || (s?.source?.startsWith('plan') ? 'PLAN SMC' : 'BAYANGAN'))}</span>` : ''}</td></tr>`;
    }).join('');
    this.cardLine(`<div class="va-news">
      <div class="va-src"><span><span class="tag info">ARTIKEL</span> <a href="${esc(art.link || '#')}" target="_blank" rel="noopener">${esc(art.title || item.title)} ↗</a></span>
        <span class="dim">${art.fullText ? 'isi lengkap dianalisa' : `hanya judul & ringkasan${art.reason ? ` (${esc(art.reason)})` : ''}`}</span></div>
      ${a ? `<div class="va-result"><span class="ai-signal sm ${c.signal === 'BELI' ? 'sig-buy' : c.signal === 'JUAL' ? 'sig-sell' : 'sig-neutral'}">${esc(c.signal)}</span>
          <b>${c.final}/100</b><span class="dim">${esc(a.horizon)} · ${esc(String(r.model || '').replace(/:free$/, ''))}</span><p>${esc(a.summary)}</p></div>`
        : `<div class="note ${r?.failed ? 'down' : 'flat'}">${r?.failed ? `Analisa gagal: ${esc(r.failed)}` : `Analisa AI tidak tersedia${r?.aiError ? `: ${esc(r.aiError.message)}` : ''} — arah dari sentimen berita: <b>${esc((r?.direction || 'netral').toUpperCase())}</b>`}</div>`}
      ${rows ? `<table class="va-tf"><thead><tr><th>TF</th><th>SMC</th><th>Sinyal (bila ada momentum)</th><th>Presisi</th></tr></thead><tbody>${rows}</tbody></table>` : ''}
      ${r?.trigger1m?.pattern ? `<div class="va-src"><span><span class="tag ${r.trigger1m.direction === 'bullish' ? 'bull' : 'bear'}">PEMICU 1M</span> ${esc(r.trigger1m.pattern)}
        <span class="dim">${r.trigger1m.barsAgo} mnt lalu · di aset ini searah dalam ${r.trigger1m.horizonMin} mnt: ${r.trigger1m.hitRate == null ? '–' : `${Math.round(r.trigger1m.hitRate * 100)}%`} dari ${r.trigger1m.n}</span></span></div>` : ''}
      ${a ? `<div class="va-detail hidden">${aiResultHtml(r)}</div>` : ''}
    </div>`);
  }

  /** What is read after the article: the AI verdict, then each timeframe's signal. */
  speech(item, r) {
    const name = this.names(item) || r?.symbol || '';
    const a = r?.analysis;
    const c = r?.confluence;
    const verdict = a
      ? `Analisa AI untuk ${name}: sinyal ${c.signal.toLowerCase()}, keyakinan ${c.final} persen, untuk ${a.horizon}. ${a.summary}`
      : r?.failed ? `Analisa tidak berhasil. ${r.failed}.`
        : `Analisa AI tidak tersedia. Arah dari sentimen berita: ${r?.direction || 'netral'}.`;
    const decimals = this.store.quotes?.get(r?.symbol)?.decimals;
    const tfs = (r?.timeframes || []).map((t) => {
      const when = TF_NAME[t.tf] || t.tf;
      if (t.status === 'signal') {
        const s = t.setup;
        const side = t.direction === 'bullish' ? 'beli' : 'jual';
        if (s?.entry == null) return `${when}: ${side}, tunggu zona entry.`;
        return `${when}: ${side}, entry ${spokenPrice(s.entry, decimals)}, stop loss ${spokenPrice(s.stop, decimals)}, target ${spokenPrice(s.target, decimals)}${s.level ? `, presisi ${s.level.toLowerCase()}` : ''}.`;
      }
      return t.status === 'against' ? `${when}: berlawanan.` : t.status === 'no_momentum' ? `${when}: belum ada momentum.` : '';
    }).filter(Boolean);
    const t = r?.trigger1m;
    if (t?.pattern) {
      tfs.push(`Pemicu 1 menit: ${t.pattern}, ${t.barsAgo} menit lalu${t.n ? `; di aset ini searah ${Math.round(t.hitRate * 100)} persen dari ${t.n} kejadian` : ''}.`);
    }
    return { verdict, signals: tfs.length ? `Sinyal per timeframe. ${tfs.join(' ')}` : '' };
  }

  async handle(item, direct) {
    const lang = item.lang === 'en' ? 'en' : 'id';
    const symbol = item.symbols?.[0]?.symbol;
    this.showCard(item, 'AI menganalisa berita');
    this.cardLine('<span class="va-busy">Mengambil isi berita dari link dan menjalankan analisa AI sebelum dibacakan…</span>');
    this.cardActions('<button class="btn" data-va="open">BUKA BERITA ↗</button><button class="btn danger" data-va="stop">STOP</button>');
    let r = null;
    if (symbol) {
      try { r = await api('/api/ai/news', { json: { id: item.id, symbol } }); } catch (e) { r = { failed: e.message, symbol }; }
    }
    if (this.cancelled) return;
    this.renderNews(item, r);
    this.cardActions(`${r?.analysis ? '<button class="btn" data-va="detail">DETAIL ANALISA</button>' : ''}<button class="btn" data-va="analysis">BUKA CHART</button>
      <button class="btn" data-va="open">BUKA BERITA ↗</button><button class="btn danger" data-va="stop">STOP</button>`);
    this.cardState('membacakan');
    const { verdict, signals } = this.speech(item, r);
    // headline, then the article's lead (the whole article from a 🔊 button), then the analysis
    if (!(await this.speak(`Berita${item.source ? ` dari ${item.source}` : ''} untuk ${this.names(item) || symbol || 'pasar'}.`))) return;
    if (!(await this.speak(item.title, lang))) return;
    const full = String(r?.article?.text || item.summary || '').replace(item.title, '').replace(/^[.\s]+/, '').trim();
    const first = direct ? full : lead(full, LEAD_CHARS);
    const progress = (i, n) => this.cardState(`membacakan isi ${i}/${n}`);
    if (first && !(await this.speaker.say(first, lang, progress))) return;
    this.cardState('membacakan hasil AI');
    if (!(await this.speak(verdict))) return;
    if (signals && !(await this.speak(signals))) return;
    if (!(await this.speak('Ini bukan saran investasi.'))) return;
    const rest = full.slice(first.length).trim();
    if (!direct && r?.article?.fullText && rest.length > 80) {
      if (!(await this.speak('Mau saya bacakan isi berita lengkapnya? Jawab ya atau tidak.'))) return;
      if ((await this.ask()) === 'yes') {
        this.cardState('membacakan isi lengkap');
        await this.speaker.say(rest, lang, progress);
      }
    }
    if (!this.cancelled) this.cardState('selesai');
  }

  /** Waits for "ya" / "tidak": spoken (offline recognition), clicked or typed; no answer = no. */
  ask() {
    this.cardState('menunggu jawaban');
    this.cardActions('<button class="btn" data-va="yes">YA, BACAKAN (Y)</button><button class="btn danger" data-va="no">TIDAK (N)</button>');
    return new Promise((resolve) => {
      let settled = false;
      const done = (v) => { if (settled) return; settled = true; this.answer = null; clearTimeout(timer); resolve(v); };
      this.answer = done;
      const timer = setTimeout(() => done('no'), ANSWER_MS + 4000);
      const canListen = this.settings.listen && this.status?.stt?.ready && this.status?.packages?.whisper && navigator.mediaDevices?.getUserMedia;
      if (!canListen) {
        this.cardLine('Klik YA / TIDAK atau tekan Y / N.');
        return;
      }
      this.cardLine('<span class="va-mic">🎤 Mendengarkan… ucapkan "ya" atau "tidak"</span><i class="va-level"><b></b></i>');
      recordAnswer(ANSWER_MS, (rms) => {
        const bar = this.card.querySelector('.va-level b');
        if (bar) bar.style.width = `${Math.min(100, rms * 600)}%`;
      }).then(async (wav) => {
        if (settled) return;
        if (!wav) { this.cardLine('Tidak terdengar jawaban — klik YA / TIDAK atau tekan Y / N.'); return; }
        this.cardLine('Mengenali jawaban…');
        const res = await fetch('/api/voice/stt?lang=id', { method: 'POST', headers: { 'Content-Type': 'audio/wav' }, body: wav });
        const out = await res.json().catch(() => ({}));
        if (out.intent) { this.cardLine(`Terdengar: "${esc(out.text)}"`); done(out.intent); }
        else this.cardLine(`Terdengar: "${esc(out.text || '…')}" — belum jelas, klik YA / TIDAK.`);
      }).catch((e) => this.cardLine(`Mikrofon tidak tersedia (${esc(e.message)}) — klik YA / TIDAK.`));
    });
  }
}
