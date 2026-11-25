using Android.Content;
using Android.Content.Res;
using Sharx.App;

namespace Sharx.Android;

/// <summary>
/// Satu <see cref="SharxRuntime"/> + server UI lokal per proses. Dimulai dari layar utama (di thread latar,
/// bukan thread UI) dan dijaga tetap hidup oleh <see cref="SharxService"/>; kegagalan service tidak
/// menghentikan aplikasi.
/// </summary>
internal static class AppHost
{
    private static readonly object Gate = new();
    private static Task<string>? _start;
    private static SharxRuntime? _runtime;
    private static EmbeddedServer? _server;

    public static string DataDirectory(Context context) =>
        Path.Combine(context.FilesDir?.AbsolutePath ?? System.Environment.GetFolderPath(System.Environment.SpecialFolder.Personal), "data");

    /// <summary>Alamat UI lokal; memulai runtime jika belum berjalan (atau jika percobaan sebelumnya gagal).</summary>
    public static Task<string> EnsureStarted(Context context)
    {
        lock (Gate)
        {
            if (_start is { IsFaulted: false, IsCanceled: false })
            {
                return _start;
            }

            var app = context.ApplicationContext ?? context;
            var dataDirectory = DataDirectory(app);
            var assets = app.Assets;
            _start = Task.Run(() => Start(dataDirectory, assets));
            return _start;
        }
    }

    /// <summary>Transfer sedang berjalan (untuk konfirmasi saat keluar).</summary>
    public static bool TransferRunning
    {
        get
        {
            lock (Gate)
            {
                return _runtime?.Service.IsRunning == true;
            }
        }
    }

    /// <summary>Hentikan transfer dan server dengan rapi (maks. 10 detik).</summary>
    public static void Stop()
    {
        SharxRuntime? runtime;
        EmbeddedServer? server;
        lock (Gate)
        {
            runtime = _runtime;
            server = _server;
            _runtime = null;
            _server = null;
            _start = null;
        }

        Task.Run(async () =>
        {
            if (server is not null)
            {
                await server.DisposeAsync().ConfigureAwait(false);
            }

            if (runtime is not null)
            {
                await runtime.DisposeAsync().ConfigureAwait(false);
            }
        }).Wait(TimeSpan.FromSeconds(10));
    }

    private static string Start(string dataDirectory, AssetManager? assets)
    {
        SharxRuntime.InstallCrashGuards(dataDirectory);
        var runtime = new SharxRuntime(dataDirectory);
        EmbeddedServer server;
        try
        {
            server = new EmbeddedServer(runtime, path => OpenAsset(assets, path));
            server.Start();
        }
        catch
        {
            runtime.DisposeAsync().AsTask().Wait(TimeSpan.FromSeconds(5));
            throw;
        }

        runtime.Start(server.Address);
        lock (Gate)
        {
            _runtime = runtime;
            _server = server;
        }

        return server.Address;
    }

    /// <summary>File UI dari asset aplikasi (salinan wwwroot desktop).</summary>
    private static Stream? OpenAsset(AssetManager? assets, string path)
    {
        try
        {
            return assets?.Open($"wwwroot/{path}");
        }
        catch (Java.IO.IOException)
        {
            return null;
        }
    }
}

/// <summary>
/// Error yang menutup aplikasi disimpan ke file dan ditampilkan saat aplikasi dibuka lagi, supaya bisa
/// disalin dan dilaporkan tanpa kabel / adb.
/// </summary>
internal static class CrashLog
{
    private static readonly object Gate = new();
    private static string? _path;

    public static void Init(string filesDirectory) => _path = Path.Combine(filesDirectory, "last-crash.txt");

    private const string FatalMark = "[BERHENTI] ";

    /// <param name="fatal">true = error ini menutup aplikasi; false = langkah gagal, aplikasi tetap berjalan.</param>
    public static void Write(string kind, object? error, bool fatal = false)
    {
        if (_path is null)
        {
            return;
        }

        try
        {
            var release = global::Android.OS.Build.VERSION.Release;
            var api = (int)global::Android.OS.Build.VERSION.SdkInt;
            var device = global::Android.OS.Build.Manufacturer + " " + global::Android.OS.Build.Model;
            lock (Gate)
            {
                File.AppendAllText(
                    _path,
                    $"{(fatal ? FatalMark : "")}{DateTimeOffset.Now:yyyy-MM-dd HH:mm:ss} {kind}\nAndroid {release} (API {api}), {device}\n{error}\n\n");
            }
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            // Tidak bisa menulis; jangan menambah error baru.
        }
    }

    /// <summary>Isi laporan error terakhir (lalu dihapus) dan apakah ada error yang menutup aplikasi.</summary>
    public static (string Text, bool Fatal)? TakeLast()
    {
        if (_path is null)
        {
            return null;
        }

        try
        {
            lock (Gate)
            {
                if (!File.Exists(_path))
                {
                    return null;
                }

                var text = File.ReadAllText(_path);
                File.Delete(_path);
                return (text.Length > 20000 ? text[^20000..] : text, text.Contains(FatalMark, StringComparison.Ordinal));
            }
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            return null;
        }
    }
}
