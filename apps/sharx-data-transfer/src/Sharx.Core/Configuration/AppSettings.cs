namespace Sharx.Core.Configuration;

/// <summary>Semua pengaturan aplikasi. Disimpan sebagai <c>data/settings.json</c>.</summary>
public sealed class AppSettings
{
    public ServerSettings Server { get; set; } = new();
    public MappingSettings Mapping { get; set; } = new();
    public CounterSettings Counter { get; set; } = new();
    public PrinterSettings Printer { get; set; } = new();
    public FeedbackSettings Feedback { get; set; } = new();
    public GeneralSettings General { get; set; } = new();
}

/// <summary>Cara memotong aliran TCP dari server menjadi satu record data.</summary>
public enum FrameMode
{
    /// <summary>Satu record diakhiri CR, LF atau CRLF.</summary>
    Line,
    /// <summary>Satu record diakhiri ETX (0x03); STX di depan dibuang.</summary>
    Etx,
    /// <summary>Satu record dibungkus STX ... ETX; byte di luar bingkai diabaikan.</summary>
    StxEtx,
    /// <summary>Satu record diakhiri terminator custom (boleh token seperti &lt;CR&gt;).</summary>
    Custom,
    /// <summary>Setiap paket TCP yang diterima dianggap satu record.</summary>
    Packet,
}

public sealed class ServerSettings
{
    public string Host { get; set; } = "192.168.1.100";
    public int Port { get; set; } = 8080;
    public FrameMode Framing { get; set; } = FrameMode.Line;
    public string CustomTerminator { get; set; } = "<CR><LF>";
    public string Encoding { get; set; } = "utf-8";
    public bool AutoReconnect { get; set; } = true;
    public int ReconnectDelaySeconds { get; set; } = 3;
    public int ConnectTimeoutSeconds { get; set; } = 5;
    public int MaxFrameBytes { get; set; } = 65536;

    /// <summary>
    /// Data yang belum diakhiri terminator diproses sebagai satu record setelah tidak ada data baru
    /// selama N ms (untuk server yang mengirim tanpa CR/LF). 0 = nonaktif.
    /// </summary>
    public int IdleFlushMs { get; set; } = 300;
}

/// <summary>Cara data dari server dipecah menjadi field.</summary>
public enum ParseMode
{
    /// <summary>Data dipisah dengan delimiter, field dipilih berdasarkan urutan (posisi 1, 2, 3, ...).</summary>
    Delimited,
    /// <summary>Data dengan lebar tetap, field dipilih berdasarkan karakter awal dan panjang.</summary>
    FixedWidth,
}

public sealed class MappingSettings
{
    public ParseMode Mode { get; set; } = ParseMode.Delimited;

    /// <summary>Pemisah field. Boleh token seperti <c>&lt;TAB&gt;</c>.</summary>
    public string Delimiter { get; set; } = ",";

    public bool TrimFields { get; set; } = true;

    /// <summary>Jumlah field yang wajib ada (mode Delimited). 0 = tidak dicek.</summary>
    public int ExpectedFieldCount { get; set; }

    /// <summary>Contoh data untuk preview di halaman Settings.</summary>
    public string SampleData { get; set; } = "ANANDA02,867892,000";

    public List<FieldMapping> Fields { get; set; } =
    [
        new() { Key = FieldKeys.Operator, Label = "Nama Operator", Position = 1, Required = true },
        new() { Key = FieldKeys.Template, Label = "Template CIJ", Position = 2, Required = true },
        new() { Key = FieldKeys.ServerCounter, Label = "Counter (server)", Position = 3, Required = false },
    ];
}

/// <summary>Nama kunci field khusus yang dipakai logika counter.</summary>
public static class FieldKeys
{
    public const string Operator = "OPERATOR";
    public const string Template = "TEMPLATE";
    public const string ServerCounter = "SERVER_COUNTER";
}

public sealed class FieldMapping
{
    /// <summary>Kunci field, dipakai sebagai placeholder <c>{KEY}</c> di output printer.</summary>
    public string Key { get; set; } = "";

    public string Label { get; set; } = "";

    /// <summary>Urutan field (1 = field pertama) untuk mode Delimited.</summary>
    public int Position { get; set; } = 1;

