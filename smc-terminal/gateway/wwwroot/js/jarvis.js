// JARVIS tab: a voice AI assistant.
//   desktop (mode "local"): Mark LVII stays its own program; the gateway installs and runs it on this PC
//     (/api/jarvis/install|start|stop) and this tab shows its control panel (the dashboard it serves on port 8000),
//     signed in with the one-time key of Mark LVII's "Remote Control" button.
//   Android (mode "phone"): Mark LVII's assistant runs on the phone itself (jarvis-phone.js, from the Android patch).
import { $, esc, toast } from './util.js';

const SOURCE = 'https://github.com/FatihMakes/Mark-LVII';
const KEY = /^[A-HJKMNP-Z2-9]{8}$/;           // alphabet of Mark LVII pairing keys (no O, I, L, 0, 1)
const STORE_SIDE = 'sharx.jarvis.side';
const STORE_ZOOM = 'sharx.jarvis.zoom.local';
const ZOOMS = [0.75, 1, 1.25, 1.5, 1.75, 2, 2.5, 3];

let root = null;
let mode = null;            // 'local' | 'phone'
let phone = null;           // jarvis-phone.js once loaded (Android)
let status = null;
let seq = 0;                // last log line received
let timer = null;
let base = null;            // dashboard URL shown in the frame (…:8000/)

const el = (name) => root.querySelector(`[data-jv="${name}"]`);
const active = () => root?.classList.contains('active');
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};

