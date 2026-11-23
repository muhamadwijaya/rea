using Sharx.Core.Configuration;
using Sharx.Core.Logging;
using Sharx.Core.Mapping;
using Sharx.Core.Transfer;
using Sharx.Core.Transport;

namespace Sharx.Core.Printing;

/// <summary>Nilai satu field data yang dikirim ke printer.</summary>
public readonly record struct FieldValue(int Id, string Name, string Value);

/// <summary>Perintah yang akan dikirim (untuk preview di Settings).</summary>
public sealed record PreviewCommand(string Name, string Text, byte[] Bytes);

/// <summary>Satu rangkaian perintah ke printer; semua perintah yang terkirim dicatat di <see cref="Trace"/>.</summary>
public sealed class PrinterCall(
    PrinterSession session,
    AppSettings settings,
    TransferStats stats,
    TransferLog log,
    List<SentCommand> trace,
    CancellationToken cancellation)
{
    public PrinterSession Session { get; } = session;
    public AppSettings Settings { get; } = settings;
    public PrinterSettings Printer => Settings.Printer;
    public TransferStats Stats { get; } = stats;
    public TransferLog Log { get; } = log;
    public List<SentCommand> Trace { get; } = trace;
    public CancellationToken Cancellation { get; } = cancellation;
    public TimeSpan ResponseTimeout => TimeSpan.FromMilliseconds(Math.Max(50, Printer.ResponseTimeoutMs));
}

/// <summary>
/// Protokol printer. Alur kerja (change template, field data, sinyal selesai cetak, counter)
/// sama untuk semua printer; driver hanya menerjemahkan ke perintah protokolnya.
/// </summary>
public interface IPrinterDriver
{
    string Name { get; }

    /// <summary>true jika printer melapor setiap selesai cetak (dibutuhkan mode counter per cetak).</summary>
    bool HasPrintFeedback(PrinterSettings printer);

    IPrinterFrameParser CreateParser(PrinterSettings printer);

    /// <summary>Panggil template yang sudah tersimpan di printer.</summary>
    Task ChangeTemplateAsync(PrinterCall call, string templateName, OutputContext context);

    /// <summary>Kosongkan antrean data di printer (tidak melakukan apa-apa jika protokol tidak mendukung).</summary>
    Task ClearAsync(PrinterCall call, OutputContext context);

    /// <summary>Kirim field data (operator, counter, ...) untuk satu cetakan.</summary>
    Task SendFieldsAsync(PrinterCall call, IReadOnlyList<FieldValue> fields, OutputContext context);

    /// <summary>true jika driver bisa mengirim Start Print / Stop Print.</summary>
    bool SupportsPrintControl(PrinterSettings printer);

    Task StartPrintAsync(PrinterCall call);

    Task StopPrintAsync(PrinterCall call);

    /// <summary>true = printer sudah start, false = belum, null = tidak bisa ditanyakan.</summary>
    Task<bool?> IsPrintStartedAsync(PrinterCall call);

    /// <summary>Info tambahan saat test koneksi (mis. daftar template di printer).</summary>
    Task<string?> InspectAsync(PrinterSession session, PrinterSettings printer, CancellationToken ct);

    IReadOnlyList<PreviewCommand> Preview(PrinterSettings printer, string templateName, IReadOnlyList<FieldValue> fields, OutputContext context);

    IReadOnlyList<string> Validate(AppSettings settings, IReadOnlyCollection<string> fieldKeys);
}

public static class PrinterDrivers
{
    private static readonly TijDriver Tij = new();
    private static readonly GenericDriver Generic = new();

    public static IPrinterDriver For(PrinterSettings printer) => printer.Protocol switch
    {
        PrinterProtocol.Generic => Generic,
        _ => Tij,
    };

    /// <summary>Isi setiap field data dari Settings untuk konteks ini.</summary>
    public static IReadOnlyList<FieldValue> RenderFields(PrinterSettings printer, OutputContext context) =>
        printer.Fields.Select(f => new FieldValue(f.FieldId, f.Name, OutputFormatter.Render(f.Value, context))).ToArray();

    public static string Describe(IEnumerable<FieldValue> fields) =>
        string.Join(", ", fields.Select(f => $"field {f.Id}={Text.ControlTokens.Visualize(f.Value)}"));
}
