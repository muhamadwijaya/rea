using System.Diagnostics;
using System.Net.Sockets;
using System.Threading.Channels;
using Sharx.Core.Configuration;
using Sharx.Core.Counter;
using Sharx.Core.Logging;
using Sharx.Core.Mapping;
using Sharx.Core.Text;
using Sharx.Core.Transport;

namespace Sharx.Core.Transfer;

/// <summary>
/// Alur utama:
/// server (TCP) → pecah sesuai Flow Mapping → counter → printer CIJ (TIJ 1E/1D)
/// → printer selesai cetak (AB) → laporan operator, template, counter ke server.
/// </summary>
/// <remarks>
/// Semua yang menyentuh printer, antrean cetak dan job aktif dijalankan berurutan oleh satu worker
/// (<see cref="WorkerAsync"/>); thread lain hanya memasukkan pekerjaan ke antrean.
/// </remarks>
public sealed partial class TransferService : IAsyncDisposable
{
    private readonly SettingsStore? _store;
    private readonly CounterEngine _counter;
    private readonly TransferLog _log;
    private readonly TransferStats _stats;
    private readonly TimeProvider _time;
    private readonly Func<PrinterSettings, IPrinterLink> _printerFactory;
    private readonly object _lifecycle = new();
    private readonly object _recordsGate = new();
    private readonly LinkedList<TransferRecord> _records = new();
    private readonly LinkedList<FeedbackRecord> _feedbackRecords = new();
    private readonly SemaphoreSlim _serverWriteGate = new(1, 1);
    private readonly CancellationTokenSource _disposeCts = new();

    private volatile AppSettings _settings;
    private CancellationTokenSource? _runCts;
    private CancellationTokenSource? _connectionCts;
    /// <summary>Perintah &amp; event printer (prioritas, tidak dibatasi; jumlahnya kecil).</summary>
    private Channel<WorkItem>? _work;

    /// <summary>Data dari server (dibatasi: jika penuh, pembacaan TCP menunggu = tidak menumpuk di memori).</summary>
    private Channel<Frame>? _frames;

    /// <summary>Satu sinyal per item di <see cref="_work"/> / <see cref="_frames"/>.</summary>
    private SemaphoreSlim? _workSignal;

    /// <summary>Feedback ke server; dikirim task terpisah sehingga server lambat tidak menahan transfer.</summary>
    private readonly Channel<FeedbackItem> _feedback;

    private Task? _feedbackTask;
    private long _feedbackDropped;
    private const int FrameQueueCapacity = 1000;
    private Task? _serverTask;
    private Task? _workerTask;
    private Task? _backgroundTask;
    private volatile bool _running;
    /// <summary>Pengirim feedback ke server yang sedang terhubung (TCP atau MQTT); null = terputus.</summary>
    private volatile Func<byte[], CancellationToken, Task>? _serverSender;
    private ServerState _serverState = ServerState.Stopped;
    private DateTimeOffset? _connectedSince;
    private string? _lastError;
    private long _sequence;

    public TransferService(
        AppSettings settings,
        SettingsStore? store,
        CounterEngine counter,
        TransferLog log,
        TransferStats stats,
        TimeProvider time,
        Func<PrinterSettings, IPrinterLink>? printerFactory = null)
    {
        _settings = settings;
        _store = store;
        _counter = counter;
        _log = log;
        _stats = stats;
        _time = time;
        _printerFactory = printerFactory ?? PrinterLinks.Create;
        _printerState = settings.Printer.Enabled ? PrinterState.Idle : PrinterState.Disabled;
        _feedback = Channel.CreateBounded<FeedbackItem>(
            new BoundedChannelOptions(Math.Clamp(settings.Feedback.MaxQueued, 1, 1_000_000))
            {
                SingleReader = true,
                FullMode = BoundedChannelFullMode.DropOldest,
            },
            dropped =>
            {
                Interlocked.Increment(ref _feedbackDropped);
                UpdateFeedback(dropped.Seq, FeedbackState.Dropped, null);
            });
    }

