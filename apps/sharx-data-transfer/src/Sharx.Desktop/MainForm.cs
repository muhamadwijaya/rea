using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text.Json;
using Microsoft.AspNetCore.Builder;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using Sharx.Core.Transfer;
using Sharx.App;
using Sharx.Web;

namespace Sharx.Desktop;

/// <summary>
/// Jendela tanpa title bar yang menampilkan UI web (WebView2) dan terbuka fullscreen.
/// Tombol minimize / maximize-restore / close ada di dalam UI dan dikirim lewat
/// <c>window.chrome.webview.postMessage</c>; saat restore, jendela bisa digeser dari bar atas UI
/// dan diubah ukurannya dari tepinya.
/// </summary>
internal sealed class MainForm : Form
{
    private const int ResizeBorder = 6;
    private const int WmNcHitTest = 0x84;
    private const int WmNcLButtonDown = 0xA1;
    private const int HtCaption = 2;
    private const int HtLeft = 10;
    private const int HtRight = 11;
    private const int HtTop = 12;
    private const int HtTopLeft = 13;
    private const int HtTopRight = 14;
    private const int HtBottom = 15;
    private const int HtBottomLeft = 16;
    private const int HtBottomRight = 17;

    private static readonly Color Background = Color.FromArgb(10, 18, 34);

    private readonly WebApplication _app;
    private readonly WebView2 _web = new() { Dock = DockStyle.Fill, DefaultBackgroundColor = Background };
    private bool _closing;

    public MainForm(WebApplication app)
    {
        _app = app;
        Text = "SHARX Data Transfer";
        FormBorderStyle = FormBorderStyle.None;
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = Background;
        MinimumSize = new Size(1024, 640);
        var area = Screen.PrimaryScreen?.WorkingArea ?? new Rectangle(0, 0, 1280, 800);
        Size = new Size(Math.Min(1600, area.Width), Math.Min(900, area.Height));
        // Tanpa border + maximize = menutupi seluruh layar (fullscreen, termasuk taskbar).
        WindowState = FormWindowState.Maximized;
        DoubleBuffered = true;
        try
        {
            Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        }
        catch (Exception ex) when (ex is ArgumentException or IOException)
        {
            // Pakai ikon default.
        }

        Controls.Add(_web);
        Load += async (_, _) =>
        {
            try
            {
                await StartAsync();
            }
            catch (Exception ex)
            {
                SharxRuntime.WriteCrash(SharxHostOptions.DefaultDataDirectory(), "Startup", ex);
                MessageBox.Show($"SHARX Data Transfer gagal dibuka:\n{ex.Message}", Text, MessageBoxButtons.OK, MessageBoxIcon.Error);
                Close();
            }
        };
        Resize += (_, _) =>
        {
            // Area tepi untuk mengubah ukuran hanya saat jendela tidak fullscreen.
            Padding = WindowState == FormWindowState.Normal ? new Padding(ResizeBorder) : Padding.Empty;
            PostWindowState();
        };
        FormClosing += OnFormClosing;
    }

