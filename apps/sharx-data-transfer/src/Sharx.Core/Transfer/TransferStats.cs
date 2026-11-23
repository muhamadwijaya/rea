namespace Sharx.Core.Transfer;

public sealed record StatsSnapshot(
    long ReceivedBytes,
    long SentBytes,
    double ReceivedRate,
    double SentRate,
    long RecordsReceived,
    long RecordsForwarded,
    long RecordsPrinted,
    long RecordsFailed);

/// <summary>Statistik transfer: total byte diterima dari server / dikirim ke printer dan kecepatannya.</summary>
public sealed class TransferStats
{
    private long _received;
    private long _sent;
    private long _records;
    private long _forwarded;
    private long _printed;
    private long _failed;
    private long _lastReceived;
    private long _lastSent;
    private long _lastSampleTicks;
    private double _receivedRate;
    private double _sentRate;

    public void AddReceived(int bytes) => Interlocked.Add(ref _received, bytes);
    public void AddSent(int bytes) => Interlocked.Add(ref _sent, bytes);
    public void RecordReceived() => Interlocked.Increment(ref _records);
    public void RecordForwarded() => Interlocked.Increment(ref _forwarded);
    public void RecordPrinted() => Interlocked.Increment(ref _printed);
    public void RecordFailed() => Interlocked.Increment(ref _failed);

    /// <summary>Hitung ulang kecepatan (byte/detik). Dipanggil periodik (±1 detik).</summary>
    public void Sample(long timestampTicks, long ticksPerSecond)
    {
        var received = Interlocked.Read(ref _received);
        var sent = Interlocked.Read(ref _sent);
        if (_lastSampleTicks != 0)
        {
            var seconds = (timestampTicks - _lastSampleTicks) / (double)ticksPerSecond;
            if (seconds > 0)
            {
                _receivedRate = Math.Max(0, (received - _lastReceived) / seconds);
                _sentRate = Math.Max(0, (sent - _lastSent) / seconds);
            }
        }

        _lastReceived = received;
        _lastSent = sent;
        _lastSampleTicks = timestampTicks;
    }

    public void Reset()
    {
        Interlocked.Exchange(ref _received, 0);
        Interlocked.Exchange(ref _sent, 0);
        Interlocked.Exchange(ref _records, 0);
        Interlocked.Exchange(ref _forwarded, 0);
        Interlocked.Exchange(ref _printed, 0);
        Interlocked.Exchange(ref _failed, 0);
        _lastReceived = 0;
        _lastSent = 0;
        _receivedRate = 0;
        _sentRate = 0;
    }

    public StatsSnapshot Snapshot() => new(
        Interlocked.Read(ref _received),
        Interlocked.Read(ref _sent),
        _receivedRate,
        _sentRate,
        Interlocked.Read(ref _records),
        Interlocked.Read(ref _forwarded),
        Interlocked.Read(ref _printed),
        Interlocked.Read(ref _failed));
}
