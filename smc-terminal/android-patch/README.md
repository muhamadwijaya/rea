# SHARX INVEST Android — patch Fix10–Fix13 (halaman baca berita, isi berita, AI tanpa macet, chart TradingView, JARVIS)

Patch ini dipasang di atas APK Android **SHARX-INVEST-AI-Fix9.apk** (paket `com.sharx.trader`, versi
3.20-independent.9). Source Android-nya tidak ada di repo ini. Patch dibuat dari APK itu: kode Java baru, hook smali di
`MainActivity`, perubahan UI web (`assets/wwwroot`) dan engine Python (Chaquopy `app.imy`). Di aplikasi, status bar
menampilkan versi **3.20-independent.13** (Fix13; Fix12 = .12, Fix11 = .11, Fix10 = .10).

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

## Fix12

1. **Pop-up asisten:**
   - Tombol (DETAIL ANALISA, BUKA CHART, BACAKAN SUMBER, BUKA BERITA, STOP SEMUA) berada di atas teks dan tidak ikut
     di-scroll. Teks analisa yang panjang di-scroll di dalam pop-up.
   - Dulu `.va-body` (min-height 18px di dalam kolom flex yang tingginya dibatasi) mengecil, sehingga isinya meluber
     dan tertimpa baris tombol.
2. **Antrean tertunda lanjut sendiri.** Misalnya saat suara perangkat gagal: setelah 5 detik asisten lanjut ke item
   berikutnya, apa pun penyebabnya. Tombol LANJUTKAN melakukan hal yang sama, tanpa menunggu.
3. **Pop-up analisa kalender ekonomi memakai template yang sama dengan berita:**
   - Tag KALENDER, jadwal/forecast/actual, dan jumlah sumber yang dianalisa dengan isi artikel.
   - Label perkiraan (DI ATAS / SESUAI / DI BAWAH / BELUM PASTI) dengan skor keyakinan, ringkasan, dan tabel aset
     (SMC, tindakan).
   - Rincian ada di DETAIL ANALISA.
   - Tombol BACAKAN SUMBER membacakan isi sumber yang dipakai AI. BUKA SUMBER membuka sumber pertama di halaman baca.

## Fix13: JARVIS di HP

