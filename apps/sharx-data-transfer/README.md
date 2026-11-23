# SHARX Data Transfer

Aplikasi C# (.NET 8) dengan UI web (HTML/CSS/JS) yang menjadi jembatan data
**server → printer CIJ/TIJ → server**. Server dan printer sama-sama dihubungi lewat **IP + port**.

1. Menerima data dari server (TCP), contoh `ANANDA02,867892,000`.
2. Memecah data sesuai **Flow Mapping** (posisi field bisa diatur): nama operator,
   nama template, counter, dan field custom.
3. Menghitung **counter per template** (aturan di bawah).
4. Mengirim ke printer:
   - **Change template**: memanggil template yang sudah dibuat dan disimpan di printer,
     memakai nama template dari server.
   - **Field data**: **field 0 = nama operator**, **field 1 = counter** (bisa ditambah / diubah).
5. Setiap printer melapor **selesai cetak**, aplikasi mengirim ke server data yang tercetak:
   `operator,template,counter` (format bisa diubah).

## Menjalankan: `run.bat`

```bat
run.bat             :: buka aplikasi desktop
run.bat browser     :: mode browser / service, UI di http://127.0.0.1:5080
run.bat simulator   :: simulator server + printer TIJ untuk uji coba tanpa mesin
run.bat publish     :: buat paket siap pakai di folder publish\
```

