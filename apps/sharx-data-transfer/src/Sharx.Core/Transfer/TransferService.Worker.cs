using System.Threading.Channels;
using Sharx.Core.Configuration;
using Sharx.Core.Counter;
using Sharx.Core.Logging;
using Sharx.Core.Mapping;
using Sharx.Core.Text;
using Sharx.Core.Transport;

namespace Sharx.Core.Transfer;

public sealed partial class TransferService
{
    private abstract record WorkItem;

    private sealed record ServerFrameItem(Frame Frame) : WorkItem;

    private sealed record ServerConnectedItem : WorkItem;

    private sealed record PrinterFrameItem(long Generation, PrinterFrame Frame) : WorkItem;

    private sealed record PrinterClosedItem(long Generation) : WorkItem;

    private sealed record EnsurePrinterItem : WorkItem;

    private sealed record ResyncItem(string Reason) : WorkItem;

    private sealed record SettingsChangedItem(AppSettings Previous, AppSettings Next) : WorkItem;

    /// <summary>Job aktif: record terakhir dari server yang menentukan template &amp; operator.</summary>
    private sealed record ActiveJob(ParsedRecord Record);

    private ActiveJob? _job;

    private async Task WorkerAsync(ChannelReader<WorkItem> reader, CancellationToken ct)
    {
        await foreach (var item in reader.ReadAllAsync(ct).ConfigureAwait(false))
        {
            try
            {
                await HandleAsync(item, ct).ConfigureAwait(false);
            }
            catch (OperationCanceledException) when (ct.IsCancellationRequested)
            {
                return;
            }
            catch (Exception ex)
            {
                _log.Write(LogStatus.Error, $"Gagal memproses: {ex.Message}");
            }
        }
    }

    private Task HandleAsync(WorkItem item, CancellationToken ct) => item switch
    {
        ServerFrameItem f => ProcessServerFrameAsync(f.Frame, ct),
        ServerConnectedItem => FlushFeedbackAsync(ct),
        PrinterFrameItem p => OnPrinterFrameAsync(p, ct),
        PrinterClosedItem c => OnPrinterClosedAsync(c),
        EnsurePrinterItem => EnsurePrinterAndResyncAsync(ct),
        ResyncItem r => ResyncAsync(r.Reason, ct),
        SettingsChangedItem s => OnSettingsChangedAsync(s, ct),
        _ => Task.CompletedTask,
    };

    private static bool UsesPrintCounting(AppSettings s) =>
        s.Printer.Enabled && s.Printer.Protocol == PrinterProtocol.Tij && s.Counter.IncrementOn == CounterIncrement.Print;

    /// <summary>Setelah aplikasi dibuka ulang: lanjutkan job terakhir hari ini (mode counter per cetak).</summary>
    private void RestoreJobFromCounter()
    {
        _job = null;
        _jobArmed = false;
        var state = _counter.Snapshot();
        var today = CounterEngine.BusinessDateFor(_time.GetLocalNow(), _settings.Counter.DayStartTime);
        if (!UsesPrintCounting(_settings) || state.ActiveTemplate is null || state.BusinessDate != today)
        {
            return;
        }

        var values = new Dictionary<string, string>(StringComparer.Ordinal)
        {
            [FieldKeys.Template] = state.ActiveTemplate,
            [FieldKeys.Operator] = state.ActiveOperator ?? "",
        };
        _job = new ActiveJob(new ParsedRecord("", [], values));
        _log.Write(LogStatus.Info, $"Melanjutkan job terakhir: template {state.ActiveTemplate}, operator {state.ActiveOperator}");
    }

