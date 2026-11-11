// Optional Claude analysis panel + API-key settings. Shared by the FUNDAMENTAL & AI tab and the analysis side panel.
import { api, esc, dirHex, arrow, fmtScore, timeAgo } from './util.js';

const DIR_KEY = { naik: 'bullish', turun: 'bearish', netral: 'neutral' };

async function post(path, body, method = 'POST') {
  const res = await fetch(path, { method, headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: body ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error(data?.message || `HTTP ${res.status}`);
  return data;
}

export function aiResultHtml(r) {
  const a = r.analysis;
  const dir = DIR_KEY[a.direction] || 'neutral';
  return `<div class="ai-result">
    <div class="ai-top"><span class="ai-dir" style="color:${dirHex(dir)}">${arrow(dir)} ${esc(a.direction.toUpperCase())}</span>
      <span class="ai-conf"><b>${a.confidence}</b>/100 keyakinan</span><span class="tag ghost">${esc(a.horizon)}</span></div>
    <div class="conf-bar"><i style="width:${a.confidence}%;background:${dirHex(dir)}"></i></div>
    <p>${esc(a.summary)}</p>
    <div class="ai-cols">
      <div><h5>PENDORONG</h5><ul>${a.drivers.map((x) => `<li>${esc(x)}</li>`).join('')}</ul></div>
      <div><h5>RISIKO</h5><ul>${a.risks.map((x) => `<li>${esc(x)}</li>`).join('')}</ul></div>
    </div>
    ${a.keyLevels.length ? `<h5>LEVEL KUNCI</h5><div class="chips">${a.keyLevels.map((x) => `<span class="chip">${esc(x)}</span>`).join('')}</div>` : ''}
    <h5>INVALIDASI</h5><p>${esc(a.invalidation)}</p>
    <div class="note">Kualitas data: ${esc(a.dataQuality)} · fakta: ${r.facts.headlines} berita, ${r.facts.releases} rilis data, skor aturan ${fmtScore(r.facts.ruleBasedOutlook.score)} ·
      ${esc(r.model)} · ${r.usage.input + r.usage.output} token · ${Math.round(r.elapsedMs / 1000)} dtk · ${timeAgo(r.generatedAt)}.
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
      <div class="ai-out"><div class="note">Claude membaca fakta dari engine (harga, bias SMC multi-timeframe, rilis data ekonomi
        beserta actual/forecast & reaksi pasar nyata, berita terkait + sentimennya, akurasi terukur model aturan) lalu menulis
        penilaian terstruktur. Hanya fakta itu yang boleh dipakai.</div></div>
      <details class="ai-settings"><summary>PENGATURAN AI (API KEY)</summary>
        <div class="toolbar" style="margin-top:8px">
          <input class="input ai-key" type="password" placeholder="sk-ant-…" autocomplete="off" style="flex:1">
          <button class="btn ai-save" type="button">SIMPAN</button><button class="btn ai-del" type="button">HAPUS</button>
        </div>
        <div class="note">Key disimpan lokal di komputer ini (Windows: dienkripsi DPAPI akun Anda) dan hanya dikirim ke Anthropic API saat
          Anda menekan tombol analisa. Setiap analisa memakai kuota/biaya akun Anthropic Anda.</div>
      </details>
    </div>`;
  const $ = (s) => el.querySelector(s);
  const refreshStatus = async () => {
    try {
      const s = await api('/api/settings/ai');
      $('.ai-status').innerHTML = s.configured
        ? `<span class="tag bull">AI AKTIF</span> <span class="muted">${esc(s.model)} · key ${s.source === 'saved' ? 'tersimpan' : 'dari ANTHROPIC_API_KEY'}</span>`
        : '<span class="tag neu">AI BELUM AKTIF</span> <span class="muted">masukkan Anthropic API key di pengaturan di bawah</span>';
    } catch { /* ignore */ }
  };
  $('.ai-run').addEventListener('click', async () => {
    const symbol = $('.ai-symbol').value.trim();
    if (!symbol) return;
    $('.ai-run').disabled = true;
    $('.ai-out').innerHTML = `<div class="loading">Claude menganalisa ${esc(symbol)} (bisa 20–90 detik)</div>`;
    try {
      $('.ai-out').innerHTML = aiResultHtml(await post('/api/ai/analyze', { symbol }));
    } catch (e) {
      $('.ai-out').innerHTML = `<div class="error-box">${esc(e.message)}</div>`;
      if (/belum aktif|ditolak/i.test(e.message)) $('.ai-settings').open = true;
    } finally {
      $('.ai-run').disabled = false;
    }
  });
  $('.ai-save').addEventListener('click', async () => {
    try {
      await post('/api/settings/ai', { apiKey: $('.ai-key').value.trim() });
      $('.ai-key').value = '';
      refreshStatus();
    } catch (e) {
      $('.ai-status').innerHTML = `<span class="tag bear">GAGAL</span> <span class="muted">${esc(e.message)}</span>`;
    }
  });
  $('.ai-del').addEventListener('click', async () => { await post('/api/settings/ai', null, 'DELETE').catch(() => {}); refreshStatus(); });
  refreshStatus();
  return { setSymbol: (s) => { $('.ai-symbol').value = s; } };
}
