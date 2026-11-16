# SHARX INVEST Android — patch Fix10–Fix11 (halaman baca berita, isi berita, AI tanpa macet, chart TradingView)

Patch ini dipasang di atas APK Android **SHARX-INVEST-AI-Fix9.apk** (paket `com.sharx.trader`, versi
3.20-independent.9). Source Android-nya tidak ada di repo ini. Patch dibuat dari APK itu: kode Java baru, hook smali di
`MainActivity`, perubahan UI web (`assets/wwwroot`) dan engine Python (Chaquopy `app.imy`). Di aplikasi, status bar
menampilkan versi **3.20-independent.11** (Fix11; Fix10 = .10).

## Perubahan

1. **Halaman baca berita di dalam aplikasi** (`ReaderHooks.java`, `web/reader.js`, `web/voice.js.diff`).
   - Judul berita, "Buka sumber ↗" dan link luar lain tidak lagi membuka browser atau aplikasi lain. Halaman artikel
     tampil di atas aplikasi, dengan tombol ✕ TUTUP, 🔊 BACAKAN, AI ANALISA dan BROWSER ↗ (cadangan bila halaman perlu
     dibuka di luar). Tombol kembali Android membuka halaman sebelumnya atau menutup halaman baca.
   - Setelah halaman tampil, teks artikel diambil dari halaman yang sedang dilihat (Mozilla Readability, cadangan:
     paragraf halaman). Teks itu dibacakan suara AI, lalu dianalisa AI.
   - Teks yang sama disimpan engine sebagai isi artikel berita itu (`POST /api/news/article-text`), jadi AI menganalisa
     persis yang dibacakan. Halaman yang judulnya bukan berita itu tidak dipakai untuk analisa.
   - Bila halaman meminta verifikasi (cek bot/captcha), pengguna menyelesaikannya sendiri di halaman baca. Teks diambil
     otomatis setelah berita tampil (dicoba ±2 menit, setelah itu lewat tombol BACAKAN).
   - BACAKAN: hanya membacakan ulang. AI ANALISA: hanya menganalisa isi halaman. Berita yang dibuka mendahului
     antrean asisten; bacaan yang terpotong dilanjutkan sesudahnya.
2. **Pengambilan isi berita latar belakang** (`python/article_retrieval.py.diff`).
   - Bug "bukan artikel" diperbaiki: halaman yang menulis `og:type=website` tetapi judulnya sama dengan judul berita
     (mis. Antara) tidak lagi ditolak.
   - Bila HTTP biasa gagal (redirect JavaScript seperti Google News, situs yang hanya melayani browser), engine memakai
     browser tersembunyi aplikasi (`ReaderHooks.fetch`). Browser ini memakai user agent WebView biasa (tidak diubah),
     satu halaman sekaligus, tanpa gambar, batas 30 detik.
   - Halaman yang menampilkan cek bot atau captcha tidak dipaksa: berita itu tetap tanpa isi lengkap dan bisa dibuka di
     halaman baca.
3. **Ticker TradingView** (`web/tv.js.diff`, `web/app.js.diff`, `ReaderHooks.tradingViewSymbol`).
   - Klik simbol di ticker atas membuka halaman chart aset itu di aplikasi (`largeChartUrl` →
     `#tv?tvwidgetsymbol=FX:EURUSD` → `#forex/analisa/EURUSD/1h`).
   - Link TradingView lain yang membawa simbol (`/symbols/FX-EURUSD/`, `?symbol=`) juga membuka chart aplikasi, bukan
     aplikasi TradingView.

## Fix11

1. **Setiap berita baru dibuka otomatis.**
   - Asisten membuka halaman berita di halaman baca, mengambil teksnya, lalu menutup halaman itu sendiri supaya chart
     tidak tertutup.
   - Teks dibacakan, sementara AI menganalisa teks yang sama secara paralel. Setelah bacaan selesai, hasil AI
     dibacakan.
   - Halaman yang dibuka manual tidak ditutup otomatis. Menyentuh halaman otomatis menjadikannya manual.
   - Halaman otomatis menyerah setelah 30 detik tanpa teks, atau 6 detik setelah muncul halaman verifikasi yang tidak
     disentuh. Setelah itu AI memakai pengambilan isi engine.
   - Asisten tidak pernah mengambil alih halaman yang sedang dibaca manual.
