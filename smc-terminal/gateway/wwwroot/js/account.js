// Virtual account of the AI TRAINING charts: a balance and a lot size entered by the user. Money per trade comes from
// the engine's contract spec (units per lot, quote → account currency), the trade's result in R and its stop:
// profit = R × |entry − stop| × lot × units × conversion. Stored per viewer (localStorage); trades are not changed.

const KEY = 'sharx.account';
const DEFAULTS = {
  balance: { USD: 1000, IDR: 10_000_000 },
  lot: { forex: 0.1, kripto: 0.1, 'saham-as': 1, 'saham-idx': 10 },
};

export function loadAccount() {
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch { /* private window: defaults */ }
  return { balance: { ...DEFAULTS.balance, ...(saved.balance || {}) }, lot: { ...DEFAULTS.lot, ...(saved.lot || {}) } };
}

export function saveAccount(account) {
  try { localStorage.setItem(KEY, JSON.stringify(account)); } catch { /* per-viewer convenience only */ }
  window.dispatchEvent(new CustomEvent('sharx-account', { detail: account }));  // the other chart's inputs follow
}

/** The spec actually used: a cross without a rate falls back to the quote currency (no conversion invented). */
export function effectiveSpec(spec) {
  if (!spec) return null;
  if (spec.convert === 'cross' && !(spec.rate > 0)) return { ...spec, convert: 'same', account: spec.quote, fallback: true };
  return spec;
}

function conversion(spec, price) {
  if (spec.convert === 'cents') return 0.01;
  if (spec.convert === 'per_price') return price > 0 ? 1 / price : null;
  if (spec.convert === 'cross') return spec.rate;
  return 1;
}

/** Money at risk of one trade: the distance to its stop, for `lot` lots, in the account currency. */
export function riskMoney(t, spec, lot) {
  const c = conversion(spec, t.entry);
  return c == null ? null : Math.abs(t.entry - t.stop) * lot * spec.size * c;
}

/** Result of one closed trade in the account currency (null while it is not closed). */
export function tradeMoney(t, spec, lot) {
  if (t.r == null || !['win', 'loss', 'timeout'].includes(t.status)) return null;
  const c = conversion(spec, t.exit ?? t.entry);
  return c == null ? null : t.r * Math.abs(t.entry - t.stop) * lot * spec.size * c;
}

/** Equity of the virtual account over the closed trades, in the order they closed. */
export function accountStats(trades, spec, balance, lot) {
  const closed = trades.filter((t) => t.closed_t != null && tradeMoney(t, spec, lot) != null)
    .sort((a, b) => a.closed_t - b.closed_t);
  let equity = balance, peak = balance, maxDD = 0, maxDDpct = 0, bust = null, worst = 0;
  const risks = [];
  let counted = 0;
  for (const t of closed) {  // a blown account trades no more: the run stops at the trade that emptied it
    const m = tradeMoney(t, spec, lot);
    equity += m;
    counted += 1;
    worst = Math.min(worst, m);
    peak = Math.max(peak, equity);
    if (peak - equity > maxDD) { maxDD = peak - equity; maxDDpct = peak > 0 ? maxDD / peak : 0; }
    const r = riskMoney(t, spec, lot);
    if (r != null) risks.push(r);
    if (equity <= 0) { bust = counted; break; }
  }
  return {
    start: balance, end: equity, pnl: equity - balance, pct: balance > 0 ? (equity - balance) / balance : null,
    maxDD, maxDDpct, bust, trades: counted, worst,
    risk: risks.length ? risks.reduce((a, b) => a + b, 0) / risks.length : null,
  };
}

export function fmtMoney(v, currency, signed = false) {
  if (v == null || !Number.isFinite(v)) return '–';
  const digits = currency === 'IDR' || currency === 'JPY' ? 0 : 2;
  let text;
  try {
    text = new Intl.NumberFormat(currency === 'IDR' ? 'id-ID' : 'en-US', {
      style: 'currency', currency, minimumFractionDigits: digits, maximumFractionDigits: digits,
    }).format(Math.abs(v));
  } catch {  // a quote "currency" Intl does not know (e.g. cents): plain number + code
    text = `${Math.abs(v).toFixed(digits)} ${currency}`;
  }
  return `${v < 0 ? '−' : signed && v > 0 ? '+' : ''}${text}`;
}

