# SHARX INVEST Android — patch Fix10–Fix16 (halaman baca berita, isi berita, AI tanpa macet, chart TradingView, JARVIS)

Patch ini dipasang di atas APK Android **SHARX-INVEST-AI-Fix9.apk** (paket `com.sharx.trader`, versi
3.20-independent.9). Source Android-nya tidak ada di repo ini. Patch dibuat dari APK itu: kode Java baru, hook smali di
`MainActivity`, perubahan UI web (`assets/wwwroot`) dan engine Python (Chaquopy `app.imy`). Di aplikasi, status bar
menampilkan versi **3.20-independent.16** (Fix16; Fix15 = .15, Fix14 = .14, Fix13 = .13, Fix12 = .12, Fix11 = .11,
Fix10 = .10).

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

## Fix13–Fix14: JARVIS di HP

Tab **JARVIS** menjalankan asisten suara SHARX di HP itu sendiri, tanpa PC.

1. **Percakapan:**
   - Gemini Live: `gemini-3.1-flash-live-preview`, lalu `gemini-2.5-flash-native-audio-preview-12-2025` bila yang
     pertama ditolak. Alasan penolakan tampil sebagai catatan di percakapan.
   - Pilihan suara, transkrip, session resumption dan sliding window.
   - Persona dan memori jangka panjang milik SHARX sendiri (`app/jarvis_phone.py`, `app/jarvis_memory.py`).
2. **Alat di HP:**
   - Waktu, cuaca (Open-Meteo), membaca halaman web (ditandai sebagai konten luar), pencarian Google (alat bawaan
     Gemini Live), pengingat.
   - Analisa SMC, harga, berita dan kalender ekonomi SHARX, serta membuka chart SHARX.
   - Volume, senter, baterai, layar tetap menyala, membuka pengaturan, aplikasi, tautan dan peta.
   - Panggilan, SMS, WhatsApp dan e-mail **hanya terbuka siap kirim**: pengguna yang menekan kirim atau panggil.
   - Tidak ada izin Android baru: manifest tidak diubah.
3. **Suara:**
   - **Fix14:** mikrofon direkam Android sendiri (`JarvisHooks`, AudioRecord 16 kHz) dan diambil engine. Di Fix13,
     getUserMedia WebView gagal di sebagian HP dengan `NotReadableError`.
     - Izin mikrofon diminta bila belum ada.
     - Mode speaker memakai sumber VOICE_RECOGNITION. Mode headset memakai VOICE_COMMUNICATION dengan peredam gema.
   - Engine meneruskan suara ke Gemini Live lewat pustaka `websockets` 15.0.1 (BSD-3, dari PyPI dengan cek SHA-256).
   - Balasan 24 kHz diputar di WebView. **Gemini API key** disimpan di Android Keystore (`SecretVault`) dan tidak
     pernah dikirim ke halaman web.
   - Mode speaker HP: mikrofon jeda selama JARVIS bicara. Mode headset: bisa memotong dengan suara. Ketuk orb untuk
     memotong.
   - Asisten berita menunda pekerjaan dan suaranya selama JARVIS bekerja atau berbicara (Fix15, lihat di bawah) dan
     selama pengguna mengetik di tab JARVIS. Pekerjaan yang sedang jalan kembali ke antrean.
4. **HP → PC** (bila JARVIS jalan di PC lewat SHARX desktop):
   - Pasangkan sekali dengan kode Remote Control dari HUD PC. Engine memakai halaman auto-login dashboard JARVIS PC dan
     menyimpan device token di Keystore.
   - Alat `pc_command` mengirim permintaan ke `/api/command` dashboard PC dan mengembalikan jawaban JARVIS PC dari log
     `/ws`-nya.
   - Panel lengkap PC tetap bisa dibuka di **PANEL PC**. Panel itu meminta kode Remote Control sendiri, karena kode
     hanya sekali pakai.
5. **PC → HP** (opsional, mati secara bawaan):
   - Centang "Izinkan PC mengontrol HP ini". HP lalu menerima permintaan di jaringan lokal (port 8010, hanya alamat
     privat) dengan kode sekali pakai (10 menit, dibatasi 6 percobaan per 5 menit).
   - Setelah pairing PC memakai token; HP hanya menyimpan SHA-256-nya.
   - Permintaan PC masuk ke percakapan JARVIS di HP (dimulai sendiri bila aplikasi terbuka) dan jawabannya dikirim
     balik ke PC.
   - Di PC: SHARX desktop → tab JARVIS → **KONTROL HP**. SHARX desktop juga memasang plugin `phone_command` ke JARVIS
     PC, sehingga JARVIS di PC bisa menyuruh HP lewat suara.
