# SHARX INVEST — Screener Saham, Forex & Kripto (Smart Money Concept)

<img src="gateway/wwwroot/img/sharx-logo.png" alt="SHARX INVEST" width="360">

Aplikasi lokal untuk memindai **forex, emas, indeks, kripto, saham AS dan saham IDX** dengan analisa
**Smart Money Concept** (struktur BOS/CHoCH, order block, FVG, likuiditas, premium/discount), **berita
realtime**, **kalender ekonomi**, dan ringkasan **arah market**. Chart realtime memakai widget resmi
TradingView; overlay SMC digambar di atas TradingView Lightweight Charts.

> Bukan saran investasi. Semua skor dan skenario dihasilkan aturan otomatis.

## Menjalankan (Windows) — aplikasi desktop

1. Install **Python 3.10+** — <https://www.python.org/downloads/> (centang *Add python.exe to PATH*).
2. Install **.NET SDK 8** (atau lebih baru) — <https://dotnet.microsoft.com/download/dotnet/8.0>.
3. Klik dua kali **`start.bat`**.

`start.bat` menjalankan `setup.bat` secara otomatis bila perlu (membuat virtualenv Python, menginstall
dependensi, mem-build aplikasi ke folder `app\`), lalu membuka **SHARX INVEST** sebagai aplikasi Windows
dengan jendelanya sendiri — bukan di browser, tanpa jendela konsol. Engine Python berjalan tersembunyi
di belakang aplikasi dan ikut berhenti saat jendela ditutup.

| File | Fungsi |
| --- | --- |
| `start.bat` | Buka aplikasi desktop (setup otomatis bila belum) |
| `buat-shortcut.bat` | Buat ikon **SHARX INVEST** di Desktop (langsung membuka `app\SharxInvest.exe`) |
| `setup.bat` | Install/build manual; `setup.bat --rebuild` untuk build ulang setelah mengubah kode |
| `start-browser.bat` | Cadangan: server di satu jendela konsol, UI di browser (http://127.0.0.1:5080) |
| `stop.bat` | Paksa hentikan semua proses SHARX INVEST |

Tampilan memakai warna logo: dasar **biru navy** (#002644) dan **hijau sirip** (#99FF00) untuk tombol aksi;
tombol tutup/hapus/batal **merah**. Logo & ikon: `gateway/wwwroot/img/sharx-logo.png`, `desktop/sharx.ico`.
Setiap kali dibuka, aplikasi tampil **layar penuh** (tanpa title bar/tombol close Windows) dan menampilkan
**loading screen** berisi tahapan — gateway, koneksi realtime, engine Python, data harga & screener, arah
market, berita, kalender — sampai semuanya siap. Kontrol jendela ada di pojok kanan atas aplikasi:
**A− / 100% / A+** ukuran tampilan (juga **Ctrl −**, **Ctrl 0**, **Ctrl +** atau Ctrl + scroll), **─** minimize,
**❐** mode jendela/layar penuh (juga **F11**), **✕** keluar. **F5** memuat ulang tampilan. Ukuran tampilan, ukuran &
posisi jendela, dan mode layar penuh **tersimpan otomatis** sehingga sama persis saat aplikasi dibuka lagi.
Data lokal (pengaturan, key AI terenkripsi, cache, jurnal virtual trading) ada di `%LOCALAPPDATA%\SharxInvest`
(file dari versi lama "SMC Terminal" disalin otomatis sekali). Tampilan dirender **60 fps** (penghitung FPS di
status bar bawah); orbs digambar setiap frame. Upgrade dari versi lama: `start.bat` mem-build ulang otomatis dan
membuang `SMCTerminal.exe` lama — jalankan `buat-shortcut.bat` lagi untuk shortcut baru.
Tautan berita dibuka di browser default.

Aplikasi desktop memakai **Microsoft Edge WebView2 Runtime** (sudah ada di Windows 10/11). Bila belum
terpasang, aplikasi memberi tautan instalasi dan sementara membuka UI di browser.
Log ada di folder `logs\` (`gateway.log`, `engine.log`).

Linux/macOS (mode browser): `./start.sh`.

## Halaman

Semua halaman muat dalam satu layar (tanpa scroll halaman, diuji pada 1920×1080 dan 1366×768). Info yang
banyak dipisah ke **tab** di dalam panel; hanya daftar yang memang panjang (berita, tabel screener,
watchlist) yang bergulir di dalam panelnya sendiri.

| Halaman | Isi |
| --- | --- |
| **Dashboard** | KPI, pencarian, lima cek SMC, panel MARKET ORBS bertab ↓, tab ARAH · SINYAL · ALERT · VERDICT · ANALISA |
| ↳ Orbs 3D | Satu bola per pasar (selalu formasi 2×2, ukuran ikut panel sehingga zoom UI tidak mengubah formasi). Tiap filamen = 1 aset dari universe penuh; panjang = besar gerak hari ini, **emas = naik, biru = turun**; berputar pelan dengan perspektif & kedalaman. Tiap bola bisa **di-zoom sendiri** (scroll; bola yang di-zoom membesar memenuhi panel tanpa terpotong dan memperlihatkan semua asetnya, klik ganda = kembali) dan diseret untuk memutar; bola berhenti hanya selama kursor di atasnya dan **langsung berputar lagi** saat kursor pergi. Label open → harga sekarang & %, hover/klik aset = analisa, tag berita mengambang 3 menit dengan sentimen naik/netral/turun — **klik tag = buka berita** — + tally "condong ke mana" 60 menit |
| ↳ Peta Aset | Semua aset satu pasar sebagai kotak, **dikelompokkan per sektor/kategori** (saham: Energy, Financials, Technology, …; forex: Mayor, Cross, Eksotis, Logam, Energi, Pertanian; kripto: Layer 1, DeFi & RWA, Token Exchange, Meme, AI, …). Warna = % hari ini atau bias SMC 1D (**hijau = naik, kuning = netral, merah = turun**); penanda berita 60 menit, berita baru 3 menit, CHoCH baru 1D. Panel kanan: hitungan live + daftar TERKUAT / TERLEMAH / RAMAI BERITA / CHoCH BARU (hover = temukan di peta, klik = analisa) |
| **Forex** · **Saham AS** · **Saham IDX** · **Kripto** | Tiap pasar punya sub-tab sendiri ↓ |
| ↳ Analisa SMC | Watchlist pasar + chart SMC **realtime** (TF 1M · 5M · 10M · 15M · 1H · 4H · 1D · 1W; candle terakhir bergerak dari data 1 menit tiap ±3 detik, jam berjalan + hitung mundur penutupan candle, status LIVE / PASAR TUTUP) dengan (BOS/CHoCH, HH/HL/LH/LL, order block, FVG, EQH/EQL, BSL/SSL, premium/discount, EMA, entry/SL/TP; layer lewat menu LAYER SMC) atau chart TradingView live / split; panel kanan bertab BIAS · MTF · **PLAN** (sinyal virtual trading: waktu sinyal muncul & terdeteksi, entry/SL/TP, kapan batal otomatis, kapan entry terpicu, hasil, akurasi aset ini & riwayat upgrade SMC) · NARASI · LEVEL · **LAPORAN** (saham/kripto: ringkasan, valuasi, analis, kejutan laba, laba rugi / neraca / arus kas tahunan & kuartalan, pemegang saham: komposisi, institusi & reksa dana terbesar dengan jumlah lembar, orang dalam) · TV · BERITA · AI |
| ↳ Screener | Tab SMC SCREENER: **SEMUA ASET 1D / SEMUA ASET 4H** (seluruh universe pasar itu) atau aset inti 1M/5M/10M/15M/1H/4H/1D; filter kondisi SMC (CHoCH/BOS baru ≤ 3 bar, swing bullish/bearish, swing = internal, harga di OB, zona discount/premium, FVG terbuka, RSI ≤ 30 / ≥ 70), bias, sektor, cari, sortir · tab TRADINGVIEW SCREENER realtime pasar itu |
| ↳ Berita | Berita realtime khusus pasar itu: tiap sumber dipantau terpisah tiap 15 detik (Google News 30 detik) dan headline baru langsung dikirim (push SignalR) & langsung masuk daftar tanpa tombol "berita baru", sentimen per aset, filter, TradingView News pasar itu, status sumber |
| ↳ Fundamental & AI | Rilis data ekonomi (forecast, previous, actual dari headline, prediksi arah mata uang & aset terkait, **reaksi pasar nyata 60 menit**, ✔/✘), **akurasi terukur** model, outlook gabungan per aset (SMC + fundamental + berita), dan **analisa AI** (OpenRouter model gratis dengan rotasi otomatis, Claude, atau API lain): AI menilai arah dari berita, kalender ekonomi, laporan keuangan nyata, kinerja harga dan rencana ke depan, lalu **dicocokkan dengan SMC** → sinyal BELI / JUAL / NETRAL |
| ↳ Dividen & Benefit | Jadwal ex-dividen (diumumkan emiten, atau **perkiraan** dari ritme pembayaran), jumlah & yield 12 bulan, halving kripto (BTC/LTC/BCH dari tinggi blok), keputusan suku bunga bank sentral untuk forex, serta headline benefit/aksi korporasi (dividen, buyback, split, rights, RUPS; airdrop, staking, token unlock, burn, listing, upgrade); tiap event dengan **hype** (volume 5 hari vs 20 hari + berita terkait 7 hari + gerak harga 5 hari) |
| ↳ Arah Market | Tab RINGKASAN (breadth, regime, sentimen, outlook) · KEKUATAN MATA UANG (forex) · PENGGERAK & STRUKTUR · INSTRUMEN KUNCI · EVENT HIGH-IMPACT · PETA PASAR (heatmap TradingView) |
| **AI Training** | Virtual trading sinyal SMC: win rate live & backtest out-of-sample (terkualifikasi dan semua) vs target 70% dan tujuan 99%, rincian per asal parameter & pasar, kurva ekuitas (R), papan akurasi per aset & timeframe, grup pasar, jurnal sinyal (muncul → terdeteksi → entry → hasil; trade AI atau bayangan), log upgrade SMC otomatis, live chart trade virtual per aset |
| **Kalender** | Kalender ekonomi minggu ini + countdown event high-impact + kalender TradingView |
| **Metodologi** | Tab CARA KERJA 7 LANGKAH · STRUKTUR · OB/FVG/LIKUIDITAS · SKOR · ARAH & BERITA · DATA & ARSITEKTUR |

**Aset**: 57 aset inti (`config/watchlist.json`, dipindai SMC 1H/4H/1D) + **universe ±2.100 aset**
(`config/universe.json`): seluruh S&P 500 + saham AS populer & ETF, seluruh saham BEI yang tersedia di Yahoo
(±880), **±600 kripto** dari 1.000 teratas CoinGecko yang tersedia di Yahoo (identitas ticker dicek dengan harga CoinGecko,
termasuk ticker bernomor seperti `SUI20947-USD`; versi wrapped/staked/bridged dan saham ter-tokenisasi tidak dimasukkan
karena duplikat; kategori dari CoinGecko), 44 pair forex mayor/cross/eksotis
dan komoditas. Seluruh universe dipindai SMC **1D tiap 20 menit dan 4H tiap 40 menit** (±1,5–2 menit per sapuan),
dan harga open/last-nya diperbarui bergiliran tiap ±2 menit. Perbarui daftar dengan `python engine/tools/build_universe.py` (butuh `engine/tools/requirements.txt`).
Berita dari 40 sumber RSS dibagi per pasar dan ditautkan ke aset (ticker, nama perusahaan/koin, alias mata uang).

**Analisa AI (opsional)**: buka FUNDAMENTAL & AI › Pengaturan AI, pilih provider dan masukkan API key-nya
(disimpan lokal, terenkripsi DPAPI di Windows, tidak pernah dikirim ke browser):

| Provider | Keterangan |
| --- | --- |
| **OpenRouter** (default) | Key gratis dari <https://openrouter.ai/keys> (`sk-or-…`, atau `OPENROUTER_API_KEY`). Hanya model **gratis** yang dipakai, diambil otomatis dari katalog OpenRouter (model teks ≥ 32K konteks, ukuran kecil dilewati, model yang mendukung JSON ketat diutamakan, `openrouter/free` paling akhir). Bila model sedang dibatasi (429), tidak tersedia, terlalu lama, atau jawabannya tidak valid, model itu dijeda dan aplikasi **otomatis pindah ke model gratis berikutnya** (maks. 6 percobaan / ±200 detik); model yang terakhir berhasil dipakai lebih dulu. Bila kuota harian model gratis akun Anda habis, aplikasi berhenti dan memberi tahu kapan kuota pulih. Bisa juga dikunci ke satu model tertentu. |
| **Anthropic Claude** | Key `sk-ant-…` (atau `ANTHROPIC_API_KEY`); model `claude-opus-5-5` dengan fallback server-side. Memakai kuota/biaya akun Anthropic Anda. |
| **API lain** | Endpoint apa pun yang kompatibel OpenAI `/chat/completions` (Groq, Together, DeepSeek, LM Studio/Ollama lokal, dll.): isi base URL, nama model dan key bila perlu (atau `AI_CUSTOM_API_KEY`). |

Fakta yang dikirim ke AI (sama untuk semua provider) bersifat **fundamental**, tanpa analisa chart:

- berita terkait aset + sentimennya;
- kalender ekonomi: rilis (actual vs forecast + reaksi pasar nyata) dan jadwal 7 hari ke depan beserta skenarionya;
- laporan nyata emiten (saham AS & IDX, Yahoo Finance): pendapatan & laba kuartalan, kejutan EPS, valuasi,
  profitabilitas, utang, **pemegang saham** (komposisi orang dalam/institusi, institusi & reksa dana terbesar beserta
  jumlah lembar, orang dalam); kripto: kapitalisasi, suplai, volume;
- kinerja harga: return 1W/1M/3M/6M/YTD/1Y, jarak ke high/low 52 minggu, volatilitas, relatif ke benchmark
  (S&P 500 / IHSG / BTC);
- rencana & ekspektasi ke depan: jadwal laporan berikutnya + konsensus EPS/pendapatan, target harga & rekomendasi
  analis, berita rencana perusahaan.

AI menjawab arah naik/turun/netral + keyakinan + horizon. Engine lalu **mencocokkan dengan bias SMC multi-timeframe
sesuai horizon** (intraday: 1H/15M/5M · 1–3 hari: 4H/1H/15M · 1–2 minggu: 1D/4H/1H): bila searah, keyakinan **naik** (5 + 0,2 × |skor SMC|, maks. +20); bila berlawanan, keyakinan turun
dengan besaran yang sama; bila SMC belum jelas (|skor| < 15) tidak berubah. Besarnya dikalikan **akurasi terukur SMC
aset itu** dari virtual trading (×0,5–1,3): SMC yang terbukti akurat di aset itu berbobot lebih. Keyakinan akhir dibatasi 95. Sinyal
akhir **BELI / JUAL** bila keyakinan akhir ≥ 55, selain itu **NETRAL**. Hasil menampilkan konfluensi ini, katalis
mendatang, plan SMC 4H, model yang menjawab dan model yang dilewati. Keyakinan adalah kekuatan bukti, **bukan jaminan** — tidak ada model yang dapat menjamin akurasi 99% di
pasar; karena itu aplikasi menampilkan akurasi nyata model aturan yang diverifikasi dengan reaksi harga setelah rilis data.

## Asisten suara berita

Tombol **🔊** di kanan atas mengaktifkan asisten suara (pengaturan disimpan di komputer ini):

1. Saat berita baru yang **terkait aset** masuk, **AI menganalisanya lebih dulu** — sebelum apa pun dibacakan: engine
   mengambil isi artikel dari **link** beritanya, dan AI menilai dampak berita itu terhadap aset (artikel = fakta utama;
   kalender ekonomi, laporan, kinerja dan berita lain sebagai konteks). Link berita dilampirkan di hasil analisa.
2. Arah AI dicocokkan dengan SMC **di setiap timeframe (5M, 15M, 1H, 4H, 1D)**. Timeframe hanya mendapat **sinyal sendiri**
   (BUY/SELL, entry, stop loss, target, RR) bila punya **momentum searah**: bias SMC-nya searah (|skor| ≥ 15) dan break
   struktur terakhirnya searah dan baru (≤ 20 bar). Setup diambil dari virtual trading timeframe itu (parameter hasil
   tuning, dengan level presisi NORMAL/KETAT/SANGAT KETAT) atau, bila belum ada, dari plan SMC-nya. Timeframe lain
   ditandai "belum ada momentum" atau "berlawanan" — tidak dipaksa jadi sinyal.
3. Setelah analisa selesai baru dibacakan: sumber & judul, inti isi berita, hasil AI (sinyal, keyakinan, ringkasan) dan
   sinyal per timeframe. Lalu asisten bertanya apakah isi lengkapnya mau dibacakan — jawab dengan **suara** ("ya" /
   "tidak", dikenali offline), klik **YA / TIDAK**, atau tekan **Y / N**.
4. Tombol **🔊** di setiap berita (tab BERITA) melakukan hal yang sama dan langsung membacakan isi lengkapnya.

Kartu asisten menampilkan link artikel, hasil AI, tabel sinyal per timeframe dan tombol DETAIL ANALISA (analisa AI
lengkap). Tanpa AI aktif (belum ada API key) arah diambil dari sentimen berita yang ditandai, dan itu disebutkan.
Hasil analisa satu berita disimpan 30 menit, jadi tombol 🔊 berikutnya tidak memakai kuota AI lagi.

Pilihan: semua berita bertag / hanya aset inti / hanya sentimen kuat; kecepatan & volume; mikrofon on/off; mesin suara.
Bila berita datang beruntun, hanya 3 terbaru yang diproses (yang lebih dari 5 menit dilewati).

**Suara natural — gratis, tanpa batas, offline (default)**: [Supertonic 3](https://huggingface.co/Supertone/supertonic-3)
(Supertone; kode MIT, bobot model BigScience OpenRAIL-M) — satu model multibahasa di komputer ini, jadi bahasa Indonesia
dan bahasa Inggris dibacakan oleh **suara yang sama** yang mirip manusia, dengan 10 pilihan suara (5 pria, 5 wanita).
Uji pengenalan ucapan (Whisper small, kalimat berita yang sama): Supertonic 3 **0 kata salah** dalam bahasa Indonesia
(Piper 14–19%), bahasa Inggris 0%; ±5× lebih cepat dari waktu nyata di CPU; ±400 MB, diunduh sekali dari revisi
Hugging Face yang dipatok dan diverifikasi SHA-256 per file. Lisensi OpenRAIL-M melarang pemakaian untuk meniru suara
orang sungguhan tanpa izin.

**Bahasa Inggris tetap dibaca bahasa Inggris**: frasa Inggris dua kata atau lebih ("wait and see", "the Fed",
"risk-off sentiment") dibacakan dengan pelafalan Inggris oleh suara yang sama; satu kata Inggris di tengah kalimat
Indonesia ("rebound", "outlook") dibaca di dalam kalimatnya supaya intonasi tetap alami. Pada uji 4 kalimat campuran ×
5 kali, cara ini salah dengar 9,7% kata, dibanding 17% bila dipecah per kata atau dibaca seluruhnya sebagai Indonesia.

**Persona JARVIS** (opsional): suara pilihan ditambah warna suara asisten AI (resonansi halus, pantulan pendek) — gaya
orisinal, bukan tiruan suara aktor mana pun.

**Cadangan: [Piper](https://github.com/OHF-Voice/piper1-gpl)** (GPL-3.0, dipasang terpisah) dengan suara Indonesia
`id_ID-news_tts-medium`, English `en_GB-vctk-medium` / `en_US-lessac-medium` (model untuk pemakaian pribadi), dan
suara Windows bila paket suara belum terpasang. Pengenal jawaban: **Whisper tiny** (±75 MB, faster-whisper). Semua
model diunduh sekali dari pengaturan suara (UNDUH YANG DIPERLUKAN), disimpan di `%LOCALAPPDATA%\SharxInvest\voices`
dan `\stt`; setelah itu tidak perlu internet untuk suara. Paket Python-nya (`engine/requirements-voice.txt`) dipasang
`setup.bat`.

**Isi berita lengkap** diambil oleh engine dari link berita, seperti pembaca biasa (trafilatura), lalu diberikan ke
AI. Model AI sendiri tidak membuka website — ia hanya membaca teks yang dikirim kepadanya — jadi engine yang membuka
link atas nama AI. Yang bisa dibaca utuh adalah link langsung penerbit yang mengizinkan pembaca otomatis (uji
12 Nov 2026: 74% berita bertag dari link langsung terbaca utuh, mis. CNBC Indonesia, Kontan, detikFinance, CNN
Indonesia, Liputan6, IDX Channel, Bloomberg, CryptoSlate). Yang tidak bisa:
- **tautan Google News** (berita dari pencarian Google News): link-nya hanya pengalihan yang berjalan di browser, jadi
  pembaca otomatis — termasuk AI mana pun — hanya mendapat judul dan ringkasan. Bila penerbitnya juga punya feed
  langsung di aplikasi, berita yang sama otomatis memakai link langsung itu;
- situs yang menolak pembaca otomatis (HTTP 401/403/429: Investing.com, MarketWatch, Nasdaq, Seeking Alpha, The
  Block, …), paywall dan verifikasi bot.

Pembatasan ini tidak diakali (tidak menyamar sebagai browser manusia). Asisten menyebutkan alasannya, AI menilai dari
judul dan ringkasan, dan tombol BUKA BERITA membuka artikel di browser. Opsi *Hanya berita yang isinya bisa dibaca AI*
di pengaturan suara melewati tautan Google News.

## Virtual trading & upgrade SMC otomatis (AI Training)

Setiap aset inti (TF 15M/1H/4H/1D) dan seluruh universe (TF 1D) dipantau. Strategi (kausal — hanya memakai bar
sampai saat itu) — **aturan SMC BOS/CHoCH → area BUY/SELL, tanpa skip**: setiap BOS atau CHoCH, baik **swing
(besar)** maupun **internal (kecil)**, langsung menjadi setup ke arah break-nya — bullish = **AREA BUY**, bearish =
**AREA SELL**. Area = **FVG** di leg yang mematahkan struktur (FVG yang sudah tertutup sebelum break tidak dipakai);
bila leg tidak punya FVG terbuka, area = order block leg itu. Entry limit di area (tengah), stop di balik awal leg ±
ATR, **TP1 = puncak BOS/CHoCH** (high tertinggi leg bullish / low terendah leg bearish) dan **TP2 = lanjutan** di luar
puncak (0,618 atau 1× panjang leg). Di TP1 50% ditutup dan stop pindah ke entry; sisanya ke TP2. Selama belum entry,
TP1 ikut puncak baru bila leg masih berlanjut. Tidak ada break yang dilewati — reward kecil pun tetap diambil.

- **Aturan dikunci**: AI/tuner hanya menyetel *cara* (panjang pivot, entry di tengah/tepi area, jarak stop, jarak TP2,
  kedaluwarsa), tidak pernah *memilih* break — tidak ada filter yang melewati BOS/CHoCH. Setting versi lama yang
  melewati break (filter tren, premium/discount, reward minimal, satu skala saja, konfluensi, TF tinggi) atau memakai
  target lain tidak dipakai lagi; aset itu kembali ke aturan ini lalu dilatih ulang.
- **Hasil terukur aturan ini** (57 aset inti × 15M/1H/4H/1D, 1,33 juta candle, tanpa tuning): aturan lama (order
  block, 1 target) 5.264 trade, win 30,5%, +0,043R/trade; v3.11 (FVG wajib, reward ≥ 1:1) 17.035 trade, win 42,9%,
  +0,007R; **v3.12 semua BOS/CHoCH tanpa skip 48.895 trade, win 41,4%, +0,060R**; dengan stop 1,0 ATR win 50,0%,
  +0,028R. Sekitar separuh area tidak pernah disentuh harga lagi (tanpa entry). Aturan ini sendiri **tidak**
  menghasilkan 99%. Mode presisi dan EXPERT menilai aset/trade mana yang terbukti, tanpa menghapus area dari chart.

- **Checklist ICT per setup** (grade di setiap area chart & kartu PLAN). Model ICT yang umum diajarkan: bias TF besar →
  **sweep likuiditas** (harga mengambil stop di high/low sebelumnya) → **displacement** (candle body besar searah, di
  sini body ≥ 1× ATR) yang membuat **MSS/CHoCH** dan meninggalkan **FVG** → entry saat retrace ke FVG di **discount**
  (long, ≥ 50% retracement leg) / **premium** (short), idealnya **OTE** 62–79% → stop di balik sweep → TP1 high/low
  leg (internal), TP2 −0,62 extension / likuiditas berikutnya; untuk TF intraday di dalam **killzone** (waktu New York:
  London 02:00–05:00, NY AM 07:00–10:00, NY PM 13:30–16:00). Grade: **A+** = semua syarat (sweep, displacement, FVG,
  discount/premium, searah TF besar, killzone bila berlaku), **A** = model inti (4 syarat pertama), **B** = 3 dari 4,
  **C** = sisanya. Semua area tetap tergambar; grade hanya menunjukkan seberapa "ICT" setup itu.
- **Hasil terukur checklist ICT** (57 aset inti × 15M/1H/4H/1D, 48.899 trade, aturan v3.12): semua setup win 41,4%
  (+0,060R); model ICT lengkap (A+/A) win 40,2% dari 1.179 trade (+0,044R); A+ saja 40,2% dari 246 trade (+0,106R).
  Per syarat: dengan sweep 46,2% vs tanpa 40,7%; displacement 42,1% vs 40,6%; searah TF besar 41,7% vs 41,3%;
  killzone 40,9% vs 41,9%; entry di discount/premium 32,9% (+0,090R) vs tidak 57,8% (+0,002R) — entry lebih dalam
  memperbesar reward tetapi menurunkan win rate. Mengikuti ICT lebih ketat **tidak** menaikkan win rate di data ini;
  satu-satunya syarat yang jelas menaikkan win rate adalah sweep likuiditas (+5,5 poin). Sumber konsep: materi edukasi
  ICT/SMC publik (tidak ada aturan resmi tertulis; definisi tiap pengajar berbeda).
- **Timestamp**: waktu bar sinyal muncul, waktu sistem pertama mendeteksinya, waktu kedaluwarsa, waktu entry terpicu,
  waktu selesai.
- **Manajemen posisi**: aturan TP1/TP2 — 50% di TP1 (puncak BOS/CHoCH), stop ke entry, sisa ke TP2; trade yang sempat
  TP1 lalu kembali ke entry tercatat PROFIT (½ × R TP1). Bila TP1 dan stop tersentuh di bar yang sama tetap dihitung
  LOSS.
- **Batal otomatis** sebelum entry bila: struktur berbalik (CHoCH/BOS berlawanan di skala yang sama — break baru itu
  langsung punya area sendiri), harga close menembus stop, atau kedaluwarsa (mis. 30 bar). Area tetap tergambar.
- **Satu posisi sekaligus** (per aset & timeframe): selama sebuah trade masih berjalan (sudah entry, belum TP/SL/batas
  waktu), setup lain tidak membuka posisi — bila harga menyentuh areanya saat itu, setup batal dengan keterangan
  *posisi lain masih berjalan* (termasuk entry di candle yang sama dengan penutupan trade sebelumnya, karena urutan di
  dalam satu candle tidak diketahui). Berlaku di backtest, tuning, training live (lintas setting) dan jurnal live.
  Terukur (57 aset × 15M–1D): 16.850 entry tumpang tindih diblok; 32.070 trade, win 41,8%, +0,078R/trade (sebelumnya
  48.901 trade tumpang tindih, win 41,4%, +0,060R).
- **Eksekusi virtual** saat harga menyentuh entry → PROFIT (target) / LOSS (stop; bila satu bar menyentuh keduanya
  dihitung LOSS) / ditutup waktu. Hasil dalam R; dicatat di `training.db` (SQLite) di folder data.
- **Timeframe 5M sampai mingguan**: setiap aset dilatih terpisah di 5M, 15M, 1H, 4H, 1D dan 1W — tiap timeframe punya
  parameter, bukti dan sinyalnya sendiri.
- **TRAINING LAB** (tab di halaman AI Training): pilih pasar (FOREX / SAHAM AS / SAHAM IDX / KRIPTO, satu, beberapa atau
  semua), timeframe (5M–1W, satu, beberapa atau semua) dan jumlah candle (200–20.000; dibatasi riwayat Yahoo: 5M/15M
  ±60 hari, 1H/4H ±730 hari, 1D/1W seluruh riwayat), lalu MULAI. Training berjalan di engine secepat CPU — tidak menunggu
  waktu pasar (contoh: FOREX × 5M, 17 aset × 3.000 candle selesai 57 detik) — dengan progres, aset yang sedang dilatih,
  hasil per pasar × timeframe, dan tombol STOP. Training otomatis di latar berhenti sementara selama lab berjalan. Di LIVE
  CHART, IKUTI TRAINING menampilkan aset yang sedang dilatih (dengan jumlah candle lab) dan REPLAY ×1–×7 memutarnya.
- **Training live 1 aset**: di TRAINING LAB, setelah pasar dipilih muncul daftar ASET pasar itu. Pilih satu aset (timeframe
  jadi satu pilihan), isi jumlah candle (mis. 5000), lalu MULAI: chart mulai dari 5000 candle ke belakang dan training
  berjalan **maju** sampai candle terakhir, diputar ×1–×7 seperti replay. AI mulai tanpa pengetahuan (setting default),
  lalu setiap ±10% candle belajar ulang **hanya dari candle sebelumnya** dan men-trading candle berikutnya dengan hasil
  belajarnya (walk-forward). Setiap trade di chart diambil tanpa melihat masa depan, jadi win rate yang terbentuk adalah
  uji yang jujur. Engine menghitung lebih cepat dari putaran chart (contoh EURUSD 15M × 3.000 candle: ±8 detik); chart
  tidak pernah mendahului bagian yang sudah dilatih. Kotak di chart menunjukkan candle ke berapa, versi setting AI, trade
  selesai, menang/kalah, win rate dan R; tabel di bawah menunjukkan setiap langkah belajar (data belum cukup / setting
  tetap / setting baru, dengan uji internalnya). Selesai: aset dilatih seperti biasa pada semua candle itu dan tradenya
  disimpan di kamus trade. "SEMUA ASET" tetap menjalankan training batch tanpa chart.
- **SMC terus diperbaiki**: setting SMC tidak pernah final. Selama aset inti masih di bawah tujuan dan putaran tuning tidak
  menemukan yang lebih baik, pencarian berikutnya diperluas otomatis (level 0 → 2: stop lebih lebar, target sampai 1:3,
  entry di tepi area, panjang pivot lain; 54 → 360 → 900 kombinasi, semuanya aturan tanpa skip). Setting yang lebih baik tetap harus lulus uji
  out-of-sample yang sama, jadi pencarian lebih luas tidak bisa "membeli" win rate dengan mencocokkan noise.
- **TERUS PERBAIKI SAMPAI TARGET** (training live 1 aset): 20% candle terakhir dikunci untuk ujian akhir. Di sisanya,
  training maju diulang putaran demi putaran dengan pencarian makin luas, hasil terbaik disimpan, dan berhenti bila target
  99% tercapai (minimal 30 trade) atau pencarian lebih luas tidak lebih baik lagi. Lalu setting terbaik diuji **sekali**
  pada candle yang dikunci — angka ujian akhir itulah yang jujur. Contoh nyata 12 Nov: BTCUSD 15M putaran 1 35,5% →
  putaran 2 32,3% (berhenti), ujian akhir 50% dari 12 trade; ETHUSD 15M ujian akhir 70% dari 10 trade (+4,46R). Hasil
  dengan kurang dari 30 trade ditandai "sampel kecil, belum bukti", dan bila bagian belajar terlalu sedikit trade-nya,
  aplikasi menyarankan menambah candle atau memilih timeframe lebih kecil. 99% tidak tercapai di uji mana pun; tujuan
  itu tetap arah perbaikan, bukan jaminan.
- **Kamus trade**: setiap trade dari setting aktif tiap aset & timeframe disimpan permanen (tabel `patterns`) bersama
  kondisi saat sinyal muncul. Baris di luar jendela riwayat terbaru tetap disimpan, jadi kamus terus bertambah (mis. trade
  5M lebih lama dari 60 hari Yahoo).
- **Multi-timeframe**: untuk setiap trade dicatat tren swing dua timeframe di atasnya *saat itu* (hanya candle TF tinggi
  yang sudah tutup — tanpa melihat masa depan). Tabel MULTI TIMEFRAME menunjukkan win rate data uji saat TF tinggi searah,
  campuran atau berlawanan — itulah persentase gabungan multi-timeframe yang terukur. Kartu PLAN menunjukkan arah tiap timeframe saat ini (▲/▼ per TF dan persentase naik vs turun);
  sinyal tetap per timeframe.
- **Mode EXPERT** (di samping SANGAT KETAT): hanya trade dari setting yang sudah terbukti **dan** searah dengan dua
  timeframe di atasnya — eksekusi difokuskan ke arah yang dominan di multi-timeframe.
- **Pemicu 1M**: pola candlestick di bar 1 menit (engulfing, pin bar, morning/evening star, three soldiers/crows) sebagai
  pemicu awal, masing-masing dengan rekam jejaknya **di aset itu** (±7 hari data 1 menit: seberapa sering harga bergerak
  searah dalam 15 menit). Uji 12 Nov: NVDA three white soldiers 47% dari 102, BTC three black crows 47% dari 267, BBCA
  morning star 19% dari 48 — pola candlestick 1 menit sendirian kira-kira untung-untungan, jadi dipakai sebagai pemicu
  bersama SMC, bukan sinyal sendiri. Data 1 menit gratis untuk **forex** hanya satu harga per menit (tanpa OHLC), jadi
  untuk forex aplikasi menyebutkan bahwa pola tidak bisa dibaca.
- **Berita & kalender di sinyal**: setiap sinyal live mencatat sentimen berita aset itu 24 jam terakhir (searah /
  berlawanan) saat muncul. Kartu PLAN menampilkan rilis kalender ekonomi 7 hari ke depan yang menggerakkan aset itu:
  konsensus (forecast vs sebelumnya) dan arah aset bila actual di atas / di bawah forecast. Forecast adalah perkiraan
  nyata pasar sebelum rilis; sisi mana yang terjadi tidak ditebak — AI menimbangnya bersama berita. Data berita historis
  tidak tersedia untuk backtest, jadi faktor berita diukur dari trade live.
- **Riwayat panjang untuk pelatihan**: tuner belajar dari 10 tahun bar harian, 730 hari bar 1 jam / 4 jam (batas Yahoo)
  dan 60 hari bar 15 menit (maks. 6.000 bar), dengan bar terbaru dari analisa sebagai ujungnya. Bukti out-of-sample per
  aset jadi beberapa kali lebih banyak (uji 474 pasangan: 922 → 2.545 trade uji). Riwayat ini disimpan di cache terbatas
  (240 aset × timeframe) dan diunduh bertahap; tuning memakai CPU ±4× lebih banyak daripada versi sebelumnya.
- **Belajar per grup pasar**: riwayat hingga 150 aset satu pasar & timeframe (mis. SAHAM IDX 1D) digabung dan dibagi
  menurut waktu: 60% awal untuk mengurutkan 54 kombinasi aturan SMC (panjang pivot, entry di area, jarak stop,
  jarak TP2, kedaluwarsa), 20%
  berikutnya (validasi) untuk memilih, dan 20% terakhir
  (data uji yang tidak ikut memilih) untuk melaporkan hasil. Aset yang datanya sedikit memakai parameter grupnya.
- **Upgrade per aset**: kombinasi yang sama diuji pada 70% riwayat aset lalu diverifikasi pada 30% terakhir. Parameter
  aset dipakai hanya bila **lower bound win rate (90%) out-of-sample lebih tinggi** daripada parameter yang diwarisi,
  ekspektasi tetap positif dan jumlah trade cukup; bila parameter grup kemudian lebih baik untuk aset itu, aset kembali
  ke parameter grup. Urutan pewarisan: upgrade aset → grup pasar → default. Diulang tiap 6 jam; panjang pivot yang
  dipakai juga dipakai chart & screener aset itu. Setiap upgrade diberi versi dan dicatat.
- **Live chart training** (tab LIVE CHART): candle aset yang dilatih (diperbarui tiap 3 detik dari bar 1 menit) dengan
  setiap trade virtual sebagai kotak posisi — zona TP hijau, zona SL merah, garis TP1 (puncak BOS/CHoCH) dan TP2, hasil
  dalam R; trade live diberi bingkai hijau terang, sinyal yang menunggu entry tampil sebagai garis BUY/SELL LIMIT / SL /
  TP1 / TP2. BOS/CHoCH swing dan internal sama-sama digaris, dan setiap break punya kotak AREA BUY (hijau) / AREA SELL
  (merah) dari candle break sampai entry terisi atau setup berakhir. Area sebelum garis
  "DATA UJI" adalah data latih (redup); tombol DATA UJI memperbesar ke seluruh periode out-of-sample. IKUTI AKTIVITAS
  memindahkan chart otomatis ke aset dengan aktivitas trade virtual terbaru.
- **Replay dipercepat ×1–×7** (tab LIVE CHART): memutar ulang data uji (periode yang tidak dipakai melatih) candle demi
  candle — ×1 = 3 candle/detik, ×7 = 21 candle/detik — sehingga hasil setup (entry, TP/SL, R) terlihat dalam hitungan
  detik, untuk aset mana pun di mode NORMAL, KETAT atau SANGAT KETAT. Trade dan struktur baru muncul saat waktunya
  tercapai (tanpa bocoran masa depan); kotak replay menghitung trade selesai, menang/kalah, win rate dan total R, dan
  di akhir sama dengan statistik data uji. Ini mempercepat tampilan hasil backtest, bukan trade live: candle live tetap
  mengikuti pasar sungguhan.
- **Mode presisi**: NORMAL (batas bawah win rate ≥ 50%), KETAT (≥ 65%), SANGAT KETAT (≥ 80%), EXPERT (terbukti + searah multi-timeframe). Mode lebih ketat hanya
  menghitung setting dengan bukti terkuat: win rate lebih tinggi, sinyal lebih jarang. Batas bawah tiap sinyal live
  dicap saat sinyal muncul, jadi hasil live per mode tidak dipilih setelah hasilnya diketahui.
- **Trade terkualifikasi vs bayangan**: sinyal dihitung sebagai trade AI hanya bila parameter di baliknya sudah terbukti
  out-of-sample (cukup trade, batas bawah win rate ≥ 50%, ekspektasi positif). Sinyal lain tetap dijalankan secara virtual
  sebagai **trade bayangan** untuk belajar dan dilaporkan terpisah.
- **Akurasi**: halaman AI Training menampilkan win rate live & backtest dari pasangan terkualifikasi, angka semua
  pasangan, dan rincian per asal parameter (upgrade aset / grup / default) serta per pasar. Karena itu angka "semua"
  bisa turun saat banyak aset baru yang belum terbukti mulai dilatih — itu pengenceran oleh data yang belum terbukti,
  bukan berarti sinyal yang terbukti memburuk. Backtest terkualifikasi ikut dipakai untuk memilih pasangan sehingga bisa
  sedikit optimistis; bukti yang benar-benar ke depan adalah trade live terkualifikasi (statusnya ditetapkan saat sinyal
  muncul, sebelum hasilnya diketahui).
- **Tujuan 99%** ditampilkan sebagai arah perbaikan, tetapi yang dilaporkan selalu hasil terukur. Win rate setinggi itu
  tidak dapat dijamin dan tidak dikejar dengan cara yang menipu (target sangat kecil / stop sangat jauh / overfitting).

## Arsitektur

```
SharxInvest.exe — aplikasi desktop C# (WinForms + WebView2, desktop/)
   │  menampilkan UI dalam jendela sendiri; menjalankan gateway di dalam proses
   ▼
Gateway C# — ASP.NET Core 8 (gateway/)
   • UI & REST API (/api/*), push realtime SignalR (/hub/market)
   • 40 feed RSS (ETag/If-Modified-Since, dedup; tautan langsung penerbit menggantikan tautan Google News untuk berita yang sama), klasifikasi berita per pasar, kalender ForexFactory
   • refresh screener & arah market berkala, alert (CHoCH/BOS baru, bias berubah, berita kuat, event ≤ 30 mnt)
   • EngineSupervisor: menjalankan engine Python tersembunyi, cek /health, restart otomatis bila crash,
     proses anak terikat job object Windows sehingga tidak pernah tertinggal
   │  HTTP lokal 127.0.0.1
   ▼
Engine Python — FastAPI (engine/)
   • data OHLC Yahoo Finance (yfinance) + cache TTL, unduhan batch
   • engine SMC (smc.py), skor bias, skenario & narasi (analysis.py)
   • screener per pasar (screener.py), arah market & kekuatan mata uang (direction.py)
   • sentimen headline EN/ID + tag aset (sentiment.py, tagger.py), fundamental & outlook
   • pemindaian universe 1D/4H & harga seluruh aset di background (background.py)
   • analisa AI multi-provider (app/ai/: OpenRouter rotasi model gratis, Claude, OpenAI-compatible) +
     konfluensi dengan SMC; laporan perusahaan & pemegang saham (company.py), kinerja (profile.py);
     blok sektor/kategori (blocks.py); dividen & benefit (benefits.py); virtual trading & tuner (training.py);
     suara offline & isi artikel untuk asisten berita (voice.py)
```

## Konfigurasi

- **Watchlist**: `config/watchlist.json` — `symbol`, `name`, `yahoo` (ticker data), `tv` (simbol
  TradingView), `class` (`forex | us | idx | index | commodity | crypto`), opsional `market`
  (`forex | saham-as | saham-idx | kripto`) untuk menentukan halaman. Simbol lain tetap bisa
  dianalisa lewat kotak pencarian (mis. `IDX:ANTM`, `NYSE:KO`, `CADJPY`, `^FTSE`).
- **Gateway**: `gateway/appsettings.json` → bagian `Terminal`: daftar feed berita, interval refresh
  berita/screener/arah market, timeframe screener default, URL kalender.
- **Port**: aplikasi desktop memakai port lokal bebas secara otomatis (utamakan 5080 dan 8765);
  mode browser memakai `WEB_PORT` di `start-browser.bat`.
- **Kategori berita → pasar**: `gateway/Services/Markets.cs`.

## API

| Endpoint | Keterangan |
| --- | --- |
| `GET /api/analyze?symbol=EURUSD&tf=1h` | Analisa SMC lengkap + candle + MTF (`tf`: 1m, 5m, 10m, 15m, 1h, 4h, 1d, 1w) |
| `GET /api/live?symbol=BTCUSD` | Bar 1 menit hari ini (cache 3 detik) untuk candle realtime |
| `GET /api/company?symbol=BBRI` | Laporan perusahaan: valuasi, analis, laba rugi/neraca/arus kas, pemegang saham |
| `GET /api/benefits?market=saham-idx` | Jadwal dividen & benefit + hype |
| `GET /api/training/summary` · `GET /api/training/asset?symbol=EURUSD&tf=1h` · `GET /api/training/chart?symbol=EURUSD&tf=1h&bars=5000` | Virtual trading: akurasi, multi-timeframe, jurnal, upgrade, trade untuk live chart |
| `GET /api/training/lab` · `POST /api/training/lab/start` `{"markets":["forex"],"tfs":["5m"],"bars":5000}` · `POST /api/training/lab/stop` | Training lab (batch) |
| `GET /api/training/session` · `GET /api/training/session/candles` · `POST /api/training/session/start` `{"symbol":"EURUSD","tf":"15m","bars":5000,"improve":true}` · `POST /api/training/session/stop` | Training live 1 aset (walk-forward; `improve` = terus perbaiki + ujian akhir) |
| `GET /api/voice/status` · `POST /api/voice/setup` | Status & unduh model suara (`{"items":["st3","stt"]}`; Piper: `id`, `en`, `engb`) |
| `POST /api/voice/tts` `{"text":"…","lang":"id","rate":1,"engine":"supertonic","voice":"M1","persona":"natural","mixed":true}` | Suara (audio/wav): Supertonic 3 atau Piper; `mixed` = frasa Inggris dibaca dalam bahasa Inggris |
| `POST /api/ai/news` `{"id":"…","symbol":"NVDA"}` | Analisa AI satu berita (isi dari link-nya) + sinyal per timeframe yang punya momentum |
| `POST /api/voice/stt` (audio/wav 16 kHz) | Kenali jawaban singkat → `{"text","intent":"yes|no|null"}` |
| `GET /api/news/article?id=…` | Isi lengkap berita (atau ringkasan + alasan) |
| `GET /api/screener?market=all&tf=4h` | Screener (`market`: all, forex, saham-as, saham-idx, kripto) |
| `GET /api/direction` | Arah market + sentimen berita + event high-impact |
| `GET /api/news?market=kripto&category=&sentiment=&q=&limit=` | Berita per pasar (terbaru dulu) |
| `GET /api/news/sentiment?market=forex` | Ringkasan sentimen 12 jam per pasar |
| `GET /api/quotes?market=all` | Open / last / % seluruh universe + blok sektor/kategori tiap aset |
| `GET /api/screener?market=saham-idx&scope=all&tf=1d` | SMC seluruh aset suatu pasar (`tf`: 1d atau 4h) |
| `GET /api/fundamental` | Rilis data + prediksi + reaksi nyata + akurasi |
| `GET /api/outlook?symbols=EURUSD,NVDA` | Outlook gabungan SMC + fundamental + berita |
| `POST /api/ai/analyze` `{"symbol":"EURUSD"}` | Analisa AI dengan provider terpilih (butuh API key) |
| `GET /api/ai/models` | Model gratis OpenRouter yang dipakai, urutan & status jeda |
| `GET/POST/DELETE /api/settings/ai` | Provider, model, base URL & key AI (key tidak pernah dikembalikan) |
| `GET /api/symbol-news?symbol=AAPL` | Berita per simbol |
| `GET /api/calendar` · `/api/alerts` · `/api/stats` · `/api/health` | Kalender, alert, KPI, status |

## Sumber data & keterbatasan

- **Analisa SMC** memakai Yahoo Finance via `yfinance` (gratis, tanpa API key). Data bisa tertunda
  tergantung bursa (kripto & forex hampir realtime; saham IDX umumnya tertunda); candle realtime di chart SMC mengikuti
  bar 1 menit Yahoo yang diperbarui tiap ±3 detik; emas & perak memakai kontrak futures (`GC=F`, `SI=F`) sebagai proksi XAUUSD/XAGUSD.
  Overlay SMC dihitung ulang tiap 60 detik; candle terakhir bergerak tiap ±3 detik.
- **Harga tick realtime**: widget resmi TradingView (ticker tape, advanced chart, screener, gauge
  teknikal, heatmap, news, kalender). Widget gratis TradingView tidak bisa memuat indikator custom,
  jadi overlay SMC digambar sendiri di chart Lightweight Charts.
- **Engine SMC** adalah implementasi orisinal dari konsep SMC yang umum (bukan salinan kode
  indikator pihak ketiga). Aturannya terdokumentasi di tab Metodologi dan `engine/app/smc.py`.
- **Sentimen berita** berbasis leksikon (EN + ID) dengan penanganan negasi — cepat & transparan,
  tetapi tidak memahami konteks sepenuhnya.
- Butuh koneksi internet (data harga, RSS, widget, font, library CDN).

## Pengembangan

```bash
cd engine && python -m pip install -r requirements.txt pytest && python -m pytest -q tests
dotnet build gateway/SmcTerminal.Gateway.csproj -c Release
dotnet publish desktop/SmcTerminal.Desktop.csproj -c Release -r win-x64 --self-contained false -o app   # Windows
```
