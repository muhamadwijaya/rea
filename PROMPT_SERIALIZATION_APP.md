# PROMPT SUPER LENGKAP — "SERIALX" Software Serialization & Labeling System
### UI: PySide6 | Tema: Navy Blue + Monster Energy Green (Neon Boost)

---

## 0. INSTRUKSI UMUM UNTUK AI / DEVELOPER

Buatkan saya **aplikasi desktop lengkap** bernama **"SERIALX — Industrial Serialization & Marking Suite"** menggunakan **Python 3.11+** dan **PySide6 (Qt 6.6+)**. Aplikasi ini adalah sistem serialisasi produksi untuk mencetak/menempelkan **kode unik (serial number, QR, DataMatrix, barcode)** pada produk/kemasan di jalur produksi (production line), terintegrasi dengan **printer industri, scanner, dan kamera vision**.

Hasil akhir harus berupa **proyek yang bisa langsung dijalankan** (`python main.py`) tanpa error, dengan semua file terpisah rapi, kode bersih, bertipe-hint penuh, dan ber-API dokumentasi docstring.

**Aturan wajib:**
1. Jangan gunakan placeholder `TODO` atau fungsi kosong — semua fitur harus berfungsi nyata.
2. Semua state tersimpan di **SQLite** (WAL mode) + **QSettings** untuk preferensi UI.
3. Tidak boleh ada dependensi komersial/berbayar. Boleh: `pyside6`, `qrcode`, `pylibdmtx`/`treepoem` fallback, `python-barcode`, `openpyxl`, `pyserial`, `Pillow`, `jinja2`.
4. Setiap modul maksimal ±500 baris; pisahkan tanggung jawab.
5. Sertakan **test smoke** (`pytest-qt`) minimal 10 test.
6. Kode harus jalan di **Windows 10/11** dan **Ubuntu 22.04+** (paths pakai `pathlib`).

---

## 1. SPESIFIKASI VISUAL / TEMA (BAGIAN PALING PENTING)

Desain tema **"ABYSS NEON"** — perpaduan **Navy Blue gelap** sebagai basis dan **Monster Energy Green** yang **dibuat lebih nyala/neon** daripada hijau asli Monster.

### 1.1 Palet Warna Wajib ( HEX )

```
── BACKGROUND LAYERS ──
bg.void        #04070E   (hampir hitam kebiruan — root window)
bg.deep        #071022   (main area / central widget)
bg.navy        #0B1B34   (panel, card, groupbox)
bg.navy-2      #102544   (hover surface, elevated card)
bg.navy-3      #16305A   (selected item, table header)
bg.input       #081428   (input field, spinbox, combo dropdown)

── BORDER & DIVIDER ──
line.hair      #14294A   (border halus)
line.base      #1E3A66   (border normal)
line.strong    #2C5288   (border focus / panel aktif)

── TEKS ──
text.primary   #E6F1FF
text.secondary #9DB4D6
text.muted     #5C76A0
text.disabled  #3A4E6E
text.on-accent #02170C   (teks di atas tombol hijau neon)

── AKSEN HIJAU NEON (Monster Energy yang DILEBIHKAN) ──
neon.core      #00FF5F   ← warna utama, lebih terang & lebih jenuh dari #00E025 khas Monster
neon.bright    #6BFF9E   ← highlight / hover
neon.dim       #00C94A   ← pressed / disabled accent
neon.deep      #007A30   ← shadow tint, border gelap aksen
glow.green     rgba(0, 255, 95, 0.55)   ← box-shadow / glow
glow.soft      rgba(0, 255, 95, 0.18)   ← ambient halo

── AKSEN SEKUNDER (biru elektrik utk info/link) ──
azure          #2BD7FF
azure.dim      #1488A8

── SEMANTIK ──
ok        #00FF5F      warn      #FFC53D      err  #FF3B5C
info      #2BD7FF      idle      #5C76A0      run  #00FF5F (berkedip lembut)
```

> Catatan hijau: hijau Monster asli sekitar `#00E025`. Di sini naikkan ke `#00FF5F` dengan **glow** agar terasa "menyala di kegelapan". Semua elemen interaktif utama (tombol primer, toggle ON, progress, status RUNNING, indikator laser) WAJIB memakai `neon.core` + glow.

