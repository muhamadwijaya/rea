using System.Globalization;
using System.Text;
using System.Threading.Channels;
using Sharx.Core.Text;

namespace Sharx.Core.Logging;

public enum LogStatus
{
    Ok,
    Info,
    Warn,
    Error,
}

public sealed record LogEntry(long Id, DateTimeOffset Time, LogStatus Status, string Message);

/// <summary>
/// Log transfer: disimpan di memori (untuk tampilan live) dan di file harian <c>logs/yyyy-MM-dd.log</c>.
/// Penulisan file dilakukan di background (antrean terbatas) sehingga tidak pernah menahan transfer data.
/// </summary>
public sealed class TransferLog : IAsyncDisposable
{
    /// <summary>Jumlah baris terakhir yang ditampilkan di UI.</summary>
    public const int ViewLimit = 50;

    private const int Capacity = 200;
    private const int FileQueueCapacity = 100_000;
    private static readonly Encoding Utf8NoBom = new UTF8Encoding(encoderShouldEmitUTF8Identifier: false);

    private readonly object _gate = new();
    private readonly LinkedList<LogEntry> _recent = new();
    private readonly string? _directory;
    private readonly TimeProvider _time;
    private readonly Channel<LogEntry>? _fileQueue;
    private readonly Task _fileWriter = Task.CompletedTask;
    private long _nextId;
    private long _dropped;

    public TransferLog(string? directory, TimeProvider time)
    {
        _directory = directory;
        _time = time;
        if (directory is null)
        {
            return;
        }

        Directory.CreateDirectory(directory);
        _fileQueue = Channel.CreateBounded<LogEntry>(new BoundedChannelOptions(FileQueueCapacity)
        {
            SingleReader = true,
            FullMode = BoundedChannelFullMode.DropWrite,
        });
        _fileWriter = Task.Run(FileWriterLoopAsync);
    }

    public event Action<LogEntry>? Added;

    public LogEntry Write(LogStatus status, string message)
    {
        var now = _time.GetLocalNow();
        var clean = ControlTokens.Visualize(message ?? "");
        LogEntry entry;
        lock (_gate)
        {
            entry = new LogEntry(++_nextId, now, status, clean);
            _recent.AddLast(entry);
            if (_recent.Count > Capacity)
            {
                _recent.RemoveFirst();
            }
        }

        if (_fileQueue is not null && !_fileQueue.Writer.TryWrite(entry))
        {
            Interlocked.Increment(ref _dropped);
        }

        Added?.Invoke(entry);
        return entry;
    }

    public IReadOnlyList<LogEntry> Recent(int count)
    {
        lock (_gate)
        {
            return _recent.Skip(Math.Max(0, _recent.Count - count)).ToArray();
        }
    }

    /// <summary>Tanggal yang punya file log, terbaru dulu.</summary>
    public IReadOnlyList<string> Dates()
    {
        if (_directory is null || !Directory.Exists(_directory))
        {
            return [];
        }

        return Directory.EnumerateFiles(_directory, "*.log")
            .Select(Path.GetFileNameWithoutExtension)
            .Where(name => DateTime.TryParseExact(name, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out _))
            .Select(name => name!)
            .OrderDescending()
            .ToArray();
    }

    public sealed record DayPage(IReadOnlyList<LogEntry> Entries, int Matched);

    /// <summary>
    /// Baca log satu hari: hanya <paramref name="limit"/> baris terakhir yang cocok dengan filter
    /// yang dikembalikan (file dibaca berurutan, memori tetap kecil walau file besar).
    /// </summary>
    public DayPage ReadDay(string date, LogStatus? status = null, string? query = null, int limit = ViewLimit)
    {
        if (_directory is null
            || !DateTime.TryParseExact(date, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out _))
        {
            return new DayPage([], 0);
        }

        var path = Path.Combine(_directory, date + ".log");
        if (!File.Exists(path))
        {
            return new DayPage([], 0);
        }

        var tail = new Queue<LogEntry>(Math.Max(1, limit));
        var matched = 0;
        long id = 0;
        using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
        using var reader = new StreamReader(stream, Encoding.UTF8);
        while (reader.ReadLine() is { } line)
        {
            id++;
            if (!TryParseLine(line, id, out var entry)
                || (status is not null && entry.Status != status)
                || (!string.IsNullOrEmpty(query) && !entry.Message.Contains(query, StringComparison.OrdinalIgnoreCase)))
            {
                continue;
            }

            matched++;
            if (limit <= 0)
            {
                continue;
            }

            if (tail.Count == limit)
            {
                tail.Dequeue();
            }

            tail.Enqueue(entry);
        }

        return new DayPage([.. tail], matched);
    }

