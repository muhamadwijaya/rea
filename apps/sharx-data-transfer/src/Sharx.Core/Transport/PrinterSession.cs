namespace Sharx.Core.Transport;

/// <summary>
/// Satu koneksi aktif ke printer: membaca terus-menerus (balasan + sinyal print start/end)
/// dan mengirim perintah satu per satu sambil menunggu balasannya.
/// </summary>
public sealed class PrinterSession : IAsyncDisposable
{
    private readonly IPrinterLink _link;
    private readonly IPrinterFrameParser _parser;
    private readonly Action<PrinterFrame> _onUnsolicited;
    private readonly Action<int>? _onBytesReceived;
    private readonly SemaphoreSlim _requestGate = new(1, 1);
    private readonly object _pendingGate = new();
    private readonly CancellationTokenSource _cts = new();
    private PendingRequest? _pending;
    private Task _readTask = Task.CompletedTask;
    private int _disposed;
    private long _lastActivity = Environment.TickCount64;
    private volatile string? _closeReason;

    private PrinterSession(
        IPrinterLink link,
        IPrinterFrameParser parser,
        Action<PrinterFrame> onUnsolicited,
        Action<int>? onBytesReceived)
    {
        _link = link;
        _parser = parser;
        _onUnsolicited = onUnsolicited;
        _onBytesReceived = onBytesReceived;
    }

    public string Endpoint => _link.Endpoint;

    /// <summary>Selesai saat koneksi printer terputus atau session ditutup.</summary>
    public Task Completion => _readTask;

    public bool IsConnected => !_readTask.IsCompleted && Volatile.Read(ref _disposed) == 0;

    /// <summary>Penyebab koneksi terputus dari sisi printer / jaringan (null selama terhubung).</summary>
    public string? CloseReason => _closeReason;

    /// <summary>Lama tidak ada data dikirim ke / diterima dari printer.</summary>
    public TimeSpan IdleFor => TimeSpan.FromMilliseconds(Environment.TickCount64 - Interlocked.Read(ref _lastActivity));

    private void Touch() => Interlocked.Exchange(ref _lastActivity, Environment.TickCount64);

    public static async Task<PrinterSession> OpenAsync(
        IPrinterLink link,
        IPrinterFrameParser parser,
        Action<PrinterFrame> onUnsolicited,
        Action<int>? onBytesReceived,
        CancellationToken ct)
    {
        var session = new PrinterSession(link, parser, onUnsolicited, onBytesReceived);
        try
        {
            await link.ConnectAsync(ct).ConfigureAwait(false);
        }
        catch
        {
            await link.DisposeAsync().ConfigureAwait(false);
            throw;
        }

        session._readTask = Task.Run(() => session.ReadLoopAsync(session._cts.Token));
        return session;
    }

    /// <summary>
    /// Kirim <paramref name="payload"/>. Jika <paramref name="isReply"/> diisi, tunggu frame yang cocok
    /// sampai <paramref name="timeout"/>; selain itu langsung kembali (null).
    /// </summary>
    public async Task<PrinterFrame?> RequestAsync(
        byte[] payload,
        Func<PrinterFrame, bool>? isReply,
        TimeSpan timeout,
        CancellationToken ct)
    {
        await _requestGate.WaitAsync(ct).ConfigureAwait(false);
        PendingRequest? pending = null;
        try
        {
            if (!IsConnected)
            {
                throw new IOException("Koneksi printer terputus");
            }

            if (isReply is not null)
            {
                pending = new PendingRequest(isReply);
                lock (_pendingGate)
                {
                    _pending = pending;
                }
            }

            // Printer yang berhenti menerima data tidak boleh menahan transfer selamanya.
            using (var sendTimeout = CancellationTokenSource.CreateLinkedTokenSource(ct))
            {
                sendTimeout.CancelAfter(timeout + TimeSpan.FromSeconds(5));
                try
                {
                    await _link.SendAsync(payload, sendTimeout.Token).ConfigureAwait(false);
                    Touch();
                }
                catch (OperationCanceledException) when (!ct.IsCancellationRequested)
                {
                    throw new TimeoutException("Printer tidak menerima data (kirim timeout)");
                }
            }

            if (pending is null)
            {
                return null;
            }

            try
            {
                return await pending.Completion.Task.WaitAsync(timeout, ct).ConfigureAwait(false);
            }
            catch (TimeoutException)
            {
                throw new TimeoutException($"Printer tidak membalas dalam {timeout.TotalMilliseconds:0} ms");
            }
        }
        finally
        {
            if (pending is not null)
            {
                lock (_pendingGate)
                {
                    if (ReferenceEquals(_pending, pending))
                    {
                        _pending = null;
                    }
                }
            }

            _requestGate.Release();
        }
    }

    public async ValueTask DisposeAsync()
    {
        if (Interlocked.Exchange(ref _disposed, 1) == 1)
        {
            return;
        }

        await _cts.CancelAsync().ConfigureAwait(false);
        await _link.DisposeAsync().ConfigureAwait(false);
        try
        {
            await _readTask.ConfigureAwait(false);
        }
        catch (Exception ex) when (ex is OperationCanceledException or IOException or ObjectDisposedException)
        {
            // Ditutup.
        }

        _cts.Dispose();
    }

    private async Task ReadLoopAsync(CancellationToken ct)
    {
        var buffer = new byte[4096];
        try
        {
            while (!ct.IsCancellationRequested)
            {
                var n = await _link.ReadAsync(buffer, ct).ConfigureAwait(false);
                if (n <= 0)
                {
                    if (!ct.IsCancellationRequested)
                    {
                        _closeReason = TcpTuning.DescribeClose(null, "printer");
                    }

                    break;
                }

                Touch();
                _onBytesReceived?.Invoke(n);
                foreach (var frame in _parser.Push(buffer.AsSpan(0, n)))
                {
                    Dispatch(frame);
                }
            }
        }
        catch (Exception ex) when (ex is IOException or ObjectDisposedException or OperationCanceledException
                                       or InvalidOperationException or UnauthorizedAccessException)
        {
            // Ditutup aplikasi (Dispose) bukan penyebab dari sisi printer.
            if (!ct.IsCancellationRequested)
            {
                _closeReason = TcpTuning.DescribeClose(ex, "printer");
            }
        }
        finally
        {
            lock (_pendingGate)
            {
                _pending?.Completion.TrySetException(new IOException("Koneksi printer terputus"));
                _pending = null;
            }
        }
    }

    private void Dispatch(PrinterFrame frame)
    {
        lock (_pendingGate)
        {
            if (_pending is not null && _pending.IsReply(frame))
            {
                _pending.Completion.TrySetResult(frame);
                _pending = null;
                return;
            }
        }

        _onUnsolicited(frame);
    }

    private sealed class PendingRequest(Func<PrinterFrame, bool> isReply)
    {
        public Func<PrinterFrame, bool> IsReply { get; } = isReply;

        public TaskCompletionSource<PrinterFrame> Completion { get; } =
            new(TaskCreationOptions.RunContinuationsAsynchronously);
    }
}