    public AppSettings Settings => _settings;
    public CounterEngine Counter => _counter;
    public TransferLog Log => _log;
    public TransferStats Stats => _stats;
    public bool IsRunning => _running;

    public event Action<TransferStatus>? StatusChanged;
    public event Action<TransferRecord>? RecordProcessed;

    /// <summary>Feedback baru (Queued) atau perubahan statusnya (Sent / Dropped), dengan Seq yang sama.</summary>
    public event Action<FeedbackRecord>? FeedbackChanged;
    public event Action<StatsSnapshot>? StatsUpdated;

    public TransferStatus Status
    {
        get
        {
            var s = _settings;
            var job = _job;
            JobStatus? jobStatus = null;
            if (job is not null)
            {
                jobStatus = new JobStatus(
                    job.Record.Operator,
                    job.Record.Template,
                    _nextToSendText,
                    _pendingCount,
                    _jobArmed,
                    _lastPrintedText);
            }

            return new TransferStatus(
                _running,
                _serverState,
                $"{s.Server.Host}:{s.Server.Port}",
                _connectedSince,
                s.Printer.Enabled ? _printerState : PrinterState.Disabled,
                PrinterLinks.Describe(s.Printer),
                Printing.PrinterDrivers.For(s.Printer).Name,
                s.Printer.DisplayName(),
                _printerMessage,
                _lastError,
                jobStatus,
                _feedback.Reader.Count,
                _printState,
                WaitingForPrinter,
                _waitingCount,
                Printing.PrinterDrivers.For(s.Printer).SupportsPrintControl(s.Printer),
                s.Server.Protocol.ToString(),
                s.Feedback.Enabled,
                s.Feedback.EffectiveTemplate());
        }
    }

    public IReadOnlyList<TransferRecord> RecentRecords(int count)
    {
        lock (_recordsGate)
        {
            return _records.Take(count).ToArray();
        }
    }

    /// <summary>Feedback terbaru (paling baru di depan) dengan status pengirimannya.</summary>
    public IReadOnlyList<FeedbackRecord> RecentFeedback(int count)
    {
        lock (_recordsGate)
        {
            return _feedbackRecords.Take(count).ToArray();
        }
    }

    /// <summary>Timer 1 detik: kecepatan transfer, ganti hari, koneksi ulang printer.</summary>
    public void StartBackground()
    {
        if (_backgroundTask is not null)
        {
            return;
        }

        _log.Prune(_settings.General.LogRetentionDays);
        CheckDayRollover();
        _backgroundTask = Task.Run(() => BackgroundLoopAsync(_disposeCts.Token));
    }

    public bool Start()
    {
        lock (_lifecycle)
        {
            if (_running || _runCts is not null)
            {
                return false;
            }

            _runCts = new CancellationTokenSource();
            var token = _runCts.Token;
            _work = Channel.CreateUnbounded<WorkItem>(new UnboundedChannelOptions { SingleReader = true });
            _frames = Channel.CreateBounded<Frame>(new BoundedChannelOptions(FrameQueueCapacity)
            {
                SingleReader = true,
                SingleWriter = true,
                FullMode = BoundedChannelFullMode.Wait,
            });
            _workSignal = new SemaphoreSlim(0);
            _running = true;
            _lastError = null;
            _printerNeedsJob = true;
            RestoreJobFromCounter();
            var work = _work.Reader;
            var frames = _frames.Reader;
            var signal = _workSignal;
            _workerTask = Task.Run(() => WorkerAsync(work, frames, signal, token));
            _serverTask = Task.Run(() => RunServerAsync(token));
            _feedbackTask = Task.Run(() => FeedbackSenderAsync(token));
        }

        var s = _settings;
        _log.Write(LogStatus.Ok, $"Transfer started ({s.Server.Host}:{s.Server.Port})");
        RaiseStatus();
        if (s.Printer.Enabled)
        {
            Enqueue(new EnsurePrinterItem());
        }

        return true;
    }