async function call(path, body) {
  let res;
  try {
    res = await fetch(`/api/jarvis/${path}`, {
      method: body === undefined ? 'GET' : 'POST',
      headers: { Accept: 'application/json', ...(body === undefined ? {} : { 'Content-Type': 'application/json', 'X-Sharx-Action': '1' }) },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new Error('Layanan aplikasi tidak dapat dihubungi.');
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error(data?.message || `HTTP ${res.status}`);
  return data;
}

// ------------------------------------------------------------------ layout

function build() {
  root.innerHTML = `
    <div class="jv ${store.get(STORE_SIDE) === 'hidden' ? 'jv-collapsed' : ''}">
      <div class="panel jv-side">
        <div class="panel-head"><span class="diamond">◆</span> JARVIS · MARK LVII<span class="right"><span class="jv-chip" data-jv="chip">…</span></span></div>
        <div class="jv-body">
          <p class="jv-about">Asisten AI suara untuk PC (<a href="${SOURCE}" target="_blank" rel="noopener">Mark LVII</a>,
            Gemini Live): perintah suara dan ketik, kendali PC, file, rutinitas. Panel kendalinya tampil di kanan.</p>
          <div class="jv-actions" data-jv="local-actions" hidden>
            <button class="btn-primary" data-jv="install">PASANG</button>
            <button class="btn" data-jv="start">▶ JALANKAN</button>
            <button class="btn jv-stop" data-jv="stop">■ HENTIKAN</button>
            <button class="btn jv-stop" data-jv="cancel">BATALKAN</button>
          </div>
          <dl class="jv-facts" data-jv="facts"></dl>
          <form class="jv-form" data-jv="pair" autocomplete="off">
            <label>KODE REMOTE CONTROL <input data-jv="key" maxlength="12" placeholder="8 karakter dari HUD" spellcheck="false" autocapitalize="characters"></label>
            <button class="btn" type="submit">MASUK</button>
          </form>
          <details class="jv-sec" data-jv="phonesec"><summary>KONTROL HP <span data-jv="phonechip"></span></summary>
            <form class="jv-form jv-col" data-jv="phoneform" autocomplete="off">
              <label>ALAMAT HP <input data-jv="phoneaddr" inputmode="url" placeholder="192.168.1.7:8010" spellcheck="false"></label>
              <label>KODE DARI HP <input data-jv="phonekey" maxlength="12" placeholder="8 karakter" spellcheck="false" autocapitalize="characters"></label>
              <button class="btn-primary" type="submit">PASANGKAN HP</button>
            </form>
            <div class="jv-note" data-jv="phoneinfo"></div>
            <form class="jv-form" data-jv="phonecmd" autocomplete="off">
              <label>PERINTAH UNTUK HP <input data-jv="phonetext" placeholder="mis. pasang pengingat jam 8 cek emas" maxlength="2000"></label>
              <button class="btn" type="submit">KIRIM</button>
            </form>
            <div class="jv-note" data-jv="phonereply"></div>
            <button class="btn jv-stop" data-jv="phoneunpair">LUPAKAN HP</button>
          </details>
          <ol class="jv-steps" data-jv="steps"></ol>
          <div class="jv-logbox" data-jv="logbox" hidden>
            <div class="jv-logtitle">LOG PEMASANGAN &amp; JARVIS</div>
            <pre class="jv-log" data-jv="log"></pre>
          </div>
        </div>
      </div>
      <div class="panel jv-main">
        <div class="panel-head"><button class="jv-toggle" data-jv="side" title="Tampilkan / sembunyikan pengaturan">☰</button>
          <span class="diamond">◆</span> PANEL KENDALI JARVIS <span class="sub" data-jv="where"></span>
          <span class="right"><span class="jv-zoom" title="Ukuran panel JARVIS"><button data-jv="zoom-out">A−</button><b data-jv="zoom">100%</b><button data-jv="zoom-in">A+</button></span>
            <button class="btn" data-jv="reload">⟳ MUAT ULANG</button>
            <a class="btn" data-jv="open" target="_blank" rel="noopener" hidden>BROWSER ↗</a></span></div>
        <div class="jv-stage">
          <!-- the panel is another origin (port 8000), so the sandbox still stops it from navigating this app even with
               allow-same-origin, which the panel needs for its sign-in storage -->
          <iframe data-jv="frame" title="Panel kendali JARVIS" referrerpolicy="no-referrer"
            sandbox="allow-scripts allow-same-origin allow-forms allow-modals allow-downloads allow-popups allow-popups-to-escape-sandbox"></iframe>
          <div class="jv-empty" data-jv="empty"></div>
        </div>
      </div>
    </div>`;

  el('install').addEventListener('click', () => act('install'));
  el('start').addEventListener('click', () => act('start'));
  el('stop').addEventListener('click', () => act('stop'));
  el('cancel').addEventListener('click', () => act('cancel'));
  el('zoom-out').addEventListener('click', () => zoom(-1));
  el('zoom-in').addEventListener('click', () => zoom(1));
  el('reload').addEventListener('click', () => (base ? frame(base) : refresh()));
  el('phoneform').addEventListener('submit', async (e) => {
    e.preventDefault();
    const key = el('phonekey').value.trim().toUpperCase().replace(/[\s-]/g, '');
    if (!KEY.test(key)) return toast('Kode tidak valid', 'Kode dari HP terdiri dari 8 huruf/angka (tanpa O, I, L, 0, 1).');
    try {
      const r = await call('phone/pair', { address: el('phoneaddr').value, key });
      el('phonekey').value = '';
      toast('JARVIS', r.message);
    } catch (err) { toast('Kontrol HP', err.message); }
    refresh();
  });
  el('phonecmd').addEventListener('submit', async (e) => {
    e.preventDefault();
    const text = el('phonetext').value.trim();
    if (!text) return;
    el('phonereply').textContent = 'Mengirim ke HP… (JARVIS di HP menjawab dalam beberapa detik)';
    try {
      const r = await call('phone/command', { text });
      el('phonereply').textContent = `HP: ${r.message}`;
      el('phonetext').value = '';
    } catch (err) { el('phonereply').textContent = err.message; }
  });
  el('phoneunpair').addEventListener('click', async () => {
    try { await call('phone/unpair', {}); } catch (err) { toast('Kontrol HP', err.message); }
    refresh();
  });
  el('side').addEventListener('click', () => {
    const box = root.querySelector('.jv');
    box.classList.toggle('jv-collapsed');
    store.set(STORE_SIDE, box.classList.contains('jv-collapsed') ? 'hidden' : 'shown');
  });
  el('pair').addEventListener('submit', (e) => {
    e.preventDefault();
    const key = el('key').value.trim().toUpperCase().replace(/[\s-]/g, '');
    if (!KEY.test(key)) return toast('Kode tidak valid', 'Kode Remote Control terdiri dari 8 huruf/angka (tanpa O, I, L, 0, 1).');
    if (!base) return toast('Panel JARVIS belum tersambung', 'Jalankan JARVIS dulu.');
    el('key').value = '';
    // the key is used once: the dashboard's own auto-login page signs in and remembers this device until JARVIS restarts
    frame(`${base}auto-login?key=${encodeURIComponent(key)}`);
  });
}

// ------------------------------------------------------------------ dashboard frame

/** The panel is drawn at 1/zoom of the stage and scaled up (Mark LVII's panel is laid out for a phone). */
function zoom(step = 0) {
  const saved = Number(store.get(STORE_ZOOM));
  let z = ZOOMS.includes(saved) ? saved : 1;
  z = ZOOMS[Math.max(0, Math.min(ZOOMS.length - 1, ZOOMS.indexOf(z) + step))];
  if (step) store.set(STORE_ZOOM, String(z));
  root.querySelector('.jv-stage').style.setProperty('--jv-zoom', String(z));
  el('zoom').textContent = `${Math.round(z * 100)}%`;
}

/** Navigates the panel frame; the frame then moves on by itself (login → panel), so it is only navigated again when
 *  the dashboard address changes, after a pairing key, or on MUAT ULANG. */
function frame(url) {
  el('frame').src = url || 'about:blank';
  root.querySelector('.jv-stage').classList.toggle('jv-live', !!url);
}

function showDashboard(url, where) {
  if (url !== base) {
    base = url;
    frame(url);
  }
  el('where').textContent = where || '';
  const open = el('open');
  open.hidden = !url;
  if (url) open.href = url;
}

function empty(html) {
  el('empty').innerHTML = html;
}

// ------------------------------------------------------------------ desktop: install / run on this PC

async function act(path) {
  const buttons = root.querySelectorAll('.jv-actions button');
  buttons.forEach((b) => { b.disabled = true; });
  try {
    const r = await call(path, {});
    toast('JARVIS', r?.message || '');
  } catch (err) {
    toast('JARVIS', err.message);
  } finally {
    buttons.forEach((b) => { b.disabled = false; });
    refresh();
  }
}

function chip(text, cls) {
  const c = el('chip');
  c.textContent = text;
  c.className = `jv-chip ${cls || ''}`;
}

const when = (iso) => (iso ? new Date(iso).toLocaleString('id-ID', { hour12: false }) : '–');

function renderLocal(s) {
  el('local-actions').hidden = false;
  el('logbox').hidden = false;
  const job = s.job;
  const installing = job?.state === 'running';
  const proc = s.process || {};
  const running = proc.state === 'running' || proc.state === 'stopping';
  const dash = s.dashboard || {};
  const external = !running && (dash.state === 'online' || dash.state === 'https');

  el('install').textContent = s.installed ? 'PERBARUI' : 'PASANG';
  el('install').hidden = installing || running;
  el('cancel').hidden = !installing;
  el('start').hidden = installing || running || external || !s.installed;
  el('stop').hidden = !running;

  if (installing) chip(`MEMASANG · ${job.step.toUpperCase()}`, 'busy');
  else if (dash.state === 'online') chip(external ? 'AKTIF · DI LUAR SHARX' : 'AKTIF', 'on');
  else if (dash.state === 'https') chip('AKTIF · HTTPS', 'warn');
  else if (running) chip('MEMULAI…', 'busy');
  else if (s.installed) chip('SIAP', '');
  else chip('BELUM TERPASANG', 'off');

  const rows = [
    ['Mark LVII', s.install ? `${s.install.commit ? `commit ${s.install.commit.slice(0, 7)} · ` : ''}${when(s.install.installedAt)}${s.install.setupOk ? '' : ' · setup belum selesai'}` : 'belum terpasang'],
    ['Python', s.install?.pythonVersion || '3.11–3.13 (dicari otomatis)'],
    ['Folder', s.folder],
    ['Proses', running ? `berjalan · pid ${proc.pid}` : proc.state === 'exited' ? `berhenti (exit ${proc.exitCode ?? '?'}) · ${when(proc.exited)}` : external ? 'dijalankan di luar SHARX' : 'tidak berjalan'],
    ['Dashboard', dash.state === 'online' ? dash.url : dash.state === 'https' ? `HTTPS ${dash.url || ''}` : dash.state === 'other' ? (dash.detail || 'port dipakai program lain') : 'belum aktif'],
    ['Alamat untuk HP', dash.lanUrl ? dash.lanUrl.replace(/^http:\/\//, '').replace(/\/$/, '') : 'tidak ada jaringan lokal'],
  ];
  if (job?.state === 'failed' || job?.state === 'canceled') rows.splice(1, 0, ['Pemasangan', job.error || job.state]);
  el('facts').innerHTML = rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('');

  const phone = s.phone || {};
  el('phonechip').textContent = phone.paired ? '· TERPASANG' : '';
  el('phonechip').className = phone.paired ? 'up' : '';
  if (phone.address && !el('phoneaddr').value) el('phoneaddr').value = phone.address;
  el('phonecmd').hidden = !phone.paired;
  el('phoneunpair').hidden = !phone.paired;
  el('phoneinfo').innerHTML = phone.paired
    ? `HP <b>${esc(phone.address)}</b> terpasang. JARVIS di PC bisa menyuruh HP lewat suara
       (${phone.pluginInstalled ? 'plugin phone_command terpasang' : 'plugin terpasang saat PASANG/PERBARUI'}; jalankan ulang JARVIS bila baru dipasangkan).`
    : 'Di HP: tab JARVIS → KONTROL PC → centang "Izinkan PC mengontrol HP ini", lalu isi alamat dan kode yang tampil di HP.';

  el('steps').innerHTML = [
    `<b>PASANG</b> (sekali): mengunduh Mark LVII, membuat virtualenv Python 3.11–3.13 khusus JARVIS, lalu menjalankan
     setup.py-nya (dependensi + browser Playwright, ±1–2 GB, 5–20 menit). Python tidak dipasang otomatis: bila belum
     ada, pasang Python 3.12 dari python.org.`,
    `<b>JALANKAN</b>: jendela HUD JARVIS terbuka. Pertama kali, tempel Gemini API key gratis
     (<a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener">aistudio.google.com/apikey</a>) di layar
     setup HUD. Key disimpan Mark LVII di folder JARVIS, tidak dikirim ke SHARX.`,
    `Di HUD tekan <b>Remote Control</b> → ketik kode 8 karakternya di <b>KODE REMOTE CONTROL</b> → panel kendali tampil di
     kanan. Windows bisa meminta izin firewall saat itu: izinkan bila HP juga dipakai.`,
    `<b>HP Android</b>: JARVIS jalan sendiri di HP. Untuk mengontrol PC ini dari HP: tab JARVIS di HP → KONTROL PC →
     alamat <b>${esc(dash.lanUrl ? dash.lanUrl.replace(/^http:\/\//, '').replace(/\/$/, '') : '(IP PC):8000')}</b> + kode Remote
     Control (HP dan PC di Wi-Fi yang sama).`,
    `<b>KONTROL HP</b>: PC ini juga bisa menyuruh JARVIS di HP (pasang sekali dengan kode dari HP).`,
    `JARVIS yang dijalankan dari tab ini ikut ditutup saat SHARX ditutup.`,
  ].map((t) => `<li>${t}</li>`).join('');

  if (s.log?.length) {
    const log = el('log');
    const stick = log.scrollTop + log.clientHeight >= log.scrollHeight - 8;
    log.textContent = (log.textContent ? `${log.textContent}\n` : '') + s.log.join('\n');
    if (log.textContent.length > 60000) log.textContent = log.textContent.slice(-50000);
    if (stick) log.scrollTop = log.scrollHeight;
  }
  seq = s.logSeq ?? seq;

  if (dash.state === 'online') {
    showDashboard(dash.url, external ? 'JARVIS di PC ini (dijalankan di luar SHARX)' : 'JARVIS di PC ini');
  } else {
    showDashboard(null);
    empty(dash.state === 'https'
      ? `<b>Dashboard JARVIS memakai HTTPS</b> dengan sertifikat buatan sendiri, jadi tidak bisa tampil di dalam aplikasi.
         Buka <a href="${esc(dash.url || '#')}" target="_blank" rel="noopener">di browser</a>, atau matikan
         <code>"dashboard_https"</code> di <code>config/api_keys.json</code> folder JARVIS lalu jalankan ulang JARVIS.`
      : dash.state === 'other'
        ? `<b>${esc(dash.detail || 'Port 8000 dipakai program lain.')}</b><br>Tutup program itu, lalu jalankan JARVIS.`
        : running
          ? `<b>JARVIS sedang dimulai…</b><br>Bila ini pertama kali, tempel Gemini API key di jendela HUD JARVIS. Panel kendali
             tampil di sini setelah JARVIS tersambung.`
          : installing
            ? `<b>Memasang JARVIS…</b><br>Lihat log di kiri. Langkah sekarang: ${esc(job.step)}.`
            : s.installed
              ? '<b>JARVIS belum berjalan.</b><br>Tekan ▶ JALANKAN.'
              : '<b>JARVIS belum terpasang.</b><br>Tekan PASANG (sekali).');
  }
}

// ------------------------------------------------------------------ refresh

async function refresh() {
  clearTimeout(timer);
  timer = null;
  if (!active()) return;
  let delay = 4000;
  try {
    if (mode === 'phone') return phone.showPhone();
    status = await call(`status?after=${seq}`);
    if (mode === null) {
      mode = status.mode;
      if (mode === 'phone') {
        phone = await import('./jarvis-phone.js');
        phone.mountPhone(root, status);
        return phone.showPhone();
      }
      zoom();
    }
    renderLocal(status);
    const busy = status.job?.state === 'running' || status.process?.state === 'running' && status.dashboard?.state !== 'online';
    delay = busy ? 1500 : 4000;
  } catch (err) {
    chip('ERROR', 'off');
    empty(`<b>${esc(err.message)}</b>`);
    delay = 8000;
  }
  if (active()) timer = setTimeout(refresh, delay);
}

export default {
  init() {
    root = $('#tab-jarvis');
    build();
    empty('<b>Memuat…</b>');
  },
  show() {
    refresh();
  },
};
