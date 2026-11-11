// AI analysis panel + provider settings (OpenRouter free models with automatic rotation, Claude, or any
// OpenAI-compatible API). Shared by the FUNDAMENTAL & AI tab and the analysis side panel.
import { api, esc, dirHex, arrow, fmtScore, fmtPct, fmtPrice, timeAgo } from './util.js';

const DIR_KEY = { naik: 'bullish', turun: 'bearish', netral: 'neutral' };
const KEY_HINT = {
  openrouter: { placeholder: 'sk-or-v1-…', note: 'Buat key gratis di openrouter.ai/keys. Hanya model gratis (:free) yang dipakai; bila satu model sedang dibatasi/habis, aplikasi otomatis pindah ke model gratis lain.' },
  anthropic: { placeholder: 'sk-ant-…', note: 'Model claude-opus-5-5 dengan fallback server-side. Setiap analisa memakai kuota/biaya akun Anthropic Anda.' },
  custom: { placeholder: 'API key (kosongkan bila tidak perlu)', note: 'Endpoint apa pun yang kompatibel OpenAI /chat/completions (mis. Groq, Together, DeepSeek, LM Studio/Ollama lokal).' },
};

async function post(path, body, method = 'POST') {
  const res = await fetch(path, { method, headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: body ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error(data?.message || `HTTP ${res.status}`);
  return data;
}

const shortModel = (id) => String(id || '').replace(/:free$/, '');

function attemptsHtml(r) {
  const tries = r.attempts || [];
  if (tries.length <= 1) return '';
  return `<div class="ai-tries">${tries.map((a) => `<span class="${a.ok ? 'ok' : 'fail'}" title="${esc(a.reason || 'berhasil')}">${a.ok ? '✔' : '✘'} ${esc(shortModel(a.model))}${a.ok ? '' : ` · ${esc(a.reason)}`}</span>`).join('<i>→</i>')}</div>`;
}

const SIGNAL_CLASS = { BELI: 'sig-buy', JUAL: 'sig-sell', NETRAL: 'sig-neutral' };
const STATUS_TEXT = { match: ['COCOK ✔', 'bull'], conflict: ['BERTENTANGAN ✘', 'bear'], neutral: ['NETRAL', 'neu'] };
const list = (items) => (items?.length ? `<ul>${items.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : '<p class="dim">–</p>');

function planHtml(plan, decimals) {
  if (!plan || plan.entry == null) return '';
  const side = plan.direction === 'bullish' ? 'BUY' : 'SELL';
  return `<h5>PLAN SMC 4H</h5><div class="chips"><span class="chip">${side} ${esc(plan.status || '')}</span>
    <span class="chip">entry ${fmtPrice(plan.entry, decimals)}</span><span class="chip">SL ${fmtPrice(plan.stop, decimals)}</span>
    ${(plan.targets || []).map((t, i) => `<span class="chip">TP${i + 1} ${fmtPrice(t, decimals)}${plan.rr?.[i] ? ` · RR ${plan.rr[i]}` : ''}</span>`).join('')}</div>`;
}

export function aiResultHtml(r) {
  const a = r.analysis;
  const c = r.confluence || { signal: 'NETRAL', final: a.confidence, base: a.confidence, adjustment: 0, status: 'neutral', note: '' };
  const dir = DIR_KEY[a.direction] || 'neutral';
  const [statusText, statusClass] = STATUS_TEXT[c.status] || STATUS_TEXT.neutral;
  const perf = r.facts?.performance?.returnsPct;
  return `<div class="ai-result">
    <div class="ai-top">
      <span class="ai-signal ${SIGNAL_CLASS[c.signal] || 'sig-neutral'}">${esc(c.signal)}</span>
      <span class="ai-conf"><b>${c.final}</b>/100 keyakinan akhir</span><span class="tag ghost">${esc(a.horizon)}</span>
    </div>
    <div class="conf-bar"><i style="width:${c.final}%;background:${c.signal === 'BELI' ? 'var(--go)' : c.signal === 'JUAL' ? 'var(--stop)' : 'var(--neu)'}"></i></div>
    <div class="ai-conflu">
      <div><span>AI · FUNDAMENTAL</span><b style="color:${dirHex(dir)}">${arrow(dir)} ${esc(a.direction.toUpperCase())} ${a.confidence}</b></div>
      <div><span>SMC · MTF 1D/4H/1H</span><b class="${c.smcScore > 0 ? 'up' : c.smcScore < 0 ? 'down' : ''}">${esc(c.smcLabel || '–')} ${fmtScore(c.smcScore)}</b></div>
      <div><span>KONFLUENSI</span><b><span class="tag ${statusClass}">${statusText}</span> ${c.adjustment > 0 ? '+' : ''}${c.adjustment || 0}</b></div>
    </div>
    <div class="note" style="margin-top:4px">${esc(c.note)} Keyakinan AI ${c.base} → akhir ${c.final} (maks. 95).</div>
    <p>${esc(a.summary)}</p>
    ${a.fundamentalView ? `<h5>LAPORAN · KINERJA · RENCANA</h5><p>${esc(a.fundamentalView)}</p>` : ''}
    ${perf ? `<div class="chips">${Object.entries(perf).filter(([, v]) => v != null).map(([k, v]) => `<span class="chip">${k} <b class="${v > 0 ? 'up' : v < 0 ? 'down' : ''}">${fmtPct(v)}</b></span>`).join('')}</div>` : ''}
    <div class="ai-cols">
      <div><h5>PENDORONG</h5>${list(a.drivers)}</div>
      <div><h5>RISIKO</h5>${list(a.risks)}</div>
    </div>
    ${a.catalysts?.length ? `<h5>KATALIS MENDATANG</h5>${list(a.catalysts)}` : ''}
    ${a.keyLevels.length ? `<h5>LEVEL KUNCI</h5><div class="chips">${a.keyLevels.map((x) => `<span class="chip">${esc(x)}</span>`).join('')}</div>` : ''}
    ${planHtml(r.smc?.plan4h)}
    <h5>INVALIDASI</h5><p>${esc(a.invalidation)}</p>
    ${attemptsHtml(r)}
    <div class="note">Kualitas data: ${esc(a.dataQuality)} · fakta: ${r.facts.headlines} berita, ${r.facts.releases} rilis + ${r.facts.upcoming ?? 0} jadwal data ekonomi${r.facts.reports ? ', laporan emiten' : ''}${r.facts.notes?.length ? ` (${esc(r.facts.notes.join('; '))})` : ''} ·
      ${esc(r.providerName || '')} · <b>${esc(shortModel(r.model))}</b> · ${(r.usage?.input || 0) + (r.usage?.output || 0)} token · ${Math.round(r.elapsedMs / 1000)} dtk · ${timeAgo(r.generatedAt)}.
      Keyakinan menggambarkan kekuatan bukti, bukan jaminan — bukan saran investasi.</div>
  </div>`;
}

/** Mounts the AI panel into `el` with `initial` preselected. */
export function mountAiPanel(el, { initial = '' } = {}) {
  el.innerHTML = `
    <div class="ai-panel">
      <div class="ai-status"></div>
      <div class="toolbar">
        <input class="input ai-symbol" list="symbol-list" placeholder="simbol" value="${esc(initial)}" style="width:150px;min-width:0">
        <button class="btn-primary ai-run" type="button">✦ ANALISA DENGAN AI</button>
      </div>
      <div class="ai-out"><div class="note">AI menilai arah secara fundamental dari fakta nyata: berita terkait + sentimennya,
        kalender ekonomi (rilis actual vs forecast + reaksi pasar, dan jadwal mendatang), laporan keuangan emiten, kinerja harga,
        serta rencana & ekspektasi ke depan (jadwal laporan, konsensus, target analis). Hasilnya lalu dicocokkan dengan analisa
        SMC multi-timeframe: <b>searah → keyakinan naik</b>, berlawanan → turun. Sinyal akhir: BELI / JUAL / NETRAL.</div></div>
      <details class="ai-settings"><summary>PENGATURAN AI (PROVIDER &amp; API KEY)</summary>
        <div class="seg ai-provider" style="margin-top:8px"></div>
        <div class="ai-opt ai-opt-openrouter toolbar"><span class="muted">MODEL</span><select class="input ai-or-model" style="flex:1;min-width:0"></select></div>
        <div class="ai-opt ai-opt-custom toolbar">
          <input class="input ai-base" placeholder="Base URL, mis. https://api.groq.com/openai/v1" style="flex:2;min-width:0">
          <input class="input ai-model" placeholder="nama model" style="flex:1;min-width:0">
          <button class="btn ai-opt-save" type="button">SIMPAN</button>
        </div>
        <div class="toolbar">
          <input class="input ai-key" type="password" autocomplete="off" style="flex:1;min-width:0">
          <button class="btn ai-save" type="button">SIMPAN KEY</button><button class="btn danger ai-del" type="button">HAPUS</button>
        </div>
        <div class="note ai-note"></div>
      </details>
    </div>`;
  const $ = (s) => el.querySelector(s);
  let settings = null;
  let modelsLoaded = false;

  const loadModels = async (selected) => {
    const sel = $('.ai-or-model');
    try {
      const m = await api('/api/ai/models');
      const now = Date.now() / 1000;
      sel.innerHTML = '<option value="auto">Otomatis — rotasi model gratis</option>' + m.models.map((x) => {
        const cool = x.cooldownUntil && x.cooldownUntil > now ? ` · jeda ${Math.ceil((x.cooldownUntil - now) / 60)} mnt (${x.cooldownReason})` : '';
        return `<option value="${esc(x.id)}">${esc(shortModel(x.id))}${x.structured ? ' · JSON ketat' : ''}${x.id === m.lastGood ? ' · terakhir berhasil' : ''}${cool}</option>`;
      }).join('');
      modelsLoaded = true;
    } catch {
      sel.innerHTML = '<option value="auto">Otomatis — rotasi model gratis</option>';
    }
    sel.value = [...sel.options].some((o) => o.value === selected) ? selected : 'auto';
  };

  const render = (s) => {
    settings = s;
    const current = s.providers.find((p) => p.id === s.provider);
    const where = current.source === 'saved' ? 'key tersimpan' : current.source === 'environment' ? `key dari ${current.envVar}` : 'tanpa key';
    const model = s.provider === 'openrouter' ? (s.options.openrouterModel === 'auto' ? 'model otomatis' : shortModel(s.options.openrouterModel))
      : s.provider === 'anthropic' ? s.anthropicModel : (s.options.customModel || 'model belum diisi');
    $('.ai-status').innerHTML = s.configured
      ? `<span class="tag bull">AI AKTIF</span> <span class="muted">${esc(current.name)} · ${esc(model)} · ${esc(where)}</span>`
      : `<span class="tag neu">AI BELUM AKTIF</span> <span class="muted">${esc(current.name)} — buka pengaturan AI di bawah dan masukkan API key</span>`;
    $('.ai-provider').innerHTML = s.providers.map((p) => `<button data-p="${p.id}" class="${p.id === s.provider ? 'active' : ''}" title="${esc(p.name)}">${p.id === 'openrouter' ? 'OPENROUTER (GRATIS)' : p.id === 'anthropic' ? 'CLAUDE' : 'API LAIN'}${p.configured ? ' ✔' : ''}</button>`).join('');
    el.querySelectorAll('.ai-opt').forEach((o) => o.classList.toggle('hidden', !o.classList.contains(`ai-opt-${s.provider}`)));
    $('.ai-key').placeholder = KEY_HINT[s.provider].placeholder;
    $('.ai-note').textContent = `${KEY_HINT[s.provider].note} Key disimpan lokal di komputer ini${s.encrypted ? ' (dienkripsi DPAPI akun Windows Anda)' : ''}, tidak pernah dikirim ke browser, dan hanya dikirim ke provider saat Anda menekan tombol analisa.`;
    $('.ai-base').value = s.options.customBaseUrl || '';
    $('.ai-model').value = s.options.customModel || '';
    if (s.provider === 'openrouter') {
      if (!modelsLoaded) loadModels(s.options.openrouterModel);
      else $('.ai-or-model').value = s.options.openrouterModel;
    }
  };
  const fail = (e) => { $('.ai-status').innerHTML = `<span class="tag bear">GAGAL</span> <span class="muted">${esc(e.message)}</span>`; };
  const refresh = async () => { try { render(await api('/api/settings/ai')); } catch { /* engine/gateway warming */ } };
  const save = async (body) => { try { render(await post('/api/settings/ai', body)); } catch (e) { fail(e); } };

  $('.ai-provider').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-p]');
    if (b && b.dataset.p !== settings?.provider) save({ provider: b.dataset.p });
  });
  $('.ai-or-model').addEventListener('change', () => save({ openrouterModel: $('.ai-or-model').value }));
  $('.ai-or-model').addEventListener('focus', () => loadModels($('.ai-or-model').value));
  $('.ai-opt-save').addEventListener('click', () => save({ customBaseUrl: $('.ai-base').value.trim(), customModel: $('.ai-model').value.trim() }));
  $('.ai-save').addEventListener('click', async () => {
    const apiKey = $('.ai-key').value.trim();
    if (!apiKey) return;
    await save({ apiKey, keyProvider: settings?.provider });
    $('.ai-key').value = '';
  });
  $('.ai-del').addEventListener('click', async () => {
    try { render(await post(`/api/settings/ai?provider=${encodeURIComponent(settings?.provider || '')}`, null, 'DELETE')); } catch (e) { fail(e); }
  });
  $('.ai-run').addEventListener('click', async () => {
    const symbol = $('.ai-symbol').value.trim();
    if (!symbol) return;
    $('.ai-run').disabled = true;
    const who = settings?.provider === 'openrouter' ? 'AI gratis OpenRouter' : settings?.provider === 'anthropic' ? 'Claude' : 'AI';
    $('.ai-out').innerHTML = `<div class="loading">${esc(who)} menganalisa ${esc(symbol)} (bisa 20–120 detik${settings?.provider === 'openrouter' ? ', otomatis pindah model bila perlu' : ''})</div>`;
    try {
      $('.ai-out').innerHTML = aiResultHtml(await post('/api/ai/analyze', { symbol }));
      if (settings?.provider === 'openrouter') modelsLoaded = false;
    } catch (e) {
      $('.ai-out').innerHTML = `<div class="error-box">${esc(e.message)}</div>`;
      if (/belum aktif|ditolak|api key/i.test(e.message)) $('.ai-settings').open = true;
    } finally {
      $('.ai-run').disabled = false;
    }
  });
  refresh();
  return { setSymbol: (s) => { $('.ai-symbol').value = s; } };
}
