# SMC Terminal — Screener Saham & Forex (Smart Money Concept)

Terminal lokal untuk memindai **forex, emas, indeks, kripto, saham AS dan saham IDX** dengan analisa
**Smart Money Concept** (struktur BOS/CHoCH, order block, FVG, likuiditas, premium/discount), **berita
realtime**, **kalender ekonomi**, dan ringkasan **arah market**. Chart realtime memakai widget resmi
TradingView; overlay SMC digambar di atas TradingView Lightweight Charts.

> Bukan saran investasi. Semua skor dan skenario dihasilkan aturan otomatis.

## Menjalankan (Windows) — aplikasi desktop

1. Install **Python 3.10+** — <https://www.python.org/downloads/> (centang *Add python.exe to PATH*).
2. Install **.NET SDK 8** (atau lebih baru) — <https://dotnet.microsoft.com/download/dotnet/8.0>.
3. Klik dua kali **`start.bat`**.

`start.bat` menjalankan `setup.bat` secara otomatis bila perlu (membuat virtualenv Python, menginstall
dependensi, mem-build aplikasi ke folder `app\`), lalu membuka **SMC Terminal** sebagai aplikasi Windows
dengan jendelanya sendiri — bukan di browser, tanpa jendela konsol. Engine Python berjalan tersembunyi
di belakang aplikasi dan ikut berhenti saat jendela ditutup.

| File | Fungsi |
| --- | --- |
| `start.bat` | Buka aplikasi desktop (setup otomatis bila belum) |
| `buat-shortcut.bat` | Buat ikon **SMC Terminal** di Desktop (langsung membuka `app\SMCTerminal.exe`) |
| `setup.bat` | Install/build manual; `setup.bat --rebuild` untuk build ulang setelah mengubah kode |
| `start-browser.bat` | Cadangan: server di satu jendela konsol, UI di browser (http://127.0.0.1:5080) |
| `stop.bat` | Paksa hentikan semua proses SMC Terminal |

Setiap kali dibuka, aplikasi tampil **layar penuh** (tanpa title bar/tombol close Windows) dan menampilkan
**loading screen** berisi tahapan — gateway, koneksi realtime, engine Python, data harga & screener, arah
market, berita, kalender — sampai semuanya siap. Kontrol jendela ada di pojok kanan atas aplikasi:
**─** minimize, **❐** mode jendela/layar penuh (juga **F11**), **✕** keluar. **F5** memuat ulang tampilan.
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
| **Dashboard** | KPI, pencarian simbol, kartu FOREX / SAHAM AS / SAHAM IDX / KRIPTO, radar SMC + *verdict stack*, tab ARAH MARKET · SINYAL · ALERT · ANALISA |
| **Forex** · **Saham AS** · **Saham IDX** · **Kripto** | Tiap pasar punya 4 sub-tab sendiri ↓ |
| ↳ Analisa SMC | Watchlist pasar + chart SMC (BOS/CHoCH, HH/HL/LH/LL, order block, FVG, EQH/EQL, BSL/SSL, premium/discount, EMA, entry/SL/TP; layer lewat menu LAYER SMC) atau chart TradingView live / split; panel kanan bertab BIAS · MTF · PLAN · NARASI · LEVEL · TV · BERITA |
| ↳ Screener | Tab SMC SCREENER (filter bias, TF 1H/4H/1D, sortir) · tab TRADINGVIEW SCREENER realtime pasar itu |
| ↳ Berita | Berita realtime khusus pasar itu (push SignalR), sentimen per aset, filter, TradingView News pasar itu, status sumber |
| ↳ Arah Market | Tab RINGKASAN (breadth, regime, sentimen, outlook) · KEKUATAN MATA UANG (forex) · PENGGERAK & STRUKTUR · INSTRUMEN KUNCI · EVENT HIGH-IMPACT · PETA PASAR (heatmap TradingView) |
| **Kalender** | Kalender ekonomi minggu ini + countdown event high-impact + kalender TradingView |
| **Metodologi** | Tab CARA KERJA 7 LANGKAH · STRUKTUR · OB/FVG/LIKUIDITAS · SKOR · ARAH & BERITA · DATA & ARSITEKTUR |

Watchlist bawaan: 13 forex + emas/perak/minyak + DXY (FOREX), 12 saham AS + SPX/NDX/DJI/VIX/US10Y
(SAHAM AS), 12 saham BEI + IHSG (SAHAM IDX), 10 koin (KRIPTO). Berita dari 28 sumber RSS dibagi per pasar
berdasarkan kategori feed dan aset yang disebut di judul.

## Arsitektur

```
SMCTerminal.exe — aplikasi desktop C# (WinForms + WebView2, desktop/)
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
   • sentimen headline EN/ID + tag aset (sentiment.py)
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
| `GET /api/analyze?symbol=EURUSD&tf=1h` | Analisa SMC lengkap + candle + MTF (`tf`: 15m, 1h, 4h, 1d, 1w) |
| `GET /api/screener?market=all&tf=4h` | Screener (`market`: all, forex, saham-as, saham-idx, kripto) |
| `GET /api/direction` | Arah market + sentimen berita + event high-impact |
| `GET /api/news?market=kripto&category=&sentiment=&q=&limit=` | Berita per pasar (terbaru dulu) |
| `GET /api/news/sentiment?market=forex` | Ringkasan sentimen 12 jam per pasar |
| `GET /api/symbol-news?symbol=AAPL` | Berita per simbol |
| `GET /api/calendar` · `/api/alerts` · `/api/stats` · `/api/health` | Kalender, alert, KPI, status |

## Sumber data & keterbatasan

- **Analisa SMC** memakai Yahoo Finance via `yfinance` (gratis, tanpa API key). Data bisa tertunda
  tergantung bursa; emas & perak memakai kontrak futures (`GC=F`, `SI=F`) sebagai proksi XAUUSD/XAGUSD.
  Chart SMC diperbarui tiap 60 detik.
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
