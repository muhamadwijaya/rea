using System.Globalization;
using System.Text;
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

/// <summary>Log transfer: disimpan di memori (untuk tampilan live) dan di file harian <c>logs/yyyy-MM-dd.log</c>.</summary>
public sealed class TransferLog
{
    private const int Capacity = 500;
    private static readonly Encoding Utf8NoBom = new UTF8Encoding(encoderShouldEmitUTF8Identifier: false);
    private readonly object _gate = new();
    private readonly LinkedList<LogEntry> _recent = new();
    private readonly string? _directory;
    private readonly TimeProvider _time;
    private long _nextId;

    public TransferLog(string? directory, TimeProvider time)
    {
        _directory = directory;
        _time = time;
        if (directory is not null)
        {
            Directory.CreateDirectory(directory);
        }
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

            AppendToFile(entry);
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

    /// <summary>Baca log satu hari dari file.</summary>
    public IReadOnlyList<LogEntry> ReadDay(string date)
    {
        if (_directory is null
            || !DateTime.TryParseExact(date, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out _))
        {
            return [];
        }

        var path = Path.Combine(_directory, date + ".log");
        if (!File.Exists(path))
        {
            return [];
        }

        var entries = new List<LogEntry>();
        string[] lines;
        lock (_gate)
        {
            using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
            using var reader = new StreamReader(stream, Encoding.UTF8);
            lines = reader.ReadToEnd().Split('\n', StringSplitOptions.RemoveEmptyEntries);
        }

        long id = 0;
        foreach (var line in lines)
        {
            var parts = line.TrimEnd('\r').Split('\t', 3);
            if (parts.Length == 3
                && DateTimeOffset.TryParse(parts[0], CultureInfo.InvariantCulture, DateTimeStyles.None, out var time)
                && Enum.TryParse<LogStatus>(parts[1], true, out var status))
            {
                entries.Add(new LogEntry(++id, time, status, parts[2]));
            }
        }

        return entries;
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
            catch (IOException)
            {
                // File sedang dibuka program lain; coba lagi lain waktu.
            }
        }

        return removed;
    }

    private void AppendToFile(LogEntry entry)
    {
        if (_directory is null)
        {
            return;
        }

        try
        {
            var path = Path.Combine(_directory, entry.Time.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture) + ".log");
            var line = $"{entry.Time.ToString("O", CultureInfo.InvariantCulture)}\t{entry.Status}\t{entry.Message}\n";
            File.AppendAllText(path, line, Utf8NoBom);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            // Log file gagal ditulis: entri tetap tersedia di memori.
        }
    }
}
