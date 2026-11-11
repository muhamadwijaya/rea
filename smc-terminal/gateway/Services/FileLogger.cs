using System.Text;

namespace SmcTerminal.Gateway.Services;

/// <summary>Minimal file logger for the desktop app, which has no console.</summary>
public sealed class FileLoggerProvider : ILoggerProvider
{
    private readonly StreamWriter? _writer;
    private readonly object _gate = new();

    public FileLoggerProvider(string path)
    {
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            if (File.Exists(path) && new FileInfo(path).Length > 5_000_000)
            {
                File.Move(path, path + ".old", overwrite: true);
            }
            _writer = new StreamWriter(new FileStream(path, FileMode.Append, FileAccess.Write, FileShare.ReadWrite), Encoding.UTF8)
            {
                AutoFlush = true,
            };
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            _writer = null;
        }
    }

    public ILogger CreateLogger(string categoryName) => new FileLogger(this, categoryName);

    public void Dispose()
    {
        lock (_gate)
        {
            _writer?.Dispose();
        }
    }

    private void Write(string line)
    {
        lock (_gate)
        {
            _writer?.WriteLine(line);
        }
    }

    private sealed class FileLogger(FileLoggerProvider provider, string category) : ILogger
    {
        public IDisposable? BeginScope<TState>(TState state) where TState : notnull => null;

        public bool IsEnabled(LogLevel logLevel) => logLevel != LogLevel.None;

        public void Log<TState>(LogLevel logLevel, EventId eventId, TState state, Exception? exception, Func<TState, Exception?, string> formatter)
        {
            var line = $"{DateTimeOffset.Now:yyyy-MM-dd HH:mm:ss} {logLevel.ToString()[..4].ToUpperInvariant()} {category}: {formatter(state, exception)}";
            provider.Write(exception is null ? line : $"{line}{Environment.NewLine}{exception}");
        }
    }
}
