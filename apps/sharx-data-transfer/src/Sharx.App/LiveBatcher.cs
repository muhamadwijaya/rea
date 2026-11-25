using Sharx.Core.Logging;
using Sharx.Core.Transfer;

namespace Sharx.App;

/// <summary>
/// Menggabungkan event live (data, log, status, statistik, counter) dan mengirimnya ke UI paling sering
/// setiap 250 ms, maksimal 50 data + 50 log + 50 feedback per kiriman. Seberapa pun derasnya data, beban UI tetap kecil.
/// </summary>
public sealed class LiveBatcher : IAsyncDisposable
{
    private static readonly TimeSpan Interval = TimeSpan.FromMilliseconds(250);

    private readonly LiveHub _hub;
    private readonly Func<object> _counterView;
    private readonly object _gate = new();
    private readonly Queue<TransferRecord> _records = new();
    private readonly Queue<LogEntry> _logs = new();
    private readonly Queue<FeedbackRecord> _feedback = new();
    private readonly CancellationTokenSource _cts = new();
    private readonly Task _loop;
    private TransferStatus? _status;
    private StatsSnapshot? _stats;
    private bool _counterChanged;

    public LiveBatcher(TransferService service, LiveHub hub, Func<object> counterView)
    {
        _hub = hub;
        _counterView = counterView;
        service.StatusChanged += status => Set(() => _status = status);
        service.StatsUpdated += stats => Set(() => _stats = stats);
        service.Counter.Changed += () => Set(() => _counterChanged = true);
        service.RecordProcessed += record => Set(() => Keep(_records, record));
        service.Log.Added += entry => Set(() => Keep(_logs, entry));
        service.FeedbackChanged += record => Set(() => Keep(_feedback, record));
        _loop = Task.Run(() => LoopAsync(_cts.Token));
    }

    public async ValueTask DisposeAsync()
    {
        await _cts.CancelAsync().ConfigureAwait(false);
        try
        {
            await _loop.ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            // Selesai.
        }

        _cts.Dispose();
    }

    private void Set(Action change)
    {
        lock (_gate)
        {
            change();
        }
    }

    private static void Keep<T>(Queue<T> queue, T item)
    {
        queue.Enqueue(item);
        while (queue.Count > TransferLog.ViewLimit)
        {
            queue.Dequeue();
        }
    }

    private async Task LoopAsync(CancellationToken ct)
    {
        using var timer = new PeriodicTimer(Interval);
        while (await timer.WaitForNextTickAsync(ct).ConfigureAwait(false))
        {
            TransferStatus? status;
            StatsSnapshot? stats;
            TransferRecord[] records;
            LogEntry[] logs;
            FeedbackRecord[] feedback;
            bool counter;
            lock (_gate)
            {
                status = _status;
                stats = _stats;
                records = [.. _records];
                logs = [.. _logs];
                feedback = [.. _feedback];
                counter = _counterChanged;
                _status = null;
                _stats = null;
                _records.Clear();
                _logs.Clear();
                _feedback.Clear();
                _counterChanged = false;
            }

            if (_hub.ClientCount == 0
                || (status is null && stats is null && records.Length == 0 && logs.Length == 0
                    && feedback.Length == 0 && !counter))
            {
                continue;
            }

            try
            {
                _hub.Broadcast("batch", new
                {
                    status,
                    stats,
                    records,
                    logs,
                    // Urutan lama → baru; Seq yang sama = perubahan status (Queued → Sent / Dropped).
                    feedback,
                    counter = counter ? _counterView() : null,
                });
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                // Satu kiriman UI yang gagal tidak boleh menghentikan update berikutnya.
            }
        }
    }
}
