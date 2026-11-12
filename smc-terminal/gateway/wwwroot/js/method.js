// Metodologi: every rule the engine applies, split over tabs (no page scroll). First tab is the 7-step walkthrough.
import { $, esc } from './util.js';

const STEPS = [
  ['STRUKTUR', 'Baca struktur pasar', 'Engine mencari swing high/low (pivot fractal) di dua skala: swing (besar) dan internal (kecil). Setiap pivot diberi label HH, HL, LH atau LL sehingga terlihat apakah pasar membuat puncak & lembah yang makin tinggi atau makin rendah.'],
  ['BOS/CHoCH', 'Deteksi break struktur', 'Close yang menembus swing high/low terakhir dicatat. Searah tren = BOS (Break of Structure, tren berlanjut). Berlawanan tren = CHoCH (Change of Character, tanda awal pembalikan). Tidak ada look-ahead: pivot baru dianggap ada setelah bar konfirmasinya tutup.'],
  ['ORDER BLOCK', 'Tandai order block', 'Setiap break menghasilkan order block: candle asal dorongan (low terendah sebelum break bullish / high tertinggi sebelum break bearish). OB dilacak sampai disentuh ulang (tapped) atau ditembus close (invalid).'],
  ['FVG', 'Temukan imbalance', 'Fair Value Gap = celah tiga candle (low candle ke-3 di atas high candle ke-1, atau sebaliknya) yang cukup besar terhadap ATR. Engine mencatat persentase terisi dan kapan gap tertutup penuh.'],
  ['LIKUIDITAS', 'Petakan likuiditas', 'Equal highs/lows (EQH/EQL) dan swing high/low yang belum tersentuh adalah kolam stop-loss (buy-side & sell-side liquidity). Harga cenderung bergerak ke sana — dipakai sebagai target.'],
  ['ZONA P/D', 'Premium vs discount', 'Dealing range = swing high & low aktif. Di atas 50% = premium (mahal, area jual), di bawah 50% = discount (murah, area beli). Strong/weak high-low ditentukan dari arah tren swing.'],
  ['BIAS', 'Skor bias & skenario', 'Tujuh komponen (struktur swing & internal, CHoCH terbaru, EMA 50/200, posisi premium/discount, momentum RSI, reaksi OB) digabung menjadi skor −100…+100, diperkuat konfluensi 1D/4H/1H, lalu dibuat skenario entry, invalidasi dan target likuiditas.'],
];
const STEP_POS = [[8, 22], [36, 22], [64, 22], [92, 22], [78, 78], [50, 78], [22, 78]];

function renderSteps() {
  const root = $('#method');
  const nodes = STEP_POS.map(([x, y], i) => `
    <div class="step-node ${i === step ? 'active' : i < step ? 'done' : ''}" data-step="${i}" style="left:${x}%;top:${y}%">
      <div class="dot">${i + 1}</div><div class="lbl">${STEPS[i][0]}</div>
    </div>`).join('');
  const pts = STEP_POS.map(([x, y]) => [x, y - 9]);
  const path = `M${pts[0][0]} ${pts[0][1]} L${pts[3][0]} ${pts[3][1]} C${pts[3][0] + 8} ${pts[3][1]}, ${pts[3][0] + 8} ${pts[4][1]}, ${pts[4][0]} ${pts[4][1]} L${pts[6][0]} ${pts[6][1]}`;
  root.querySelector('.steps').innerHTML = `<svg viewBox="0 0 100 100" preserveAspectRatio="none"><path d="${path}" fill="none" stroke="#3b4a66" stroke-width="0.6" stroke-dasharray="0.6 1.4" vector-effect="non-scaling-stroke"/></svg>${nodes}`;
  const [, title, text] = STEPS[step];
  root.querySelector('.step-card').innerHTML = `
    <div class="top"><span class="n">LANGKAH ${step + 1} / 7</span>
      <span class="step-nav"><button data-d="-1" aria-label="sebelumnya">‹</button><button data-d="1" aria-label="berikutnya">›</button></span></div>
    <h3>${esc(title)}</h3><p>${esc(text)}</p>`;
}

function setStep(i, manual) {
  step = (i + STEPS.length) % STEPS.length;
  renderSteps();
  if (manual) { clearInterval(stepTimer); stepTimer = setInterval(() => setStep(step + 1), 9000); }
}


