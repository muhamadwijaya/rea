// RENCANA JARVIS: JARVIS's own trading plan for an asset (said by voice and saved with set_trading_plan in
// app/jarvis_phone.py), shown at the top of the analysis PLAN tab, above the app's rule-based scenario.
import { api, esc, fmtPrice, on, timeAgo, toast } from './util.js';

const slots = new Set();  // one per market page's analysis view
const SIDE = { buy: ['BUY', 'up'], sell: ['SELL', 'down'], wait: ['TUNGGU', 'flat'] };

on('jarvis', (evt) => {
  if (evt?.type !== 'plan') return;
  for (const slot of [...slots]) {
    if (!slot.isConnected) slots.delete(slot);
    else if (slot.dataset.symbol === evt.symbol) render(slot);
  }
});

export function mountJarvisPlan(el, symbol, decimals) {
  if (!el) return;
  for (const slot of [...slots]) if (!slot.isConnected) slots.delete(slot);
  el.dataset.symbol = String(symbol || '').toUpperCase();
  el.dataset.decimals = Number.isInteger(decimals) ? String(decimals) : '';
  el.setAttribute('data-jv-skip', '');  // read_screen gives JARVIS its plan as data, not as this card's text
  slots.add(el);
  render(el);
}

async function render(el) {
  const symbol = el.dataset.symbol;
  let plan = null;
  try {
    plan = (await api(`/api/jarvis/plan?symbol=${encodeURIComponent(symbol)}`)).plan;
  } catch { /* JARVIS is not available here */ }
  if (el.dataset.symbol !== symbol) return;  // the view moved to another asset meanwhile
  if (!plan) {
    el.innerHTML = '';
    return;
  }
  const decimals = Number.isInteger(plan.decimals) ? plan.decimals : el.dataset.decimals === '' ? undefined : Number(el.dataset.decimals);
  const fmt = (v) => fmtPrice(v, decimals);
  const [word, cls] = SIDE[plan.direction] || SIDE.wait;
  const app = plan.app?.direction;
  const changed = SIDE[app] && app !== plan.direction ? `Mengubah skenario aplikasi: ${SIDE[app][0]} → ${word}` : '';
  const until = new Date(plan.validUntilTs * 1000).toLocaleString('id-ID', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false });
  el.innerHTML = `
    <div class="jvp jvp-${esc(plan.direction)}">
      <div class="jvp-head"><b>RENCANA JARVIS</b><span class="tag ${cls}">${word}</span>
        <span class="muted">TF ${esc(String(plan.timeframe || '').toUpperCase())} · ${esc(timeAgo(plan.createdTs))}</span>
        <button class="jvp-x" type="button" title="Hapus rencana JARVIS">✕</button></div>
      ${changed ? `<div class="jvp-changed">${esc(changed)}</div>` : ''}
      ${plan.direction === 'wait' ? '' : `<div class="plan-grid">
        <div><div class="k">ENTRY</div><div class="v accent">${fmt(plan.entry)}</div></div>
        <div><div class="k">STOP</div><div class="v down">${fmt(plan.stop)}</div></div>
        <div><div class="k">TARGET</div><div class="v up">${(plan.targets || []).map(fmt).join(' · ')}</div></div>
        <div><div class="k">RISK : REWARD</div><div class="v">${(plan.rr || []).map((r) => `1:${r}`).join(' · ')}</div></div>
      </div>`}
      <div class="jvp-why">${esc(plan.reason)}</div>
      ${plan.condition ? `<div class="jvp-cond"><b>Syarat:</b> ${esc(plan.condition)}</div>` : ''}
      <div class="note">Dibuat JARVIS (AI) dari data di layar, arah market dan kalender ekonomi. Berlaku sampai ${esc(until)}.
        Bukan saran investasi.</div>
    </div>`;
  el.querySelector('.jvp-x').onclick = async () => {
    try {
      await api('/api/jarvis/plan', { json: { symbol, action: 'clear' } });
      el.innerHTML = '';
    } catch (err) {
      toast('Rencana JARVIS', err.message);
    }
  };
}