6. **Batas:**
   - Kendali komputer (mouse/keyboard, aplikasi Windows, file PC, layar/kamera, otomasi browser) tidak bisa berjalan
     di HP. JARVIS HP meneruskannya ke PC bila terpasang.
   - JARVIS HP berjalan selama aplikasi terbuka; pengingat juga berbunyi saat aplikasi di latar, selama engine SHARX
     masih hidup.
   - Lewat HTTP biasa di Wi-Fi, perintah antara HP dan PC tidak dienkripsi. Akses dijaga kode pairing dan token: pakai
     di jaringan rumah sendiri.

## Fix15: JARVIS memakai seluruh aplikasi

1. **"Analisa EURUSD"** (alat `analyze_asset`, satu panggilan):
   - JARVIS membuka chart SMC aset itu, menunggu chart selesai digambar, lalu mengirim screenshot chart (candle dan
     overlay SMC, JPEG maks. 1024 px) ke Gemini. JARVIS melihat chart itu sendiri sebelum menjelaskan.
   - Bersamaan dengan itu engine mengambil:
     - analisa SMC (bias, struktur, zona, rencana entry/SL/TP);
     - kalender ekonomi 48 jam untuk mata uang yang menggerakkan aset itu;
     - berita aset itu: feed BERITA-nya dan berita bertag aset itu dalam 24 jam.
   - Bila berita aset itu sedikit, ditambah berita penggeraknya, mis. USD dan EUR untuk EURUSD, atau emas, minyak dan
     kripto. Berita tambahan ini ditandai "bukan tentang aset itu sendiri".
   - Dua artikel terbaru dibuka otomatis di halaman baca (di atas chart), teksnya diambil, lalu halaman ditutup
     sendiri. Teks itu juga disimpan engine sebagai isi berita, sama seperti asisten berita.
   - Batas waktunya 45 detik. Bila chart belum selesai dimuat, JARVIS menjawab dulu dari data, lalu screenshot
     menyusul saat chart tergambar. Ini bisa terjadi saat aplikasi baru dibuka dan screener masih memindai pasar.
     Screenshot susulan dikirim paling lambat 90 detik, dan hanya bila belum ada chart lain yang diminta.
2. **Alat lain:**
   - `look_at_chart`: membuka chart dan melihatnya saja.
   - `read_news`: membuka satu berita (dari [id] `market_news`) di halaman baca, mengambil teksnya lalu menutupnya.
     JARVIS membacakan isinya bila diminta, atau meringkasnya.
   - `open_page`: membuka halaman mana pun:
     - dashboard;
     - forex, saham-as, saham-idx dan kripto, beserta bagian analisa, screener, berita, arah, fundamental dan benefit;
     - training, kalender dan metodologi.
   - Data aplikasi: `market_overview` (arah market dan sentimen berita), `screener`, `alerts`, `company_report`,
     `dividends` dan `training_stats`. Hasilnya diringkas supaya pendek untuk diucapkan.
   - Isi berita dan halaman web tetap ditandai sebagai konten luar: instruksi di dalamnya tidak diikuti.
3. **Asisten berita mengalah ke JARVIS:**
   - Saat JARVIS bekerja (berpikir, alat berjalan) atau berbicara, asisten berita menunda antreannya dan suaranya
     berhenti. Pekerjaan yang sedang jalan kembali ke antrean.
   - 2,5 detik setelah JARVIS selesai bicara, asisten berita boleh jalan lagi walaupun percakapan JARVIS masih terbuka.
     Di Fix14 asisten berita ditunda selama percakapan terbuka.
   - Chart yang dibuka JARVIS tidak memicu analisa suara asisten berita, karena JARVIS sudah menjawab.
   - Mode speaker: selama suara asisten berita terdengar, mikrofon JARVIS jeda supaya JARVIS tidak mendengar suara itu.
     Ketuk orb untuk menghentikan suara berita dan bicara ke JARVIS.
   - Mengetik di tab JARVIS menunda asisten berita selama ada teks yang belum terkirim, atau sampai 6 detik setelah
     ketikan terakhir.

## Fix16: JARVIS ikut layar dan membuat rencana trading sendiri

1. **JARVIS membaca layar** (alat `read_screen`):
   - Di halaman analisa aset, JARVIS membaca teks yang tampil di semua tab samping: BIAS, MTF, PLAN, NARASI, LEVEL,
     LAPORAN, BERITA dan AI, termasuk tab yang sedang tertutup. Tab TV tidak bisa dibaca karena isinya halaman
     TradingView sendiri.
   - Ditambah konteks aset itu (juga lewat alat `market_context`):
     - suasana pasar (risk-on/risk-off);
     - outlook penggeraknya beserta hubungannya, mis. Dolar AS berlawanan arah dengan EURUSD dan emas;
     - kekuatan kedua mata uang untuk pair forex;
     - kalender ekonomi mata uangnya: 12 jam terakhir dengan hasil rilisnya, dan 48 jam ke depan.
   - Di halaman lain JARVIS membaca daftar atau ringkasan yang tampil (screener, berita, kalender, dashboard, dll.).