const CARDS = [
  ['1 · Struktur pasar (pivot)', `<p>Pivot = bar yang high-nya tertinggi (atau low-nya terendah) di jendela <code>2×N+1</code> bar. N swing / internal per timeframe: 15M 15/4 · 1H 12/4 · 4H 10/3 · 1D 10/3 · 1W 8/3. Pivot baru diketahui setelah N bar di kanannya tutup (tanpa look-ahead).</p><p>Label: <b>HH</b> higher high, <b>LH</b> lower high, <b>HL</b> higher low, <b>LL</b> lower low.</p>`],
  ['2 · BOS & CHoCH', `<p>Saat <b>close</b> menembus pivot high/low aktif terakhir:</p><ul><li><b>BOS</b> — searah tren sebelumnya (kelanjutan).</li><li><b>CHoCH</b> — berlawanan tren (perubahan karakter / potensi reversal).</li></ul><p>Dihitung terpisah untuk struktur swing (besar) dan internal (kecil, ditandai <code>i</code>).</p>`],
  ['3 · Order block', `<p>Setiap break membuat OB dari candle dengan low terendah (break bullish) atau high tertinggi (break bearish) antara pivot yang ditembus dan bar break. Candle > 2 ATR dipangkas ke body.</p><ul><li><b>Tapped</b> — harga kembali masuk ke OB.</li><li><b>Invalid</b> — close menembus sisi jauh OB.</li></ul>`],
  ['4 · Fair Value Gap', `<p>Bullish FVG: <code>low[i] &gt; high[i-2]</code> dan candle tengah close di atas <code>high[i-2]</code>, ukuran ≥ 0.15 ATR. Bearish kebalikannya. Persentase terisi dilacak; FVG dianggap tertutup saat harga menyentuh sisi jauhnya.</p>`],
  ['5 · Likuiditas', `<p><b>EQH/EQL</b>: dua pivot (panjang 3) berurutan dengan selisih ≤ 0.1 ATR — kolam stop-loss. <b>BSL/SSL</b>: swing high di atas / low di bawah harga yang belum tersentuh. Dipakai sebagai target skenario.</p>`],
  ['6 · Premium / Discount', `<p>Dealing range = pivot swing high & low terakhir (diperluas oleh ekstrem sesudahnya). Posisi &gt; 55% = <b>premium</b>, &lt; 45% = <b>discount</b>, di antaranya <b>equilibrium</b>. Tren bullish → range low = <i>strong low</i>, range high = <i>weak high</i> (target).</p>`],
  ['7 · Skor bias (−100…+100)', `<table class="kv"><tr><td>Tren swing</td><td>±30</td></tr><tr><td>Tren internal</td><td>±15</td></tr><tr><td>CHoCH ≤ 12 bar (swing/internal)</td><td>±15 / ±10</td></tr><tr><td>EMA50 &amp; EMA200</td><td>±15</td></tr><tr><td>Posisi premium/discount</td><td>±10</td></tr><tr><td>Momentum RSI(14)</td><td>±10</td></tr><tr><td>Harga di dalam OB aktif</td><td>±5</td></tr></table><p>≥ +50 NAIK KUAT · ≥ +20 NAIK · −20…+20 SIDEWAYS · ≤ −20 TURUN · ≤ −50 TURUN KUAT. MTF: 1D 45% · 4H 35% · 1H 20%.</p>`],
  ['Skenario trading', `<p>Searah bias: zona entry = OB aktif / FVG terbuka terdekat di arah pullback. Stop = sisi jauh zona ± 0.3 ATR. Target = likuiditas terdekat (BSL untuk buy, SSL untuk sell), lalu ujung dealing range. RR dihitung otomatis. Bias netral → tidak ada skenario.</p>`],
  ['Arah market', `<p>Untuk DXY, mayor FX, emas, minyak, SPX, NDX, IHSG, VIX, US10Y, BTC dan USD/IDR: skor = 55% bias 1D + 45% bias 4H. <b>Kekuatan mata uang</b> = rata-rata skor semua pair G8 (base +, quote −). <b>Regime</b> risk-on/off menimbang indeks saham, kripto, IHSG (+) dan VIX, JPY, CHF, emas (−). Gateway menambah sentimen berita 12 jam & event high-impact 48 jam.</p>`],
  ['Sentimen berita', `<p>Leksikon finansial Inggris + Indonesia (mis. <i>surge, plunge, menguat, anjlok</i>) dengan penanganan negasi (<i>not, tidak, belum</i>). Skor = tanh(Σ bobot / 3). Kata yang cocok ditampilkan di tooltip label sentimen. Aset ditandai dari kata kunci (Fed → USD, emas → GOLD, IHSG → IDX, …).</p>`],
  ['Sumber data & keterbatasan', `<ul><li>Analisa SMC: Yahoo Finance via <code>yfinance</code> — bisa tertunda tergantung bursa; emas/perak memakai kontrak futures sebagai proksi.</li><li>Universe ±1.600 aset (S&amp;P 500 + saham populer & ETF, seluruh saham BEI yang tersedia di Yahoo, ±80 kripto teratas, 44 pair forex, komoditas) dibangun oleh <code>engine/tools/build_universe.py</code>. Harga open/last semua aset diperbarui bergiliran tiap ±2 menit; SMC 4H/1H untuk 57 aset inti, SMC 1D untuk seluruh universe tiap 20 menit.</li><li>Chart realtime, teknikal & screener pasar penuh: widget resmi TradingView.</li><li>Berita: RSS publik (dikonfigurasi di <code>gateway/appsettings.json</code>).</li><li>Kalender: ekspor JSON mingguan ForexFactory.</li><li>Semua output adalah hasil aturan otomatis, <b>bukan saran investasi</b>.</li></ul>`],
  ['Fundamental: rilis data ekonomi', `<p>Untuk tiap event kalender (CPI, PPI, PMI, GDP, NFP, pengangguran, suku bunga, …) engine menentukan sifat indikator (mis. pengangguran lebih tinggi = buruk bagi mata uang). Setelah rilis, headline berita yang menyebut indikator & negaranya (≤ 24 jam) dibaca: angka <i>actual</i> hanya diambil bila menempel pada nama indikator; bila tidak ada angka, dipakai mayoritas isyarat (di atas/di bawah ekspektasi, hot/cool). Arah mata uang lalu <b>diverifikasi</b> dengan pergerakan pair proksi 60 menit setelah rilis. Akurasi = terkonfirmasi / (terkonfirmasi + berlawanan), dihitung sekali per rilis — angka nyata, bukan klaim.</p>`],
  ['Outlook gabungan & AI', `<p>Outlook aset = 40% bias SMC multi-timeframe + 35% fundamental (skor mata uang dari rilis data, dipetakan ke pair/emas/saham/kripto/IHSG) + 25% sentimen berita yang ditandai ke aset itu. <b>Analisa AI (opsional)</b> menilai arah secara <b>fundamental</b> (tanpa chart): berita & sentimennya, kalender ekonomi (rilis + jadwal 7 hari), laporan keuangan nyata emiten (pendapatan, laba, kejutan EPS, valuasi), kinerja harga (return, high/low 52 minggu, relatif ke benchmark) dan rencana ke depan (jadwal laporan + konsensus, target analis). Lalu engine <b>mencocokkan dengan bias SMC 1D/4H/1H</b>: searah → keyakinan naik 5 + 0,2×|skor SMC| (maks. +20), berlawanan → turun sebesar itu, SMC belum jelas → tetap. Keyakinan akhir maks. 95; sinyal BELI/JUAL bila ≥ 55, selain itu NETRAL. Provider default <b>OpenRouter</b> memakai model gratis saja: model terbaik dicoba dulu, dan bila sedang dibatasi, tidak tersedia, lambat, atau jawabannya tidak valid, model itu dijeda dan aplikasi otomatis pindah ke model gratis berikutnya (yang terakhir berhasil dipakai lebih dulu). Pilihan lain: Anthropic Claude atau API apa pun yang kompatibel OpenAI. Jawaban setiap model divalidasi sebelum ditampilkan; tidak ada model yang dapat menjamin akurasi 99%.</p>`],
  ['Penandaan berita per aset', `<p>Headline ditautkan ke aset lewat ticker huruf besar (<code>$NVDA</code>, <code>BBCA</code>), nama perusahaan/koin, pair (<code>EUR/USD</code>) dan alias (yen, rupiah, emas, minyak, S&amp;P 500, IHSG …) beserta tanda arah (mis. "yen menguat" = USDJPY turun). Tag inilah yang mengambang 3 menit di orbs dan dihitung untuk "condong ke mana".</p>`],
  ['Halaman per pasar', `<p>FOREX, SAHAM AS, SAHAM IDX dan KRIPTO masing-masing punya Analisa SMC, Screener, Berita dan Arah Market sendiri. Instrumen dibagi oleh engine (field <code>market</code> di <code>config/watchlist.json</code>); berita dibagi oleh gateway dari kategori feed + aset yang disebut di headline (mis. berita forex yang menyebut Bitcoin ikut tampil di KRIPTO).</p>`],
  ['Arsitektur', `<p><b>Aplikasi desktop (C# WinForms + WebView2)</b> — jendela sendiri, menjalankan gateway di dalam proses. <b>Gateway C# (ASP.NET Core 8)</b> — API, agregasi RSS realtime, kalender, alert, push SignalR, serta mengelola engine Python sebagai proses tersembunyi (restart otomatis bila crash). <b>Engine Python (FastAPI)</b> — data harga, SMC, skor bias, screener, arah market, sentimen. Log: folder <code>logs/</code>.</p>`],
  ['Virtual trading sinyal SMC', `<p>Setiap break struktur (CHoCH/BOS) membuat sinyal dari order block yang memicunya: entry limit di OB (tengah atau tepi), stop di balik OB ± k·ATR, target = likuiditas yang dibuat impuls atau RR tetap — tidak pernah kurang dari 1:1. Semua keputusan kausal: hanya memakai bar sampai saat itu.</p>
    <p><b>Timestamp</b>: bar saat sinyal muncul, waktu sistem pertama mendeteksinya, waktu kedaluwarsa, waktu entry terpicu dan waktu selesai. <b>Batal otomatis</b> sebelum entry bila struktur berbalik, harga lebih dulu mencapai target, harga close menembus stop, atau sinyal kedaluwarsa. Setelah entry: PROFIT di target, LOSS di stop (satu bar menyentuh keduanya = LOSS), atau ditutup karena batas waktu. Hasil dicatat dalam R di jurnal lokal.</p>`],
  ['Upgrade SMC otomatis', `<p><b>Grup pasar</b>: riwayat hingga 150 aset satu pasar &amp; timeframe digabung dan dibagi menurut waktu — 60% awal untuk mengurutkan ±1.150 kombinasi parameter SMC, 20% berikutnya untuk memilih, 20% terakhir (tidak ikut memilih) untuk melaporkan hasil. <b>Per aset</b>: kombinasi yang sama diuji pada 70% riwayat lalu diverifikasi pada 30% terakhir; parameter aset dipakai hanya bila batas bawah win rate (Wilson 90%) out-of-sample lebih tinggi daripada parameter yang diwarisi, ekspektasi tetap positif dan jumlah trade cukup. Pewarisan: upgrade aset → grup → default. Diulang tiap 6 jam; tiap upgrade diberi versi dan dicatat.</p>
    <p><b>Terus perbaiki</b>: selama belum mencapai tujuan, pencarian SMC diperluas otomatis (sampai 8.640 kombinasi); di training live, putaran diulang sampai target atau tidak ada perbaikan, lalu ujian akhir sekali di 20% candle terakhir yang tidak pernah dipakai belajar. Hasil &lt; 30 trade ditandai sampel kecil.</p>
    <p><b>Training live 1 aset</b>: pilih aset dan jumlah candle; chart dimulai dari candle terlama dan AI berlatih maju — belajar ulang tiap ±10% candle hanya dari candle sebelumnya lalu men-trading bagian berikutnya — diputar ×1–×7. Setiap trade di chart adalah uji tanpa melihat masa depan.</p>
    <p><b>Training Lab &amp; multi-timeframe</b>: pilih pasar, timeframe (5M–1W) dan jumlah candle, lalu latih secepat CPU. Setiap trade disimpan di kamus trade bersama tren dua timeframe di atasnya saat itu (tanpa melihat masa depan); win rate saat TF tinggi searah vs berlawanan diukur, dan mode EXPERT hanya mengambil trade terbukti yang searah multi-timeframe. Pemicu 1M = pola candlestick 1 menit dengan rekam jejak per aset.</p>
    <p><b>Riwayat panjang</b>: tuner belajar dari 10 tahun bar harian, 730 hari bar 1 jam/4 jam dan 60 hari bar 15 menit, sehingga bukti out-of-sample per aset beberapa kali lebih banyak. <b>Replay ×1–×7</b> di tab LIVE CHART memutar ulang data uji candle demi candle untuk melihat hasil setup dengan cepat — hasil backtest yang diputar ulang, bukan trade live.</p>
    <p><b>Konfluensi &amp; manajemen</b>: tuner juga mencoba syarat sweep likuiditas (ujung leg menembus low/high yang belum tersentuh lalu close kembali) dan/atau FVG di leg break, serta TP1 50% di +1R dengan stop pindah ke entry. <b>Mode presisi</b> NORMAL ≥ 50%, KETAT ≥ 65%, SANGAT KETAT ≥ 80% (batas bawah win rate terbukti): makin ketat, win rate makin tinggi tetapi sinyal makin jarang.</p>
    <p><b>Terkualifikasi vs bayangan</b>: sinyal dihitung sebagai trade AI hanya bila parameternya sudah terbukti (batas bawah win rate ≥ 50% dan ekspektasi positif); sisanya dicatat sebagai trade bayangan untuk belajar. Angka "semua pasangan" bisa turun saat banyak aset belum terbukti ikut dilatih — itu pengenceran, bukan sinyal terbukti yang memburuk. Akurasi terukur ini juga menentukan bobot SMC saat dicocokkan dengan analisa AI.</p>
    <p>Target 70% dan tujuan 99% ditampilkan sebagai arah perbaikan; yang dilaporkan selalu hasil terukur. Tidak ada metode yang dapat menjamin 99% di pasar.</p>`],
];

