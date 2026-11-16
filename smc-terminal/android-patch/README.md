# SHARX INVEST Android — patch Fix10 (halaman baca berita, isi berita, chart TradingView)

Patch ini dipasang di atas APK Android **SHARX-INVEST-AI-Fix9.apk** (paket `com.sharx.trader`, versi
3.20-independent.9). Source Android-nya tidak ada di repo ini. Patch dibuat dari APK itu: kode Java baru, hook smali di
`MainActivity`, perubahan UI web (`assets/wwwroot`) dan engine Python (Chaquopy `app.imy`). Di aplikasi, status bar
menampilkan versi **3.20-independent.10**.

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
- **`ReaderHooks.java` hanya dikompilasi** (javac + d8, API 26) dan hook smali diperiksa dengan dexdump. Di server ini
  tidak ada emulator Android (tanpa KVM), jadi perilaku native belum dijalankan di perangkat.

## Build

```bash
APKTOOL=apktool_2.10.0.jar BUILD_TOOLS=build-tools/36 ANDROID_JAR=android-35/android.jar \
  ./build.sh SHARX-INVEST-AI-Fix9.apk release.jks store-pass.txt SHARX-INVEST-AI-Fix10.apk
```

Perlu Java 17+, python3, npm (Readability.js 0.5.0, Apache-2.0, diunduh saat build), unzip, zip dan patch. Resource
dan manifest APK tidak diubah (apktool `-r`). APK hasil ditandatangani kunci baru. Kunci asli Fix9 tidak tersedia,
jadi **versi lama harus di-uninstall dulu** (API key AI perlu dimasukkan lagi). Simpan keystore itu di tempat aman
dan pakai lagi untuk versi berikutnya. Keystore dan APK tidak disimpan di repo ini.
