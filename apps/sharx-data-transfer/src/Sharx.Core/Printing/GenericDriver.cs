using System.Globalization;
using System.Text;
using Sharx.Core.Configuration;
using Sharx.Core.Mapping;
using Sharx.Core.Text;
using Sharx.Core.Transfer;
using Sharx.Core.Transport;

namespace Sharx.Core.Printing;

/// <summary>
/// Protokol yang perintahnya diatur di Settings: change template, field data (per field atau satu
/// perintah), kosongkan antrean, balasan ACK/NAK dan sinyal selesai cetak.
/// </summary>
public sealed class GenericDriver : IPrinterDriver
{
    public const string TemplateNameKey = "TEMPLATE_NAME";
    public const string FieldIdKey = "FIELD_ID";
    public const string FieldNameKey = "FIELD_NAME";
    public const string FieldValueKey = "FIELD_VALUE";

    public string Name => "Generic";

    public bool HasPrintFeedback(PrinterSettings printer) => !string.IsNullOrEmpty(printer.Generic.PrintDonePattern);

    public IPrinterFrameParser CreateParser(PrinterSettings printer) =>
        new TextFrameParser(TextEncodings.Resolve(printer.Encoding).GetBytes(ControlTokens.Decode(printer.Generic.PrintDonePattern ?? "")));

    public Task ChangeTemplateAsync(PrinterCall call, string templateName, OutputContext context)
    {
        var g = call.Printer.Generic;
        return string.IsNullOrEmpty(g.TemplateCommand)
            ? Task.CompletedTask
            : SendAsync(call, "Change template", Render(g.TemplateCommand, context, TemplateValues(templateName)));
    }

    public Task ClearAsync(PrinterCall call, OutputContext context)
    {
        var g = call.Printer.Generic;
        return string.IsNullOrEmpty(g.ClearCommand)
            ? Task.CompletedTask
            : SendAsync(call, "Kosongkan antrean", Render(g.ClearCommand, context, []));
    }

    public async Task SendFieldsAsync(PrinterCall call, IReadOnlyList<FieldValue> fields, OutputContext context)
    {
        foreach (var (name, text) in FieldCommands(call.Printer, fields, context))
        {
            await SendAsync(call, name, text).ConfigureAwait(false);
        }
    }

    public bool SupportsPrintControl(PrinterSettings printer) =>
        !string.IsNullOrEmpty(printer.Generic.StartCommand) && !string.IsNullOrEmpty(printer.Generic.StopCommand);

    public Task StartPrintAsync(PrinterCall call) =>
        string.IsNullOrEmpty(call.Printer.Generic.StartCommand)
            ? throw new PrinterException("Perintah Start Print belum diisi di Settings")
            : SendAsync(call, "Start print", Render(call.Printer.Generic.StartCommand, EmptyContext(), []));

    public Task StopPrintAsync(PrinterCall call) =>
        string.IsNullOrEmpty(call.Printer.Generic.StopCommand)
            ? throw new PrinterException("Perintah Stop Print belum diisi di Settings")
            : SendAsync(call, "Stop print", Render(call.Printer.Generic.StopCommand, EmptyContext(), []));

    public Task<bool?> IsPrintStartedAsync(PrinterCall call) => Task.FromResult<bool?>(null);

    private static OutputContext EmptyContext() =>
        new(new ParsedRecord("", [], new Dictionary<string, string>()), 0, "", "", DateTimeOffset.Now, 0);

    public Task<string?> InspectAsync(PrinterSession session, PrinterSettings printer, CancellationToken ct) =>
        Task.FromResult<string?>(null);

    public IReadOnlyList<PreviewCommand> Preview(
        PrinterSettings p,
        string templateName,
        IReadOnlyList<FieldValue> fields,
        OutputContext context)
    {
        var g = p.Generic;
        var encoding = TextEncodings.Resolve(p.Encoding);
        var list = new List<PreviewCommand>();
        void Add(string name, string text) =>
            list.Add(new PreviewCommand(name, ControlTokens.Visualize(text), CommandEncoder.Encode(text, encoding)));

        if (!string.IsNullOrEmpty(g.TemplateCommand))
        {
            Add("Change template", Render(g.TemplateCommand, context, TemplateValues(templateName)));
        }

        if (p.ClearOnJobChange && !string.IsNullOrEmpty(g.ClearCommand))
        {
            Add("Kosongkan antrean", Render(g.ClearCommand, context, []));
        }

        foreach (var (name, text) in FieldCommands(p, fields, context))
        {
            Add(name, text);
        }

        return list;
    }