    public async Task StopAsync()
    {
        CancellationTokenSource? cts;
        Task? server;
        Task? worker;
        Task? feedback;
        lock (_lifecycle)
        {
            cts = _runCts;
            server = _serverTask;
            worker = _workerTask;
            feedback = _feedbackTask;
            if (cts is null)
            {
                return;
            }

            _running = false;
        }

        await cts.CancelAsync().ConfigureAwait(false);
        foreach (var task in new[] { server, worker, feedback })
        {
            if (task is null)
            {
                continue;
            }

            try
            {
                await task.ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
                // Berhenti normal.
            }
        }

        await ClosePrinterAsync(PrinterState.Idle, null).ConfigureAwait(false);
        lock (_lifecycle)
        {
            cts.Dispose();
            _runCts = null;
            _serverTask = null;
            _workerTask = null;
            _feedbackTask = null;
            _work = null;
            _frames = null;
            _workSignal?.Dispose();
            _workSignal = null;
        }

        _serverState = ServerState.Stopped;
        _connectedSince = null;
        _log.Write(LogStatus.Info, "Transfer stopped");
        RaiseStatus();
    }

    /// <summary>Validasi, simpan, lalu terapkan pengaturan baru.</summary>
    public Task<SettingsApplyResult> ApplySettingsAsync(AppSettings next)
    {
        var errors = SettingsValidator.Validate(next);
        if (errors.Count > 0)
        {
            return Task.FromResult(new SettingsApplyResult(false, errors, false));
        }

        try
        {
            _store?.Save(next);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            return Task.FromResult(new SettingsApplyResult(false, [$"Gagal menyimpan settings: {ex.Message}"], false));
        }

        var previous = _settings;
        _settings = next;
        _log.Write(LogStatus.Ok, "Settings disimpan");

        var reconnect = _running && ServerKey(previous.Server) != ServerKey(next.Server);
        if (reconnect)
        {
            _log.Write(LogStatus.Info, $"Alamat server berubah, koneksi ulang ke {next.Server.Host}:{next.Server.Port}");
            _connectionCts?.Cancel();
        }

        if (_running)
        {
            Enqueue(new SettingsChangedItem(previous, next));
        }
        else
        {
            _printerState = next.Printer.Enabled ? PrinterState.Idle : PrinterState.Disabled;
        }

        RaiseStatus();
        return Task.FromResult(new SettingsApplyResult(true, [], reconnect));
    }

    public bool ResetCounter(string? template)
    {
        var removed = _counter.Reset(template);
        _log.Write(LogStatus.Ok, template is null
            ? "Reset counter: semua counter template dihapus"
            : $"Reset counter template {template}");
        Enqueue(new ResyncItem("reset counter"));
        return removed;
    }

    public void SetCounter(string template, long value)
    {
        _counter.SetValue(template, value, _settings.Counter);
        _log.Write(LogStatus.Ok, $"Counter template {template} diubah manual menjadi {CounterEngine.Format(value, Math.Max(_counter.StoredWidth(template), _settings.Counter.Digits))}");
        Enqueue(new ResyncItem("counter diubah manual"));
    }

    public static async Task<ConnectionTestResult> TestServerAsync(ServerSettings server, CancellationToken ct)
    {
        if (server.Protocol == ServerProtocol.Mqtt)
        {
            return await TestMqttAsync(server, ct).ConfigureAwait(false);
        }

        var watch = Stopwatch.StartNew();
        try
        {
            using var client = new TcpClient();
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
            timeout.CancelAfter(TimeSpan.FromSeconds(Math.Clamp(server.ConnectTimeoutSeconds, 1, 120)));
            await client.ConnectAsync(server.Host, server.Port, timeout.Token).ConfigureAwait(false);
            return new ConnectionTestResult(true, $"Server {server.Host}:{server.Port} dapat dihubungi", watch.ElapsedMilliseconds);
        }
        catch (Exception ex) when (ex is SocketException or OperationCanceledException or ArgumentException)
        {
            var reason = ex is OperationCanceledException ? "timeout" : ex.Message;
            return new ConnectionTestResult(false, $"Gagal ke {server.Host}:{server.Port}: {reason}", watch.ElapsedMilliseconds);
        }
    }

