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
    int FeedbackQueued);

public sealed record SettingsApplyResult(bool Ok, IReadOnlyList<string> Errors, bool Reconnecting);

public sealed record ConnectionTestResult(bool Ok, string Message, long ElapsedMs);

/// <summary>Printer menolak perintah / membalas status gagal.</summary>
public sealed class PrinterException(string message, Exception? inner = null) : Exception(message, inner);