Tab **JARVIS** menjalankan inti asisten **Mark LVII** oleh FatihMakes ([github.com/FatihMakes/Mark-LVII](https://github.com/FatihMakes/Mark-LVII),
[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/), **hanya non-komersial**) di HP itu sendiri, tanpa PC.

1. **Yang sama dengan Mark LVII:**
   - Model Gemini Live dengan urutan Mark LVII: `gemini-3.1-flash-live-preview`, lalu
     `gemini-2.5-flash-native-audio-preview-12-2025`.
   - Suara (Charon, Puck, Kore, Fenrir, Aoede), transkrip, session resumption dan sliding window.
   - Prompt persona Mark LVII (`core/prompt.txt`). Hanya bagian `[SELF]` diganti untuk HP.
   - Memori jangka panjang Mark LVII (`memory/memory_manager.py`, format file sama).
   - Alat `save_memory`, `recall_memory`, `weather_report`, `reminder`, `shutdown_jarvis` dengan nama dan parameter Mark
     LVII.
   - Kedua file Mark LVII dibundel **tanpa diubah** dari commit `752ff61` (cek SHA-256 saat build), bersama `LICENSE`,
     `NOTICE` dan `COMMIT`.
2. **Alat di HP:**
   - Waktu, cuaca (Open-Meteo), membaca halaman web (ditandai sebagai konten tak tepercaya), pencarian Google (alat
     bawaan Gemini Live), pengingat.
   - Analisa SMC, harga, berita dan kalender ekonomi SHARX, serta membuka chart SHARX.
   - Volume, senter, baterai, layar tetap menyala, membuka pengaturan, aplikasi, tautan dan peta.
   - Panggilan, SMS, WhatsApp dan e-mail **hanya terbuka siap kirim**: pengguna yang menekan kirim atau panggil.
   - Tidak ada izin Android baru: manifest tidak diubah.
3. **Suara:**
   - Mikrofon 16 kHz PCM (AudioWorklet) dialirkan ke engine lewat `/api/jarvis/live` (WebSocket lokal, cookie sesi).
     Engine meneruskannya ke Gemini Live (pustaka `websockets` 15.0.1, BSD-3, dari PyPI dengan cek SHA-256).
   - Balasan 24 kHz diputar di WebView. **Gemini API key** disimpan di Android Keystore (`SecretVault`) dan tidak
     pernah dikirim ke halaman web.
   - Mode speaker HP: mikrofon jeda selama JARVIS bicara. Mode headset: bisa memotong dengan suara. Ketuk orb untuk
     memotong.
   - Selama percakapan JARVIS berjalan, atau selama pengguna mengetik di tab JARVIS, asisten berita menunda pekerjaan
     dan suaranya. Pekerjaan yang sedang jalan kembali ke antrean.
4. **HP → PC** (bila Mark LVII jalan di PC):
   - Pasangkan sekali dengan kode Remote Control dari HUD PC. Engine memakai halaman auto-login Mark LVII dan menyimpan
     device token di Keystore.
   - Alat `pc_command` mengirim permintaan ke `/api/command` Mark LVII dan mengembalikan jawaban JARVIS PC dari log
     `/ws`-nya.
   - Panel lengkap PC tetap bisa dibuka di **PANEL PC**. Panel itu meminta kode Remote Control sendiri, karena kode
     hanya sekali pakai.
5. **PC → HP** (opsional, mati secara bawaan):
   - Centang "Izinkan PC mengontrol HP ini". HP lalu menerima permintaan di jaringan lokal (port 8010, hanya alamat
     privat) dengan kode sekali pakai (10 menit, dibatasi 6 percobaan per 5 menit).
   - Setelah pairing PC memakai token; HP hanya menyimpan SHA-256-nya.
   - Permintaan PC masuk ke percakapan JARVIS di HP (dimulai sendiri bila aplikasi terbuka) dan jawabannya dikirim
     balik ke PC.
   - Di PC: SHARX desktop → tab JARVIS → **KONTROL HP**. SHARX desktop juga memasang plugin `phone_command` ke Mark
     LVII, sehingga JARVIS di PC bisa menyuruh HP lewat suara.
6. **Batas:**
   - Fitur desktop Mark LVII (mouse/keyboard, aplikasi Windows, file PC, layar/kamera, otomasi browser) tidak bisa
     berjalan di HP. JARVIS HP meneruskannya ke PC bila terpasang.
   - JARVIS HP berjalan selama aplikasi terbuka; pengingat juga berbunyi saat aplikasi di latar, selama engine SHARX
     masih hidup.
   - Lewat HTTP biasa di Wi-Fi, perintah antara HP dan PC tidak dienkripsi (seperti dashboard Mark LVII). Akses dijaga
     kode pairing dan token: pakai di jaringan rumah sendiri.

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
- **Fix12 diuji di Chromium (layar 1600×720):**
  - Pop-up berita dan kalender dengan teks panjang: urutan judul → tombol → teks, tombol tidak menimpa teks, teks
    bisa di-scroll, tombol tetap di tempat saat teks digulir.
  - Antrean tertunda lanjut ke item berikutnya setelah 5,1 detik.
  - Skenario A–D Fix11 diulang dan lulus.
- **`ReaderHooks.java` hanya dikompilasi** (javac + d8, API 26) dan hook smali diperiksa dengan dexdump. Di server ini
  tidak ada emulator Android (tanpa KVM), jadi perilaku native belum dijalankan di perangkat.
- **Fix13 diuji di Chromium** (emulasi Pixel 7 landscape, mikrofon tiruan Chromium) dengan:
  - Server Python APK yang dijalankan di sini.
  - **Gemini Live tiruan**: server WebSocket lokal yang berbicara dengan format JSON Live API. Gemini asli tidak diuji
    karena tidak ada key dan server ini tidak bisa menjangkaunya.
  - **Dashboard Mark LVII asli** (`dashboard/server.py` dari pemasangan nyata, tanpa HUD).
  - Hasilnya, 15 langkah lulus:
    - Tanpa key → BUTUH API KEY. Key tersimpan dan tidak muncul di JSON halaman.
    - Model pertama ditolak → pindah ke model kedua. Audio mikrofon 16 kHz → transkrip → alat → balasan audio.
    - Memori Mark tersimpan dan masuk prompt sesi berikutnya. Senter (tanpa Java di sini: "not available").
    - Pengingat dibuat, lalu jatuh tempo dan masuk ke percakapan. `open_chart` membuka chart XAUUSD.
    - Pairing PC dengan kode asli, lalu `pc_command` → jawaban PC kembali.
    - Interupsi → audio berhenti. goAway → sambung ulang dengan handle. Panel PC tampil. `shutdown_jarvis`.
- **PC → HP diuji** dengan gateway desktop (port 5080), server Python APK dan plugin `phone_command` yang dijalankan
  dengan Python milik pemasangan Mark LVII. 10 langkah lulus:
  - Kode salah ditolak, kode benar dipasangkan, kode yang sama ditolak lagi (sekali pakai).
  - Perintah saat percakapan HP mati: dimulai sendiri, dijawab, lalu selesai. Perintah juga lewat plugin Mark.
  - Perintah saat percakapan HP aktif: bergabung ke percakapan.
  - Setelah "lupakan semua PC" ditolak; setelah izin dimatikan, HP tidak menjawab.
- **Uji unit Python:** alamat PC (hanya jaringan privat), hasil probe.
- **`JarvisHooks.java` hanya dikompilasi** (javac + d8). Aksi HP, mikrofon/AEC WebView dan suara di perangkat
  **belum diuji di HP**.

## Build

```bash
APKTOOL=apktool_2.10.0.jar BUILD_TOOLS=build-tools/36 ANDROID_JAR=android-35/android.jar \
  ./build.sh SHARX-INVEST-AI-Fix9.apk release.jks store-pass.txt SHARX-INVEST-AI-Fix13.apk
```

Perlu Java 17+, python3 (dengan pip), npm (Readability.js 0.5.0, Apache-2.0, diunduh saat build), curl, unzip, zip dan
patch. File Mark LVII diambil dari checkout di `MARK_LVII=/path/ke/Mark-LVII` bila diisi; tanpa itu, arsip commit
`752ff61` diunduh dari GitHub. Keduanya dicek SHA-256, begitu juga wheel `websockets` dari PyPI. Resource
dan manifest APK tidak diubah (apktool `-r`). APK hasil ditandatangani kunci baru. Kunci asli Fix9 tidak tersedia,
jadi **Fix9 harus di-uninstall dulu** (API key AI perlu dimasukkan lagi). Fix11 dan Fix12 memakai kunci yang sama dengan
Fix10, jadi bisa dipasang di atas Fix10/Fix11 tanpa uninstall (Fix13 juga). Simpan keystore itu di tempat aman
dan pakai lagi untuk versi berikutnya. Keystore dan APK tidak disimpan di repo ini.