### 1.2 Tipografi
- Font utama: **Segoe UI** (Win) / **Inter** atau **JetBrains Mono** (fallback Linux) — daftar prioritas di QFontDatabase.
- Font angka/serial/barcode text & log: **JetBrains Mono / Consolas** (monospace).
- Ukuran: caption 11px · body 13px · subtitle 15px · title 20px · display/HUD 34px (bold, letter-spacing 1px).
- Judul besar memakai `font-weight: 800` + `letter-spacing: 0.5px`.

### 1.3 Bentuk & Ruang
- Radius: kecil 6px, kartu 10px, dialog 14px, pill button 999px.
- Border 1px selalu terlihat (kontras navy vs neon).
- Spacing grid 4px; padding standar 12/16/20.
- Shadow: `0 8px 24px rgba(0,0,0,0.55)` untuk kartu, `0 0 18px glow.green` untuk elemen aksen aktif.

### 1.4 Elemen Khas yang Harus Ada
1. **Frameless main window** dengan custom title bar: logo claw-slash (gambar vektor dibuat via QPainter — tiga cakar diagonal neon), nama app, tombol min/max/close flat, indikator koneksi mesin.
2. **Glow-on-hover**: setiap tombol primary saat hover membesar glow (`animation` via QPropertyAnimation pada ` QGraphicsDropShadowEffect` blur radius 12 → 26).
3. **Scanline overlay** sangat halus (opacity 4%) pada area preview label — kesan mesin industri.
4. **Animated gradient border** 2px pada panel "RUNNING" (gradient bergerak navy→neon→navy) pakai `QSequentialAnimationGroup`.
5. **Status LED** bulat 10px dengan radial gradient + bloom untuk Idle/Running/Paused/Error.
6. **Progress bar** custom: track navy gelap, chunk neon dengan garis-garis diagonal, plus persentase monospace di kanan.
7. **Dark scrollbar** custom (width 10px, thumb `#1E3A66`, hover `neon.dim`).
8. **Toast notification** melayang kanan-bawah, border kiri neon 3px, auto-dismiss 3 detik, animasi slide+fade.
9. **Splash screen** custom 2 detik: latar `bg.void`, logo cakar menyala dengan animasi "power on" (flicker lalu stabil), teks loading per modul.
10. Semua ikon digenerate sendiri sebagai `QIcon` dari SVG string internal (buat helper `icon(name, color)`) — tidak butuh file aset eksternal. Setidaknya 24 ikon: play, pause, stop, print, scan, camera, database, settings, user, lock, unlock, export, import, refresh, warning, check, close, search, plus, minus, trash, edit, chart, history, network, printer, qr, datamatrix, code128.

### 1.5 Cara Implementasi Tema
Buat satu sumber kebenaran: `ui/theme/tokens.py` berisi dataclass/dict semua token warna & ukuran, lalu `ui/theme/qss.py` menghasilkan **satu string QSS global** dari token tersebut (tidak hardcode hex di tempat lain). Tambahan `ui/theme/effects.py` untuk glow, LED, animated border, scanline, toast, dan `ui/theme/icons.py` untuk SVG icon factory. Sediakan juga `ThemeManager(QObject)` dengan signal `themeChanged` agar widget bisa re-polish, dan opsi accent hue (Neon Green default, Azure, Amber) di Settings.

---

## 2. ARSITEKTUR APLIKASI