    public async ValueTask DisposeAsync()
    {
        await StopAsync().ConfigureAwait(false);
        await _disposeCts.CancelAsync().ConfigureAwait(false);
        if (_backgroundTask is not null)
        {
            try
            {
                await _backgroundTask.ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
                // Berhenti normal.
            }
        }

        _disposeCts.Dispose();
    }

    private static string ServerKey(ServerSettings s) =>
        $"{s.Protocol}|{s.Host}|{s.Port}|{s.Framing}|{s.CustomTerminator}|{s.Encoding}|{s.MaxFrameBytes}|"
        + $"{s.Mqtt.ClientId}|{s.Mqtt.Username}|{s.Mqtt.Password}|{s.Mqtt.SubscribeTopic}|{s.Mqtt.Qos}|{s.Mqtt.UseTls}|"
        + $"{s.Mqtt.KeepAliveSeconds}|{s.Mqtt.CleanSession}";

    private void Enqueue(WorkItem item)
    {
        var signal = _workSignal;
        if (_work?.Writer.TryWrite(item) == true && signal is not null)
        {
            try
            {
                signal.Release();
            }
            catch (ObjectDisposedException)
            {
                // Transfer sedang berhenti.
            }
        }
    }

    // ------------------------------------------------------------------ server

    private async Task RunServerAsync(CancellationToken stopToken)
    {
        try
        {
            while (!stopToken.IsCancellationRequested)
            {
                var server = _settings.Server;
                using var connectionCts = CancellationTokenSource.CreateLinkedTokenSource(stopToken);
                _connectionCts = connectionCts;
                var reconnectNow = false;
                try
                {
                    if (server.Protocol == ServerProtocol.Mqtt)
                    {
                        await ReceiveMqttAsync(server, connectionCts.Token).ConfigureAwait(false);
                    }
                    else
                    {
                        await ReceiveTcpAsync(server, connectionCts.Token).ConfigureAwait(false);
                    }

                    _log.Write(LogStatus.Warn, "Koneksi server terputus");
                }
                catch (OperationCanceledException) when (stopToken.IsCancellationRequested)
                {
                    break;
                }
                catch (OperationCanceledException)
                {
                    reconnectNow = true;
                }
                catch (Exception ex)
                {
                    _lastError = ex.Message;
                    _log.Write(LogStatus.Error, $"Koneksi {DescribeServer(server)} gagal: {ex.Message}");
                }
                finally
                {
                    _serverSender = null;
                    _connectionCts = null;
                }

                _serverState = ServerState.Disconnected;
                _connectedSince = null;
                RaiseStatus();
                if (reconnectNow)
                {
                    continue;
                }

                if (!_settings.Server.AutoReconnect)
                {
                    _log.Write(LogStatus.Warn, "Auto reconnect nonaktif: koneksi server tidak diulang (tekan Stop lalu Start)");
                    break;
                }

                await Task.Delay(TimeSpan.FromSeconds(_settings.Server.ReconnectDelaySeconds), _time, stopToken)
                    .ConfigureAwait(false);
            }
        }
        catch (OperationCanceledException) when (stopToken.IsCancellationRequested)
        {
            // Stop Transfer ditekan.
        }
    }

    private static string DescribeServer(ServerSettings s) =>
        s.Protocol == ServerProtocol.Mqtt ? $"MQTT broker {s.Host}:{s.Port}" : $"server {s.Host}:{s.Port}";

