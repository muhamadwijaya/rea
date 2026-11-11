// News voice assistant. When a headline tagged to an asset arrives it is read aloud (asset, sentiment,
// title), then the assistant asks whether to read the full article. The answer can be spoken ("ya" /
// "tidak", recognised offline), clicked, or typed (Y / N). On "ya" the article is read in full and an AI
// analysis of the asset runs at the same time; its result is read aloud afterwards.
//
// Voices: Piper neural voices served by the engine (offline, free, no limit) — Indonesian and English —
// with the Windows / browser voices as fallback or by choice. Settings are per device (localStorage).
import { api, esc, on } from './util.js';

const KEY = 'sharx.voice';
const DEFAULTS = { enabled: false, scope: 'all', listen: true, engine: 'piper', browserVoice: '', rate: 1.0, volume: 1.0 };
const MAX_QUEUE = 3;
const STALE_MS = 5 * 60 * 1000;
const ANSWER_MS = 8000;
const SCOPES = [['all', 'Semua berita yang terkait aset'], ['core', 'Hanya aset inti (watchlist)'], ['strong', 'Hanya sentimen kuat']];

function loadSettings() {
  try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) || '{}') }; } catch { return { ...DEFAULTS }; }
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

  piperReady(lang) {
    const tts = this.a.status?.tts;
    return this.a.settings.engine === 'piper' && this.a.status?.packages?.piper && (tts?.[lang]?.ready || (lang !== 'en' && tts?.id?.ready));
  }

  async fetchWav(text, lang) {
    const res = await fetch('/api/voice/tts', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, lang, rate: this.a.settings.rate }),
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
    if (!this.piperReady(lang)) return this.browserSay(parts, lang, token, onPart);
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
      this.a.note(`Suara Piper tidak tersedia (${e.message}); memakai suara Windows.`);
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
      <label class="check"><input type="checkbox" data-k="listen" ${s.listen ? 'checked' : ''}> Jawab dengan suara ("ya" / "tidak") lewat mikrofon</label>
      <h5>SUARA</h5>
      <select class="input" data-k="engine">
        <option value="piper" ${s.engine === 'piper' ? 'selected' : ''}>Piper — suara neural offline (gratis, tanpa batas)</option>
        <option value="browser" ${s.engine === 'browser' ? 'selected' : ''}>Suara Windows / browser</option>
      </select>
      ${s.engine === 'browser' ? `<select class="input" data-k="browserVoice"><option value="">Otomatis</option>${voices.map((v) => `<option ${v.name === s.browserVoice ? 'selected' : ''}>${esc(v.name)}</option>`).join('')}</select>` : ''}
      <label class="va-range">Kecepatan <input type="range" min="0.7" max="1.5" step="0.05" data-k="rate" value="${s.rate}"><b>${Number(s.rate).toFixed(2)}×</b></label>
      <label class="va-range">Volume <input type="range" min="0.1" max="1" step="0.05" data-k="volume" value="${s.volume}"><b>${Math.round(s.volume * 100)}%</b></label>
      <h5>MODEL (SEKALI UNDUH, DISIMPAN DI KOMPUTER INI)</h5>
      ${pkg.piper === false || pkg.whisper === false ? '<div class="note down">Paket suara belum terpasang — jalankan setup.bat lagi. Sementara memakai suara Windows.</div>' : ''}
      ${model('id', 'Suara Indonesia', st?.tts?.id)}${model('en', 'Suara English', st?.tts?.en)}${model('stt', 'Pengenal jawaban (Whisper)', st?.stt)}
      <div class="toolbar"><button class="btn" data-act="dl-all">UNDUH SEMUA</button><button class="btn" data-act="test">TES SUARA</button></div>
      <div class="note">Semua suara diproses offline di komputer ini. Model suara Piper untuk pemakaian pribadi (lisensi dataset suara).
        Tombol Y / N di keyboard juga bisa menjawab.</div>
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
      const items = dl ? [dl.dataset.dl] : ['id', 'en', 'stt'];
      try {
        this.status = await api('/api/voice/setup', { json: { items } });
      } catch (err) { this.note(err.message); }
      this.renderSettings();
      this.pollDownloads();
    }
    if (act === 'test') this.speak('Halo, saya asisten berita SHARX INVEST. Laba bersih Bank Rakyat Indonesia naik sembilan persen. Sentimen naik.');
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
      const busy = [st?.tts?.id, st?.tts?.en, st?.stt].some((m) => m?.download?.status === 'downloading');
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

  async handle(item, direct) {
    const lang = item.lang === 'en' ? 'en' : 'id';
    const symbol = item.symbols?.[0]?.symbol;
    if (!direct) {
      this.showCard(item, 'membacakan tag berita');
      this.cardLine(`Sentimen <b class="${this.tone(item) === 'naik' ? 'up' : this.tone(item) === 'turun' ? 'down' : 'flat'}">${this.tone(item).toUpperCase()}</b>`);
      if (!(await this.speak(`Berita baru untuk ${this.names(item)}. Sentimen ${this.tone(item)}.`))) return;
      if (!(await this.speak(item.title, lang))) return;
      if (!(await this.speak('Mau saya bacakan berita lengkapnya, lalu jalankan analisa AI? Jawab ya atau tidak.'))) return;
      const choice = await this.ask();
      if (choice !== 'yes') { this.hideCard(); return; }
    } else {
      this.showCard(item, 'menyiapkan');
    }
    // full article + AI analysis in parallel
    const analysis = symbol ? api('/api/ai/analyze', { json: { symbol } })
      .then((r) => ({ ok: true, r }), (e) => ({ ok: false, e })) : Promise.resolve(null);
    this.cardState('mengambil isi berita');
    this.cardActions('<button class="btn" data-va="open">BUKA BERITA ↗</button><button class="btn danger" data-va="stop">STOP</button>');
    let article;
    try { article = await api(`/api/news/article?id=${encodeURIComponent(item.id)}`); } catch (e) { article = { text: `${item.title}. ${item.summary || ''}`, source: 'summary', reason: e.message }; }
    if (this.cancelled) return;
    if (article.source !== 'article') {
      this.cardLine(`<span class="flat">Isi lengkap tidak tersedia: ${esc(article.reason || '')}. Membacakan ringkasan.</span>`);
      if (!(await this.speak(`Isi lengkap tidak bisa diambil, ${article.reason || 'situs tidak mengizinkan'}. Saya bacakan ringkasannya.`))) return;
    }
    this.cardState('membacakan berita');
    const ok = await this.speaker.say(article.text, lang, (i, n) => this.cardLine(`Membacakan bagian ${i} dari ${n}${article.truncated ? ' (dipotong untuk artikel sangat panjang)' : ''}`));
    if (!ok || this.cancelled) return;
    if (!symbol) { this.hideCard(); return; }
    this.cardState('analisa AI');
    this.cardLine('Menunggu hasil analisa AI…');
    let waited = false;
    const timer = setTimeout(() => { waited = true; this.speak('Analisa AI masih berjalan, mohon tunggu.'); }, 1500);
    const res = await analysis;
    clearTimeout(timer);
    if (waited) await sleep(200);
    if (this.cancelled || !res) return;
    if (!res.ok) {
      this.cardLine(`<span class="down">Analisa AI gagal: ${esc(res.e.message)}</span>`);
      await this.speak(`Analisa AI tidak berhasil. ${res.e.message}`);
      return;
    }
    const r = res.r;
    const a = r.analysis;
    const c = r.confluence || { signal: 'NETRAL', final: a.confidence };
    this.cardLine(`<div class="va-result"><span class="ai-signal sm ${c.signal === 'BELI' ? 'sig-buy' : c.signal === 'JUAL' ? 'sig-sell' : 'sig-neutral'}">${esc(c.signal)}</span>
      <b>${c.final}/100</b> <span class="dim">${esc(a.horizon)} · ${esc(r.model || '')}</span><p>${esc(a.summary)}</p></div>`);
    this.cardActions('<button class="btn" data-va="analysis">BUKA ANALISA</button><button class="btn" data-va="open">BUKA BERITA ↗</button><button class="btn danger" data-va="stop">TUTUP</button>');
    this.cardState('membacakan hasil AI');
    const name = this.names(item) || symbol;
    const text = [
      `Hasil analisa AI untuk ${name}: sinyal ${c.signal.toLowerCase()}, keyakinan ${c.final} persen, untuk ${a.horizon}.`,
      a.summary,
      a.drivers?.length ? `Pendorong utama: ${a.drivers.slice(0, 2).join('. ')}.` : '',
      a.risks?.length ? `Risiko: ${a.risks.slice(0, 2).join('. ')}.` : '',
      c.note || '',
      'Ini bukan saran investasi.',
    ].filter(Boolean).join(' ');
    await this.speak(text);
    this.cardState('selesai');
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