    public IReadOnlyList<string> Validate(AppSettings settings, IReadOnlyCollection<string> fieldKeys)
    {
        var g = settings.Printer.Generic;
        var errors = new List<string>();
        if (string.IsNullOrEmpty(g.FieldCommand) && string.IsNullOrEmpty(g.DataCommand))
        {
            errors.Add("Generic: isi perintah field data (per field atau satu perintah)");
        }

        if (g.CommandDelayMs is < 0 or > 10000)
        {
            errors.Add("Generic: jeda antar perintah 0-10000 ms");
        }

        var fieldValueKeys = settings.Printer.Fields.Select(f => FieldKey(f.FieldId));
        void Check(string label, string template, IEnumerable<string> extra)
        {
            if (string.IsNullOrEmpty(template))
            {
                return;
            }

            foreach (var error in OutputFormatter.Validate(template, [.. fieldKeys, .. extra]))
            {
                errors.Add($"Generic {label}: {error}");
            }
        }

        Check("change template", g.TemplateCommand, [TemplateNameKey]);
        Check("kosongkan antrean", g.ClearCommand, []);
        Check("start print", g.StartCommand, []);
        Check("stop print", g.StopCommand, []);
        Check("perintah per field", g.FieldCommand, [FieldIdKey, FieldNameKey, FieldValueKey]);
        Check("perintah data", g.DataCommand, fieldValueKeys);
        return errors;
    }

    public static string FieldKey(int id) => "FIELD_" + id.ToString(CultureInfo.InvariantCulture);

    private static KeyValuePair<string, string>[] TemplateValues(string templateName) => [new(TemplateNameKey, templateName)];

    private static IEnumerable<(string Name, string Text)> FieldCommands(
        PrinterSettings p,
        IReadOnlyList<FieldValue> fields,
        OutputContext context)
    {
        var g = p.Generic;
        if (!string.IsNullOrEmpty(g.FieldCommand))
        {
            foreach (var field in fields)
            {
                yield return ($"Field {field.Id}", Render(g.FieldCommand, context,
                [
                    new(FieldIdKey, field.Id.ToString(CultureInfo.InvariantCulture)),
                    new(FieldNameKey, field.Name),
                    new(FieldValueKey, field.Value),
                ]));
            }
        }

        if (!string.IsNullOrEmpty(g.DataCommand))
        {
            yield return ("Field data", Render(g.DataCommand, context,
                fields.Select(f => new KeyValuePair<string, string>(FieldKey(f.Id), f.Value))));
        }
    }

    private static string Render(string template, OutputContext context, IEnumerable<KeyValuePair<string, string>> extra) =>
        OutputFormatter.Render(template, OutputFormatter.WithValues(context, extra));

    private static async Task SendAsync(PrinterCall call, string name, string text)
    {
        var p = call.Printer;
        var g = p.Generic;
        if (call.Trace.Count > 0 && g.CommandDelayMs > 0)
        {
            await Task.Delay(g.CommandDelayMs, call.Cancellation).ConfigureAwait(false);
        }

        var encoding = TextEncodings.Resolve(p.Encoding);
        var ack = ControlTokens.Decode(g.AckPattern ?? "");
        var nak = ControlTokens.Decode(g.NakPattern ?? "");
        var received = new StringBuilder();
        Func<PrinterFrame, bool>? isReply = null;
        if (ack.Length > 0)
        {
            isReply = frame =>
            {
                if (frame.Kind != PrinterFrameKind.Text)
                {
                    return false;
                }

                received.Append(encoding.GetString(frame.Data));
                var reply = received.ToString();
                return reply.Contains(ack, StringComparison.Ordinal) || (nak.Length > 0 && reply.Contains(nak, StringComparison.Ordinal));
            };
        }

        var bytes = CommandEncoder.Encode(text, encoding);
        await call.Session.RequestAsync(bytes, isReply, call.ResponseTimeout, call.Cancellation).ConfigureAwait(false);
        call.Stats.AddSent(bytes.Length);
        var response = received.Length > 0 ? ControlTokens.Visualize(received.ToString()) : null;
        call.Trace.Add(new SentCommand(call.Trace.Count + 1, name, ControlTokens.Visualize(text), bytes.Length, response));
        if (nak.Length > 0 && received.ToString().Contains(nak, StringComparison.Ordinal))
        {
            throw new PrinterException($"{name} ditolak printer: {response}");
        }
    }
}
