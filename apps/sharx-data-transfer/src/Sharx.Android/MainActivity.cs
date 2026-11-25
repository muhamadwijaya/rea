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
/// Layar penuh berisi UI yang sama dengan aplikasi desktop (WebView ke server lokal di <see cref="SharxService"/>).
/// </summary>
[Activity(
    Label = "@string/app_name",
    MainLauncher = true,
    Theme = "@style/SharxTheme",
    LaunchMode = LaunchMode.SingleTask,
    ScreenOrientation = ScreenOrientation.SensorLandscape,
    ConfigurationChanges = ConfigChanges.Orientation | ConfigChanges.ScreenSize | ConfigChanges.ScreenLayout
        | ConfigChanges.SmallestScreenSize | ConfigChanges.Keyboard | ConfigChanges.KeyboardHidden
        | ConfigChanges.Navigation | ConfigChanges.UiMode)]
public sealed class MainActivity : Activity
{
    private static readonly Color Background = Color.ParseColor("#06122A");
    private WebView? _web;

    protected override void OnCreate(Bundle? savedInstanceState)
    {
        base.OnCreate(savedInstanceState);
        Window?.AddFlags(WindowManagerFlags.KeepScreenOn);
        EnterFullScreen();

        var web = new WebView(this);
        web.SetBackgroundColor(Background);
        var settings = web.Settings;
        settings.JavaScriptEnabled = true;
        settings.DomStorageEnabled = true;
        settings.TextZoom = 100; // ukuran font sistem tidak mengubah tata letak dashboard
        settings.BuiltInZoomControls = true;
        settings.DisplayZoomControls = false;
        settings.SetSupportZoom(true);
        settings.MediaPlaybackRequiresUserGesture = true;
        web.SetWebViewClient(new LocalOnlyClient());
        web.SetDownloadListener(new LogDownloadListener(this));
        SetContentView(web);
        _web = web;

        RequestNotificationPermission();
        SharxService.Begin(this);
        _ = LoadUiAsync();
    }

    public override void OnWindowFocusChanged(bool hasFocus)
    {
        base.OnWindowFocusChanged(hasFocus);
        if (hasFocus)
        {
            EnterFullScreen();
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
            .SetMessage(SharxService.TransferRunning
                ? "Transfer sedang berjalan. Menutup aplikasi akan menghentikan transfer ke printer."
                : "Aplikasi SHARX Data Transfer akan ditutup.")!
            .SetNegativeButton("Batal", (_, _) => { })!
            .SetPositiveButton("Tutup", (_, _) =>
            {
                StopService(new Intent(this, typeof(SharxService)));
                FinishAndRemoveTask();
            })!
            .Show();
        return true;
    }

    protected override void OnDestroy()
    {
        _web?.Destroy();
        _web = null;
        base.OnDestroy();
    }

    private async Task LoadUiAsync()
    {
        try
        {
            var address = await SharxService.Ready.ConfigureAwait(true);
            _web?.LoadUrl(address + "/");
        }
        catch (Exception ex)
        {
            new AlertDialog.Builder(this)
                .SetTitle("SHARX Data Transfer gagal dibuka")!
                .SetMessage(ex.Message)!
                .SetPositiveButton("Tutup", (_, _) => FinishAndRemoveTask())!
                .Show();
        }
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

            view?.Context?.StartActivity(new Intent(Intent.ActionView, url).AddFlags(ActivityFlags.NewTask));
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
                catch (Exception ex) when (ex is HttpRequestException or IOException or Java.Lang.Exception)
                {
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
