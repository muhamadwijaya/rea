using System.Threading.Channels;
using Sharx.Core.Configuration;
using Sharx.Core.Counter;
using Sharx.Core.Logging;
using Sharx.Core.Mapping;
using Sharx.Core.Printing;
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

    private sealed record ReadyCheckItem : WorkItem;

    private sealed record PrintControlItem(bool Start, TaskCompletionSource<ConnectionTestResult> Result) : WorkItem;

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
        ServerFrameItem f => ProcessServerFrameAsync(f.Frame, replay: false, ct),
        ServerConnectedItem => FlushFeedbackAsync(ct),
        PrinterFrameItem p => OnPrinterFrameAsync(p, ct),
        PrinterClosedItem c => OnPrinterClosedAsync(c),
        EnsurePrinterItem => EnsurePrinterAndResyncAsync(ct),
        ResyncItem r => ResyncAsync(r.Reason, ct),
        SettingsChangedItem s => OnSettingsChangedAsync(s, ct),
        ReadyCheckItem => CheckPrinterReadyAsync(ct),
        PrintControlItem p => OnPrintControlAsync(p, ct),
        _ => Task.CompletedTask,
    };

    private static bool UsesPrintCounting(AppSettings s) =>
        s.Printer.Enabled && s.Counter.IncrementOn == CounterIncrement.Print;

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

    /// <summary>
    /// Proses satu record dari server. <paramref name="replay"/> = record yang sebelumnya menunggu printer start.
    /// Mengembalikan false jika record masih harus menunggu printer start.
    /// </summary>
    private async Task<bool> ProcessServerFrameAsync(Frame frame, bool replay, CancellationToken ct)
    {
        var settings = _settings;
        var text = TextEncodings.Resolve(settings.Server.Encoding).GetString(frame.Data);
        var seq = Interlocked.Increment(ref _sequence);
        var now = _time.GetLocalNow();
        if (!replay)
        {
            _stats.RecordReceived();
            _log.Write(LogStatus.Ok, $"Data received: {frame.Data.Length} bytes");
        }

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
            return true;
        }

        var decision = _counter.Evaluate(parsed.Record, settings.Counter);
        if (!settings.Printer.Enabled)
        {
            ProcessMonitorOnly(settings, parsed.Record, decision, record);
            return true;
        }

        var trace = new List<SentCommand>();
        try
        {
            if (!replay && !UsesPrintCounting(settings) && _waitingFrames.Count > 0)
            {
                // Urutan data dijaga: data lama yang menunggu printer start dikirim dulu.
                throw new PrinterNotReadyException("Masih ada data yang menunggu printer start");
            }

            if (settings.Counter.IncrementOn == CounterIncrement.Print)
            {
                var changed = await StartJobAsync(settings, parsed.Record, decision, trace, ct).ConfigureAwait(false);
                if (!changed)
                {
                    _log.Write(LogStatus.Info, $"Job sama ({decision.Template} / {decision.Operator}), printer tidak diubah");
                    Publish(record with
                    {
                        Status = RecordStatus.Ok,
                        Counter = _nextToSendText,
                        CounterNote = "Job sama dengan yang aktif; tidak ada perintah dikirim",
                    });
                    return true;
                }
            }
            else
            {
                await SendRecordAsync(settings, parsed.Record, decision, trace, ct).ConfigureAwait(false);
            }
        }
        catch (PrinterNotReadyException ex)
        {
            MarkPrintNotStarted();
            if (replay)
            {
                return false;
            }

            if (!UsesPrintCounting(settings))
            {
                DeferFrame(frame);
            }

            Publish(record with
            {
                Status = RecordStatus.Waiting,
                Counter = decision.Text,
                CounterNote = "Printer belum start: data disimpan dan dikirim otomatis saat printer start",
                Commands = trace,
                Message = ex.Message,
            });
            RaiseStatus();
            return false;
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
            return true;
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
        return true;
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
        var context = new OutputContext(
            parsed.Record,
            decision.Value,
            decision.Text,
            decision.BusinessDate,
            _time.GetLocalNow(),
            Interlocked.Read(ref _sequence) + 1);

        var driver = PrinterDrivers.For(draft.Printer);
        var fields = PrinterDrivers.RenderFields(draft.Printer, context);
        var commands = new List<object>();
        try
        {
            var templateName = OutputFormatter.Render(draft.Printer.TemplateName, context);
            foreach (var c in driver.Preview(draft.Printer, templateName, fields, context))
            {
                commands.Add(new { c.Name, Send = true, c.Text, Hex = Convert.ToHexString(c.Bytes) });
            }
        }
        catch (ArgumentException ex)
        {
            settingErrors = [.. settingErrors, ex.Message];
        }

        var remoteFields = fields.Select(f => new { f.Id, Value = ControlTokens.Visualize(f.Value) }).ToArray();
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
                Hex = Convert.ToHexString(CommandEncoder.Encode(feedback, TextEncodings.Resolve(draft.Server.Encoding))),
            },
        };
    }
}