// Card titles grouped into tabs.
const TABS = [
  ['cara', 'CARA KERJA · 7 LANGKAH', null],
  ['struktur', 'STRUKTUR & BOS/CHoCH', ['1 · Struktur pasar (pivot)', '2 · BOS & CHoCH']],
  ['zona', 'OB · FVG · LIKUIDITAS', ['3 · Order block', '4 · Fair Value Gap', '5 · Likuiditas', '6 · Premium / Discount']],
  ['skor', 'SKOR & SKENARIO', ['7 · Skor bias (−100…+100)', 'Skenario trading']],
  ['arah', 'ARAH MARKET & BERITA', ['Arah market', 'Sentimen berita', 'Penandaan berita per aset', 'Halaman per pasar']],
  ['fund', 'FUNDAMENTAL & AI', ['Fundamental: rilis data ekonomi', 'Outlook gabungan & AI']],
  ['training', 'AI TRAINING', ['Virtual trading sinyal SMC', 'Upgrade SMC otomatis']],
  ['data', 'DATA & ARSITEKTUR', ['Sumber data & keterbatasan', 'Arsitektur']],
];

let step = 0;
let stepTimer;

export default {
  init() {
    const byTitle = Object.fromEntries(CARDS.map(([t, body]) => [t, body]));
    const card = (t) => `<div class="panel"><div class="panel-body"><h3>${t}</h3>${byTitle[t]}</div></div>`;
    $('#method').innerHTML = `
      <div class="ptabs">${TABS.map(([k, l], i) => `<button data-t="${k}" class="${i ? '' : 'active'}">${l}</button>`).join('')}</div>
      ${TABS.map(([k, , titles], i) => `<div class="pane ${i ? '' : 'active'}" data-t="${k}"><div class="panel-body">${titles
        ? `<div class="method-grid">${titles.map(card).join('')}</div>`
        : `<div class="steps-wrap"><div class="steps"></div><div class="step-card"></div></div>
           <p class="note" style="margin-top:22px">Klik nomor langkah untuk detail. Rincian aturan & bobot ada di tab berikutnya. Semua output adalah hasil aturan otomatis, bukan saran investasi.</p>`}</div></div>`).join('')}`;
    const root = $('#method');
    root.querySelector('.steps').addEventListener('click', (e) => { const n = e.target.closest('[data-step]'); if (n) setStep(+n.dataset.step, true); });
    root.querySelector('.step-card').addEventListener('click', (e) => { const b = e.target.closest('button[data-d]'); if (b) setStep(step + +b.dataset.d, true); });
    renderSteps();
    stepTimer = setInterval(() => setStep(step + 1), 9000);
  },
};
