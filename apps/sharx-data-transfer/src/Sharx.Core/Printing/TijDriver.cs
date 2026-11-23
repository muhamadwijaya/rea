using Sharx.Core.Configuration;
using Sharx.Core.Logging;
using Sharx.Core.Mapping;
using Sharx.Core.Text;
using Sharx.Core.Transfer;
using Sharx.Core.Transport;

namespace Sharx.Core.Printing;

/// <summary>
/// DOD TIJ Protocol 4.8: change template = 1E Load Print Message (+01 / +11),
/// field data = 1D Download Remote Field Data, kosongkan antrean = 21, selesai cetak = AB.
/// </summary>
public sealed class TijDriver : IPrinterDriver
{
    public string Name => "TIJ Protocol 4.8";

    public bool HasPrintFeedback(PrinterSettings printer) => true;

    public IPrinterFrameParser CreateParser(PrinterSettings printer) => new TijFrameParser();

    public static byte SlaveAddress(PrinterSettings p) =>
        p.Tij.SlaveAddress is >= 0 and <= 255
            ? (byte)p.Tij.SlaveAddress
            : p.Transport == PrinterTransport.Tcp ? TijProtocol.SlaveFromHost(p.Host) : (byte)0;

    public async Task ChangeTemplateAsync(PrinterCall call, string templateName, OutputContext context)
    {
        var p = call.Printer;
        var slave = SlaveAddress(p);
        await RequestAsync(call, TijProtocol.CmdLoadMessage,
            TijProtocol.LoadMessage(slave, templateName, TextEncodings.Resolve(p.Encoding)), $"template {templateName}", 0x00);
        if (p.Tij.UpdateAfterLoad)
        {
            await RequestAsync(call, TijProtocol.CmdUpdatePrint, TijProtocol.Command(slave, TijProtocol.CmdUpdatePrint), "", 0x00, 0x12);
        }

    }

    public bool SupportsPrintControl(PrinterSettings printer) => true;

    public Task StartPrintAsync(PrinterCall call) =>
        RequestAsync(call, TijProtocol.CmdStartPrint,
            TijProtocol.Command(SlaveAddress(call.Printer), TijProtocol.CmdStartPrint), "", 0x00, 0x11);

    public Task StopPrintAsync(PrinterCall call) =>
        RequestAsync(call, TijProtocol.CmdStopPrint,
            TijProtocol.Command(SlaveAddress(call.Printer), TijProtocol.CmdStopPrint), "", 0x00, TijProtocol.StatusPrintNotStarted);

    /// <summary>14 Request Printer Status: P_Status 12 = print belum start (tersedia sejak firmware v1277).</summary>
    public async Task<bool?> IsPrintStartedAsync(PrinterCall call)
    {
        var payload = TijProtocol.Command(SlaveAddress(call.Printer), TijProtocol.CmdPrinterStatus);
        var reply = await call.Session.RequestAsync(
            payload,
            f => f.Kind is PrinterFrameKind.Ack or PrinterFrameKind.Nak && f.Command == TijProtocol.CmdPrinterStatus,
            call.ResponseTimeout,
            call.Cancellation).ConfigureAwait(false);
        call.Stats.AddSent(payload.Length);
        return reply is { Kind: PrinterFrameKind.Ack }
            ? reply.Status != TijProtocol.StatusPrintNotStarted
            : null;
    }

    public Task ClearAsync(PrinterCall call, OutputContext context) =>
        RequestAsync(call, TijProtocol.CmdClearBuffer,
            TijProtocol.Command(SlaveAddress(call.Printer), TijProtocol.CmdClearBuffer), "", 0x00, 0x12);

    public Task SendFieldsAsync(PrinterCall call, IReadOnlyList<FieldValue> fields, OutputContext context) =>
        RequestAsync(call, TijProtocol.CmdRemoteFields, RemoteFields(call.Printer, fields), PrinterDrivers.Describe(fields), 0x00, 0x42);