2. **Ikut layar:** selama percakapan JARVIS berjalan, halaman atau tab samping yang Anda buka diberitahukan ke JARVIS,
   dan JARVIS langsung menanggapi.
   - Halaman analisa aset: JARVIS membaca semua tab, mencocokkannya dengan arah market dan kalender, lalu memberi
     rencananya sendiri.
   - Tab samping, mis. PLAN: JARVIS mulai dari isi tab itu. Bila aset itu baru saja dianalisa, JARVIS cukup menyebut
     apa yang ditambahkan tab itu dan apakah rencananya berubah.
   - Halaman lain: JARVIS menyebut yang menonjol, lalu bertanya aset mana yang mau dianalisa.
   - Pemberitahuan dikirim setelah Anda diam 1,5 detik di halaman itu, jadi pindah tab cepat dihitung sekali. Untuk
     halaman analisa, pemberitahuan menunggu sampai datanya selesai dimuat.
   - JARVIS baru menanggapi setelah selesai bicara dan saat Anda tidak sedang bicara.
   - Halaman yang dibuka JARVIS sendiri tidak dilaporkan balik. Halaman yang sama dilaporkan lagi paling cepat
     3 menit kemudian.
   - Asisten berita diam selama JARVIS menanggapi.
   - Bisa dimatikan di PENGATURAN → "Tanggapi halaman yang dibuka".