const MARKET_ACCOUNT = { 'saham-idx': 'IDR' };

/**
 * BALANCE / LOT inputs for one market. `onChange()` runs after every valid edit (values are saved).
 * Returns { el, balance(), lot(), currency(spec), market(m) } — `market(m)` switches the lot remembered per market.
 */
export function accountInputs(onChange) {
  let account = loadAccount();
  let market = 'forex';
  const el = document.createElement('span');
  el.className = 'acct';
  el.innerHTML = `<label title="Saldo awal akun virtual (mata uang akun: USD, saham IDX: Rupiah)">BALANCE
      <input class="input acct-balance" type="number" min="0" step="any"><b class="acct-cur">USD</b></label>
    <label title="Ukuran posisi per trade dalam lot (forex 1 lot = 100.000 unit, emas 1 lot = 100 oz, saham 1 lot = 100 lembar, kripto 1 lot = 1 koin)">LOT
      <input class="input acct-lot" type="number" min="0" step="any"></label>`;
  const balanceInput = el.querySelector('.acct-balance');
  const lotInput = el.querySelector('.acct-lot');
  const currency = () => MARKET_ACCOUNT[market] || 'USD';
  const show = () => {
    balanceInput.value = account.balance[currency()];
    lotInput.value = account.lot[market] ?? 0.1;
    el.querySelector('.acct-cur').textContent = currency();
    lotInput.step = market === 'saham-idx' || market === 'saham-as' ? '1' : '0.01';
  };
  const read = () => {
    const b = Number(balanceInput.value), l = Number(lotInput.value);
    if (Number.isFinite(b) && b >= 0) account.balance[currency()] = b;
    if (Number.isFinite(l) && l > 0) account.lot[market] = l;
    saveAccount(account);
    onChange();
  };
  balanceInput.addEventListener('change', read);
  lotInput.addEventListener('change', read);
  window.addEventListener('sharx-account', (e) => {
    if (e.detail === account) return;
    account = { balance: { ...e.detail.balance }, lot: { ...e.detail.lot } };
    show();
    onChange();
  });
  show();
  return {
    el,
    balance: () => account.balance[currency()],
    lot: () => account.lot[market] ?? 0.1,
    market(m) { if (m && m !== market) { market = m; show(); } },
  };
}

/** One line for the charts: start → end balance, drawdown, lot size and the money at risk per trade. */
export function accountLine(stats, spec, lot) {
  if (!spec) return '';
  if (!stats) return '<span class="dim">AKUN: kurs konversi tidak tersedia</span>';
  const cur = spec.account;
  const units = lot * spec.size;
  const pct = (v) => (v == null ? '–' : `${v > 0 ? '+' : ''}${(v * 100).toFixed(1)}%`);
  return `<span class="acct-line">AKUN: <b>${fmtMoney(stats.start, cur)}</b> → <b class="${stats.pnl > 0 ? 'up' : stats.pnl < 0 ? 'down' : ''}">${fmtMoney(stats.end, cur)}</b>
    (${fmtMoney(stats.pnl, cur, true)}, ${pct(stats.pct)}) · max drawdown ${fmtMoney(-stats.maxDD, cur)} (${pct(-stats.maxDDpct)})
    · ${lot} lot = ${units.toLocaleString('id-ID', { maximumFractionDigits: 4 })} ${spec.unit}${stats.risk != null ? ` · risiko rata-rata ${fmtMoney(stats.risk, cur)}/trade` : ''}
    ${stats.bust ? ` · <b class="down">AKUN HABIS di trade ke-${stats.bust} — trade berikutnya tidak dihitung; kecilkan lot atau tambah balance</b>` : ''}${spec.fallback ? ` <span class="dim">(dalam ${spec.quote}: kurs ke USD tidak tersedia)</span>` : ''}</span>`;
}