```
serialx/
├── main.py                     # bootstrap: high-DPI, splash, app, window
├── requirements.txt
├── config/settings.default.json
├── core/
│   ├── app_config.py           # load/save JSON + validasi (dataclass)
│   ├── db.py                   # SQLite layer, migration runner, repository
│   ├── models.py               # dataclass domain: Product, Job, SerialRange, Record, User, AuditLog
│   ├── serial_engine.py        # generator nomor seri (pattern engine, checksum, uniqueness)
│   ├── barcode_service.py      # render QR/DataMatrix/Code128/EAN128 -> QPixmap/QImage bytes
│   ├── label_renderer.py       # komposisi label (template JSON -> QImage 300dpi), preview painter
│   ├── printer_backend.py      # interface PrinterBackend + ZPL/TSC/Eltron driver + File/Fake driver
│   ├── vision_client.py        # OCR/camera capture abstraction (FakeVision utk demo)
│   ├── plc_bridge.py           # pyserial Modbus RTU / TCP trigger in-out + simulator
│   ├── job_manager.py          # state machine produksi (QThread worker, signals)
│   ├── exporter.py             # CSV/XLSX/JSON/XML GS1-like export + hashing
│   ├── crypto_utils.py         # HMAC signing serial, lisensi offline, password hashing
│   ├── logger.py               # RotatingFileHandler + QtLogHandler -> pane log
│   └── i18n.py                 # ID/EN string catalog sederhana
├── ui/
│   ├── theme/{tokens,qss,effects,icons}.py
│   ├── widgets/{titlebar,led,progress,toast,card,searchbox,segmented,stat_tile,table}.py
│   ├── pages/{dashboard,job_setup,serial_rules,label_designer,print_queue,vision_verify,database,reports,aggregation,reprint,settings,about}.py
│   ├── dialogs/{login,new_job,serial_range,product_crud,confirm,import_wizard,export_wizard,license}.py
│   └── main_window.py          # nav sidebar + QStackedWidget + statusbar HUD
├── workers/ threads & queues
├── tests/
└── assets/ (opsional, jangan diwajibkan)
```

**Pola:** MVVM ringan — Pages hanya menyusun widget; logika di `core/*` yang murni Python (bisa dites tanpa Qt kecuali perlu). Komunikasi lewat **Qt Signal/Slot**, tidak ada polling berat di GUI. Worker panjang = `QThread`/`QRunnable` + `QThreadPool`.

---

## 3. MODUL & FITUR LENGKAP

### 3.1 Autentikasi & Hak Akses
- Login awal (default `admin/admin123`, dipaksa ganti password saat first run).
- Password hash **PBKDF2-HMAC-SHA256** (120k iterasi) + salt per user.
- Role: `admin`, `operator`, `qa`, `viewer` — kontrol visibility/enabled tiap halaman & aksi destruktif.
- Session timeout 15 menit idle → auto-lock overlay (blur + form login).
- Menu "Kunci Layar" (Ctrl+L).

### 3.2 Dashboard (Halaman Utama)
HUD ala control room:
- 6 **StatTile**: Total Hari Ini, Target vs Aktual (progress ring neon), Reject Rate, WIP, Uptime Mesin, Throughput (pcs/menit).
- Grafik sparkline 24 jam (custom QPainter line chart, area gradient neon, tanpa matplotlib).
- Panel "Mesin": status printer/scanner/vision/plc dengan LED + tombol reconnect & self-test.
- Panel "Job Aktif": nama produk, pola serial, counter terbaru, ETA, tombol START/PAUSE/STOP besar (primary neon).
- Log aktivitas terakhir 8 baris.

### 3.3 Job Setup (Wizard 5 Langkah)
Segmented stepper bergaya neon: **1 Produk → 2 Aturan Serial → 3 Layout Label → 4 Perangkat Cetak → 5 Verifikasi & Simpan**.
- Pilih/duplikasi job tersimpan, set target qty, shift, line, batch/lot, tanggal kedaluwarsa, priority.
- Preview live di kanan (label thumbnail + contoh serial ter-render).
- Validasi keras: tidak bisa start jika range habis, pola invalid, printer offline (kecuali mode simulasi).

### 3.4 Serial Rules Engine (Inti Serialisasi)
Builder pola dengan token, contoh pola: `SRX{YY}{MM}{DD}-{LN}{SEQ:6}-{CRC2}`
Token tersedia: `{YYYY} {YY} {MM} {DD} {HH} {MIN} {SEQ:n} {LOT} {SKU} {PROD} {LINE} {RAND:n} {BASE32:n} {LuhnCheck} {CRC2} {CRC1} {MOD10} {GS1AI:01} {DATE11} {CHECKSUM}`
- Opsi: prefix/suffix tetap, step, pad char, huruf besar, hindari karakter ambigu (I,O,0,1), urutan ascending/descending/acak-terjamin (deterministic shuffle dgn seed), anti-duplikat (cek DB sebelum issue), reserve block (ambil blok 500 nomor sekali query utk performa), sinkronisasi antar-stasiun (kolom `issued_at`, `station_id`).
- Level hierarki serialisasi (Level 1 unit → Level 2 pack → Level 3 case → Level 4 pallet) dengan aturan parent-child & agregasi GTIN-14/CSID.
- Tanda tangan: setiap serial punya `sig = HMAC-SHA256(secret, serial+lot)[:8]` base32 utk verifikasi keaslian offline.

