# SHARX Data Transfer

Aplikasi C# (.NET 8) dengan UI web (HTML/CSS/JS) yang menjadi jembatan data
**server → printer CIJ/TIJ → server**:

1. Menerima data dari server lewat TCP (IP + port), contoh `ANANDA02,867892,000`.
2. Memecah data sesuai **Flow Mapping** (posisi field bisa diatur): nama operator,
   nama template di printer, counter, dan field custom.
3. Menghitung **counter per template** (aturan di bawah).
4. Mengirim ke printer dengan **TIJ Protocol 4.8**:
   - `1E` Load Print Message → nama message = template dari server
   - `01` Update Print Contents (opsional), `21` Clear Buffers of 1D (saat job berubah)
   - `1D` Download Remote Field Data → **field 0 = nama operator**, **field 1 = counter**
5. Setiap printer mengirim sinyal **Print End (`AB`)**, aplikasi melapor ke server data
   yang tercetak: `operator,template,counter` (format bisa diubah).

## Aturan counter

| Kejadian                                  | Hasil                                                                     |
| ----------------------------------------- | ------------------------------------------------------------------------- |
| Template sama, operator berubah           | Counter **lanjut** dari hitungan terakhir                                 |
| Template berubah                          | Counter **pindah** ke counter template baru (tersimpan per template)      |
| Kembali ke template lama (hari yang sama) | Counter template itu dilanjutkan                                          |
| Ganti hari (jam ganti hari bisa diatur)   | Semua counter direset                                                     |
| Tombol **Reset Counter**                  | Semua counter (atau satu template) direset, printer langsung disinkronkan |

- Mode **counter per cetak** (default): counter naik setiap printer melapor selesai cetak (`AB`).
  Setelah setiap cetak, nilai berikutnya langsung dikirim ke printer (`1D`).
- Mode **counter per data**: counter naik setiap data dari server berhasil dikirim ke printer.
- Template baru mulai dari counter yang dikirim server (`000` → cetakan pertama `001`),
  atau dari nilai awal di Settings.
- Counter disimpan di `data/counters.json`, jadi tetap ada walau aplikasi ditutup
  (sampai ganti hari / Reset Counter). Saat Start, job terakhir hari ini dikirim ulang ke printer.

## Menjalankan

Butuh [.NET 8 SDK](https://dotnet.microsoft.com/download/dotnet/8.0). Untuk aplikasi desktop
di Windows juga butuh **Microsoft Edge WebView2 Runtime** (sudah ada di Windows 10/11 terbaru).

```powershell
# Aplikasi desktop (jendela seperti desain, Windows)
dotnet run --project src/Sharx.Desktop

# Mode browser / service (Windows / Linux): UI di http://127.0.0.1:5080
dotnet run --project src/Sharx.Host -- --port 5080

# Paket siap pakai (self-contained) ke folder publish\
powershell -ExecutionPolicy Bypass -File publish.ps1
```

Opsi: `--data <folder>` lokasi data (default folder `data` di samping exe),
`--port <n>`, `--no-browser`, `--allow-remote` (izinkan UI dibuka dari komputer lain).

### Uji tanpa mesin (simulator)

```powershell
dotnet run --project tools/Sharx.Simulator -- printer --port 9100 --messages 867892,555111 --print-interval 2
dotnet run --project tools/Sharx.Simulator -- server --port 9000 --jobs "ANANDA02,867892,000;BUDI01,867892,000;BUDI01,555111,000"
```

Lalu di **Settings**: Server IP `127.0.0.1` port `9000`, Printer IP `127.0.0.1` port `9100`,
simpan, tekan **Start Transfer**. Simulator printer menampilkan perintah yang diterima dan
"mencetak" tiap 2 detik; simulator server menampilkan feedback `operator,template,counter`.

## Halaman

- **Dashboard**: status koneksi, statistik (Received = byte dari server, Sent = byte ke printer +
  feedback ke server), Incoming Data (Source `Server` = data masuk, `Printer` = feedback cetak),
  Transfer Log, Start/Stop.
- **Data Transfer**: job aktif, counter terakhir & berikutnya, status printer, counter per template
  (ubah/reset), detail data yang dikirim.
- **Log History**: log per hari (filter, cari, download).
- **Settings**:
  - _Koneksi Server_: IP, port, pemisah record (CR/LF, ETX, STX..ETX, custom, per paket).
  - _Flow Mapping Data_: contoh data, delimiter atau lebar tetap, posisi tiap field, nama message
    (`1E`), remote field (`1D`), format feedback ke server, preview HEX perintah yang dikirim.
  - _Counter_: mode, nilai awal, step, digit, batas maksimum, jam ganti hari.
  - _Printer CIJ_: protokol (TIJ / custom text), TCP atau RS232, slave address
    (`-1` = byte terakhir IP printer), antrean buffer `1D`, timeout. **Test Koneksi Printer**
    menampilkan daftar message di printer (`1F`).

### Placeholder

`{OPERATOR}` `{TEMPLATE}` `{SERVER_COUNTER}` `{COUNTER}` `{RAW}` `{DATE}` `{TIME}`
`{PROD_DATE}` `{SEQ}` `{F1}`..`{Fn}` (field hasil split) dan kunci field custom.
Format: `{COUNTER:D6}`, `{DATE:ddMMyy}`, `{OPERATOR:L10}` (rata kiri 10), `{TEMPLATE:Z8}` (isi nol).
Karakter kontrol: `<CR>` `<LF>` `<STX>` `<ETX>` `<ESC>` `<TAB>` `<ACK>` `<0x1B>` dst.

## Catatan pemasangan printer TIJ

- Printer TIJ hanya menerima **satu** klien TCP; jangan sambungkan software lain bersamaan.
- Message di printer harus punya **remote field** dengan nomor sesuai Settings (default 0 dan 1).
- _Buffer Setting_ `1D` di printer minimal sama dengan "Antrean buffer 1D" di Settings (default 1).
- Status `P_Status 12` (_Print not started_) berarti print belum di-start; aktifkan
  "Kirim 11 Start Print setelah load" atau start dari panel printer. Job dicoba ulang otomatis.

## Struktur

```
src/Sharx.Core      logika: parser, counter, protokol TIJ, koneksi server/printer
src/Sharx.Web       REST API + WebSocket + UI (wwwroot: index.html, css, js, font)
src/Sharx.Host      exe mode browser / service
src/Sharx.Desktop   exe Windows (WinForms + WebView2, jendela tanpa border)
tools/Sharx.Simulator  simulator server & printer TIJ
tests/Sharx.Core.Tests xUnit (termasuk contoh checksum dari dokumen protokol)
```

UI hanya bisa diakses dari komputer yang sama (127.0.0.1) kecuali memakai `--allow-remote`.
Font Roboto Condensed & Roboto Mono berlisensi SIL Open Font License (lihat `wwwroot/fonts`).
