using System.Diagnostics;
using System.Net.Sockets;
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
    /// <summary>Field data yang sudah dikirim ke printer dan menunggu dicetak.</summary>
    private sealed record PendingPrint(
        ParsedRecord Record,
        long Counter,
        string CounterText,
        string BusinessDate,
        int Width,
        long Epoch);

    private readonly LinkedList<PendingPrint> _pending = new();
    private readonly Queue<byte[]> _outbox = new();
    private PrinterSession? _printer;
    private string? _printerKey;
    private long _printerGeneration;
    private PrinterState _printerState;
    private string? _printerMessage;
    private string? _lastPrinterError;
    private DateTimeOffset _lastPrinterAttempt;
    private volatile bool _printerNeedsJob = true;
    private volatile bool _jobArmed;
    private volatile int _pendingCount;
    private volatile int _outboxCount;
    private volatile string? _nextToSendText;
    private volatile string? _lastPrintedText;
    private PendingPrint? _lastSent;
    private long _nextToSend;

    private bool PrinterConnected => _printer?.IsConnected == true;

    // ------------------------------------------------------------------ koneksi printer

    private async Task<PrinterSession> EnsurePrinterAsync(PrinterSettings p, CancellationToken ct)
    {
        var key = PrinterLinks.ConnectionKey(p);
        if (_printer is { IsConnected: true } current && _printerKey == key)
        {
            return current;
        }

        if (_printer is not null)
        {
            await ClosePrinterAsync(PrinterState.Connecting, null).ConfigureAwait(false);
        }

        var writer = _work?.Writer ?? throw new OperationCanceledException();
        _lastPrinterAttempt = _time.GetUtcNow();
        SetPrinterState(PrinterState.Connecting, null);
        var generation = Interlocked.Increment(ref _printerGeneration);
        var driver = PrinterDrivers.For(p);
        var parser = driver.CreateParser(p);

        PrinterSession session;
        try
        {
            session = await PrinterSession.OpenAsync(
                _printerFactory(p),
                parser,
                frame => writer.TryWrite(new PrinterFrameItem(generation, frame)),
                null,
                ct).ConfigureAwait(false);
        }
        catch (Exception ex) when (IsTransportError(ex))
        {
            SetPrinterState(PrinterState.Error, ex.Message);
            throw new PrinterException($"Tidak bisa konek ke printer {PrinterLinks.Describe(p)}: {ex.Message}", ex);
        }

        _ = session.Completion.ContinueWith(
            _ => writer.TryWrite(new PrinterClosedItem(generation)),
            CancellationToken.None,
            TaskContinuationOptions.ExecuteSynchronously,
            TaskScheduler.Default);

        _printer = session;
        _printerKey = key;
        _printerNeedsJob = true;
        _pending.Clear();
        _pendingCount = 0;
        _lastPrinterError = null;
        SetPrinterState(PrinterState.Connected, null);
        _log.Write(LogStatus.Ok, $"Connected to printer {session.Endpoint} ({driver.Name})");
        return session;
    }

    private async Task ClosePrinterAsync(PrinterState state, string? message)
    {
        var session = _printer;
        _printer = null;
        _printerKey = null;
        Interlocked.Increment(ref _printerGeneration);
        _printerNeedsJob = true;
        _jobArmed = false;
        if (session is not null)
        {
            await session.DisposeAsync().ConfigureAwait(false);
        }

        SetPrinterState(state, message);
    }

    private void SetPrinterState(PrinterState state, string? message)
    {
        _printerState = state;
        _printerMessage = message;
        RaiseStatus();
    }

    private async Task EnsurePrinterAndResyncAsync(CancellationToken ct)
    {
        var s = _settings;
        if (!_running || !s.Printer.Enabled)
        {
            return;
        }

        var wasConnected = PrinterConnected;
        try
        {
            await EnsurePrinterAsync(s.Printer, ct).ConfigureAwait(false);
        }
        catch (PrinterException ex)
        {
            if (_lastPrinterError != ex.Message)
            {
                _log.Write(LogStatus.Warn, $"{ex.Message} (dicoba ulang tiap {s.Printer.ReconnectDelaySeconds} detik)");
                _lastPrinterError = ex.Message;
            }

            return;
        }

        if (!wasConnected && _job is not null && UsesPrintCounting(s))
        {
            await ResyncAsync("printer terhubung", ct).ConfigureAwait(false);
        }
    }

    private async Task OnPrinterClosedAsync(PrinterClosedItem item)
    {
        if (item.Generation != Interlocked.Read(ref _printerGeneration) || _printer is null)
        {
            return;
        }

        var endpoint = _printer.Endpoint;
        await ClosePrinterAsync(PrinterState.Error, "Koneksi printer terputus").ConfigureAwait(false);
        _log.Write(LogStatus.Warn, $"Koneksi printer {endpoint} terputus");
    }

    private async Task OnPrinterFrameAsync(PrinterFrameItem item, CancellationToken ct)
    {
        if (item.Generation != Interlocked.Read(ref _printerGeneration))
        {
            return;
        }

        switch (item.Frame.Kind)
        {
            case PrinterFrameKind.PrintEnd:
                await OnPrintEndAsync(ct).ConfigureAwait(false);
                break;
            case PrinterFrameKind.PrintStart:
                break;
            case PrinterFrameKind.Text:
                _log.Write(LogStatus.Info, $"Printer: {item.Frame.Describe()}");
                break;
            default:
                _log.Write(LogStatus.Warn, $"Balasan printer tidak terduga: {item.Frame.Describe()}");
                break;
        }
    }

    // ------------------------------------------------------------------ job (semua protokol)

    private PrinterCall Call(PrinterSession session, AppSettings s, List<SentCommand> trace, CancellationToken ct) =>
        new(session, s, _stats, _log, trace, ct);

    /// <summary>Digit counter untuk record ini (Settings, panjang counter server, atau yang tersimpan).</summary>
    private int CounterWidth(AppSettings s, ParsedRecord? record)
    {
        var width = CounterEngine.WidthFor(s.Counter, record?.ServerCounter);
        return width > 0 || record is null ? width : _counter.StoredWidth(record.Template);
    }

    private OutputContext ContextFor(AppSettings s, ParsedRecord record, long counter)
    {
        var now = _time.GetLocalNow();
        return new OutputContext(
            record,
            counter,
            CounterEngine.Format(counter, CounterWidth(s, record)),
            CounterEngine.BusinessDateFor(now, s.Counter.DayStartTime),
            now,
            Interlocked.Read(ref _sequence));
    }

    /// <summary>Change template (jika perlu) lalu kosongkan antrean data printer (jika perlu).</summary>
    private async Task PrepareJobAsync(
        PrinterSession session,
        AppSettings s,
        ParsedRecord record,
        bool changeTemplate,
        bool clear,
        List<SentCommand> trace,
        CancellationToken ct)
    {
        var driver = PrinterDrivers.For(s.Printer);
        var context = ContextFor(s, record, 0);
        if (changeTemplate)
        {
            var name = OutputFormatter.Render(s.Printer.TemplateName, context);
            await driver.ChangeTemplateAsync(Call(session, s, trace, ct), name, context).ConfigureAwait(false);
            _log.Write(LogStatus.Ok, $"Change template ke {name} ({driver.Name})");
        }

        if (clear)
        {
            await driver.ClearAsync(Call(session, s, trace, ct), context).ConfigureAwait(false);
        }
    }

    /// <summary>Kirim field data (operator, counter, ...) untuk satu nilai counter.</summary>
    private async Task<PendingPrint> SendFieldsAsync(
        PrinterSession session,
        AppSettings s,
        ParsedRecord record,
        long counter,
        List<SentCommand> trace,
        CancellationToken ct)
    {
        var context = ContextFor(s, record, counter);
        var fields = PrinterDrivers.RenderFields(s.Printer, context);
        await PrinterDrivers.For(s.Printer).SendFieldsAsync(Call(session, s, trace, ct), fields, context).ConfigureAwait(false);
        return new PendingPrint(record, counter, context.CounterText, context.BusinessDate, CounterWidth(s, record), _counter.Epoch);
    }

    /// <summary>
    /// Mode counter per cetak: siapkan job baru di printer. Mengembalikan false jika job sama dengan yang aktif.
    /// </summary>
    private async Task<bool> StartJobAsync(
        AppSettings s,
        ParsedRecord record,
        CounterDecision decision,
        List<SentCommand> trace,
        CancellationToken ct)
    {
        var same = _job is not null && _jobArmed && !_printerNeedsJob && PrinterConnected && _pending.Count > 0
            && !decision.TemplateChanged && !decision.OperatorChanged && !decision.DayRolled;
        if (same)
        {
            return false;
        }

        var firstJob = _job is null;
        if (!_counter.CommitJob(decision))
        {
            decision = _counter.Evaluate(record, s.Counter);
            _counter.CommitJob(decision);
        }

        _job = new ActiveJob(record);
        _jobArmed = false;
        var session = await EnsurePrinterAsync(s.Printer, ct).ConfigureAwait(false);
        var changeTemplate = decision.TemplateChanged || _printerNeedsJob || firstJob;
        await PrepareJobAsync(session, s, record, changeTemplate, s.Printer.ClearOnJobChange, trace, ct).ConfigureAwait(false);
        ResetQueue(_counter.NextValue(record.Template, s.Counter) ?? decision.Value);
        await TopUpAsync(session, s, trace, ct).ConfigureAwait(false);
        _jobArmed = true;
        _printerNeedsJob = false;
        return true;
    }

    /// <summary>Mode counter per data: setiap record dikirim ke printer dengan counter berikutnya.</summary>
    private async Task SendRecordAsync(
        AppSettings s,
        ParsedRecord record,
        CounterDecision decision,
        List<SentCommand> trace,
        CancellationToken ct)
    {
        var session = await EnsurePrinterAsync(s.Printer, ct).ConfigureAwait(false);
        var changeTemplate = decision.TemplateChanged || _printerNeedsJob || _job is null;
        await PrepareJobAsync(session, s, record, changeTemplate, false, trace, ct).ConfigureAwait(false);
        var pending = await SendFieldsAsync(session, s, record, decision.Value, trace, ct).ConfigureAwait(false);
        if (!_counter.Commit(decision))
        {
            _log.Write(LogStatus.Warn, $"Counter direset saat pengiriman; nilai {decision.Text} tidak disimpan");
        }

        AddPending(pending);
        _job = new ActiveJob(record);
        _jobArmed = true;
        _printerNeedsJob = false;
    }

    private void ResetQueue(long nextValue)
    {
        _pending.Clear();
        _pendingCount = 0;
        _nextToSend = nextValue;
        _nextToSendText = CounterEngine.Format(nextValue, CounterWidth(_settings, _job?.Record));
    }

    private void AddPending(PendingPrint pending)
    {
        _pending.AddLast(pending);
        while (_pending.Count > 100)
        {
            _pending.RemoveFirst();
        }

        _lastSent = pending;
        _pendingCount = _pending.Count;
        _nextToSendText = _pending.First?.Value.CounterText;
    }

    /// <summary>Isi antrean printer sampai <see cref="PrinterSettings.Lookahead"/> data menunggu cetak.</summary>
    private async Task TopUpAsync(PrinterSession session, AppSettings s, List<SentCommand> trace, CancellationToken ct)
    {
        var job = _job ?? throw new InvalidOperationException("Belum ada job aktif");
        var lookahead = Math.Clamp(s.Printer.Lookahead, 1, 10);
        while (_pending.Count < lookahead)
        {
            var value = _nextToSend;
            var pending = await SendFieldsAsync(session, s, job.Record, value, trace, ct).ConfigureAwait(false);
            AddPending(pending);
            _nextToSend = CounterEngine.Next(value, false, s.Counter);
        }
    }

    /// <summary>Kirim ulang job aktif ke printer (setelah reset counter, ganti hari, printer konek ulang, dll.).</summary>
    private async Task ResyncAsync(string reason, CancellationToken ct)
    {
        var s = _settings;
        if (!_running || _job is null || !UsesPrintCounting(s))
        {
            return;
        }

        var job = _job;
        try
        {
            var session = await EnsurePrinterAsync(s.Printer, ct).ConfigureAwait(false);
            var decision = _counter.Evaluate(job.Record, s.Counter);
            if (!_counter.CommitJob(decision))
            {
                decision = _counter.Evaluate(job.Record, s.Counter);
                _counter.CommitJob(decision);
            }

            var trace = new List<SentCommand>();
            _jobArmed = false;
            await PrepareJobAsync(session, s, job.Record, _printerNeedsJob, true, trace, ct).ConfigureAwait(false);
            ResetQueue(_counter.NextValue(job.Record.Template, s.Counter) ?? decision.Value);
            await TopUpAsync(session, s, trace, ct).ConfigureAwait(false);
            _jobArmed = true;
            _printerNeedsJob = false;
            _lastPrinterError = null;
            _log.Write(LogStatus.Info,
                $"Printer disinkronkan ({reason}): {job.Record.Template} / {job.Record.Operator}, counter berikutnya {_nextToSendText}");
        }
        catch (Exception ex) when (ex is PrinterException || IsTransportError(ex))
        {
            _jobArmed = false;
            if (ex is not PrinterException)
            {
                await ClosePrinterAsync(PrinterState.Error, ex.Message).ConfigureAwait(false);
            }

            if (_lastPrinterError != ex.Message)
            {
                _log.Write(LogStatus.Warn, $"Job belum terkirim ke printer ({reason}): {ex.Message}. Dicoba ulang otomatis.");
                _lastPrinterError = ex.Message;
            }
        }

        RaiseStatus();
    }

    // ------------------------------------------------------------------ after print → server

    private async Task OnPrintEndAsync(CancellationToken ct)
    {
        var s = _settings;
        PendingPrint printed;
        var repeat = false;
        if (_pending.First is { } first)
        {
            printed = first.Value;
            _pending.RemoveFirst();
        }
        else if (_lastSent is not null)
        {
            printed = _lastSent;
            repeat = true;
        }
        else
        {
            _log.Write(LogStatus.Warn, "Printer selesai cetak, tetapi belum ada data yang dikirim software");
            return;
        }

        _pendingCount = _pending.Count;
        _nextToSendText = _pending.First?.Value.CounterText ?? (UsesPrintCounting(s) ? CounterEngine.Format(_nextToSend, CounterWidth(s, _job?.Record)) : null);
        if (UsesPrintCounting(s) && !repeat
            && !_counter.CommitPrint(printed.Record.Template, printed.Record.Operator, printed.Counter, printed.Width, printed.Epoch))
        {
            _log.Write(LogStatus.Info, $"Counter sudah direset; cetakan {printed.CounterText} tidak menambah counter");
        }

        _stats.RecordPrinted();
        var now = _time.GetLocalNow();
        var seq = Interlocked.Increment(ref _sequence);
        _lastPrintedText = $"{printed.CounterText} @ {now:HH:mm:ss}";

        var feedback = "";
        string? note = null;
        var bytes = 0;
        if (s.Feedback.Enabled)
        {
            var context = new OutputContext(printed.Record, printed.Counter, printed.CounterText, printed.BusinessDate, now, seq);
            feedback = OutputFormatter.Render(s.Feedback.Template, context);
            var payload = CommandEncoder.Encode(feedback, TextEncodings.Resolve(s.Server.Encoding));
            bytes = payload.Length;
            note = await SendFeedbackAsync(payload, ct).ConfigureAwait(false);
        }

        var summary = $"{printed.Record.Operator} / {printed.Record.Template} / {printed.CounterText}";
        if (repeat)
        {
            _log.Write(LogStatus.Warn, $"Printer mencetak ulang data terakhir (buffer 1D kosong): {summary}");
        }
        else
        {
            _log.Write(LogStatus.Ok, $"Printed: {summary}" + (s.Feedback.Enabled ? $" -> server: {Trimmed(feedback)}" : ""));
        }

        if (note is not null)
        {
            _log.Write(LogStatus.Warn, note);
        }

        Publish(new TransferRecord
        {
            Seq = seq,
            Time = now,
            Source = "Printer",
            Data = s.Feedback.Enabled ? Trimmed(feedback) : summary,
            Bytes = bytes,
            Operator = printed.Record.Operator,
            Template = printed.Record.Template,
            Counter = printed.CounterText,
            CounterNote = repeat ? "Cetak ulang data terakhir (buffer kosong)" : "Selesai cetak",
            Commands = s.Feedback.Enabled ? [new SentCommand(1, "Feedback ke server", Trimmed(feedback), bytes, null)] : [],
            Status = RecordStatus.Printed,
            Message = note,
        });

        if (UsesPrintCounting(s) && _job is not null && _jobArmed && _printer is { IsConnected: true } session)
        {
            try
            {
                await TopUpAsync(session, s, [], ct).ConfigureAwait(false);
            }
            catch (Exception ex) when (ex is PrinterException || IsTransportError(ex))
            {
                _jobArmed = false;
                _log.Write(LogStatus.Error, $"Gagal kirim counter berikutnya ke printer: {ex.Message}");
            }
        }

        RaiseStatus();
    }

    private async Task<string?> SendFeedbackAsync(byte[] payload, CancellationToken ct)
    {
        if (_outbox.Count > 0)
        {
            await FlushFeedbackAsync(ct).ConfigureAwait(false);
        }

        if (_outbox.Count == 0 && await TryWriteServerAsync(payload, ct).ConfigureAwait(false))
        {
            return null;
        }

        var max = Math.Max(1, _settings.Feedback.MaxQueued);
        while (_outbox.Count >= max)
        {
            _outbox.Dequeue();
        }

        _outbox.Enqueue(payload);
        _outboxCount = _outbox.Count;
        return $"Server tidak terhubung: laporan diantrikan ({_outbox.Count})";
    }

    private async Task FlushFeedbackAsync(CancellationToken ct)
    {
        var sent = 0;
        while (_outbox.TryPeek(out var payload))
        {
            if (!await TryWriteServerAsync(payload, ct).ConfigureAwait(false))
            {
                break;
            }

            _outbox.Dequeue();
            sent++;
        }

        _outboxCount = _outbox.Count;
        if (sent > 0)
        {
            _log.Write(LogStatus.Ok, $"{sent} laporan tertunda terkirim ke server");
            RaiseStatus();
        }
    }

    private async Task<bool> TryWriteServerAsync(byte[] payload, CancellationToken ct)
    {
        var stream = _serverStream;
        if (stream is null)
        {
            return false;
        }

        await _serverWriteGate.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            await stream.WriteAsync(payload, ct).ConfigureAwait(false);
            _stats.AddSent(payload.Length);
            return true;
        }
        catch (Exception ex) when (ex is IOException or ObjectDisposedException or SocketException)
        {
            _log.Write(LogStatus.Warn, $"Gagal kirim laporan ke server: {ex.Message}");
            return false;
        }
        finally
        {
            _serverWriteGate.Release();
        }
    }

    // ------------------------------------------------------------------ test

    public async Task<ConnectionTestResult> TestPrinterAsync(PrinterSettings printer, CancellationToken ct)
    {
        var watch = Stopwatch.StartNew();
        var driver = PrinterDrivers.For(printer);
        var active = _printer;
        var useActive = active is { IsConnected: true } && _printerKey == PrinterLinks.ConnectionKey(printer);
        PrinterSession? temporary = null;
        try
        {
            var session = useActive ? active! : null;
            if (session is null)
            {
                temporary = await PrinterSession.OpenAsync(_printerFactory(printer), driver.CreateParser(printer), _ => { }, null, ct)
                    .ConfigureAwait(false);
                session = temporary;
            }

            var detail = await driver.InspectAsync(session, printer, ct).ConfigureAwait(false);
            var message = $"Printer {session.Endpoint} terhubung ({driver.Name})" + (detail is null ? "" : $". {detail}");
            return new ConnectionTestResult(true, message, watch.ElapsedMilliseconds);
        }
        catch (Exception ex) when (IsTransportError(ex) || ex is PrinterException)
        {
            return new ConnectionTestResult(false, $"Gagal ke printer {PrinterLinks.Describe(printer)}: {ex.Message}", watch.ElapsedMilliseconds);
        }
        finally
        {
            if (temporary is not null)
            {
                await temporary.DisposeAsync().ConfigureAwait(false);
            }
        }
    }
}