    public async Task<string?> InspectAsync(PrinterSession session, PrinterSettings printer, CancellationToken ct)
    {
        var reply = await session.RequestAsync(
            TijProtocol.Command(SlaveAddress(printer), TijProtocol.CmdMessageNames),
            f => f.Kind is PrinterFrameKind.Ack or PrinterFrameKind.Nak && f.Command == TijProtocol.CmdMessageNames,
            TimeSpan.FromMilliseconds(Math.Max(500, printer.ResponseTimeoutMs)),
            ct).ConfigureAwait(false);
        if (reply is not { Kind: PrinterFrameKind.Ack })
        {
            return reply?.Describe();
        }

        var names = TijProtocol.ParseMessageNames(reply.Data, TextEncodings.Resolve(printer.Encoding));
        return names.Count > 0
            ? $"Template di printer: {string.Join(", ", names)}"
            : $"Status {reply.Status:X2} ({TijProtocol.PrinterStatusText(reply.Status)})";
    }

    public IReadOnlyList<PreviewCommand> Preview(
        PrinterSettings p,
        string templateName,
        IReadOnlyList<FieldValue> fields,
        OutputContext context)
    {
        var slave = SlaveAddress(p);
        var list = new List<PreviewCommand>
        {
            new("Change template: Load Print Message (1E)", $"template = {templateName}",
                TijProtocol.LoadMessage(slave, templateName, TextEncodings.Resolve(p.Encoding))),
        };
        if (p.Tij.UpdateAfterLoad)
        {
            list.Add(new("Update Print Contents (01)", "", TijProtocol.Command(slave, TijProtocol.CmdUpdatePrint)));
        }

        if (p.ClearOnJobChange)
        {
            list.Add(new("Clear Buffers of 1D (21)", "", TijProtocol.Command(slave, TijProtocol.CmdClearBuffer)));
        }

        list.Add(new("Field data: Download Remote Field Data (1D)", PrinterDrivers.Describe(fields), RemoteFields(p, fields)));
        return list;
    }

    public IReadOnlyList<string> Validate(AppSettings settings, IReadOnlyCollection<string> fieldKeys)
    {
        var errors = new List<string>();
        if (settings.Printer.Tij.SlaveAddress is < -1 or > 255)
        {
            errors.Add("Slave address -1 (otomatis) atau 0-255");
        }

        return errors;
    }

    private static byte[] RemoteFields(PrinterSettings p, IReadOnlyList<FieldValue> fields) =>
        TijProtocol.RemoteFields(
            SlaveAddress(p),
            fields.Select(f => ((byte)Math.Clamp(f.Id, 0, 255), f.Value)).ToArray(),
            TextEncodings.Resolve(p.Encoding));

    /// <summary>Kirim satu perintah TIJ dan periksa balasannya (ACK + P_Status yang diterima).</summary>
    private static async Task RequestAsync(PrinterCall call, byte command, byte[] payload, string detail, params byte[] okStatuses)
    {
        var name = TijProtocol.CommandName(command);
        var frame = await call.Session.RequestAsync(
            payload,
            f => f.Kind is PrinterFrameKind.Ack or PrinterFrameKind.Nak && f.Command == command,
            call.ResponseTimeout,
            call.Cancellation).ConfigureAwait(false) ?? throw new IOException($"{name}: tidak ada balasan");
        call.Stats.AddSent(payload.Length);
        call.Trace.Add(new SentCommand(call.Trace.Count + 1, name, detail, payload.Length, frame.Describe()));

        if (frame.Kind == PrinterFrameKind.Nak)
        {
            throw new PrinterException(
                $"{name} ditolak printer: {TijProtocol.CommandStatusText(frame.Status)} (NAK {frame.Status:X2})");
        }

        if (frame.Status == 0x01)
        {
            call.Log.Write(LogStatus.Warn, $"{name}: printer melaporkan error/warning aktif (P_Status 01)");
            return;
        }

        if (!okStatuses.Contains(frame.Status))
        {
            var text = $"{name}: {TijProtocol.PrinterStatusText(frame.Status)} (P_Status {frame.Status:X2})";
            throw frame.Status == TijProtocol.StatusPrintNotStarted
                ? new PrinterNotReadyException(text)
                : new PrinterException(text);
        }

        // 42 (buffer now full) normal saat buffer 1D terisi; status lain yang diterima dicatat.
        if (frame.Status is not (0x00 or 0x42))
        {
            call.Log.Write(LogStatus.Warn, $"{name}: {TijProtocol.PrinterStatusText(frame.Status)} (P_Status {frame.Status:X2})");
        }
    }
}
