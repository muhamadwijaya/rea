using Android.App;
using Android.Content;
using Android.Content.PM;
using Android.Net.Wifi;
using Android.OS;
using Sharx.App;

namespace Sharx.Android;

/// <summary>
/// Foreground service yang memegang <see cref="SharxRuntime"/> dan server UI lokal: transfer ke printer dan
/// server tetap berjalan saat layar mati atau aplikasi ada di belakang (seperti jendela desktop yang di-minimize).
/// </summary>
[Service(Exported = false, ForegroundServiceType = ForegroundService.TypeConnectedDevice)]
public sealed class SharxService : Service
{
    private const string ChannelId = "sharx_transfer";
    private const int NotificationId = 1;

    private static readonly object Gate = new();
    private static TaskCompletionSource<string> _ready = NewReady();
    private static SharxRuntime? _runtime;
    private static EmbeddedServer? _server;

    private PowerManager.WakeLock? _wakeLock;
    private WifiManager.WifiLock? _wifiLock;

    /// <summary>Alamat UI lokal setelah service siap (atau error jika gagal dibuka).</summary>
    public static Task<string> Ready
    {
        get
        {
            lock (Gate)
            {
                return _ready.Task;
            }
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

    public static string DataDirectory(Context context) =>
        Path.Combine(context.FilesDir?.AbsolutePath ?? System.Environment.GetFolderPath(System.Environment.SpecialFolder.Personal), "data");

    public static void Begin(Context context)
    {
        context.StartForegroundService(new Intent(context, typeof(SharxService)));
    }

    public override IBinder? OnBind(Intent? intent) => null;

    public override StartCommandResult OnStartCommand(Intent? intent, StartCommandFlags flags, int startId)
    {
        ShowNotification();
        lock (Gate)
        {
            if (_runtime is null)
            {
                StartRuntime();
            }
        }

        AcquireLocks();
        return StartCommandResult.Sticky;
    }

    public override void OnDestroy()
    {
        lock (Gate)
        {
            var server = _server;
            var runtime = _runtime;
            _server = null;
            _runtime = null;
            if (_ready.Task.IsCompleted)
            {
                _ready = NewReady();
            }

            // Tutup koneksi printer / server dengan rapi (maks. 10 detik) sebelum proses berhenti.
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

        _wifiLock?.Release();
        _wakeLock?.Release();
        _wifiLock = null;
        _wakeLock = null;
        base.OnDestroy();
    }

    private static TaskCompletionSource<string> NewReady() => new(TaskCreationOptions.RunContinuationsAsynchronously);

    private void StartRuntime()
    {
        var dataDirectory = DataDirectory(this);
        try
        {
            SharxRuntime.InstallCrashGuards(dataDirectory);
            var runtime = new SharxRuntime(dataDirectory);
            var assets = Assets;
            var server = new EmbeddedServer(runtime, path => OpenAsset(assets, path));
            server.Start();
            runtime.Start(server.Address);
            _runtime = runtime;
            _server = server;
            _ready.TrySetResult(server.Address);
        }
        catch (Exception ex)
        {
            SharxRuntime.WriteCrash(dataDirectory, "AndroidStartup", ex);
            _ready.TrySetException(ex);
        }
    }

    /// <summary>File UI dari asset aplikasi (salinan wwwroot desktop).</summary>
    private static Stream? OpenAsset(global::Android.Content.Res.AssetManager? assets, string path)
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

    private void ShowNotification()
    {
        var manager = (NotificationManager?)GetSystemService(NotificationService);
        manager?.CreateNotificationChannel(new NotificationChannel(ChannelId, "Transfer data", NotificationImportance.Low)
        {
            Description = "SHARX Data Transfer berjalan di latar belakang",
        });

        var open = PendingIntent.GetActivity(
            this,
            0,
            new Intent(this, typeof(MainActivity)).SetFlags(ActivityFlags.SingleTop),
            PendingIntentFlags.Immutable | PendingIntentFlags.UpdateCurrent);
        var notification = new Notification.Builder(this, ChannelId)
            .SetContentTitle("SHARX Data Transfer")
            .SetContentText("Transfer data ke printer dan server aktif")
            .SetSmallIcon(Resource.Mipmap.appicon)
            .SetOngoing(true)
            .SetContentIntent(open)
            .Build();

        if (OperatingSystem.IsAndroidVersionAtLeast(29))
        {
            StartForeground(NotificationId, notification, ForegroundService.TypeConnectedDevice);
        }
        else
        {
            StartForeground(NotificationId, notification);
        }
    }

    /// <summary>CPU dan Wi-Fi tidak tidur selama aplikasi berjalan (koneksi printer / server stabil).</summary>
    private void AcquireLocks()
    {
        if (_wakeLock is null && GetSystemService(PowerService) is PowerManager power)
        {
            _wakeLock = power.NewWakeLock(WakeLockFlags.Partial, "sharx:transfer");
            _wakeLock?.SetReferenceCounted(false);
            _wakeLock?.Acquire();
        }

        if (_wifiLock is null && ApplicationContext?.GetSystemService(WifiService) is WifiManager wifi)
        {
#pragma warning disable CS0618 // FullHighPerf hanya dipakai di bawah Android 10.
            var mode = OperatingSystem.IsAndroidVersionAtLeast(29)
                ? global::Android.Net.WifiMode.FullLowLatency
                : global::Android.Net.WifiMode.FullHighPerf;
#pragma warning restore CS0618
            _wifiLock = wifi.CreateWifiLock(mode, "sharx:transfer");
            _wifiLock?.SetReferenceCounted(false);
            _wifiLock?.Acquire();
        }
    }
}