2. **Kegagalan AI tidak menghentikan antrean.**
   - Analisa AI (berita, aset, kalender) dicoba lagi sampai 3 kali dengan jeda 5 detik. Kalau tetap gagal, alasannya
     ditampilkan dan dibacakan (mis. kuota harian OpenRouter habis), lalu asisten lanjut sendiri ke item berikutnya.
   - Antrean lama yang tersimpan dalam keadaan tertunda berjalan lagi otomatis.
   - Engine menjaga permintaan OpenRouter di bawah ±20 per menit: permintaan ke-17 dalam satu menit menunggu, bukan
     gagal.
   - Saat kuota harian habis, engine langsung menyebut alasan dan jam pulihnya tanpa membuang permintaan.
3. **Kalender ekonomi.**
   - Sumber publik event dibuat sekali (`POST /api/calendar/sources`, berlaku 15 menit).
   - Halaman 6 sumber teratas dibuka otomatis dan teksnya diambil. Isi artikel (≤3.000 karakter per sumber, ≤18.000
     total) ikut ke fakta AI. Sumber lain yang belum punya teks diambil cepat lewat HTTP.
   - Hasilnya lalu dibacakan.

## Terukur dan diuji

- **Kode Python APK dijalankan di server ini** terhadap 3 berita terbaru dari setiap 40 feed:
  - Sebelum patch: isi lengkap 65 dari 120.
  - Dengan perbaikan bug identitas: 71 dari 117.
  - Sisanya (Investing.com, MarketWatch, Nasdaq, Bloomberg, Google News, CoinDesk, The Block, Seeking Alpha) menolak
    HTTP non-browser atau butuh JavaScript, jadi bergantung pada browser aplikasi.
  - Di Chromium headless dari IP server ini, sebagian besar situs itu menampilkan cek bot, dan link Google News tidak
    berpindah ke penerbit.
  - **Hasil di HP belum diukur.**
- **UI web diuji di Chromium** dengan server Python APK yang dijalankan di sini dan jembatan `SharxReader` tiruan:
  - Ticker → chart: EURUSD dan IHSG.
  - Klik judul berita dan "Buka sumber" → halaman baca, tanpa popup.
  - Teks halaman → disimpan engine → dibacakan → analisa AI. Mode BACAKAN dan AI ANALISA juga diuji.
  - Tidak ada error halaman.
- **Fix11 diuji di Chromium** dengan server Python APK, halaman baca tiruan (mode otomatis) dan OpenRouter tiruan:
  - **Berita baru:** halaman dibuka otomatis → teks disimpan → dibacakan → AI (gagal 1×, model berikutnya berhasil)
    → hasil AI dibacakan.
  - **AI selalu gagal:** 3 percobaan × 2 model per item, lalu asisten lanjut ke berita berikutnya. Antrean tidak
    berhenti; 2 item selesai dalam 30 detik.
  - **Halaman tanpa teks:** AI tetap jalan.
  - **Kalender:** 6 halaman sumber dibuka, 9 dari 12 sumber sampai ke AI dengan isi artikel, lalu dibacakan.
  - **Uji unit Python:** jeda per menit, kuota harian, sumber kalender.
- **`ReaderHooks.java` hanya dikompilasi** (javac + d8, API 26) dan hook smali diperiksa dengan dexdump. Di server ini
  tidak ada emulator Android (tanpa KVM), jadi perilaku native belum dijalankan di perangkat.

## Build

```bash
APKTOOL=apktool_2.10.0.jar BUILD_TOOLS=build-tools/36 ANDROID_JAR=android-35/android.jar \
  ./build.sh SHARX-INVEST-AI-Fix9.apk release.jks store-pass.txt SHARX-INVEST-AI-Fix10.apk
```

Perlu Java 17+, python3, npm (Readability.js 0.5.0, Apache-2.0, diunduh saat build), unzip, zip dan patch. Resource
dan manifest APK tidak diubah (apktool `-r`). APK hasil ditandatangani kunci baru. Kunci asli Fix9 tidak tersedia,
jadi **Fix9 harus di-uninstall dulu** (API key AI perlu dimasukkan lagi). Fix11 memakai kunci yang sama dengan Fix10,
jadi bisa dipasang di atas Fix10 tanpa uninstall. Simpan keystore itu di tempat aman
dan pakai lagi untuk versi berikutnya. Keystore dan APK tidak disimpan di repo ini.
