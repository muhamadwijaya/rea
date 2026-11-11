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
| **AI Training** | Virtual trading sinyal SMC: win rate live & backtest out-of-sample vs target 70% dan tujuan 99%, kurva ekuitas (R), papan akurasi per aset & timeframe, jurnal sinyal (muncul → terdeteksi → entry → hasil), log upgrade SMC otomatis |
| **Kalender** | Kalender ekonomi minggu ini + countdown event high-impact + kalender TradingView |
| **Metodologi** | Tab CARA KERJA 7 LANGKAH · STRUKTUR · OB/FVG/LIKUIDITAS · SKOR · ARAH & BERITA · DATA & ARSITEKTUR |

**Aset**: 57 aset inti (`config/watchlist.json`, dipindai SMC 1H/4H/1D) + **universe ±2.100 aset**
(`config/universe.json`): seluruh S&P 500 + saham AS populer & ETF, seluruh saham BEI yang tersedia di Yahoo
(±880), **±600 kripto** dari 1.000 teratas CoinGecko yang tersedia di Yahoo (identitas ticker dicek dengan harga CoinGecko,
termasuk ticker bernomor seperti `SUI20947-USD`; versi wrapped/staked/bridged dan saham ter-tokenisasi tidak dimasukkan
karena duplikat; kategori dari CoinGecko), 44 pair forex mayor/cross/eksotis
dan komoditas. Seluruh universe dipindai SMC **1D tiap 20 menit dan 4H tiap 40 menit** (±1,5–2 menit per sapuan),
dan harga open/last-nya diperbarui bergiliran tiap ±2 menit. Perbarui daftar dengan `python engine/tools/build_universe.py` (butuh `engine/tools/requirements.txt`).
Berita dari 28 sumber RSS dibagi per pasar dan ditautkan ke aset (ticker, nama perusahaan/koin, alias mata uang).

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

1. Saat berita baru yang **terkait aset** masuk, asisten membacakan tag-nya: aset, sentimen (naik / netral / turun)
   dan judul berita.
2. Lalu bertanya *"Mau saya bacakan berita lengkapnya, lalu jalankan analisa AI?"*. Jawab dengan **suara** ("ya" /
   "tidak", dikenali offline lewat mikrofon), klik **YA / TIDAK**, atau tekan **Y / N**. Tanpa jawaban = dilewati.
3. Bila **ya**: isi berita lengkap dibacakan, sementara **analisa AI** aset itu berjalan bersamaan; setelah berita selesai,
   **hasil AI dibacakan** (sinyal BELI / JUAL / NETRAL, keyakinan, ringkasan, pendorong, risiko, konfluensi SMC).
4. Tombol **🔊** di setiap berita (tab BERITA) langsung membacakan berita lengkap + hasil AI tanpa bertanya.

Pilihan: semua berita bertag / hanya aset inti / hanya sentimen kuat; kecepatan & volume; mikrofon on/off; suara
Piper atau suara Windows. Bila berita datang beruntun, hanya 3 terbaru yang dibacakan (yang lebih dari 5 menit dilewati).

**Suara — gratis, tanpa batas, offline**: [Piper](https://github.com/OHF-Voice/piper1-gpl) (suara neural) dengan
suara **Indonesia** `id_ID-news_tts-medium` dan **English** `en_US-lessac-medium` (masing-masing ±63 MB), serta
pengenal jawaban **Whisper tiny** (±75 MB, via faster-whisper). Model diunduh sekali dari pengaturan suara (UNDUH SEMUA),
diverifikasi checksum, disimpan di `%LOCALAPPDATA%\SharxInvest\voices` dan `\stt`; setelah itu tidak perlu internet
untuk suara. Paket Python-nya (`engine/requirements-voice.txt`) dipasang `setup.bat`; bila gagal, aplikasi tetap
berjalan dan memakai suara Windows. Lisensi: Piper GPL-3.0 (dipasang terpisah, tidak dibundel); model suara Piper
mengikuti lisensi dataset masing-masing — untuk **pemakaian pribadi / non-komersial**. Kualitas Piper "medium":
jelas dan natural, tetapi belum sehalus suara neural berbayar.

**Isi berita lengkap** diambil dari halaman artikel seperti pembaca biasa (trafilatura). Situs yang menolak pembaca
otomatis (HTTP 403/429, verifikasi bot, paywall) dan tautan Google News tidak diakali: asisten mengatakannya lalu
membacakan ringkasan dari feed; tombol BUKA BERITA membuka artikel di browser.

## Virtual trading & upgrade SMC otomatis (AI Training)

Setiap aset inti (TF 15M/1H/4H/1D) dan seluruh universe (TF 1D) dipantau. Strategi (kausal — hanya memakai bar
sampai saat itu): break struktur (CHoCH/BOS) membuat **sinyal** dari order block-nya — entry limit di OB, stop di balik
OB ± ATR, target = likuiditas impuls atau RR tetap (minimal **1:1**).

- **Timestamp**: waktu bar sinyal muncul, waktu sistem pertama mendeteksinya, waktu kedaluwarsa, waktu entry terpicu,
  waktu selesai.
- **Batal otomatis** sebelum entry bila: struktur berbalik (CHoCH/BOS berlawanan), harga mencapai target lebih dulu,
  harga close menembus stop, atau kedaluwarsa (mis. 30 bar).
- **Eksekusi virtual** saat harga menyentuh entry → PROFIT (target) / LOSS (stop; bila satu bar menyentuh keduanya
  dihitung LOSS) / ditutup waktu. Hasil dalam R; dicatat di `training.db` (SQLite) di folder data.
- **Akurasi**: trade live yang selesai + backtest walk-forward pada riwayat.
- **Upgrade otomatis**: per aset & timeframe, ±1.150 kombinasi parameter SMC (panjang pivot, scope, CHoCH/BOS, entry,
  stop, target, filter premium/discount & tren, kedaluwarsa) diuji pada 70% riwayat lalu diverifikasi pada 30%
  terakhir. Parameter baru dipakai hanya bila **lower bound win rate (90%) out-of-sample lebih tinggi**, ekspektasi
  tetap positif dan jumlah trade cukup. Diulang tiap 6 jam per aset, sehingga terus membaik seiring data bertambah.
  Panjang pivot hasil upgrade juga dipakai chart & screener aset itu. Setiap upgrade diberi versi dan dicatat.
- **Tujuan 99%** ditampilkan sebagai arah perbaikan, tetapi yang dilaporkan selalu hasil terukur. Win rate setinggi itu
  tidak dapat dijamin dan tidak dikejar dengan cara yang menipu (target sangat kecil / stop sangat jauh / overfitting).

## Arsitektur

```
SharxInvest.exe — aplikasi desktop C# (WinForms + WebView2, desktop/)
   │  menampilkan UI dalam jendela sendiri; menjalankan gateway di dalam proses
   ▼
Gateway C# — ASP.NET Core 8 (gateway/)
   • UI & REST API (/api/*), push realtime SignalR (/hub/market)
   • 28 feed RSS (ETag/If-Modified-Since, dedup), klasifikasi berita per pasar, kalender ForexFactory
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
| `GET /api/training/summary` · `GET /api/training/asset?symbol=EURUSD&tf=1h` | Virtual trading: akurasi, jurnal, upgrade |
| `GET /api/voice/status` · `POST /api/voice/setup` | Status & unduh model suara (`{"items":["id","en","stt"]}`) |
| `POST /api/voice/tts` `{"text":"…","lang":"id","rate":1}` | Suara Piper (audio/wav) |
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
