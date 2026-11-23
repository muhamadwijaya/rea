using System.Diagnostics;
using System.Net.Sockets;
using System.Text;
using Sharx.Core.Configuration;
using Sharx.Core.Counter;
using Sharx.Core.Logging;
using Sharx.Core.Mapping;
using Sharx.Core.Text;
using Sharx.Core.Transport;

namespace Sharx.Core.Transfer;

public sealed partial class TransferService
{
    /// <summary>Data yang sudah dikirim ke printer (1D) dan menunggu dicetak.</summary>
    private sealed record PendingPrint(
        ParsedRecord Record,
        long Counter,
        string CounterText,
        string BusinessDate,
        long Epoch,
        string Fields);

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

    public static byte SlaveAddress(PrinterSettings p) =>
        p.Tij.SlaveAddress is >= 0 and <= 255
            ? (byte)p.Tij.SlaveAddress
            : p.Transport == PrinterTransport.Tcp ? TijProtocol.SlaveFromHost(p.Host) : (byte)0;

    private static (byte Id, string Value)[] RenderFields(PrinterSettings p, OutputContext context) =>
        p.Tij.Fields.Select(f => ((byte)Math.Clamp(f.FieldId, 0, 255), OutputFormatter.Render(f.Value, context))).ToArray();

    private static string DescribeFields(IEnumerable<(byte Id, string Value)> fields) =>
        string.Join(", ", fields.Select(f => $"field {f.Id}={ControlTokens.Visualize(f.Value)}"));

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
        IPrinterFrameParser parser = p.Protocol == PrinterProtocol.Tij
            ? new TijFrameParser()
            : new TextFrameParser(TextEncodings.Resolve(p.Encoding).GetBytes(ControlTokens.Decode(p.PrintDoneSignal ?? "")));

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
        _log.Write(LogStatus.Ok, $"Connected to printer {session.Endpoint} ({(p.Protocol == PrinterProtocol.Tij ? "TIJ" : "custom text")})");
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

    // ------------------------------------------------------------------ TIJ

    private async Task<PrinterFrame> TijRequestAsync(
        PrinterSession session,
        AppSettings s,
        byte command,
        byte[] payload,
        string detail,
        List<SentCommand> trace,
        CancellationToken ct,
        params byte[] okStatuses)
    {
        var name = TijProtocol.CommandName(command);
        var frame = await session.RequestAsync(
            payload,
            f => f.Kind is PrinterFrameKind.Ack or PrinterFrameKind.Nak && f.Command == command,
            TimeSpan.FromMilliseconds(Math.Max(50, s.Printer.ResponseTimeoutMs)),
            ct).ConfigureAwait(false) ?? throw new IOException($"{name}: tidak ada balasan");
        _stats.AddSent(payload.Length);
        trace.Add(new SentCommand(trace.Count + 1, name, detail, payload.Length, frame.Describe()));

        if (frame.Kind == PrinterFrameKind.Nak)
        {
            throw new PrinterException(
                $"{name} ditolak printer: {TijProtocol.CommandStatusText(frame.Status)} (NAK {frame.Status:X2})");
        }

        if (frame.Status == 0x01)
        {
            _log.Write(LogStatus.Warn, $"{name}: printer melaporkan error/warning aktif (P_Status 01)");
            return frame;
        }

        if (!okStatuses.Contains(frame.Status))
        {
            throw new PrinterException($"{name}: {TijProtocol.PrinterStatusText(frame.Status)} (P_Status {frame.Status:X2})");
        }

        // 42 (buffer now full) normal saat buffer 1D terisi penuh; status lain yang diterima dicatat.
        if (frame.Status is not (0x00 or 0x42))
        {
            _log.Write(LogStatus.Warn, $"{name}: {TijProtocol.PrinterStatusText(frame.Status)} (P_Status {frame.Status:X2})");
        }

        return frame;
    }