    private async Task ProcessServerFrameAsync(Frame frame, CancellationToken ct)
    {
        var settings = _settings;
        var text = TextEncodings.Resolve(settings.Server.Encoding).GetString(frame.Data);
        var seq = Interlocked.Increment(ref _sequence);
        var now = _time.GetLocalNow();
        _stats.RecordReceived();
        _log.Write(LogStatus.Ok, $"Data received: {frame.Data.Length} bytes");

        var parsed = RecordParser.Parse(text, settings.Mapping);
        var record = new TransferRecord
        {
            Seq = seq,
            Time = now,
            Data = ControlTokens.Visualize(text),
            Bytes = frame.Data.Length,
            Operator = parsed.Record.Operator,
            Template = parsed.Record.Template,
            ServerCounter = parsed.Record.ServerCounter,
            Fields = parsed.Mapped,
        };

        var errors = parsed.Errors.ToList();
        if (frame.Truncated)
        {
            errors.Insert(0, $"Data melebihi {settings.Server.MaxFrameBytes} byte tanpa terminator");
        }

        if (errors.Count > 0)
        {
            var message = string.Join("; ", errors);
            _stats.RecordFailed();
            _log.Write(LogStatus.Error, $"Data ditolak: {message}");
            Publish(record with { Status = RecordStatus.Rejected, Message = message });
            return;
        }

        var decision = _counter.Evaluate(parsed.Record, settings.Counter);
        if (!settings.Printer.Enabled)
        {
            ProcessMonitorOnly(settings, parsed.Record, decision, record);
            return;
        }

        var trace = new List<SentCommand>();
        try
        {
            if (settings.Printer.Protocol == PrinterProtocol.CustomText)
            {
                await SendCustomTextAsync(settings, parsed.Record, decision, seq, trace, ct).ConfigureAwait(false);
            }
            else if (settings.Counter.IncrementOn == CounterIncrement.Print)
            {
                var changed = await StartTijJobAsync(settings, parsed.Record, decision, trace, ct).ConfigureAwait(false);
                if (!changed)
                {
                    _log.Write(LogStatus.Info, $"Job sama ({decision.Template} / {decision.Operator}), printer tidak diubah");
                    Publish(record with
                    {
                        Status = RecordStatus.Ok,
                        Counter = _nextToSendText,
                        CounterNote = "Job sama dengan yang aktif; tidak ada perintah dikirim",
                    });
                    return;
                }
            }
            else
            {
                await SendTijRecordAsync(settings, parsed.Record, decision, trace, ct).ConfigureAwait(false);
            }
        }
        catch (Exception ex) when (ex is PrinterException || IsTransportError(ex))
        {
            if (ex is not PrinterException)
            {
                await ClosePrinterAsync(PrinterState.Error, ex.Message).ConfigureAwait(false);
            }

            _stats.RecordFailed();
            _log.Write(LogStatus.Error, $"Gagal kirim ke printer: {ex.Message}");
            Publish(record with
            {
                Status = RecordStatus.PrinterError,
                Counter = decision.Text,
                CounterNote = UsesPrintCounting(settings)
                    ? "Job disimpan; akan dikirim ulang otomatis saat printer siap"
                    : "Counter tidak berubah karena data gagal dikirim",
                Commands = trace,
                Message = ex.Message,
            });
            RaiseStatus();
            return;
        }

        _stats.RecordForwarded();
        LogCounterNotes(decision);
        _log.Write(LogStatus.Ok, $"Sent to printer: {Summarize(trace)}");
        Publish(record with
        {
            Status = RecordStatus.Ok,
            Counter = decision.Text,
            CounterNote = decision.Explanation,
            Commands = trace,
        });
        RaiseStatus();
    }

    private void ProcessMonitorOnly(AppSettings settings, ParsedRecord parsed, CounterDecision decision, TransferRecord record)
    {
        var committed = settings.Counter.IncrementOn == CounterIncrement.Print
            ? _counter.CommitJob(decision)
            : _counter.Commit(decision);
        if (committed)
        {
            LogCounterNotes(decision);
        }

        _job = new ActiveJob(parsed);
        _log.Write(LogStatus.Info, $"Printer nonaktif (monitor): {decision.Template} / {decision.Operator} / {decision.Text}");
        Publish(record with
        {
            Status = RecordStatus.Monitor,
            Counter = decision.Text,
            CounterNote = decision.Explanation,
        });
    }

    private void LogCounterNotes(CounterDecision decision)
    {
        if (decision.DayRolled)
        {
            _log.Write(LogStatus.Info, $"Ganti hari ({decision.BusinessDate}): semua counter direset");
        }

        if (decision.TemplateChanged && decision.PreviousTemplate is not null)
        {
            _log.Write(LogStatus.Info, $"Template berubah {decision.PreviousTemplate} -> {decision.Template}: counter {decision.Text}");
        }
        else if (decision.OperatorChanged && decision.PreviousOperator is not null)
        {
            _log.Write(LogStatus.Info, $"Operator berubah {decision.PreviousOperator} -> {decision.Operator}: counter lanjut {decision.Text}");
        }
    }

    private static string Summarize(IReadOnlyList<SentCommand> sent)
    {
        if (sent.Count == 0)
        {
            return "(tidak ada perintah dikirim)";
        }

        var text = string.Join(" | ", sent.Select(s => s.Data.Length > 0 ? $"{s.Name}: {s.Data}" : s.Name));
        return text.Length > 300 ? text[..300] + "..." : text;
    }

