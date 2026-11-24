using Sharx.Core.Mapping;

namespace Sharx.Core.Transfer;

public enum ServerState
{
    Stopped,
    Connecting,
    Connected,
    Disconnected,
}

public enum PrinterState
{
    Disabled,
    Idle,
    Connecting,
    Connected,
    Error,
}

public enum RecordStatus
{
    /// <summary>Data dari server diteruskan ke printer.</summary>
    Ok,
    /// <summary>Printer selesai mencetak (feedback after print) dan dilaporkan ke server.</summary>
    Printed,
    /// <summary>Printer dinonaktifkan; data hanya dimonitor.</summary>
    Monitor,
    /// <summary>Data tidak sesuai Flow Mapping; tidak diteruskan.</summary>
    Rejected,
    /// <summary>Pengiriman ke printer gagal.</summary>
    PrinterError,
    /// <summary>Printer belum start; data disimpan dan dikirim otomatis saat printer start.</summary>
    Waiting,
}

public sealed record SentCommand(int Step, string Name, string Data, int Bytes, string? Response);

public sealed record TransferRecord
{
    public required long Seq { get; init; }
    public required DateTimeOffset Time { get; init; }

    /// <summary>"Server" untuk data masuk, "Printer" untuk feedback setelah cetak.</summary>
    public string Source { get; init; } = "Server";

    /// <summary>Data (karakter kontrol ditampilkan sebagai token).</summary>
    public required string Data { get; init; }

    public required int Bytes { get; init; }
    public string? Operator { get; init; }
    public string? Template { get; init; }
    public string? ServerCounter { get; init; }
    public string? Counter { get; init; }
    public string? CounterNote { get; init; }
    public IReadOnlyList<MappedField> Fields { get; init; } = [];
    public IReadOnlyList<SentCommand> Commands { get; init; } = [];
    public RecordStatus Status { get; init; }
    public string? Message { get; init; }
}

/// <summary>Job yang sedang aktif di printer.</summary>
public sealed record JobStatus(
    string Operator,
    string Template,
    string? NextCounter,
    int Pending,
    bool Armed,
    string? LastPrinted);

public sealed record TransferStatus(
    bool Running,
    ServerState Server,
    string ServerEndpoint,
    DateTimeOffset? ConnectedSince,
    PrinterState Printer,
    string PrinterEndpoint,
    string PrinterProtocol,
    string? PrinterMessage,
    string? LastError,
    JobStatus? Job,
    int FeedbackQueued,
    PrintState PrintState,
    bool WaitingForPrinter,
    int WaitingRecords,
    bool CanControlPrint,
    string ServerProtocol,
    bool FeedbackEnabled,
    string FeedbackFormat);

/// <summary>Status pengiriman satu laporan feedback ke server.</summary>
public enum FeedbackState
{
    /// <summary>Di antrean: server belum terhubung atau sedang dikirim.</summary>
    Queued,

    /// <summary>Diterima server (TCP terkirim / broker MQTT menerima publish).</summary>
    Sent,

    /// <summary>Dibuang karena antrean feedback penuh saat server lama terputus.</summary>
    Dropped,
}

/// <summary>Data yang dikirim balik ke server setelah printer melapor selesai cetak.</summary>
public sealed record FeedbackRecord(
    long Seq,
    DateTimeOffset Time,
    string Operator,
    string Template,
    string Counter,
    string Data,
    int Bytes,
    FeedbackState State,
    DateTimeOffset? SentAt);

public sealed record SettingsApplyResult(bool Ok, IReadOnlyList<string> Errors, bool Reconnecting);

public sealed record ConnectionTestResult(bool Ok, string Message, long ElapsedMs);

/// <summary>Printer menolak perintah / membalas status gagal.</summary>
public class PrinterException(string message, Exception? inner = null) : Exception(message, inner);

/// <summary>Printer terhubung tetapi print belum di-start; data ditahan sampai printer start.</summary>
public class PrinterNotReadyException(string message) : PrinterException(message);

/// <summary>Antrean (buffer) printer penuh; data ditahan sampai printer selesai mencetak.</summary>
public sealed class PrinterBusyException(string message) : PrinterNotReadyException(message);

/// <summary>Status print di printer.</summary>
public enum PrintState
{
    Unknown,
    Started,
    NotStarted,
}
