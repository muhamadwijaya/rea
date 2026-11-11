# SMC Terminal — Screener Saham & Forex (Smart Money Concept)

Terminal lokal untuk memindai **forex, emas, indeks, kripto, saham AS dan saham IDX** dengan analisa
**Smart Money Concept** (struktur BOS/CHoCH, order block, FVG, likuiditas, premium/discount), **berita
realtime**, **kalender ekonomi**, dan ringkasan **arah market**. Chart realtime memakai widget resmi
TradingView; overlay SMC digambar di atas TradingView Lightweight Charts.

> Bukan saran investasi. Semua skor dan skenario dihasilkan aturan otomatis.

## Menjalankan (Windows)

1. Install **Python 3.10+** — <https://www.python.org/downloads/> (centang *Add python.exe to PATH*).
2. Install **.NET SDK 8** (atau lebih baru) — <https://dotnet.microsoft.com/download/dotnet/8.0>.
3. Klik dua kali **`start.bat`**.

Run pertama membuat virtualenv Python, menginstall dependensi, dan mem-build gateway C# (beberapa
menit). Setelah itu dua jendela log terbuka (Engine Python & Gateway C#) dan browser membuka
<http://localhost:5080>. Scan screener pertama butuh ±20 detik; berikutnya memakai cache.

Untuk berhenti: klik dua kali **`stop.bat`** (atau tutup kedua jendela log).

Linux/macOS: `./start.sh`.

## Tab

| Tab | Isi |
| --- | --- |
| **Dashboard** | Pencarian simbol, 7 langkah cara kerja, radar SMC seluruh watchlist, *verdict stack*, arah market hari ini, KPI, analisa terbaru, sinyal struktur, alert live |
| **Screener** | Tabel SMC 49 instrumen (filter kelas aset / bias / TF 1H-4H-1D, sortir per kolom) + TradingView Screener realtime (forex, saham AS, saham Indonesia, kripto) |
| **Analisa SMC** | Chart SMC (BOS/CHoCH swing & internal, HH/HL/LH/LL, order block, FVG, EQH/EQL, BSL/SSL, premium/discount, EMA, garis entry/SL/TP), mode TradingView Live / Split, skor bias + komponennya, multi-timeframe 1D/4H/1H, skenario trading, narasi, level kunci, gauge teknikal TradingView, berita simbol |
| **Arah Market** | Regime risk-on/risk-off, arah USD, saham AS, IHSG, emas, minyak, kripto, yield; kekuatan mata uang G8; event high-impact 48 jam; sentimen berita; heatmap forex & S&P 500 |
| **Berita** | Berita realtime dari 19 sumber RSS (push via SignalR), sentimen & tag aset per headline, filter kategori/sentimen/kata kunci, TradingView News |
| **Kalender** | Kalender ekonomi minggu ini (countdown event high-impact berikutnya, filter dampak & mata uang) + kalender TradingView |
| **Metodologi** | Semua aturan engine, bobot skor, sumber data dan keterbatasan |

## Arsitektur

```
Browser (HTML/CSS/JS, Lightweight Charts, widget TradingView)
   │  REST /api/*  +  SignalR /hub/market (push berita, screener, alert, statistik)
   ▼
Gateway C# — ASP.NET Core 8  (gateway/, port 5080)
   • menyajikan UI & API, push realtime
   • agregasi 19 feed RSS (ETag/If-Modified-Since, dedup), kalender ForexFactory
   • refresh screener & arah market berkala, deteksi alert (CHoCH/BOS baru, bias berubah,
     berita bersentimen kuat, event high-impact ≤ 30 menit)
   │  HTTP lokal 127.0.0.1:8765
   ▼
Engine Python — FastAPI  (engine/)
   • data OHLC Yahoo Finance (yfinance) + cache TTL per timeframe, unduhan batch
   • engine SMC (smc.py), skor bias, skenario & narasi (analysis.py)
   • screener (screener.py), arah market & kekuatan mata uang (direction.py)
   • sentimen headline EN/ID (sentiment.py)
```

## Konfigurasi

- **Watchlist**: `config/watchlist.json` — `symbol`, `name`, `yahoo` (ticker data), `tv` (simbol
  TradingView), `class` (`forex | us | idx | index | commodity | crypto`). Simbol lain tetap bisa
  dianalisa lewat kotak pencarian (mis. `IDX:ANTM`, `NYSE:KO`, `CADJPY`, `^FTSE`).
- **Gateway**: `gateway/appsettings.json` → bagian `Terminal`: daftar feed berita, interval refresh
  berita/screener/arah market, timeframe screener default, URL kalender.
- **Port**: variabel `ENGINE_PORT` dan `WEB_PORT` di awal `start.bat` / `stop.bat`.

## API

| Endpoint | Keterangan |
| --- | --- |
| `GET /api/analyze?symbol=EURUSD&tf=1h` | Analisa SMC lengkap + candle + MTF (`tf`: 15m, 1h, 4h, 1d, 1w) |
| `GET /api/screener?group=all&tf=4h` | Screener (`group`: all, forex, us, idx, macro) |
| `GET /api/direction` | Arah market + sentimen berita + event high-impact |
| `GET /api/news?category=&sentiment=&q=&limit=` | Berita tersimpan (terbaru dulu) |
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
```
