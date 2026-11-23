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

    /// <summary>Jumlah digit counter (zero padding). 3 =&gt; 001.</summary>
    public int Digits { get; set; } = 3;

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
    Tcp,
    Serial,
}

/// <summary>Bahasa komunikasi dengan printer.</summary>
public enum PrinterProtocol
{
    /// <summary>DOD TIJ Protocol 4.8: ESC STX addr cmd data ESC ETX checksum.</summary>
    Tij,
    /// <summary>Teks bebas sesuai langkah output (untuk printer lain).</summary>
    CustomText,
}

/// <summary>Kapan satu langkah output dikirim ke printer.</summary>
public enum StepTrigger
{
    /// <summary>Setiap data.</summary>
    Always,
    /// <summary>Hanya jika template berubah (atau data pertama setelah Start).</summary>
    TemplateChanged,
    /// <summary>Hanya jika operator berubah (atau data pertama setelah Start).</summary>
    OperatorChanged,
    /// <summary>Jika template atau operator berubah (atau data pertama setelah Start).</summary>
    JobChanged,
}

public sealed class OutputStep
{
    public string Name { get; set; } = "";
    public bool Enabled { get; set; } = true;
    public StepTrigger Trigger { get; set; } = StepTrigger.Always;

    /// <summary>Format data ke printer, mis. <c>&lt;STX&gt;{TEMPLATE}|{OPERATOR}|{COUNTER}&lt;ETX&gt;</c>.</summary>
    public string Template { get; set; } = "";
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

    public TijSettings Tij { get; set; } = new();

    // ---- Mode CustomText ----

    /// <summary>Jeda antar langkah output (ms).</summary>
    public int StepDelayMs { get; set; } = 50;

    /// <summary>Tunggu balasan printer setelah setiap langkah.</summary>
    public bool WaitForResponse { get; set; }

    /// <summary>Balasan yang dianggap sukses (boleh token, mis. &lt;ACK&gt;). Kosong = balasan apa pun.</summary>
    public string ExpectedResponse { get; set; } = "";

    /// <summary>Teks dari printer yang berarti "selesai cetak" (feedback after print). Kosong = tidak ada.</summary>
    public string PrintDoneSignal { get; set; } = "";

    public List<OutputStep> Steps { get; set; } =
    [
        new()
        {
            Name = "Kirim data ke printer",
            Trigger = StepTrigger.Always,
            Template = "{TEMPLATE},{OPERATOR},{COUNTER}<CR><LF>",
        },
    ];
}

/// <summary>Pengaturan DOD TIJ Protocol 4.8.</summary>
public sealed class TijSettings
{
    /// <summary>Slave address (byte terakhir IP printer). -1 = otomatis dari IP printer.</summary>
    public int SlaveAddress { get; set; } = -1;

    /// <summary>Nama message yang di-load (perintah 1E). Default nama template dari server.</summary>
    public string MessageName { get; set; } = "{TEMPLATE}";

    /// <summary>Kirim perintah 01 (Update Print Contents) setelah load message.</summary>
    public bool UpdateAfterLoad { get; set; } = true;

    /// <summary>Kirim perintah 11 (Start Print) setelah load message.</summary>
    public bool StartPrintAfterLoad { get; set; }

    /// <summary>Kirim perintah 21 (Clear Buffers of 1D) saat job (template/operator) berubah.</summary>
    public bool ClearBufferOnJobChange { get; set; } = true;

    /// <summary>Jumlah data 1D yang diantrikan di buffer printer (harus &lt;= Buffer Setting printer).</summary>
    public int Lookahead { get; set; } = 1;

    /// <summary>Isi remote field (perintah 1D).</summary>
    public List<RemoteField> Fields { get; set; } =
    [
        new() { FieldId = 0, Name = "Nama operator", Value = "{OPERATOR}" },
        new() { FieldId = 1, Name = "Counter", Value = "{COUNTER}" },
    ];
}

public sealed class RemoteField
{
    /// <summary>Nomor field di message printer (0-255).</summary>
    public int FieldId { get; set; }

    public string Name { get; set; } = "";

    /// <summary>Isi field, boleh placeholder seperti {OPERATOR} / {COUNTER}.</summary>
    public string Value { get; set; } = "";
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

    /// <summary>Jumlah baris data terakhir yang ditampilkan di tabel live.</summary>
    public int LiveRows { get; set; } = 200;

    /// <summary>Langsung Start Transfer saat aplikasi dibuka.</summary>
    public bool AutoStart { get; set; }
}
