using Android.App;
using Android.Content;
using Android.Content.PM;
using Android.Graphics;
using Android.OS;
using Android.Provider;
using Android.Views;
using Android.Webkit;
using Android.Widget;

namespace Sharx.Android;

/// <summary>
/// Layar penuh berisi UI yang sama dengan aplikasi desktop (WebView ke server lokal <see cref="AppHost"/>).
/// </summary>
[Activity(
    Label = "@string/app_name",
    MainLauncher = true,
    Exported = true,
    Theme = "@style/SharxTheme",
    LaunchMode = LaunchMode.SingleTask,
    ScreenOrientation = ScreenOrientation.SensorLandscape,
    ConfigurationChanges = ConfigChanges.Orientation | ConfigChanges.ScreenSize | ConfigChanges.ScreenLayout
        | ConfigChanges.SmallestScreenSize | ConfigChanges.Keyboard | ConfigChanges.KeyboardHidden
        | ConfigChanges.Navigation | ConfigChanges.UiMode)]
public sealed class MainActivity : Activity
{
    private WebView? _web;
    private TextView? _status;
    private bool _serviceRequested;

    protected override void OnCreate(Bundle? savedInstanceState)
    {
        base.OnCreate(savedInstanceState);
        Guard("Layar penuh", () =>
        {
            Window?.AddFlags(WindowManagerFlags.KeepScreenOn);
            EnterFullScreen();
        });

        // Pesan "memuat" tampil dulu; WebView menggantikannya setelah UI siap.
        _status = new TextView(this)
        {
            Text = "SHARX Data Transfer\nMemuat...",
            Gravity = GravityFlags.Center,
            TextSize = 20,
        };
        _status.SetTextColor(Color.White);
        _status.SetBackgroundColor(Color.Rgb(6, 18, 42));
        SetContentView(_status);

        var lastCrash = CrashLog.TakeLast();
        if (lastCrash is not null)
        {
            ShowReport("Aplikasi sempat berhenti", "Kirim laporan ini ke pembuat aplikasi:\n\n" + lastCrash);
        }

        Guard("Izin notifikasi", RequestNotificationPermission);
        _ = LoadUiAsync();
    }

    protected override void OnResume()
    {
        base.OnResume();
        // Foreground service dimulai setelah layar tampil (beberapa ROM menolaknya saat layar baru dibuat).
        if (!_serviceRequested)
        {
            _serviceRequested = true;
            SharxService.Begin(this);
        }
    }

    public override void OnWindowFocusChanged(bool hasFocus)
    {
        base.OnWindowFocusChanged(hasFocus);
        if (hasFocus)
        {
            Guard("Layar penuh", EnterFullScreen);
        }
    }

    /// <summary>Tombol Back = keluar (dengan konfirmasi jika transfer berjalan), seperti tombol tutup di desktop.</summary>
    public override bool OnKeyDown(Keycode keyCode, KeyEvent? e)
    {
        if (keyCode != Keycode.Back)
        {
            return base.OnKeyDown(keyCode, e);
        }

        new AlertDialog.Builder(this)
            .SetTitle("Tutup aplikasi?")!
            .SetMessage(AppHost.TransferRunning
                ? "Transfer sedang berjalan. Menutup aplikasi akan menghentikan transfer ke printer."
                : "Aplikasi SHARX Data Transfer akan ditutup.")!
            .SetNegativeButton("Batal", (_, _) => { })!
            .SetPositiveButton("Tutup", (_, _) =>
            {
                AppHost.Stop();
                SharxService.End(this);
                FinishAndRemoveTask();
            })!
            .Show();
        return true;
    }

    protected override void OnDestroy()
    {
        Guard("Tutup WebView", () => _web?.Destroy());
        _web = null;
        base.OnDestroy();
    }

    private async Task LoadUiAsync()
    {
        try
        {
            var address = await AppHost.EnsureStarted(this).ConfigureAwait(true);
            var web = CreateWebView();
            SetContentView(web);
            _web = web;
            _status = null;
            web.LoadUrl(address + "/");
        }
        catch (Exception ex)
        {
            CrashLog.Write("Memulai aplikasi", ex);
            ShowReport("SHARX Data Transfer gagal dibuka", ex.ToString());
            if (_status is not null)
            {
                _status.Text = "SHARX Data Transfer gagal dibuka.\nTutup lalu buka lagi aplikasi.";
            }
        }
    }

    private WebView CreateWebView()
    {
        var web = new WebView(this);
        web.SetBackgroundColor(Color.Rgb(6, 18, 42));
        var settings = web.Settings;
        settings.JavaScriptEnabled = true;
        settings.DomStorageEnabled = true;
        settings.TextZoom = 100; // ukuran font sistem tidak mengubah tata letak dashboard
        settings.BuiltInZoomControls = true;
        settings.DisplayZoomControls = false;
        settings.SetSupportZoom(true);
        web.SetWebViewClient(new LocalOnlyClient());
        web.SetDownloadListener(new LogDownloadListener(this));
        return web;
    }

    /// <summary>Langkah khusus Android yang gagal dicatat, tidak menutup aplikasi.</summary>
    private static void Guard(string step, Action action)
    {
        try
        {
            action();
        }
        catch (Exception ex)
        {
            CrashLog.Write(step, ex);
        }
    }