### 3.5 Label Designer (Simple WYSIWYG)
Canvas drag-drop dengan grid snapping, zoom 25–800%, ruler, dan property panel kanan:
- Elemen: Text (static/dynamic field), Barcode (Code128/EAN128/ITF), QR (URL/text/vCard/GS1 Digital Link), DataMatrix (GS1-128 AIME), Line, Rect, Image (logo PNG), Shape.
- Dynamic field binding ke variabel job: `{{serial}} {{sku}} {{lot}} {{exp}} {{date}} {{weight}} {{origin}}`.
- Kontrol font, size pt, bold, alignment, rotation, margin, quiet zone, module size, error-correction (EC) level QR (M/Q/H).
- Template disimpan sebagai JSON versi-x di DB + ekspor/impor `.slx` (zip json+asset).
- Undo/redo stack, salin/hapus elemen (Del), arrow-key nudge, align left/center/right/top/middle/bottom, distribute.
- Render output: `QImage` resolusi 300/600 dpi untuk cetak; pratinjau cepat realtime.

### 3.6 Print Queue & Printer Integration
- Antrian item dengan kolom: seq, serial, label, status (Queued/Sent/Printed/Failed/Rewind), station, waktu, retry count.
- Backend: **ZPL II** generator (utama), **TSC TSPL**, **Eltron/EPL**, **PDF single-page**, **Image PNG**, **Dummy/File** (untuk uji), **Simulator** (render ke layar).
- Kirim via: USB/COM (pyserial), Network TCP port 9100, Shared folder spool, CUPS/lpr subprocess.
- Fitur: reprint N label, batch send dengan pacing delay ms, auto-pause saat sensor `paper_out`/`head_open` dari PLC, real-time ack parsing (`~TA`, `^ERR`), heartbeat, konfigurasi kecepatan/density/offset.
- Error handling: retry 3x exponential backoff → tandai FAILED + toast merah + audit log.

### 3.7 Vision / Verification Module
- Mode: **Scanner Handheld** (keyboard wedge input), **Camera OCR** (simulasi/fake recogniser + hook nyata utk pytesseract bila tersedia), **Serial Verify** (cocokkan scan vs DB: OK/DUP/UNKNOWN/WRONG-BATCH).
- Tampilkan video frame/grab besar dengan overlay bounding box neon + hasil decode + confidence bar.
- Reconcile pass: scan akhir karton → cocokkan isi list → laporkan missing/extra.
- Anti-duplicate global: scan serial yang sudah `printed` → alarm suara (QApplication.beep) + modal merah.

### 3.8 Database Browser
- Tabel virtual (model QAbstractTableModel custom, lazy fetch LIMIT/OFFSET + index) jutaan baris tetap lancar.
- Filter advanced: rentang tanggal, produk, lot, status, station, regex serial, has-sig-valid.
- Kolom sortable/resizable, sticky header, zebra row navy, row terpilih highlight neon-border.
- Aksi: view detail (drawer kanan), edit metadata (catatan), void/reprint flag, delete (admin only + alasan wajib), mass-select by filter.
- Query builder visual sederhana (field-op-value AND/OR) + raw SQL read-only utk admin.

### 3.9 Aggregation & Track&Trace
- Bangun relasi Unit→Pack→Case→Pallet secara manual-scan atau otomatis dari aturan.
- Tampilan pohon (QTreeView model) dengan ikon level & jumlah anak; klik node → tampilkan seluruh keturunan.
- Ekspor struktur agregasi (XML GS1 EPCIS-like, JSON, CSV flat parent-child).
- Deteksi konflik: unit masuk dua case → flag ERROR.