    /// <summary>Isi file log satu hari (untuk download).</summary>
    public IEnumerable<LogEntry> ReadAll(string date)
    {
        if (_directory is null
            || !DateTime.TryParseExact(date, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out _))
        {
            yield break;
        }

        var path = Path.Combine(_directory, date + ".log");
        if (!File.Exists(path))
        {
            yield break;
        }

        using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
        using var reader = new StreamReader(stream, Encoding.UTF8);
        long id = 0;
        while (reader.ReadLine() is { } line)
        {
            if (TryParseLine(line, ++id, out var entry))
            {
                yield return entry;
            }
        }
    }

    /// <summary>Hapus file log yang lebih tua dari <paramref name="days"/> hari.</summary>
    public int Prune(int days)
    {
        if (_directory is null || days <= 0)
        {
            return 0;
        }

        var cutoff = _time.GetLocalNow().Date.AddDays(-days);
        var removed = 0;
        foreach (var date in Dates())
        {
            var day = DateTime.ParseExact(date, "yyyy-MM-dd", CultureInfo.InvariantCulture);
            if (day >= cutoff)
            {
                continue;
            }

            try
            {
                File.Delete(Path.Combine(_directory, date + ".log"));
                removed++;
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
            {
                // File sedang dibuka program lain; coba lagi lain waktu.
            }
        }

        return removed;
    }

    /// <summary>Tunggu semua log tertulis ke file (dipakai saat aplikasi ditutup).</summary>
    public async ValueTask DisposeAsync()
    {
        _fileQueue?.Writer.TryComplete();
        try
        {
            await _fileWriter.WaitAsync(TimeSpan.FromSeconds(5)).ConfigureAwait(false);
        }
        catch (TimeoutException)
        {
            // Disk lambat: jangan tahan penutupan aplikasi.
        }
    }

    private static bool TryParseLine(string line, long id, out LogEntry entry)
    {
        entry = null!;
        var parts = line.TrimEnd('\r').Split('\t', 3);
        if (parts.Length != 3
            || !DateTimeOffset.TryParse(parts[0], CultureInfo.InvariantCulture, DateTimeStyles.None, out var time)
            || !Enum.TryParse<LogStatus>(parts[1], true, out var status))
        {
            return false;
        }

        entry = new LogEntry(id, time, status, parts[2]);
        return true;
    }

    /// <summary>Satu penulis file: membuka file harian sekali, menulis per batch, flush setiap batch.</summary>
    private async Task FileWriterLoopAsync()
    {
        var reader = _fileQueue!.Reader;
        StreamWriter? writer = null;
        string? currentDate = null;
        try
        {
            while (await reader.WaitToReadAsync().ConfigureAwait(false))
            {
                while (reader.TryRead(out var entry))
                {
                    var date = entry.Time.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
                    try
                    {
                        if (date != currentDate || writer is null)
                        {
                            writer?.Dispose();
                            writer = OpenDay(date);
                            currentDate = date;
                        }

                        writer.Write(entry.Time.ToString("O", CultureInfo.InvariantCulture));
                        writer.Write('\t');
                        writer.Write(entry.Status.ToString());
                        writer.Write('\t');
                        writer.Write(entry.Message);
                        writer.Write('\n');
                    }
                    catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
                    {
                        // Disk penuh / file terkunci: buang baris ini, coba buka ulang di baris berikutnya.
                        writer?.Dispose();
                        writer = null;
                        Interlocked.Increment(ref _dropped);
                    }
                }

                try
                {
                    writer?.Flush();
                }
                catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
                {
                    writer?.Dispose();
                    writer = null;
                }

                var dropped = Interlocked.Exchange(ref _dropped, 0);
                if (dropped > 0 && writer is not null)
                {
                    writer.Write($"{_time.GetLocalNow():O}\tWarn\t{dropped} baris log tidak tertulis ke file (antrean penuh / disk error)\n");
                }
            }
        }
        finally
        {
            writer?.Dispose();
        }
    }

    private StreamWriter OpenDay(string date)
    {
        var stream = new FileStream(
            Path.Combine(_directory!, date + ".log"),
            FileMode.Append,
            FileAccess.Write,
            FileShare.ReadWrite | FileShare.Delete);
        return new StreamWriter(stream, Utf8NoBom, 64 * 1024);
    }
}