    private async Task ReceiveTcpAsync(ServerSettings server, CancellationToken ct)
    {
        _serverState = ServerState.Connecting;
        RaiseStatus();

        using var client = new TcpClient();
        using (var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct))
        {
            timeout.CancelAfter(TimeSpan.FromSeconds(Math.Max(1, server.ConnectTimeoutSeconds)));
            try
            {
                await client.ConnectAsync(server.Host, server.Port, timeout.Token).ConfigureAwait(false);
            }
            catch (OperationCanceledException) when (!ct.IsCancellationRequested)
            {
                throw new TimeoutException("timeout");
            }
        }

        client.NoDelay = true;
        var stream = client.GetStream();
        _serverSender = async (payload, token) =>
        {
            await _serverWriteGate.WaitAsync(token).ConfigureAwait(false);
            try
            {
                await stream.WriteAsync(payload, token).ConfigureAwait(false);
            }
            finally
            {
                _serverWriteGate.Release();
            }
        };
        _serverState = ServerState.Connected;
        _connectedSince = _time.GetLocalNow();
        _lastError = null;
        _log.Write(LogStatus.Ok, $"Connected to server {server.Host}:{server.Port}");
        RaiseStatus();

        var decoder = new FrameDecoder(server);
        var buffer = new byte[8192];
        var writer = _frames!.Writer;
        var signal = _workSignal!;
        var idle = TimeSpan.FromMilliseconds(server.IdleFlushMs);
        var idleNoticeLogged = false;
        Task<int>? read = null;
        while (true)
        {
            read ??= stream.ReadAsync(buffer, ct).AsTask();
            if (server.IdleFlushMs > 0 && decoder.HasPending)
            {
                // Server tidak mengirim terminator: setelah jeda tanpa data, proses yang sudah ada.
                var completed = await Task.WhenAny(read, Task.Delay(idle, _time, ct)).ConfigureAwait(false);
                if (completed != read)
                {
                    if (decoder.Flush() is { } pending)
                    {
                        if (!idleNoticeLogged)
                        {
                            idleNoticeLogged = true;
                            _log.Write(LogStatus.Info,
                                $"Data dari server tanpa terminator ({DescribeFraming(server)}); diproses setelah jeda {server.IdleFlushMs} ms");
                        }

                        await writer.WriteAsync(pending, ct).ConfigureAwait(false);
                        signal.Release();
                    }

                    continue;
                }
            }

            var n = await read.ConfigureAwait(false);
            read = null;
            if (n == 0)
            {
                if (decoder.Flush() is { } last)
                {
                    await writer.WriteAsync(last, ct).ConfigureAwait(false);
                    signal.Release();
                }

                return;
            }

            _stats.AddReceived(n);
            foreach (var frame in decoder.Push(buffer.AsSpan(0, n)))
            {
                // Jika antrean penuh, tunggu (TCP ikut menahan server) — tidak menumpuk di memori.
                await writer.WriteAsync(frame, ct).ConfigureAwait(false);
                signal.Release();
            }
        }
    }

    private static string DescribeFraming(ServerSettings server) => server.Framing switch
    {
        FrameMode.Line => "CR/LF",
        FrameMode.Etx => "ETX",
        FrameMode.StxEtx => "STX..ETX",
        FrameMode.Custom => ControlTokens.Visualize(ControlTokens.Decode(server.CustomTerminator)),
        _ => server.Framing.ToString(),
    };

    // ------------------------------------------------------------------ background

    private async Task BackgroundLoopAsync(CancellationToken ct)
    {
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(1), _time);
        while (await timer.WaitForNextTickAsync(ct).ConfigureAwait(false))
        {
            try
            {
                _stats.Sample(_time.GetTimestamp(), _time.TimestampFrequency);
                StatsUpdated?.Invoke(_stats.Snapshot());
                CheckDayRollover();
                ScheduleRetries();
                var dropped = Interlocked.Exchange(ref _feedbackDropped, 0);
                if (dropped > 0)
                {
                    _log.Write(LogStatus.Warn, $"Antrean feedback penuh: {dropped} laporan terlama dibuang");
                }
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                _log.Write(LogStatus.Error, $"Background: {ex.Message}");
            }
        }
    }

    private void CheckDayRollover()
    {
        var previous = _counter.RollDayIfNeeded(_settings.Counter);
        if (previous is null)
        {
            return;
        }

        _log.Write(LogStatus.Info, $"Ganti hari ({previous} -> {_counter.Snapshot().BusinessDate}): semua counter direset");
        _log.Prune(_settings.General.LogRetentionDays);
        Enqueue(new ResyncItem("ganti hari"));
    }

    /// <summary>Jadwalkan koneksi ulang printer / pengiriman ulang job yang belum siap.</summary>
    private void ScheduleRetries()
    {
        var s = _settings;
        if (!_running || !s.Printer.Enabled)
        {
            return;
        }

        var now = _time.GetUtcNow();
        if (WaitingForPrinter && PrinterConnected)
        {
            // Printer belum start: cek berkala, kirim begitu printer start.
            if (now - _lastReadyCheck >= TimeSpan.FromSeconds(Math.Max(1, s.Printer.ReadyPollSeconds)))
            {
                _lastReadyCheck = now;
                Enqueue(new ReadyCheckItem());
            }

            return;
        }

        if (now - _lastPrinterAttempt < TimeSpan.FromSeconds(Math.Max(1, s.Printer.ReconnectDelaySeconds)))
        {
            return;
        }

        if (!PrinterConnected)
        {
            _lastPrinterAttempt = now;
            Enqueue(new EnsurePrinterItem());
        }
        else if (_job is not null && !_jobArmed && UsesPrintCounting(s))
        {
            _lastPrinterAttempt = now;
            Enqueue(new ResyncItem("coba ulang"));
        }
    }

    private void RaiseStatus() => StatusChanged?.Invoke(Status);

    private void Publish(TransferRecord record)
    {
        const int limit = TransferLog.ViewLimit;
        lock (_recordsGate)
        {
            _records.AddFirst(record);
            while (_records.Count > limit)
            {
                _records.RemoveLast();
            }
        }

        RecordProcessed?.Invoke(record);
    }

    private void AddFeedback(FeedbackRecord record)
    {
        lock (_recordsGate)
        {
            _feedbackRecords.AddFirst(record);
            while (_feedbackRecords.Count > TransferLog.ViewLimit)
            {
                _feedbackRecords.RemoveLast();
            }
        }

        FeedbackChanged?.Invoke(record);
    }

    /// <summary>Ubah status feedback yang masih tampil (50 terakhir); yang lebih lama hanya tercatat di log.</summary>
    private void UpdateFeedback(long seq, FeedbackState state, DateTimeOffset? sentAt)
    {
        FeedbackRecord? updated = null;
        lock (_recordsGate)
        {
            for (var node = _feedbackRecords.First; node is not null; node = node.Next)
            {
                if (node.Value.Seq == seq)
                {
                    updated = node.Value with { State = state, SentAt = sentAt };
                    node.Value = updated;
                    break;
                }
            }
        }

        if (updated is not null)
        {
            FeedbackChanged?.Invoke(updated);
        }
    }

    /// <summary>Satu laporan di antrean pengirim feedback.</summary>
    private sealed record FeedbackItem(long Seq, byte[] Payload);

    private static bool IsTransportError(Exception ex) =>
        ex is IOException or SocketException or TimeoutException or InvalidOperationException
            or ObjectDisposedException or UnauthorizedAccessException or ArgumentException;

    private static string Trimmed(string text) => ControlTokens.Visualize(text.TrimEnd('\r', '\n'));
}
