using System.Reflection;
using System.Text.Json;
using Sharx.Core.Configuration;
using Sharx.Core.Counter;
using Sharx.Core.Logging;
using Sharx.Core.Transfer;
using Sharx.Core.Transport;

namespace Sharx.App;

public sealed class AlreadyRunningException(string dataDirectory)
    : Exception($"SHARX Data Transfer sudah berjalan (folder data: {dataDirectory}).");

/// <summary>
/// Satu instance aplikasi: settings, counter, log, transfer dan update live ke UI. Dipakai bersama oleh
/// host desktop / browser (ASP.NET Core) dan host Android (server lokal di aplikasi).
/// </summary>
public sealed class SharxRuntime : IAsyncDisposable
{
    private readonly FileStream _instanceLock;
    private readonly LiveBatcher _batcher;
    private int _disposed;

    public SharxRuntime(
        string dataDirectory,
        TimeProvider? time = null,
        Func<PrinterSettings, IPrinterLink>? printerFactory = null)
    {
        DataDirectory = dataDirectory;
        Directory.CreateDirectory(dataDirectory);
        _instanceLock = AcquireInstanceLock(dataDirectory);
        try
        {
            time ??= TimeProvider.System;
            var store = new SettingsStore(Path.Combine(dataDirectory, "settings.json"));
            var (settings, settingsWarning) = store.Load();
            Log = new TransferLog(Path.Combine(dataDirectory, "logs"), time);
            var counter = new CounterEngine(Path.Combine(dataDirectory, "counters.json"), time);
            Service = new TransferService(settings, store, counter, Log, new TransferStats(), time, printerFactory);
            Hub = new LiveHub(JsonOptions);
            _batcher = new LiveBatcher(Service, Hub, CounterView);
            foreach (var warning in new[] { settingsWarning, counter.LoadWarning })
            {
                if (warning is not null)
                {
                    Log.Write(LogStatus.Warn, warning);
                }
            }
        }
        catch
        {
            _instanceLock.Dispose();
            throw;
        }
    }

    public static string Version { get; } =
        typeof(SharxRuntime).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion
            .Split('+')[0] ?? "1.0.0";

    /// <summary>Format JSON API dan WebSocket (camelCase, enum sebagai teks).</summary>
    public static JsonSerializerOptions JsonOptions { get; } = JsonFile.CreateOptions(indented: false);

    /// <summary>Folder settings.json, counters.json dan logs/.</summary>
    public string DataDirectory { get; }

    public TransferService Service { get; }

    public TransferLog Log { get; }

    public LiveHub Hub { get; }

    /// <summary>UI sudah bisa dibuka di <paramref name="address"/>: mulai timer latar dan Auto Start.</summary>
    public void Start(string address)
    {
        Log.Write(LogStatus.Info, $"SHARX Data Transfer v{Version} siap ({address})");
        Service.StartBackground();
        if (Service.Settings.General.AutoStart)
        {
            Service.Start();
        }
    }

    /// <summary>Keadaan lengkap untuk UI yang baru terhubung (dibatasi 50 data / log / feedback terakhir).</summary>
    public object Snapshot() => new
    {
        Version,
        DataDirectory,
        Status = Service.Status,
        Stats = Service.Stats.Snapshot(),
        Counter = CounterView(),
        Records = Service.RecentRecords(TransferLog.ViewLimit),
        Log = Service.Log.Recent(TransferLog.ViewLimit),
        Feedback = Service.RecentFeedback(TransferLog.ViewLimit),
    };

    public object CounterView()
    {
        var state = Service.Counter.Snapshot();
        var digits = Service.Settings.Counter.Digits;
        var active = state.ActiveTemplate is not null && state.Templates.TryGetValue(state.ActiveTemplate, out var c) ? c : null;
        return new
        {
            state.BusinessDate,
            state.ActiveTemplate,
            state.ActiveOperator,
            ActiveValue = active is null ? null : CounterEngine.Format(active.Value, active.Width > 0 ? active.Width : digits),
            PersistError = Service.Counter.LastPersistError,
            Templates = state.Templates
                .OrderByDescending(kv => kv.Value.UpdatedAt)
                .Select(kv => new
                {
                    Template = kv.Key,
                    kv.Value.Value,
                    Text = CounterEngine.Format(kv.Value.Value, kv.Value.Width > 0 ? kv.Value.Width : digits),
                    kv.Value.Records,
                    kv.Value.LastOperator,
                    kv.Value.UpdatedAt,
                }),
        };
    }

    /// <summary>Hentikan transfer dan tulis sisa log; koneksi printer / server ditutup dengan rapi.</summary>
    public async ValueTask DisposeAsync()
    {
        if (Interlocked.Exchange(ref _disposed, 1) == 1)
        {
            return;
        }

        try
        {
            await _batcher.DisposeAsync().ConfigureAwait(false);
            await Service.DisposeAsync().ConfigureAwait(false);
            await Log.DisposeAsync().ConfigureAwait(false);
        }
        finally
        {
            await _instanceLock.DisposeAsync().ConfigureAwait(false);
        }
    }

    /// <summary>
    /// Catat error yang tidak tertangani ke <c>crash.log</c> dan cegah task yang gagal mematikan aplikasi.
    /// </summary>
    public static void InstallCrashGuards(string dataDirectory)
    {
        TaskScheduler.UnobservedTaskException += (_, e) =>
        {
            WriteCrash(dataDirectory, "UnobservedTaskException", e.Exception);
            e.SetObserved();
        };
        AppDomain.CurrentDomain.UnhandledException += (_, e) =>
            WriteCrash(dataDirectory, "UnhandledException", e.ExceptionObject as Exception);
    }

    public static void WriteCrash(string dataDirectory, string kind, Exception? ex)
    {
        try
        {
            Directory.CreateDirectory(dataDirectory);
            File.AppendAllText(
                Path.Combine(dataDirectory, "crash.log"),
                $"{DateTimeOffset.Now:O}\t{kind}\t{ex}\n\n");
        }
        catch (Exception logError) when (logError is IOException or UnauthorizedAccessException)
        {
            // Tidak bisa menulis log crash; jangan menambah error baru.
        }
    }

    private static FileStream AcquireInstanceLock(string dataDirectory)
    {
        try
        {
            return new FileStream(
                Path.Combine(dataDirectory, ".instance.lock"),
                FileMode.OpenOrCreate,
                FileAccess.ReadWrite,
                FileShare.None,
                1,
                FileOptions.DeleteOnClose);
        }
        catch (IOException)
        {
            throw new AlreadyRunningException(dataDirectory);
        }
    }
}