    /// <summary>Load template (1E [+01] [+11]) dan/atau kosongkan buffer 1D (21).</summary>
    private async Task SetupTijJobAsync(
        PrinterSession session,
        AppSettings s,
        ParsedRecord record,
        bool loadTemplate,
        bool clearBuffer,
        List<SentCommand> trace,
        CancellationToken ct)
    {
        var tij = s.Printer.Tij;
        var slave = SlaveAddress(s.Printer);
        var encoding = TextEncodings.Resolve(s.Printer.Encoding);
        if (loadTemplate)
        {
            var context = new OutputContext(record, 0, "", "", _time.GetLocalNow(), 0, true, true);
            var name = OutputFormatter.Render(tij.MessageName, context);
            await TijRequestAsync(session, s, TijProtocol.CmdLoadMessage, TijProtocol.LoadMessage(slave, name, encoding),
                $"message {name}", trace, ct, 0x00).ConfigureAwait(false);
            _log.Write(LogStatus.Ok, $"Load template {name} ke printer (1E)");
            if (tij.UpdateAfterLoad)
            {
                await TijRequestAsync(session, s, TijProtocol.CmdUpdatePrint, TijProtocol.Command(slave, TijProtocol.CmdUpdatePrint),
                    "", trace, ct, 0x00, 0x12).ConfigureAwait(false);
            }

            if (tij.StartPrintAfterLoad)
            {
                await TijRequestAsync(session, s, TijProtocol.CmdStartPrint, TijProtocol.Command(slave, TijProtocol.CmdStartPrint),
                    "", trace, ct, 0x00, 0x11).ConfigureAwait(false);
            }
        }

        if (clearBuffer)
        {
            await TijRequestAsync(session, s, TijProtocol.CmdClearBuffer, TijProtocol.Command(slave, TijProtocol.CmdClearBuffer),
                "", trace, ct, 0x00, 0x12).ConfigureAwait(false);
        }
    }

    /// <summary>Kirim isi remote field (1D) untuk satu nilai counter.</summary>
    private async Task<PendingPrint> SendFieldsAsync(
        PrinterSession session,
        AppSettings s,
        ParsedRecord record,
        long counter,
        List<SentCommand> trace,
        CancellationToken ct)
    {
        var now = _time.GetLocalNow();
        var date = CounterEngine.BusinessDateFor(now, s.Counter.DayStartTime);
        var text = CounterEngine.Format(counter, s.Counter.Digits);
        var context = new OutputContext(record, counter, text, date, now, Interlocked.Read(ref _sequence), false, false);
        var fields = RenderFields(s.Printer, context);
        var describe = DescribeFields(fields);
        var payload = TijProtocol.RemoteFields(SlaveAddress(s.Printer), fields, TextEncodings.Resolve(s.Printer.Encoding));
        await TijRequestAsync(session, s, TijProtocol.CmdRemoteFields, payload, describe, trace, ct, 0x00, 0x42)
            .ConfigureAwait(false);
        return new PendingPrint(record, counter, text, date, _counter.Epoch, describe);
    }

    /// <summary>
    /// Mode counter per cetak: siapkan job baru di printer. Mengembalikan false jika job sama dengan yang aktif.
    /// </summary>
    private async Task<bool> StartTijJobAsync(
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
        var load = decision.TemplateChanged || _printerNeedsJob || firstJob;
        await SetupTijJobAsync(session, s, record, load, s.Printer.Tij.ClearBufferOnJobChange, trace, ct).ConfigureAwait(false);
        ResetQueue(_counter.NextValue(record.Template, s.Counter) ?? decision.Value);
        await TopUpAsync(session, s, trace, ct).ConfigureAwait(false);
        _jobArmed = true;
        _printerNeedsJob = false;
        return true;
    }