    /// <summary>Tampilkan teks error yang bisa disalin.</summary>
    private void ShowReport(string title, string text)
    {
        Guard("Tampilkan laporan", () =>
        {
            var body = new TextView(this) { Text = text, TextSize = 12 };
            body.SetTextIsSelectable(true);
            body.SetPadding(40, 24, 40, 24);
            var scroll = new ScrollView(this);
            scroll.AddView(body);
            new AlertDialog.Builder(this)
                .SetTitle(title)!
                .SetView(scroll)!
                .SetNegativeButton("Tutup", (_, _) => { })!
                .SetPositiveButton("Salin", (_, _) =>
                {
                    if (GetSystemService(ClipboardService) is ClipboardManager clipboard)
                    {
                        clipboard.PrimaryClip = ClipData.NewPlainText("SHARX error", text);
                        Toast.MakeText(this, "Laporan disalin", ToastLength.Short)?.Show();
                    }
                })!
                .Show();
        });
    }

    private void RequestNotificationPermission()
    {
        if (OperatingSystem.IsAndroidVersionAtLeast(33)
            && CheckSelfPermission(global::Android.Manifest.Permission.PostNotifications) != Permission.Granted)
        {
            RequestPermissions([global::Android.Manifest.Permission.PostNotifications], 1);
        }
    }

    /// <summary>Tanpa status bar / navigation bar; geser dari tepi untuk memunculkan sementara.</summary>
    private void EnterFullScreen()
    {
        if (Window is not { } window)
        {
            return;
        }

        if (OperatingSystem.IsAndroidVersionAtLeast(30))
        {
            window.SetDecorFitsSystemWindows(false);
            if (window.InsetsController is { } insets)
            {
                insets.Hide(WindowInsets.Type.SystemBars());
                insets.SystemBarsBehavior = (int)WindowInsetsControllerBehavior.ShowTransientBarsBySwipe;
            }

            return;
        }

#pragma warning disable CS0618 // Flag layar penuh lama untuk Android 10 ke bawah.
        window.DecorView.SystemUiVisibility = (StatusBarVisibility)(
            SystemUiFlags.ImmersiveSticky | SystemUiFlags.Fullscreen | SystemUiFlags.HideNavigation
            | SystemUiFlags.LayoutFullscreen | SystemUiFlags.LayoutHideNavigation | SystemUiFlags.LayoutStable);
#pragma warning restore CS0618
    }

    /// <summary>Hanya UI lokal yang dibuka di dalam aplikasi; tautan lain dibuka di browser.</summary>
    private sealed class LocalOnlyClient : WebViewClient
    {
        public override bool ShouldOverrideUrlLoading(WebView? view, IWebResourceRequest? request)
        {
            var url = request?.Url;
            if (url is null || url.Host is "127.0.0.1" or "localhost")
            {
                return false;
            }

            try
            {
                view?.Context?.StartActivity(new Intent(Intent.ActionView, url).AddFlags(ActivityFlags.NewTask));
            }
            catch (Exception ex)
            {
                CrashLog.Write("Buka tautan", ex);
            }

            return true;
        }
    }

    /// <summary>Download log harian (Log History) disimpan ke folder Download perangkat.</summary>
    private sealed class LogDownloadListener(Activity activity) : Java.Lang.Object, IDownloadListener
    {
        public void OnDownloadStart(string? url, string? userAgent, string? contentDisposition, string? mimetype, long contentLength)
        {
            if (url is null)
            {
                return;
            }

            var name = URLUtil.GuessFileName(url, contentDisposition, mimetype) ?? "sharx-log.txt";
            _ = Task.Run(async () =>
            {
                string message;
                try
                {
                    // Handler .NET (bukan HTTP Android) untuk server lokal aplikasi sendiri.
                    using var http = new HttpClient(new SocketsHttpHandler());
                    var bytes = await http.GetByteArrayAsync(url).ConfigureAwait(false);
                    message = $"Tersimpan: {Save(name, bytes)}";
                }
                catch (Exception ex)
                {
                    CrashLog.Write("Download log", ex);
                    message = $"Download gagal: {ex.Message}";
                }

                activity.RunOnUiThread(() => Toast.MakeText(activity, message, ToastLength.Long)?.Show());
            });
        }

        private string Save(string name, byte[] bytes)
        {
            if (OperatingSystem.IsAndroidVersionAtLeast(29))
            {
                var values = new ContentValues();
                values.Put(MediaStore.IMediaColumns.DisplayName, name);
                values.Put(MediaStore.IMediaColumns.MimeType, "text/plain");
                values.Put(MediaStore.IMediaColumns.RelativePath, global::Android.OS.Environment.DirectoryDownloads);
                var resolver = activity.ContentResolver ?? throw new IOException("ContentResolver tidak tersedia");
                var uri = resolver.Insert(MediaStore.Downloads.ExternalContentUri, values)
                    ?? throw new IOException("File download tidak bisa dibuat");
                using var output = resolver.OpenOutputStream(uri) ?? throw new IOException("File download tidak bisa ditulis");
                output.Write(bytes, 0, bytes.Length);
                return $"Download/{name}";
            }

            var folder = activity.GetExternalFilesDir(global::Android.OS.Environment.DirectoryDownloads)?.AbsolutePath
                ?? activity.FilesDir?.AbsolutePath
                ?? throw new IOException("Folder download tidak tersedia");
            var path = System.IO.Path.Combine(folder, name);
            File.WriteAllBytes(path, bytes);
            return path;
        }
    }
}