    private async Task OnSettingsChangedAsync(SettingsChangedItem change, CancellationToken ct)
    {
        var keyChanged = PrinterLinks.ConnectionKey(change.Previous.Printer) != PrinterLinks.ConnectionKey(change.Next.Printer)
            || change.Previous.Printer.Enabled != change.Next.Printer.Enabled;
        if (keyChanged)
        {
            await ClosePrinterAsync(change.Next.Printer.Enabled ? PrinterState.Idle : PrinterState.Disabled, null)
                .ConfigureAwait(false);
            _pending.Clear();
            _pendingCount = 0;
        }

        if (!change.Next.Printer.Enabled)
        {
            return;
        }

        if (keyChanged || !PrinterConnected)
        {
            await EnsurePrinterAndResyncAsync(ct).ConfigureAwait(false);
        }
        else
        {
            await ResyncAsync("settings berubah", ct).ConfigureAwait(false);
        }
    }

    /// <summary>Preview Flow Mapping: tidak mengubah counter dan tidak mengirim apa pun.</summary>
    public object Preview(AppSettings draft, string sample)
    {
        SettingsStore.Normalize(draft);
        var parsed = RecordParser.Parse(ControlTokens.Decode(sample ?? ""), draft.Mapping);
        var settingErrors = SettingsValidator.Validate(SettingsStore.Clone(draft));
        var decision = _counter.Evaluate(parsed.Record, draft.Counter);
        var now = _time.GetLocalNow();
        var encoding = TextEncodings.Resolve(draft.Printer.Encoding);
        var context = new OutputContext(
            parsed.Record,
            decision.Value,
            decision.Text,
            decision.BusinessDate,
            now,
            Interlocked.Read(ref _sequence) + 1,
            decision.TemplateChanged || _printerNeedsJob,
            decision.OperatorChanged || _printerNeedsJob);

        var commands = new List<object>();
        if (draft.Printer.Protocol == PrinterProtocol.Tij)
        {
            try
            {
                foreach (var (name, frame, note) in TijPreviewFrames(draft, context, encoding))
                {
                    commands.Add(new { Name = name, Send = true, Text = note, Hex = Convert.ToHexString(frame) });
                }
            }
            catch (ArgumentException ex)
            {
                settingErrors = [.. settingErrors, ex.Message];
            }
        }
        else
        {
            foreach (var c in OutputFormatter.Build(draft.Printer.Steps, context))
            {
                commands.Add(new
                {
                    Name = $"Langkah {c.Step}: {c.Name}",
                    c.Send,
                    Text = c.Display,
                    Hex = Convert.ToHexString(encoding.GetBytes(c.Text)),
                });
            }
        }

        var remoteFields = draft.Printer.Protocol == PrinterProtocol.Tij
            ? RenderFields(draft.Printer, context).Select(f => new { f.Id, Value = ControlTokens.Visualize(f.Value) }).ToArray()
            : [];
        var feedback = OutputFormatter.Render(draft.Feedback.Template, context);
        return new
        {
            Fields = parsed.Record.Fields,
            parsed.Mapped,
            ParseErrors = parsed.Errors,
            SettingErrors = settingErrors,
            Counter = new
            {
                decision.Value,
                decision.Text,
                decision.Explanation,
                decision.TemplateChanged,
                decision.OperatorChanged,
                decision.DayRolled,
            },
            Commands = commands,
            RemoteFields = remoteFields,
            Feedback = new
            {
                draft.Feedback.Enabled,
                Text = ControlTokens.Visualize(feedback),
                Hex = Convert.ToHexString(TextEncodings.Resolve(draft.Server.Encoding).GetBytes(feedback)),
            },
        };
    }

    private static IEnumerable<(string Name, byte[] Frame, string Note)> TijPreviewFrames(
        AppSettings s,
        OutputContext context,
        System.Text.Encoding encoding)
    {
        var tij = s.Printer.Tij;
        var slave = SlaveAddress(s.Printer);
        var messageName = OutputFormatter.Render(tij.MessageName, context);
        yield return ("Load Print Message (1E)", TijProtocol.LoadMessage(slave, messageName, encoding), $"message = {messageName}");
        if (tij.UpdateAfterLoad)
        {
            yield return ("Update Print Contents (01)", TijProtocol.Command(slave, TijProtocol.CmdUpdatePrint), "");
        }

        if (tij.StartPrintAfterLoad)
        {
            yield return ("Start Print (11)", TijProtocol.Command(slave, TijProtocol.CmdStartPrint), "");
        }

        if (tij.ClearBufferOnJobChange)
        {
            yield return ("Clear Buffers of 1D (21)", TijProtocol.Command(slave, TijProtocol.CmdClearBuffer), "");
        }

        var fields = RenderFields(s.Printer, context);
        yield return ("Download Remote Field Data (1D)", TijProtocol.RemoteFields(slave, fields, encoding), DescribeFields(fields));
    }
}