    /// <summary>Mode counter per data: setiap record dikirim ke printer dengan counter berikutnya.</summary>
    private async Task SendTijRecordAsync(
        AppSettings s,
        ParsedRecord record,
        CounterDecision decision,
        List<SentCommand> trace,
        CancellationToken ct)
    {
        var session = await EnsurePrinterAsync(s.Printer, ct).ConfigureAwait(false);
        var load = decision.TemplateChanged || _printerNeedsJob || _job is null;
        if (load)
        {
            await SetupTijJobAsync(session, s, record, true, false, trace, ct).ConfigureAwait(false);
        }

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
        _nextToSendText = CounterEngine.Format(nextValue, _settings.Counter.Digits);
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

    /// <summary>Isi buffer printer sampai <see cref="TijSettings.Lookahead"/> data menunggu cetak.</summary>
    private async Task TopUpAsync(PrinterSession session, AppSettings s, List<SentCommand> trace, CancellationToken ct)
    {
        var job = _job ?? throw new InvalidOperationException("Belum ada job aktif");
        var lookahead = Math.Clamp(s.Printer.Tij.Lookahead, 1, 10);
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
            await SetupTijJobAsync(session, s, job.Record, _printerNeedsJob, true, trace, ct).ConfigureAwait(false);
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

    // ------------------------------------------------------------------ custom text

    private async Task SendCustomTextAsync(
        AppSettings s,
        ParsedRecord record,
        CounterDecision decision,
        long seq,
        List<SentCommand> trace,
        CancellationToken ct)
    {
        var session = await EnsurePrinterAsync(s.Printer, ct).ConfigureAwait(false);
        var force = _printerNeedsJob;
        var context = new OutputContext(
            record,
            decision.Value,
            decision.Text,
            decision.BusinessDate,
            _time.GetLocalNow(),
            seq,
            decision.TemplateChanged || force,
            decision.OperatorChanged || force);
        var encoding = TextEncodings.Resolve(s.Printer.Encoding);
        var expected = ControlTokens.Decode(s.Printer.ExpectedResponse ?? "");
        foreach (var command in OutputFormatter.Build(s.Printer.Steps, context).Where(c => c.Send))
        {
            if (trace.Count > 0 && s.Printer.StepDelayMs > 0)
            {
                await Task.Delay(TimeSpan.FromMilliseconds(s.Printer.StepDelayMs), _time, ct).ConfigureAwait(false);
            }

            Func<PrinterFrame, bool>? isReply = null;
            if (s.Printer.WaitForResponse)
            {
                var received = new StringBuilder();
                isReply = f =>
                {
                    if (f.Kind != PrinterFrameKind.Text)
                    {
                        return false;
                    }

                    received.Append(encoding.GetString(f.Data));
                    return expected.Length == 0 || received.ToString().Contains(expected, StringComparison.Ordinal);
                };
            }

            var bytes = encoding.GetBytes(command.Text);
            PrinterFrame? reply;
            try
            {
                reply = await session.RequestAsync(bytes, isReply, TimeSpan.FromMilliseconds(s.Printer.ResponseTimeoutMs), ct)
                    .ConfigureAwait(false);
            }
            catch (TimeoutException)
            {
                throw new PrinterException($"Langkah {command.Step}: printer tidak membalas / balasan tidak sesuai dalam {s.Printer.ResponseTimeoutMs} ms");
            }

            _stats.AddSent(bytes.Length);
            trace.Add(new SentCommand(command.Step, command.Name, command.Display, bytes.Length,
                reply is null ? null : ControlTokens.Visualize(encoding.GetString(reply.Data))));
        }

        if (!_counter.Commit(decision))
        {
            _log.Write(LogStatus.Warn, $"Counter direset saat pengiriman; nilai {decision.Text} tidak disimpan");
        }

        if (!string.IsNullOrEmpty(s.Printer.PrintDoneSignal))
        {
            AddPending(new PendingPrint(record, decision.Value, decision.Text, decision.BusinessDate, _counter.Epoch, ""));
        }

        _job = new ActiveJob(record);
        _jobArmed = true;
        _printerNeedsJob = false;
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
        _nextToSendText = _pending.First?.Value.CounterText ?? (UsesPrintCounting(s) ? CounterEngine.Format(_nextToSend, s.Counter.Digits) : null);
        if (UsesPrintCounting(s) && !repeat
            && !_counter.CommitPrint(printed.Record.Template, printed.Record.Operator, printed.Counter, printed.Epoch))
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
            var context = new OutputContext(printed.Record, printed.Counter, printed.CounterText, printed.BusinessDate, now, seq, false, false);
            feedback = OutputFormatter.Render(s.Feedback.Template, context);
            var payload = TextEncodings.Resolve(s.Server.Encoding).GetBytes(feedback);
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
        var active = _printer;
        var useActive = active is { IsConnected: true } && _printerKey == PrinterLinks.ConnectionKey(printer);
        PrinterSession? temporary = null;
        try
        {
            var session = active!;
            if (!useActive)
            {
                IPrinterFrameParser parser = printer.Protocol == PrinterProtocol.Tij
                    ? new TijFrameParser()
                    : new TextFrameParser([]);
                temporary = await PrinterSession.OpenAsync(_printerFactory(printer), parser, _ => { }, null, ct).ConfigureAwait(false);
                session = temporary;
            }

            var message = $"Printer {session.Endpoint} terhubung";
            if (printer.Protocol == PrinterProtocol.Tij)
            {
                var reply = await session.RequestAsync(
                    TijProtocol.Command(SlaveAddress(printer), TijProtocol.CmdMessageNames),
                    f => f.Kind is PrinterFrameKind.Ack or PrinterFrameKind.Nak && f.Command == TijProtocol.CmdMessageNames,
                    TimeSpan.FromMilliseconds(Math.Max(500, printer.ResponseTimeoutMs)),
                    ct).ConfigureAwait(false);
                if (reply is { Kind: PrinterFrameKind.Ack })
                {
                    var names = TijProtocol.ParseMessageNames(reply.Data, TextEncodings.Resolve(printer.Encoding));
                    message += names.Count > 0
                        ? $". Message di printer: {string.Join(", ", names)}"
                        : $". Status {reply.Status:X2} ({TijProtocol.PrinterStatusText(reply.Status)})";
                }
                else if (reply is not null)
                {
                    message += $", tetapi {reply.Describe()}";
                }
            }

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