- Di folder source, `run.bat` butuh [.NET 8 SDK](https://dotnet.microsoft.com/download/dotnet/8.0)
  (build otomatis lalu jalan).
- `run.bat publish` menghasilkan folder `publish\` berisi `run.bat`, `README.md` dan folder
  `app\` (program + runtime .NET). Salin folder itu ke PC produksi dan jalankan `run.bat`;
  PC produksi **tidak perlu** .NET SDK. Aplikasi desktop butuh **Microsoft Edge WebView2 Runtime**
  (sudah ada di Windows 10/11).
- Settings, counter dan log disimpan di folder `data\` di samping `run.bat`.

## Aturan counter

| Kejadian                                  | Hasil                                                                     |
| ----------------------------------------- | ------------------------------------------------------------------------- |
| Template sama, operator berubah           | Counter **lanjut** dari hitungan terakhir                                 |
| Template berubah                          | Counter **pindah** ke counter template baru (tersimpan per template)      |
| Kembali ke template lama (hari yang sama) | Counter template itu dilanjutkan                                          |
| Ganti hari (jam ganti hari bisa diatur)   | Semua counter direset                                                     |
| Tombol **Reset Counter**                  | Semua counter (atau satu template) direset, printer langsung disinkronkan |

- **Counter per cetak** (default): counter naik setiap printer melapor selesai cetak.
  Setelah setiap cetak, nilai berikutnya langsung dikirim sebagai field data.
- **Counter per data**: counter naik setiap data dari server berhasil dikirim ke printer.
- Template baru mulai dari counter yang dikirim server (`000` → cetakan pertama `001`),
  atau dari nilai awal di Settings.
- Counter disimpan di `data\counters.json`, jadi tetap ada walau aplikasi ditutup
  (sampai ganti hari / Reset Counter). Saat Start, job terakhir hari ini dikirim ulang ke printer.

## Protokol printer

Alur kerja (change template → field data → selesai cetak → feedback) sama untuk semua printer;
protokolnya dipilih di **Settings → Flow Mapping Data → 5. Kirim ke printer**.

**TIJ Protocol 4.8** (sesuai dokumen protokol TIJ):

| Langkah                            | Perintah                                                                          |
| ---------------------------------- | --------------------------------------------------------------------------------- |
| Change template                    | `1E` Load Print Message (+ `01` Update Print Contents, opsional `11` Start Print) |
| Kosongkan antrean saat job berubah | `21` Clear Buffers of 1D                                                          |
| Field data                         | `1D` Download Remote Field Data (field 0 = operator, field 1 = counter)           |
| Selesai cetak                      | sinyal `AB` dari printer                                                          |

Balasan ACK/NAK dan checksum diperiksa; **Test Koneksi Printer** menampilkan daftar template di
printer (`1F`). Slave address otomatis = byte terakhir IP printer.

**Generic** (untuk protokol printer lain): perintah diisi sendiri di Settings, misalnya

| Isian                          | Contoh                                       |
| ------------------------------ | -------------------------------------------- |
| Change template                | `<STX>TEMPLATE:{TEMPLATE_NAME}<ETX>`         |
| Perintah per field             | `<STX>FIELD:{FIELD_ID}:{FIELD_VALUE}<ETX>`   |
| Atau satu perintah semua field | `<STX>{FIELD_0}\|{FIELD_1}<ETX><SUM>`        |
| Balasan sukses / gagal         | `<ACK>` / `<NAK>`                            |
| Sinyal selesai cetak           | mis. `<EOT>` (wajib untuk counter per cetak) |

Token checksum: `<SUM>` (2's complement jumlah byte sebelumnya, seperti TIJ) dan `<XOR>`.
Jika protokol printer Anda butuh perilaku lain, driver baru cukup ditambahkan di
`src/Sharx.Core/Printing/` (lihat `TijDriver.cs`) tanpa mengubah alur counter/feedback.

### Placeholder

`{OPERATOR}` `{TEMPLATE}` `{SERVER_COUNTER}` `{COUNTER}` `{RAW}` `{DATE}` `{TIME}`
`{PROD_DATE}` `{SEQ}` `{F1}`..`{Fn}` (field hasil split) dan kunci field custom.
Format: `{COUNTER:D6}`, `{DATE:ddMMyy}`, `{OPERATOR:L10}` (rata kiri 10), `{TEMPLATE:Z8}` (isi nol).
Karakter kontrol: `<CR>` `<LF>` `<STX>` `<ETX>` `<ESC>` `<TAB>` `<ACK>` `<0x1B>` dst.

## Halaman

- **Dashboard**: status koneksi, statistik (Received = byte dari server, Sent = byte ke printer +
  feedback ke server), Incoming Data (Source `Server` = data masuk, `Printer` = feedback cetak),
  Transfer Log, Start/Stop.
- **Data Transfer**: job aktif, counter terakhir & berikutnya, status printer, counter per template
  (ubah/reset), detail perintah yang dikirim.
- **Log History**: log per hari (filter, cari, download).
- **Settings**:
  - _Koneksi_: IP + port server dan IP + port printer (atau RS232), timeout, tombol test.
  - _Flow Mapping Data_: contoh data, delimiter / lebar tetap, posisi tiap field, protokol printer,
    nama template (change template), field data, format feedback, preview HEX perintah.
  - _Counter_: mode, nilai awal, step, digit, batas maksimum, jam ganti hari.

## Uji tanpa mesin

`run.bat simulator` membuka simulator printer TIJ (port 9100, template `867892`, `555111`,
mencetak tiap 2 detik) dan simulator server (port 9000, mengirim beberapa job). Di
**Settings → Koneksi** isi server `127.0.0.1:9000` dan printer `127.0.0.1:9100`, simpan,
lalu **Start Transfer**.

## Printer belum start

Jika printer terhubung tetapi print belum di-start, printer membalas `P_Status 12` (_Print not
started_). Data **tidak dianggap error**: statusnya **MENUNGGU** (kuning) dan disimpan.

- Software mengecek printer tiap 2 detik (perintah `14` Request Printer Status; bisa diatur di
  _Flow Mapping Data → Cek printer sudah start tiap_). Begitu printer di-start dari panel printer,
  job (change template + field data) langsung dikirim otomatis.
- Halaman **Data Transfer → Printer CIJ** punya tombol **Start Print** (`11`) dan **Stop Print** (`12`).
  Setelah Stop Print, job dikirim ulang otomatis saat printer start lagi.
- Centang **"Start print otomatis jika printer belum start"** jika software boleh men-start printer sendiri.
- Simulator: `SharxSimulator printer --started false` meniru printer yang belum start (tekan `S` di
  jendela simulator untuk start/stop dari "panel").

## Catatan pemasangan printer TIJ

- Printer TIJ hanya menerima **satu** klien TCP; jangan sambungkan software lain bersamaan.
- Template di printer harus punya **remote field** dengan nomor sesuai Settings (default 0 dan 1).
- _Buffer Setting_ `1D` di printer minimal sama dengan "Antrean data di printer" (default 1).
- `P_Status 12` (_Print not started_): print belum di-start. Aktifkan "Kirim 11 Start Print" atau
  start dari panel printer; job dicoba ulang otomatis.

## Struktur

```
run.bat                 menjalankan / mem-publish aplikasi
src/Sharx.Core          logika: parser, counter, driver printer (TIJ, Generic), koneksi
src/Sharx.Web           REST API + WebSocket + UI (wwwroot: index.html, css, js, font)
src/Sharx.Host          exe mode browser / service
src/Sharx.Desktop       exe Windows (WinForms + WebView2, jendela tanpa border)
tools/Sharx.Simulator   simulator server & printer TIJ
tests/Sharx.Core.Tests  xUnit (termasuk contoh checksum dari dokumen protokol)
```

UI hanya bisa diakses dari komputer yang sama (127.0.0.1) kecuali memakai `--allow-remote`.
Font Roboto Condensed & Roboto Mono berlisensi SIL Open Font License (lihat `wwwroot/fonts`).