    /// <summary>
    /// Karakter awal (1 = karakter pertama). Mode FixedWidth: posisi di seluruh data.
    /// Mode Delimited: opsional, mengambil sebagian isi field (0 = seluruh field).
    /// </summary>
    public int Start { get; set; }

    /// <summary>Jumlah karakter yang diambil. 0 = sampai akhir.</summary>
    public int Length { get; set; }

    /// <summary>Data ditolak (tidak diteruskan ke printer) jika field ini kosong.</summary>
    public bool Required { get; set; }
}

/// <summary>Kapan counter bertambah.</summary>
public enum CounterIncrement
{
    /// <summary>Setiap printer melapor selesai cetak (Print End / after print).</summary>
    Print,
    /// <summary>Setiap data diterima dari server dan berhasil dikirim ke printer.</summary>
    Record,
}

public sealed class CounterSettings
{
    public CounterIncrement IncrementOn { get; set; } = CounterIncrement.Print;

    /// <summary>Nilai awal counter jika belum ada data dari server.</summary>
    public long StartValue { get; set; }

    public long Step { get; set; } = 1;

    /// <summary>
    /// Jumlah digit counter (zero padding), mis. 3 =&gt; 001.
    /// 0 = ikuti panjang counter dari server (000 =&gt; 001, 00000 =&gt; 00001).
    /// </summary>
    public int Digits { get; set; }

    /// <summary>
    /// Untuk template yang belum punya counter hari ini, pakai nilai counter dari server sebagai nilai awal.
    /// </summary>
    public bool UseServerValueAsStart { get; set; } = true;

    /// <summary>true: data pertama = awal + step (000 =&gt; 001). false: data pertama = nilai awal.</summary>
    public bool IncrementBeforeUse { get; set; } = true;

    /// <summary>Batas maksimum; jika terlewati counter kembali ke nilai awal. 0 = tanpa batas.</summary>
    public long MaxValue { get; set; }

    /// <summary>Jam pergantian hari produksi (HH:mm). Counter direset saat lewat jam ini.</summary>
    public string DayStartTime { get; set; } = "00:00";
}

public enum PrinterTransport
{
    /// <summary>Ethernet: IP + port printer.</summary>
    Tcp,
    Serial,
}

/// <summary>Driver / protokol komunikasi dengan printer.</summary>
public enum PrinterProtocol
{
    /// <summary>DOD TIJ Protocol 4.8: 1E load message, 1D remote field, AB print end.</summary>
    Tij,
    /// <summary>Perintah diatur sendiri di Settings (untuk protokol printer lain).</summary>
    Generic,
}

public sealed class PrinterSettings
{
    /// <summary>false = mode monitor saja, data tidak diteruskan ke printer.</summary>
    public bool Enabled { get; set; } = true;

    public PrinterProtocol Protocol { get; set; } = PrinterProtocol.Tij;
    public PrinterTransport Transport { get; set; } = PrinterTransport.Tcp;
    public string Host { get; set; } = "192.168.1.50";
    public int Port { get; set; } = 9100;
    public string SerialPort { get; set; } = "COM1";
    public int BaudRate { get; set; } = 9600;
    public int DataBits { get; set; } = 8;
    public string Parity { get; set; } = "None";
    public string StopBits { get; set; } = "One";
    public string Encoding { get; set; } = "utf-8";
    public int ConnectTimeoutSeconds { get; set; } = 5;

    /// <summary>Coba konek ulang ke printer setiap N detik jika terputus.</summary>
    public int ReconnectDelaySeconds { get; set; } = 5;

    /// <summary>Batas waktu menunggu balasan printer (ms).</summary>
    public int ResponseTimeoutMs { get; set; } = 1000;

    /// <summary>Nama template yang dipanggil di printer (change template). Default nama template dari server.</summary>
    public string TemplateName { get; set; } = "{TEMPLATE}";

    /// <summary>Kosongkan antrean data di printer saat job (template/operator) berubah.</summary>
    public bool ClearOnJobChange { get; set; } = true;

    /// <summary>Jumlah data yang diantrikan di printer menunggu dicetak (1-10).</summary>
    public int Lookahead { get; set; } = 1;

    /// <summary>
    /// Jika printer belum start (print not started), software mengirim perintah Start Print sendiri.
    /// false = data disimpan dan dikirim otomatis begitu printer di-start dari panel / tombol Start Print.
    /// </summary>
    public bool AutoStartPrint { get; set; }

