# SHARX Data Transfer

Aplikasi C# (.NET 8) dengan UI web (HTML/CSS/JS) yang menjadi jembatan data
**server (MQTT broker) → printer CIJ/TIJ → server**.

1. Menerima data job dari server lewat **MQTT broker** (subscribe topic, default `sharx/job`),
   contoh payload `ANANDA02,867892,000`. Mode TCP langsung (IP + port) tetap tersedia.
2. Memecah data sesuai **Flow Mapping** (posisi field bisa diatur): nama operator,
   nama template, counter, dan field custom.
3. Menghitung **counter per template** (aturan di bawah).
4. Mengirim ke printer (IP + port):
   - **Change template**: memanggil template yang sudah dibuat dan disimpan di printer,
     memakai nama template dari server.
   - **Field data**: **field 0 = nama operator**, **field 1 = counter** (bisa ditambah / diubah).
5. Setiap printer melapor **selesai cetak**, aplikasi mem-publish ke server (topic feedback,
   default `sharx/printed`) data yang tercetak, default `operator,template,counter`. Data yang dikirim
   balik (dan urutannya) dipilih di **Settings → Flow Mapping Data → 6. Feedback after print**.

## MQTT

Diatur di **Settings → Koneksi**: IP + port broker (umumnya 1883), topic data masuk (subscribe,
boleh wildcard `+`/`#`), topic feedback (publish), Client ID (harus unik per komputer), username /
password, QoS 0/1/2, TLS, keep alive, clean session, retain.

- Satu pesan MQTT = satu data job (beberapa baris dalam satu pesan = beberapa data). Tidak perlu CR/LF.
- Feedback dikirim satu pesan per cetakan; akhiran `<CR><LF>` dari susunan feedback tidak ikut dikirim.
- Jika broker terputus, aplikasi menyambung ulang otomatis dan feedback yang tertunda dikirim
  setelah tersambung. Data job berikutnya ditahan di broker (QoS 1/2) jika printer lebih lambat.
- **Test Koneksi Server** mencoba login ke broker.
- Password MQTT disimpan di `data\settings.json` di komputer ini.

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
- Aplikasi desktop terbuka **fullscreen tanpa title bar**. Tombol minimize, fullscreen/restore dan
  tutup ada di pojok kanan atas aplikasi (**F11** = fullscreen/restore). Saat restore, jendela digeser
  dari bar menu atas dan ukurannya diubah dari tepi jendela.

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

- **Dashboard** (satu layar untuk operator, tanpa scroll; tampilan menyesuaikan ukuran jendela
  mulai 1280×720):
  - Atas: menu (Dashboard, Log History, Settings, About) dan status **MQTT Broker**
    (ONLINE / CONNECTING / OFFLINE), **printer** (STARTED / NOT STARTED / OFFLINE) dan operator aktif.
  - Kiri: **Counter Terakhir** (besar), counter berikutnya, template aktif, grafik cetakan per detik;
    **Control Panel** dengan toggle **Start / Stop Print** dan **Start / Stop Transfer**: belum jalan =
    Start menyala, Stop mati; sudah jalan = Start mati, Stop menyala (status printer yang belum terbaca
    dianggap belum start). Di bawahnya status print (menunggu printer, antrean).
  - Diagram alur **Printer → Queue → Server** (kabel bergerak saat transfer berjalan) dengan
    Received (byte dari server), Sent (byte ke printer + feedback ke server), Tercetak, Gagal dan
    grafik lalu lintas.
  - **Incoming Data** (Source `Server` = data masuk, `Printer` = feedback cetak; kolom Counter & Status).
  - **Transfer Log** kanan: semua kejadian. **Transfer Log** bawah (_After Print → Server_): setiap
    printer mengirim sinyal selesai cetak, data yang dikirim balik ke server dengan statusnya
    (`OK` = diterima server, `MENUNGGU` = server terputus / antre, `DIBUANG` = antrean penuh).
  - **Counter per Template** (ubah / reset) dan tombol Reset Counter.
- **Log History**: log per hari (filter status, cari, download file lengkap).
- **Settings**:
  - _Koneksi_: IP + port server dan IP + port printer (atau RS232), timeout, tombol test.
  - _Flow Mapping Data_: contoh data, delimiter / lebar tetap, posisi tiap field, protokol printer,
    nama template (change template), field data, data feedback ke server (pilih data, urutan, format,
    pemisah, akhiran; atau format custom), preview HEX perintah.
  - _Counter_: mode, nilai awal, step, digit, batas maksimum, jam ganti hari.