### 3.10 Reports & Analytics
- Preset: Produksi Harian, Shift Summary, Reject/Anomaly, Serial Usage/Audit Trail, Print Performance, Downtime, Traceability One-Page (per serial).
- Filter periode + grup (jam/shift/hari/minggu) + bandingkan periode sebelumnya.
- Chart kustom QPainter: bar, stacked bar, line multi-series, donut; tooltip hover; legend neon.
- Ekspor: PDF (QPrinter), XLSX (openpyxl), CSV, PNG chart; kirim email opsional (SMTP config di Settings, SSL/TLS).

### 3.11 Reprint & Recovery
- Cari serial via scan/manual → reprint dengan watermark "REPRINT" opsional + pencatatan siapa/kapan/alasan.
- Recover job terputus: resume counter dari `last_issued+1`, deteksi gap, opsional fill-gap mode.
- Blacklist/hold-list nomor (mis. cacat cetak) agar tidak dipakai ulang.

### 3.12 Settings
Tab-group: Umum (bahasa, tema accent, DPI, autostart, notifikasi) · Mesin (COM/port/IP, baud, protocol, timeout, retry) · Serial (secret HMAC, prefix global, cadangan DB path) · Keamanan (session timeout, force HTTPS tidak relevan—skip, audit retention hari) · Lisensi (masukkan key, aktivasi offline file, masa berlaku, modul aktif) · Backup (auto backup harian, restore, purge data > N bulan, vacuum DB) · About (versi, build, cek update lokal).

### 3.13 Sistem Lisensi Offline (untuk aplikasi ini sendiri)
- Lisensi = `base64(JSON payload + RSA-sign)`; pakai kunci publik embed, verifikasi tanda tangan; payload: `machine_id`, `expiry`, `modules[]`, `max_stations`.
- `machine_id` = hash stable (MotherBoard UUID bila terbaca, fallback hostname+mac).
- Tanpa lisensi valid → mode DEMO 30 hari dengan watermark & batas 1000 serial/hari.

