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
    private readonly Queue<Frame> _waitingFrames = new();
    private volatile int _waitingCount;
    private volatile PrintState _printState = PrintState.Unknown;
    private DateTimeOffset _lastReadyCheck;
    private DateTimeOffset _lastIdleCheck;

    /// <summary>Koneksi yang bertahan minimal selama ini dan terputus disambung ulang tanpa menunggu.</summary>
    internal static readonly TimeSpan StableConnection = TimeSpan.FromSeconds(5);

    /// <summary>Setelah koneksi printer terputus, coba tiap detik selama ini sebelum memakai jeda reconnect.</summary>
    private static readonly TimeSpan FastRetryWindow = TimeSpan.FromSeconds(15);

    /// <summary>Jeda antar percobaan konek printer: cepat sesaat setelah terputus, lalu sesuai Settings.</summary>
    private TimeSpan PrinterRetryDelay(PrinterSettings p) =>
        Environment.TickCount64 < _fastRetryUntilTick
            ? TimeSpan.FromSeconds(1)
            : TimeSpan.FromSeconds(Math.Max(1, p.ReconnectDelaySeconds));

    private long _fastRetryUntilTick;

    /// <summary>Cek status printer jika koneksi diam selama ini (status Start/Stop tetap terkini).</summary>
    internal static readonly TimeSpan PrinterIdleCheck = TimeSpan.FromSeconds(5);

    private long _printerConnectedTick;
    private long _printerDroppedTick;
    private bool _waitingNoticeLogged;
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
    private volatile string? _nextToSendText;
    private volatile string? _lastPrintedText;
    private PendingPrint? _lastSent;
    private long _nextToSend;

    private bool PrinterConnected => _printer?.IsConnected == true;

    /// <summary>Printer terhubung tetapi belum start, dan ada job / data yang menunggu.</summary>
    private bool WaitingForPrinter =>
        _running && _settings.Printer.Enabled
        && ((_printState == PrintState.NotStarted && _job is not null && !_jobArmed && UsesPrintCounting(_settings))
            || _waitingCount > 0);

    // ------------------------------------------------------------------ printer belum start

    private void MarkPrintStarted()
    {
        if (_printState == PrintState.NotStarted && _waitingNoticeLogged)
        {
            _log.Write(LogStatus.Ok, "Printer sudah start");
        }

        _printState = PrintState.Started;
        _waitingNoticeLogged = false;
    }

    private void MarkPrintNotStarted()
    {
        _printState = PrintState.NotStarted;
        _jobArmed = false;
        if (!_waitingNoticeLogged)
        {
            _waitingNoticeLogged = true;
            _log.Write(LogStatus.Warn,
                "Printer belum start (Print not started): data disimpan dan dikirim otomatis begitu printer di-start "
                + "(dari panel printer atau tombol Start Print)");
        }
    }

    /// <summary>Batas data yang menunggu printer; di atas ini pembacaan dari server ditahan (tidak dibuang).</summary>
    private const int MaxWaitingFrames = 10_000;

    private void DeferFrame(Frame frame)
    {
        _waitingFrames.Enqueue(frame);
        _waitingCount = _waitingFrames.Count;
    }

    /// <summary>Kirim data yang menunggu printer start (mode counter per data), berurutan.</summary>
    private async Task DrainWaitingAsync(CancellationToken ct)
    {
        var sent = 0;
        while (_waitingFrames.TryPeek(out var frame))
        {
            if (!await ProcessServerFrameAsync(frame, replay: true, ct).ConfigureAwait(false))
            {
                break;
            }

            _waitingFrames.Dequeue();
            _waitingCount = _waitingFrames.Count;
            sent++;
        }

        if (sent > 0)
        {
            _log.Write(LogStatus.Ok, $"{sent} data yang menunggu printer start sudah dikirim");
        }
    }

    /// <summary>
    /// Jalankan langkah job. Jika printer belum start dan "Start print otomatis" aktif,
    /// kirim Start Print lalu ulangi sekali.
    /// </summary>
    private async Task RunWithAutoStartAsync(
        PrinterSession session,
        AppSettings s,
        List<SentCommand> trace,
        CancellationToken ct,
        Func<Task> action)
    {
        var driver = PrinterDrivers.For(s.Printer);
        try
        {
            await action().ConfigureAwait(false);
        }
        catch (PrinterNotReadyException) when (s.Printer.AutoStartPrint && driver.SupportsPrintControl(s.Printer))
        {
            _log.Write(LogStatus.Info, "Printer belum start: mengirim Start Print otomatis");
            await driver.StartPrintAsync(Call(session, s, trace, ct)).ConfigureAwait(false);
            await action().ConfigureAwait(false);
        }

        MarkPrintStarted();
    }

    /// <summary>Cek berkala saat ada data menunggu: jika printer sudah start, langsung kirim.</summary>
    private async Task CheckPrinterReadyAsync(CancellationToken ct)
    {
        var s = _settings;
        if (!WaitingForPrinter || _printer is not { IsConnected: true } session)
        {
            return;
        }

        bool? started;
        try
        {
            started = await PrinterDrivers.For(s.Printer).IsPrintStartedAsync(Call(session, s, [], ct)).ConfigureAwait(false);
        }
        catch (Exception ex) when (ex is TimeoutException or PrinterException)
        {
            started = null; // Firmware lama tanpa perintah 14: langsung coba kirim.
        }

        if (started == false && !s.Printer.AutoStartPrint)
        {
            return;
        }

        if (UsesPrintCounting(s))
        {
            await ResyncAsync("printer start", ct).ConfigureAwait(false);
        }
        else
        {
            await DrainWaitingAsync(ct).ConfigureAwait(false);
        }

        RaiseStatus();
    }

    /// <summary>Tombol Start Print / Stop Print.</summary>
    public async Task<ConnectionTestResult> PrintControlAsync(bool start, CancellationToken ct)
    {
        if (_running)
        {
            var result = new TaskCompletionSource<ConnectionTestResult>(TaskCreationOptions.RunContinuationsAsynchronously);
            Enqueue(new PrintControlItem(start, result));
            return await result.Task.WaitAsync(TimeSpan.FromSeconds(20), ct).ConfigureAwait(false);
        }

        // Transfer belum jalan: pakai koneksi sementara.
        var s = _settings;
        var driver = PrinterDrivers.For(s.Printer);
        var watch = Stopwatch.StartNew();
        if (!driver.SupportsPrintControl(s.Printer))
        {
            return new ConnectionTestResult(false, "Perintah Start/Stop Print belum diatur untuk protokol ini", 0);
        }

        try
        {
            await using var session = await PrinterSession.OpenAsync(_printerFactory(s.Printer), driver.CreateParser(s.Printer), _ => { }, null, ct)
                .ConfigureAwait(false);
            var call = Call(session, s, [], ct);
            await (start ? driver.StartPrintAsync(call) : driver.StopPrintAsync(call)).ConfigureAwait(false);
            _printState = start ? PrintState.Started : PrintState.NotStarted;
            _log.Write(LogStatus.Ok, start ? "Start Print dikirim ke printer" : "Stop Print dikirim ke printer");
            RaiseStatus();
            return new ConnectionTestResult(true, start ? "Printer start" : "Printer stop", watch.ElapsedMilliseconds);
        }
        catch (Exception ex) when (ex is PrinterException || IsTransportError(ex))
        {
            return new ConnectionTestResult(false, ex.Message, watch.ElapsedMilliseconds);
        }
    }

    private async Task OnPrintControlAsync(PrintControlItem item, CancellationToken ct)
    {
        var s = _settings;
        var driver = PrinterDrivers.For(s.Printer);
        var watch = Stopwatch.StartNew();
        if (!s.Printer.Enabled || !driver.SupportsPrintControl(s.Printer))
        {
            item.Result.TrySetResult(new ConnectionTestResult(false, "Perintah Start/Stop Print belum diatur untuk protokol ini", 0));
            return;
        }

        try
        {
            var session = await EnsurePrinterAsync(s.Printer, ct).ConfigureAwait(false);
            var call = Call(session, s, [], ct);
            if (item.Start)
            {
                await driver.StartPrintAsync(call).ConfigureAwait(false);
                _log.Write(LogStatus.Ok, "Start Print dikirim ke printer");
                MarkPrintStarted();
                if (UsesPrintCounting(s) && _job is not null && !_jobArmed)
                {
                    await ResyncAsync("printer start", ct).ConfigureAwait(false);
                }
                else if (_waitingFrames.Count > 0)
                {
                    await DrainWaitingAsync(ct).ConfigureAwait(false);
                }
            }
            else
            {
                await driver.StopPrintAsync(call).ConfigureAwait(false);
                _log.Write(LogStatus.Ok, "Stop Print dikirim ke printer; job dikirim ulang otomatis saat printer start lagi");
                _printState = PrintState.NotStarted;
                _jobArmed = false;
                _waitingNoticeLogged = true;
            }

            item.Result.TrySetResult(new ConnectionTestResult(true, item.Start ? "Printer start" : "Printer stop", watch.ElapsedMilliseconds));
        }
        catch (Exception ex) when (ex is PrinterException || IsTransportError(ex))
        {
            if (ex is not PrinterException)
            {
                await ClosePrinterAsync(PrinterState.Error, ex.Message).ConfigureAwait(false);
            }

            item.Result.TrySetResult(new ConnectionTestResult(false, ex.Message, watch.ElapsedMilliseconds));
        }

        RaiseStatus();
    }

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

        if (_work is null)
        {
            throw new OperationCanceledException();
        }

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
                frame => Enqueue(new PrinterFrameItem(generation, frame)),
                null,
                ct).ConfigureAwait(false);
        }
        catch (Exception ex) when (IsTransportError(ex))
        {
            SetPrinterState(PrinterState.Error, ex.Message);
            // Printer TIJ hanya menerima satu klien TCP: koneksi yang menggantung (timeout) setelah terputus
            // biasanya karena ada software / PC lain yang sedang terhubung ke printer yang sama.
            var hint = _printerDroppedTick != 0 && ex is TimeoutException
                ? "; printer hanya menerima 1 koneksi, pastikan tidak ada software / PC lain yang terhubung ke printer ini"
                : "";
            throw new PrinterException($"Tidak bisa konek ke printer {PrinterLinks.Describe(p)}: {ex.Message}{hint}", ex);
        }

        _ = session.Completion.ContinueWith(
            _ => Enqueue(new PrinterClosedItem(generation)),
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
        var reconnected = _printerDroppedTick != 0
            ? $", tersambung lagi setelah {(Environment.TickCount64 - _printerDroppedTick) / 1000.0:0.0} detik"
            : "";
        _printerDroppedTick = 0;
        _printerConnectedTick = Environment.TickCount64;
        _log.Write(LogStatus.Ok, $"Connected to printer {session.Endpoint} ({driver.Name}{reconnected})");
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
        _printState = PrintState.Unknown;
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
                _log.Write(LogStatus.Warn, $"{ex.Message} (dicoba ulang tiap {PrinterRetryDelay(s.Printer).TotalSeconds:0} detik)");
                _lastPrinterError = ex.Message;
            }

            return;
        }

        if (!wasConnected && _job is not null && UsesPrintCounting(s))
        {
            await ResyncAsync("printer terhubung", ct).ConfigureAwait(false);
        }
        else if (_waitingFrames.Count > 0)
        {
            await DrainWaitingAsync(ct).ConfigureAwait(false);
        }
    }

    private async Task OnPrinterClosedAsync(PrinterClosedItem item)
    {
        if (item.Generation != Interlocked.Read(ref _printerGeneration) || _printer is null)
        {
            return;
        }

        var endpoint = _printer.Endpoint;
        var reason = _printer.CloseReason ?? "terputus";
        var lasted = TimeSpan.FromMilliseconds(Environment.TickCount64 - _printerConnectedTick);
        await ClosePrinterAsync(PrinterState.Error, $"Koneksi printer terputus: {reason}").ConfigureAwait(false);
        _printerDroppedTick = Environment.TickCount64;
        _lastPrinterError = null;
        if (lasted >= StableConnection)
        {
            // Koneksi yang sudah stabil langsung disambung ulang; job dikirim ulang setelah tersambung.
            _log.Write(LogStatus.Warn, $"Koneksi printer {endpoint} terputus ({reason}); menyambung ulang");
            _fastRetryUntilTick = Environment.TickCount64 + (long)FastRetryWindow.TotalMilliseconds;
            Enqueue(new EnsurePrinterItem());
        }
        else
        {
            // Putus lagi tak lama setelah tersambung: tunggu jeda reconnect agar printer tidak dibanjiri koneksi.
            _fastRetryUntilTick = 0;
            _log.Write(LogStatus.Warn,
                $"Koneksi printer {endpoint} terputus ({reason}); dicoba lagi dalam {_settings.Printer.ReconnectDelaySeconds} detik");
        }
    }

    /// <summary>
    /// Koneksi diam: minta status printer (TIJ: 14) supaya tombol Start / Stop Print sesuai keadaan printer.
    /// Gagal / tidak dibalas tidak memutus koneksi; koneksi yang benar-benar mati dideteksi TCP keepalive.
    /// </summary>
    private async Task CheckPrinterIdleAsync(CancellationToken ct)
    {
        var s = _settings;
        if (WaitingForPrinter || _printer is not { IsConnected: true } session || session.IdleFor < PrinterIdleCheck)
        {
            return;
        }

        bool? started;
        try
        {
            started = await PrinterDrivers.For(s.Printer).IsPrintStartedAsync(Call(session, s, [], ct)).ConfigureAwait(false);
        }
        catch (Exception ex) when (ex is PrinterException || IsTransportError(ex))
        {
            return;
        }

        var next = started switch
        {
            true => PrintState.Started,
            false => PrintState.NotStarted,
            null => _printState,
        };
        if (next == _printState)
        {
            return;
        }

        if (next == PrintState.Started)
        {
            MarkPrintStarted();
        }
        else
        {
            _printState = next;
        }

        RaiseStatus();
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
                MarkPrintStarted();
                await OnPrintEndAsync(ct).ConfigureAwait(false);
                break;
            case PrinterFrameKind.PrintStart:
                MarkPrintStarted();
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
        if (changeTemplate)
        {
            // Jika gagal (mis. printer belum start), template di-load ulang saat dikirim ulang.
            _printerNeedsJob = true;
        }

        await RunWithAutoStartAsync(session, s, trace, ct, async () =>
        {
            await PrepareJobAsync(session, s, record, changeTemplate, s.Printer.ClearOnJobChange, trace, ct).ConfigureAwait(false);
            ResetQueue(_counter.NextValue(record.Template, s.Counter) ?? decision.Value);
            await TopUpAsync(session, s, trace, ct).ConfigureAwait(false);
        }).ConfigureAwait(false);
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
        if (changeTemplate)
        {
            _printerNeedsJob = true;
        }

        PendingPrint? pending = null;
        await RunWithAutoStartAsync(session, s, trace, ct, async () =>
        {
            await PrepareJobAsync(session, s, record, changeTemplate, false, trace, ct).ConfigureAwait(false);
            pending = await SendFieldsAsync(session, s, record, decision.Value, trace, ct).ConfigureAwait(false);
        }).ConfigureAwait(false);
        if (!_counter.Commit(decision))
        {
            _log.Write(LogStatus.Warn, $"Counter direset saat pengiriman; nilai {decision.Text} tidak disimpan");
        }

        AddPending(pending!);
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
            var changeTemplate = _printerNeedsJob;
            await RunWithAutoStartAsync(session, s, trace, ct, async () =>
            {
                await PrepareJobAsync(session, s, job.Record, changeTemplate, true, trace, ct).ConfigureAwait(false);
                ResetQueue(_counter.NextValue(job.Record.Template, s.Counter) ?? decision.Value);
                await TopUpAsync(session, s, trace, ct).ConfigureAwait(false);
            }).ConfigureAwait(false);
            _jobArmed = true;
            _printerNeedsJob = false;
            _lastPrinterError = null;
            _log.Write(LogStatus.Ok,
                $"Job dikirim ke printer ({reason}): {job.Record.Template} / {job.Record.Operator}, counter berikutnya {_nextToSendText}");
        }
        catch (PrinterBusyException)
        {
            _jobArmed = false; // dikirim ulang setelah printer mencetak / cek berikutnya
        }
        catch (PrinterNotReadyException)
        {
            MarkPrintNotStarted();
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
            feedback = OutputFormatter.Render(s.Feedback.EffectiveTemplate(), context);
            var payload = CommandEncoder.Encode(feedback, TextEncodings.Resolve(s.Server.Encoding));
            bytes = payload.Length;
            // Catat dulu (Queued) supaya status Sent dari pengirim selalu menemukan laporannya.
            AddFeedback(new FeedbackRecord(
                seq,
                now,
                printed.Record.Operator,
                printed.Record.Template,
                printed.CounterText,
                Trimmed(feedback),
                bytes,
                FeedbackState.Queued,
                null));
            note = QueueFeedback(new FeedbackItem(seq, payload));
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

        if (_waitingFrames.Count > 0)
        {
            await DrainWaitingAsync(ct).ConfigureAwait(false);
        }

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

    /// <summary>Masukkan feedback ke antrean pengirim (tidak menunggu server).</summary>
    private string? QueueFeedback(FeedbackItem item)
    {
        _feedback.Writer.TryWrite(item);
        return _serverSender is null
            ? $"Server tidak terhubung: laporan diantrikan ({_feedback.Reader.Count})"
            : null;
    }

    /// <summary>
    /// Kirim feedback ke server berurutan. Jika server terputus, laporan tetap di antrean dan dikirim
    /// setelah tersambung lagi. Jika server tidak membaca data (TCP macet) 10 detik, koneksi diulang.
    /// </summary>
    private async Task FeedbackSenderAsync(CancellationToken ct)
    {
        var reader = _feedback.Reader;
        var warned = false;
        try
        {
            while (await reader.WaitToReadAsync(ct).ConfigureAwait(false))
            {
                while (reader.TryPeek(out var item))
                {
                    var send = _serverSender;
                    if (send is null)
                    {
                        await Task.Delay(TimeSpan.FromMilliseconds(300), _time, ct).ConfigureAwait(false);
                        continue;
                    }

                    using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
                    timeout.CancelAfter(TimeSpan.FromSeconds(10));
                    try
                    {
                        await send(item.Payload, timeout.Token).ConfigureAwait(false);
                        _stats.AddSent(item.Payload.Length);
                        reader.TryRead(out _);
                        UpdateFeedback(item.Seq, FeedbackState.Sent, _time.GetLocalNow());
                        if (warned)
                        {
                            warned = false;
                            _log.Write(LogStatus.Ok, "Feedback ke server terkirim lagi");
                        }
                    }
                    catch (OperationCanceledException) when (!ct.IsCancellationRequested)
                    {
                        _log.Write(LogStatus.Warn, "Server tidak menerima feedback dalam 10 detik: koneksi server diulang");
                        warned = true;
                        _connectionCts?.Cancel();
                        await Task.Delay(TimeSpan.FromMilliseconds(500), _time, ct).ConfigureAwait(false);
                    }
                    catch (Exception ex) when (ex is not OperationCanceledException)
                    {
                        // TCP putus / broker MQTT menolak: laporan tetap di antrean.
                        if (!warned)
                        {
                            warned = true;
                            _log.Write(LogStatus.Warn, $"Gagal kirim feedback ke server ({ex.Message}); dikirim ulang setelah tersambung");
                        }

                        await Task.Delay(TimeSpan.FromMilliseconds(500), _time, ct).ConfigureAwait(false);
                    }
                }
            }
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested)
        {
            // Stop Transfer: sisa antrean dikirim saat Start berikutnya.
        }
    }

    // ------------------------------------------------------------------ test

    public async Task<ConnectionTestResult> TestPrinterAsync(PrinterSettings printer, CancellationToken ct)
    {
        var watch = Stopwatch.StartNew();
        var driver = PrinterDrivers.For(printer);
        var active = _printer;
        var current = _settings.Printer;
        // Printer hanya menerima satu koneksi: koneksi kedua dari tombol Test bisa memutus transfer.
        var samePrinter = _running && current.Enabled
            && printer.Transport == current.Transport
            && PrinterLinks.Describe(printer) == PrinterLinks.Describe(current);
        if (samePrinter && (printer.Protocol != current.Protocol || active is not { IsConnected: true }))
        {
            return new ConnectionTestResult(
                false,
                printer.Protocol != current.Protocol
                    ? "Printer ini sedang dipakai transfer dengan protokol lain. Simpan settings atau Stop Transfer dulu untuk menguji."
                    : "Printer ini sedang dipakai transfer dan sedang menyambung ulang. Test tidak membuka koneksi kedua.",
                0);
        }

        PrinterSession? temporary = null;
        try
        {
            var session = samePrinter ? active! : null;
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