Semua tampilan live dan Log History hanya menampilkan **50 baris terakhir** agar UI tetap ringan
(file log lengkap tetap tersimpan di `data\logs\` dan bisa di-download).

## Ketahanan transfer

- Update ke UI digabung paling sering tiap 250 ms (maks. 50 data + 50 log), jadi data deras tidak
  membuat UI lag.
- Data dari server masuk antrean terbatas (1.000). Jika printer lebih lambat, pembacaan dari server
  ditahan lewat TCP (tidak menumpuk di memori, tidak ada data dibuang).
- Sinyal selesai cetak dari printer diproses lebih dulu daripada data server.
- Feedback ke server dikirim oleh proses terpisah dengan antrean: server lambat / terputus tidak
  menahan pengiriman ke printer; laporan dikirim setelah server tersambung lagi.
- Semua komunikasi punya batas waktu (koneksi, kirim, balasan); koneksi yang macet diulang otomatis.
- Buffer printer penuh (`P_Status 43`) atau printer belum start (`P_Status 12`): data disimpan
  (status MENUNGGU) dan dikirim otomatis, tidak dibuang.
- Log ditulis ke file oleh proses background; error tak terduga dicatat di `data\crash.log` tanpa
  menutup aplikasi.
- Hasil uji di container Linux: 20.000 data diproses dalam 7,2 detik (±2.700 data/detik termasuk
  balasan printer), memori puncak ±220 MB, lag UI maksimum 24 ms; pada 20 cetakan/detik tidak ada
  cetak ulang (jeda selesai cetak → data berikutnya median 1 ms).

## Uji tanpa mesin

`run.bat simulator` membuka simulator printer TIJ (port 9100, template `867892`, `555111`,
mencetak tiap 2 detik) dan **MQTT broker** kecil (port 1883) yang mengirim beberapa job ke
`sharx/job` dan menampilkan feedback dari `sharx/printed`. Di **Settings → Koneksi** isi broker
`127.0.0.1:1883` dan printer `127.0.0.1:9100`, simpan, lalu **Start Transfer**.
(Simulator server TCP: `SharxSimulator server --port 9000`.)

## Printer belum start

Jika printer terhubung tetapi print belum di-start, printer membalas `P_Status 12` (_Print not
started_). Data **tidak dianggap error**: statusnya **MENUNGGU** (kuning) dan disimpan.

- Software mengecek printer tiap 2 detik (perintah `14` Request Printer Status; bisa diatur di
  _Flow Mapping Data → Cek printer sudah start tiap_). Begitu printer di-start dari panel printer,
  job (change template + field data) langsung dikirim otomatis.
- **Control Panel** di Dashboard punya tombol **Start Print** (`11`) dan **Stop Print** (`12`).
  Setelah Stop Print, job dikirim ulang otomatis saat printer start lagi.
- Centang **"Start print otomatis jika printer belum start"** jika software boleh men-start printer sendiri.
- Simulator: `SharxSimulator printer --started false` meniru printer yang belum start (tekan `S` di
  jendela simulator untuk start/stop dari "panel").

## Catatan pemasangan printer TIJ

- Printer TIJ hanya menerima **satu** klien TCP; jangan sambungkan software lain bersamaan.
- Template di printer harus punya **remote field** dengan nomor sesuai Settings (default 0 dan 1).
- _Buffer Setting_ `1D` di printer minimal sama dengan "Antrean data di printer" (default 1).

## Struktur

```
run.bat                 menjalankan / mem-publish aplikasi
src/Sharx.Core          logika: parser, counter, driver printer (TIJ, Generic), koneksi
src/Sharx.Web           REST API + WebSocket + UI (wwwroot: index.html, css, js, font)
src/Sharx.Host          exe mode browser / service
src/Sharx.Desktop       exe Windows (WinForms + WebView2, fullscreen tanpa title bar)
tools/Sharx.Simulator   simulator server & printer TIJ
tests/Sharx.Core.Tests  xUnit (termasuk contoh checksum dari dokumen protokol)
```

UI hanya bisa diakses dari komputer yang sama (127.0.0.1) kecuali memakai `--allow-remote`.
Font Roboto Condensed & Roboto Mono berlisensi SIL Open Font License (lihat `wwwroot/fonts`).
Library pihak ketiga: MQTTnet (MIT), Microsoft Edge WebView2 SDK, System.IO.Ports (MIT).