### 3.14 Logging, Audit & Performa
- Semua aksi penting masuk tabel `audit_log(user, action, entity, before, after, ip, ts)`.
- Log file rotating 10MB×5 di `logs/`, level configurable; pane "Console" di statusbar bisa dibuka (Ctrl+`).
- Kecepatan: target issue 500 serial/detik (batch block), render label <80ms cache QPixmap per template-hash, DB insert pakai executemany + transaksi per 200 baris.
- High-DPI aktif, `QT_ENABLE_HIGHDPI_SCALING`, dukung 1280×720 minimum sampai 4K.

---

## 4. SKEMA DATABASE (WAJIB SESUAI)

Tabel: `users, roles_permissions, products, jobs, serial_rules, templates, serial_records(id, serial, job_id, product_id, lot, level, parent_id, sig, status, station, printed_at, verified_at, reprint_count, note), aggregation_map(unit,pack,case,pallet), print_queue, audit_log, licenses, counters(job_id,last_seq,reserved_until), settings_kv, daily_stats`.
Sertakan indeks pada `(job_id,status)`, `serial UNIQUE`, `parent_id`, `printed_at`. Migration sederhana berbasis `PRAGMA user_version`.

---

## 5. INTERAKSI & KEYBOARD SHORTCUTS

`Ctrl+N` job baru · `Ctrl+S` simpan · `Ctrl+P` cetak · `Ctrl+F` cari di halaman aktif · `Ctrl+L` lock · `Space` start/pause (di dashboard) · `Esc` batal dialog · `F1` bantuan shortcut overlay · `F5` refresh · `Ctrl+,` settings · `Ctrl+Shift+E` export · `Ctrl+Shift+I` import · `Tab` navigasi wajar di semua form · Enter di field terakhir = aksi default dialog. Semua tombol punya `toolTip` + `statusTip`.

---

## 6. DELIVERABLE YANG HARUS DIHASILKAN

1. Seluruh source code sesuai struktur §2, siap `pip install -r requirements.txt && python main.py`.
2. `README.md`: cara install, jalankan, arsitektur singkat, cara reset admin password, cara buat lisensi demo, tabel shortcut, screenshot guide (deskripsi teks).
3. `requirements.txt` + `pyproject.toml` (opsional build PyInstaller) + instruksi `pyinstaller --noconsole --name SERIALX main.py` dengan hidden-import & aset.
4. Minimal 10 pytest smoke test (serial engine pattern, checksum, uniqueness, db repo, exporter, label render ke QImage, qss parse, login hash, aggregation conflict, license verify).
5. Seed data demo: 3 produk, 2 job, 500 record serial, 1 template label, sehingga saat pertama buka aplikasi dashboard sudah terisi (tombol "Reset Demo Data" di Settings).
6. Mode `--demo` CLI: menjalankan job simulasi yang menambah record tiap 300ms supaya UI hidup (LED Running berdenyut, counter naik, antrian bergerak, sparkline bertambah).

---

## 7. KRITERIA DITERIMA (ACCEPTANCE)

- [ ] Aplikasi terbuka tanpa traceback, splash muncul, lalu jendela frameless tema navy-neon tampil.
- [ ] Ganti accent warna di Settings → seluruh UI langsung ter-polish ulang tanpa restart.
- [ ] Buat job baru wizard selesai, tekan START → counter bertambah, record masuk DB, preview label ter-update, antrian tercetak (Simulator menampilkan gambar label).
- [ ] Scan serial duplikat → alarm merah + toast + audit log tercatat.
- [ ] Restart aplikasi saat RUNNING → muncul prompt "Resume job?"; pilih resume melanjutkan dari nomor benar.
- [ ] Export XLSX & PDF report berhasil dengan data terfilter.
- [ ] Hover tombol primer menghasilkan glow jelas; scrollbar, combobox popup, datepicker, message box semuanya ikut tema gelap (termasuk QMessageBox & QFileDialog native fallback).
- [ ] Tidak ada satu pun hex color hardcoded di luar `ui/theme/tokens.py`.
- [ ] `pytest -q` lulus semua test.

---

## 8. GAYA KODE

Type hints penuh, `from __future__ import annotations`, dataclass untuk model, enum untuk status, tanpa globa mutable, fungsi pendek, docstring Google style, komentar seperlunya dalam Bahasa Indonesia pada bagian rumus (checksum, ZPL, pola serial). Nama kelas/widget jelas (`LedIndicator`, `StatTile`, `SerialRuleBuilder`). Gunakan `logging` bukan `print`. Pisahkan konstanta magic ke `tokens`/`app_config`.

---

## 9. CONTOH SNIPPET QSS YANG MENENTUKAN KARAKTER (SELARASKAN DENGAN TOKENS)

```css
QPushButton[role="primary"] {
  background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
              stop:0 #6BFF9E, stop:0.45 #00FF5F, stop:1 #00C94A);
  color: #02170C; font-weight: 800; border: none; border-radius: 8px;
  padding: 10px 18px; letter-spacing: .5px;
}
QPushButton[role="primary"]:hover { background: #6BFF9E; }
QPushButton[role="ghost"] {
  background: transparent; color: #00FF5F; border: 1px solid #00C94A;
  border-radius: 8px; padding: 9px 16px;
}
QGroupBox::title { color:#00FF5F; subcontrol-origin: margin; left: 12px; }
QTableWidget::item:selected { background:#16305A; color:#E6F1FF;
  border-left: 3px solid #00FF5F; }
```

Dan efek glow dibuat programatik:

```python
fx = QGraphicsDropShadowEffect(blurRadius=18, xOffset=0, yOffset=0)
fx.setColor(QColor(0, 255, 95, 140))
btn.setGraphicsEffect(fx)   # animate blurRadius 12->26 saat hover
```

---

## 10. PERMINTAAN AKHIR

Susun jawabanmu sebagai proyek utuh: mulai dari `requirements.txt`, `main.py`, seluruh modul §2 secara berurutan dengan path file sebagai heading (`### core/serial_engine.py`), lalu README, lalu test. Jika terlalu panjang, lanjutkan otomatis di pesan berikutnya tanpa mengulang yang sudah diberikan, dan akhiri dengan checklist §7 beserta perintah menjalankan aplikasinya. Prioritaskan kerapian tema (§1) dan kebenaran mesin serial (§3.4) di atas segalanya.