    /// <summary>Interval cek apakah printer sudah start, saat ada data menunggu (detik).</summary>
    public int ReadyPollSeconds { get; set; } = 2;

    /// <summary>Field data yang dikirim untuk setiap cetakan.</summary>
    public List<PrinterField> Fields { get; set; } =
    [
        new() { FieldId = 0, Name = "Nama operator", Value = "{OPERATOR}" },
        new() { FieldId = 1, Name = "Counter", Value = "{COUNTER}" },
    ];

    public TijSettings Tij { get; set; } = new();
    public GenericProtocolSettings Generic { get; set; } = new();
}

/// <summary>Field data di template printer.</summary>
public sealed class PrinterField
{
    /// <summary>Nomor field di template printer (0-255).</summary>
    public int FieldId { get; set; }

    public string Name { get; set; } = "";

    /// <summary>Isi field, boleh placeholder seperti {OPERATOR} / {COUNTER}.</summary>
    public string Value { get; set; } = "";
}

/// <summary>Opsi khusus DOD TIJ Protocol 4.8.</summary>
public sealed class TijSettings
{
    /// <summary>Slave address (byte terakhir IP printer). -1 = otomatis dari IP printer.</summary>
    public int SlaveAddress { get; set; } = -1;

    /// <summary>Kirim perintah 01 (Update Print Contents) setelah load message.</summary>
    public bool UpdateAfterLoad { get; set; } = true;
}

/// <summary>
/// Protokol yang diatur sendiri. Semua perintah boleh memakai placeholder, karakter kontrol
/// (&lt;STX&gt;, &lt;0x1B&gt;) dan checksum (&lt;SUM&gt;, &lt;XOR&gt;).
/// </summary>
public sealed class GenericProtocolSettings
{
    /// <summary>Perintah change template; {TEMPLATE_NAME} = nama template. Kosong = tidak dikirim.</summary>
    public string TemplateCommand { get; set; } = "<STX>TEMPLATE:{TEMPLATE_NAME}<ETX>";

    /// <summary>Perintah mengosongkan antrean data di printer. Kosong = tidak ada.</summary>
    public string ClearCommand { get; set; } = "";

    /// <summary>Perintah per field: {FIELD_ID}, {FIELD_NAME}, {FIELD_VALUE}. Kosong = tidak dipakai.</summary>
    public string FieldCommand { get; set; } = "<STX>FIELD:{FIELD_ID}:{FIELD_VALUE}<ETX>";

    /// <summary>Satu perintah untuk semua field: {FIELD_0}, {FIELD_1}, ... Kosong = tidak dipakai.</summary>
    public string DataCommand { get; set; } = "";

    /// <summary>Balasan sukses dari printer. Kosong = tidak menunggu balasan.</summary>
    public string AckPattern { get; set; } = "<ACK>";

    /// <summary>Balasan gagal dari printer. Kosong = tidak ada.</summary>
    public string NakPattern { get; set; } = "<NAK>";

    /// <summary>Sinyal dari printer setiap selesai cetak. Kosong = printer tidak mengirim feedback.</summary>
    public string PrintDonePattern { get; set; } = "";

    /// <summary>Jeda antar perintah (ms).</summary>
    public int CommandDelayMs { get; set; }

    /// <summary>Perintah start print. Kosong = tombol Start Print tidak tersedia.</summary>
    public string StartCommand { get; set; } = "";

    /// <summary>Perintah stop print. Kosong = tombol Stop Print tidak tersedia.</summary>
    public string StopCommand { get; set; } = "";
}

/// <summary>Laporan ke server setiap printer selesai mencetak.</summary>
public sealed class FeedbackSettings
{
    public bool Enabled { get; set; } = true;

    /// <summary>Format data ke server. Placeholder berisi data yang tercetak.</summary>
    public string Template { get; set; } = "{OPERATOR},{TEMPLATE},{COUNTER}<CR><LF>";

    /// <summary>Jumlah laporan yang disimpan sementara saat server terputus.</summary>
    public int MaxQueued { get; set; } = 10000;
}

public sealed class GeneralSettings
{
    /// <summary>Jumlah hari log disimpan di folder data/logs. 0 = selamanya.</summary>
    public int LogRetentionDays { get; set; } = 90;

    /// <summary>Langsung Start Transfer saat aplikasi dibuka.</summary>
    public bool AutoStart { get; set; }
}
