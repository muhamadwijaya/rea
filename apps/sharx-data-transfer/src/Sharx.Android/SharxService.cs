using Android.App;
using Android.Content;
using Android.Content.PM;
using Android.Net.Wifi;
using Android.OS;

namespace Sharx.Android;

/// <summary>
/// Foreground service yang menjaga proses tetap hidup: transfer ke printer dan server tetap berjalan saat
/// layar mati atau aplikasi di belakang (seperti jendela desktop yang di-minimize). Runtime-nya sendiri
/// dipegang <see cref="AppHost"/>; jika service ditolak sistem, aplikasi tetap berjalan selama terbuka.
/// </summary>
[Service(Exported = false, ForegroundServiceType = ForegroundService.TypeConnectedDevice)]
public sealed class SharxService : Service
{
    private const string ChannelId = "sharx_transfer";
    private const int NotificationId = 1;

    private PowerManager.WakeLock? _wakeLock;
    private WifiManager.WifiLock? _wifiLock;

    /// <summary>Minta service berjalan; gagal (dibatasi sistem / ROM) hanya dicatat.</summary>
    public static void Begin(Context context)
    {
        try
        {
            context.StartForegroundService(new Intent(context, typeof(SharxService)));
        }
        catch (Exception ex)
        {
            CrashLog.Write("Start foreground service", ex);
        }
    }

    public static void End(Context context)
    {
        try
        {
            context.StopService(new Intent(context, typeof(SharxService)));
        }
        catch (Exception ex)
        {
            CrashLog.Write("Stop service", ex);
        }
    }

    public override IBinder? OnBind(Intent? intent) => null;

    public override StartCommandResult OnStartCommand(Intent? intent, StartCommandFlags flags, int startId)
    {
        try
        {
            ShowNotification();
        }
        catch (Exception ex)
        {
            CrashLog.Write("Notifikasi foreground", ex);
        }

        try
        {
            AcquireLocks();
        }
        catch (Exception ex)
        {
            CrashLog.Write("Wake / Wi-Fi lock", ex);
        }

        // Service dibuat ulang sistem tanpa layar terbuka: tetap jalankan transfer.
        _ = AppHost.EnsureStarted(this);
        return StartCommandResult.Sticky;
    }

    public override void OnDestroy()
    {
        try
        {
            if (_wifiLock?.IsHeld == true)
            {
                _wifiLock.Release();
            }

            if (_wakeLock?.IsHeld == true)
            {
                _wakeLock.Release();
            }
        }
        catch (Exception ex)
        {
            CrashLog.Write("Lepas wake / Wi-Fi lock", ex);
        }

        _wifiLock = null;
        _wakeLock = null;
        base.OnDestroy();
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