    private async Task StartAsync()
    {
        await _app.StartAsync();
        _app.Lifetime.ApplicationStopping.Register(() =>
        {
            if (!_closing && IsHandleCreated)
            {
                BeginInvoke(Close);
            }
        });
        var url = SharxWebHost.ListeningAddress(_app);
        try
        {
            var userData = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "SharxDataTransfer",
                "WebView2");
            var environment = await CoreWebView2Environment.CreateAsync(null, userData);
            await _web.EnsureCoreWebView2Async(environment);
            var settings = _web.CoreWebView2.Settings;
            settings.AreDevToolsEnabled = Environment.GetEnvironmentVariable("SHARX_DEVTOOLS") == "1";
            settings.AreDefaultContextMenusEnabled = settings.AreDevToolsEnabled;
            settings.IsZoomControlEnabled = false;
            settings.IsStatusBarEnabled = false;
            _web.CoreWebView2.WebMessageReceived += OnWebMessage;
            _web.CoreWebView2.NewWindowRequested += (_, e) =>
            {
                e.Handled = true;
                OpenExternal(e.Uri);
            };
            _web.CoreWebView2.Navigate(url);
        }
        catch (Exception ex) when (ex is WebView2RuntimeNotFoundException or COMException)
        {
            MessageBox.Show(
                "Microsoft Edge WebView2 Runtime tidak ditemukan.\n" +
                "Install dari https://developer.microsoft.com/microsoft-edge/webview2/\n\n" +
                $"Sementara UI dibuka di browser: {url}\nTutup jendela ini untuk menghentikan aplikasi.",
                Text,
                MessageBoxButtons.OK,
                MessageBoxIcon.Warning);
            OpenExternal(url);
            WindowState = FormWindowState.Normal;
            FormBorderStyle = FormBorderStyle.FixedSingle;
            MinimumSize = Size.Empty;
            Size = new Size(420, 120);
            Controls.Remove(_web);
            Controls.Add(new Label
            {
                Text = $"SHARX Data Transfer berjalan di {url}",
                Dock = DockStyle.Fill,
                ForeColor = Color.White,
                TextAlign = ContentAlignment.MiddleCenter,
            });
        }
    }

    private void OnWebMessage(object? sender, CoreWebView2WebMessageReceivedEventArgs e)
    {
        string? command;
        try
        {
            using var json = JsonDocument.Parse(e.WebMessageAsJson);
            command = json.RootElement.TryGetProperty("cmd", out var cmd) ? cmd.GetString() : null;
        }
        catch (JsonException)
        {
            return;
        }

        switch (command)
        {
            case "state":
                PostWindowState();
                break;
            case "minimize":
                WindowState = FormWindowState.Minimized;
                break;
            case "maximize":
                WindowState = WindowState == FormWindowState.Maximized ? FormWindowState.Normal : FormWindowState.Maximized;
                break;
            case "close":
                Close();
                break;
            case "drag" when WindowState == FormWindowState.Normal:
                ReleaseCapture();
                SendMessage(Handle, WmNcLButtonDown, HtCaption, 0);
                break;
        }
    }

    /// <summary>UI menampilkan ikon maximize atau restore sesuai keadaan jendela.</summary>
    private void PostWindowState()
    {
        if (_web.CoreWebView2 is not { } core || WindowState == FormWindowState.Minimized)
        {
            return;
        }

        var state = WindowState == FormWindowState.Maximized ? "maximized" : "normal";
        core.PostWebMessageAsJson($"{{\"window\":\"{state}\"}}");
    }

    private async void OnFormClosing(object? sender, FormClosingEventArgs e)
    {
        if (_closing)
        {
            return;
        }

        if (e.CloseReason == CloseReason.UserClosing && TransferRunning())
        {
            var answer = MessageBox.Show(
                this,
                "Transfer sedang berjalan. Menutup aplikasi akan menghentikan transfer ke printer.\n\nTutup aplikasi?",
                Text,
                MessageBoxButtons.YesNo,
                MessageBoxIcon.Warning,
                MessageBoxDefaultButton.Button2);
            if (answer != DialogResult.Yes)
            {
                e.Cancel = true;
                return;
            }
        }

        // Hentikan transfer & web server dulu (menutup koneksi printer/server dengan rapi).
        e.Cancel = true;
        _closing = true;
        Hide();
        try
        {
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(10));
            await _app.StopAsync(timeout.Token);
            await _app.DisposeAsync();
        }
        catch (Exception)
        {
            // Apa pun yang gagal saat berhenti, jendela tetap ditutup.
        }

        Close();
    }

    private bool TransferRunning()
    {
        try
        {
            return _app.Services.GetService<TransferService>()?.Status.Running == true;
        }
        catch (ObjectDisposedException)
        {
            return false;
        }
    }

    protected override void WndProc(ref Message m)
    {
        if (m.Msg == WmNcHitTest && WindowState == FormWindowState.Normal)
        {
            base.WndProc(ref m);
            var point = PointToClient(new Point((short)(m.LParam.ToInt64() & 0xFFFF), (short)((m.LParam.ToInt64() >> 16) & 0xFFFF)));
            var left = point.X <= ResizeBorder;
            var right = point.X >= ClientSize.Width - ResizeBorder;
            var top = point.Y <= ResizeBorder;
            var bottom = point.Y >= ClientSize.Height - ResizeBorder;
            var hit = (top, bottom, left, right) switch
            {
                (true, _, true, _) => HtTopLeft,
                (true, _, _, true) => HtTopRight,
                (_, true, true, _) => HtBottomLeft,
                (_, true, _, true) => HtBottomRight,
                (true, _, _, _) => HtTop,
                (_, true, _, _) => HtBottom,
                (_, _, true, _) => HtLeft,
                (_, _, _, true) => HtRight,
                _ => 0,
            };
            if (hit != 0)
            {
                m.Result = hit;
            }

            return;
        }

        base.WndProc(ref m);
    }

    private static void OpenExternal(string url)
    {
        try
        {
            Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
        }
        catch (System.ComponentModel.Win32Exception)
        {
            // Tidak ada browser default.
        }
    }

    [DllImport("user32.dll")]
    private static extern bool ReleaseCapture();

    [DllImport("user32.dll")]
    private static extern IntPtr SendMessage(IntPtr hWnd, int msg, int wParam, int lParam);
}