3. **Rencana JARVIS** (alat `set_trading_plan`):
   - JARVIS boleh mempertahankan skenario aplikasi atau mengubahnya: arah (BUY, SELL atau TUNGGU), entry, stop dan
     target, dengan alasannya.
   - Bila ada rilis berdampak tinggi dalam beberapa jam, JARVIS diarahkan untuk menunggu sampai rilis lewat, atau
     menyebut risikonya dengan jelas.
   - Engine memeriksa rencana sebelum disimpan:
     - stop dan target harus di sisi yang benar;
     - entry paling jauh 15 ATR dari harga sekarang;
     - stop minimal 0,1 ATR dari entry.
   - Rencana yang salah ditolak beserta alasannya, lalu JARVIS memperbaikinya.
   - Rencana tampil di tab PLAN sebagai **RENCANA JARVIS**, di atas skenario aplikasi yang tetap tidak diubah. Kartu
     itu memuat:
     - arah, dan "Mengubah skenario aplikasi: SELL → BUY" bila arahnya berbeda;
     - entry, stop, target dan risk-reward;
     - alasan, syarat dan masa berlaku (1H: 24 jam, 4H: 3 hari, 1D: 10 hari).
   - Tombol ✕ menghapusnya. Rencana disimpan di HP (`jarvis-plans.json`) dan tetap ada setelah aplikasi dibuka ulang.

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
- **Fix13/Fix14 diuji di Chromium** (emulasi Pixel 7 landscape) dengan:
  - Server Python APK yang dijalankan di sini.
  - **Gemini Live tiruan**: server WebSocket lokal yang berbicara dengan format JSON Live API. Gemini asli tidak diuji
    karena tidak ada key dan server ini tidak bisa menjangkaunya.
  - **Dashboard JARVIS PC asli** (`dashboard/server.py` dari pemasangan Mark LVII nyata, tanpa HUD).
  - Dua jalur mikrofon:
    - jalur native Fix14, dengan pengganti `JarvisHooks` di Python yang memberi nada 16 kHz per 100 ms;
    - jalur WebView, dengan mikrofon tiruan Chromium.
  - Selama JARVIS bicara, 0 potongan mikrofon terkirim (258 dan 322 potongan total).
  - Prompt baru dipakai, tanpa teks Mark LVII.
  - Hasilnya, 15 langkah lulus di kedua jalur:
    - Tanpa key → BUTUH API KEY. Key tersimpan dan tidak muncul di JSON halaman.
    - Model pertama ditolak → pindah ke model kedua. Audio mikrofon 16 kHz → transkrip → alat → balasan audio.
    - Memori tersimpan dan masuk prompt sesi berikutnya. Senter lewat jembatan Java (pengganti di uji native; "not
      available" di uji WebView).
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
- **Di HP pengguna (Fix13):** sambungan Gemini Live asli berhasil (model `gemini-2.5-flash-native-audio-preview-12-2025`).
  Mikrofon WebView gagal dengan `NotReadableError`; karena itu Fix14 merekam mikrofon secara native.
- **`JarvisHooks.java` hanya dikompilasi** (javac + d8). Mikrofon native, aksi HP dan suara di perangkat **belum diuji
  di HP**.
- **Fix15 diuji di Chromium** dengan engine Python APK yang baru dinyalakan, Gemini Live tiruan dan halaman baca
  tiruan (menjawab seperti `ReaderHooks`). 9 langkah lulus:
  - "analisa EURUSD": alat selesai dalam 12 detik.
    - Chart tergambar dan screenshot 968×388 (57 KB) terkirim sebagai pesan berikutnya.
    - 7 judul berita, termasuk berita USD/EUR karena berita EURUSD sendiri kosong (hari Sabtu).
    - 2 artikel dibuka otomatis dan dibaca dari halaman.
    - Asisten berita tidak memulai analisa sendiri.
  - "lihat chart emas": chart XAUUSD 4H dibuka, screenshot terkirim.
  - "baca berita EURUSD": satu artikel dibuka di halaman baca, teksnya kembali ke JARVIS.
  - Chart yang datanya ditahan 52 detik: JARVIS menjawab dari data pada detik ke-45, lalu screenshot menyusul pada
    detik ke-64.
  - Arah market, screener (masih memindai) dan halaman screener forex.
  - Asisten berita ditahan selama JARVIS bekerja, lalu bebas lagi setelah JARVIS selesai, walaupun percakapan masih
    terbuka.
  - Selama suara asisten berita terdengar, 0 potongan mikrofon terkirim; sesudahnya mikrofon jalan lagi.
  - Uji Fix13/Fix14 (15 langkah, kedua jalur mikrofon) dan PC → HP (10 langkah) diulang dan lulus.
  - **Belum diuji di HP:** penglihatan chart oleh Gemini asli, halaman baca otomatis dan suara di perangkat.
- **Fix16 diuji di Chromium** dengan engine Python APK yang baru dinyalakan dan Gemini Live tiruan. 8 langkah lulus:
  - Halaman analisa EURUSD dibuka. JARVIS diberi tahu 14 detik kemudian, setelah datanya dimuat, lalu membaca
    kedelapan tab (4.150 karakter).
  - Rencana yang salah ditolak. Rencana BUY tersimpan dan tampil di tab PLAN dengan "Mengubah skenario aplikasi:
    SELL → BUY".
  - Tab PLAN dibuka: hanya tab itu yang dibaca. BIAS lalu MTF dalam 0,25 detik: satu pemberitahuan (MTF).
  - Halaman screener: JARVIS bertanya aset mana. Halaman yang dibuka JARVIS sendiri tidak dilaporkan.
  - Halaman yang dibuka saat JARVIS bicara 6 detik dilaporkan 7 detik kemudian, setelah JARVIS selesai.
  - Pengaturan dimatikan: tidak ada pemberitahuan.
  - Rencana tetap ada setelah aplikasi dibuka ulang, dan ✕ menghapusnya.
- **Arah market per aset** diuji dengan data arah asli dari engine untuk 13 aset (saat itu RISK-OFF). Contoh EURUSD:
  Dolar AS naik kuat dan berlawanan arah dengan aset; kekuatan EUR −21 vs USD +35, jadi condong SELL.
- **Pemeriksaan rencana** diuji dengan 9 kasus: stop atau target di sisi salah, entry 20 ATR, stop 0,05 ATR, tanpa
  target, tanpa alasan, arah tak dikenal, alias "beli", target tidak urut, dan TUNGGU.
- Uji Fix13–Fix15 diulang dan lulus.
- **Belum diuji di HP dan dengan Gemini asli:** apakah Gemini asli memanggil `read_screen` dan `set_trading_plan`
  sesuai aturan, dan seberapa sering ia bicara saat Anda berpindah halaman.

## Build

```bash
APKTOOL=apktool_2.10.0.jar BUILD_TOOLS=build-tools/36 ANDROID_JAR=android-35/android.jar \
  ./build.sh SHARX-INVEST-AI-Fix9.apk release.jks store-pass.txt SHARX-INVEST-AI-Fix16.apk
```

Perlu Java 17+, python3 (dengan pip), npm (Readability.js 0.5.0, Apache-2.0, diunduh saat build), curl, unzip, zip dan
patch. Wheel `websockets` dari PyPI dicek SHA-256. Resource
dan manifest APK tidak diubah (apktool `-r`). APK hasil ditandatangani kunci baru. Kunci asli Fix9 tidak tersedia,
jadi **Fix9 harus di-uninstall dulu** (API key AI perlu dimasukkan lagi). Fix11 dan Fix12 memakai kunci yang sama dengan
Fix10, jadi bisa dipasang di atas Fix10/Fix11 tanpa uninstall (Fix13 sampai Fix16 juga). Simpan keystore itu di tempat aman
dan pakai lagi untuk versi berikutnya. Keystore dan APK tidak disimpan di repo ini.
