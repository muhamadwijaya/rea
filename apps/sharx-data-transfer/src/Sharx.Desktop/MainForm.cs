using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text.Json;
using Microsoft.AspNetCore.Builder;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using Sharx.Web;

namespace Sharx.Desktop;

/// <summary>
/// Jendela tanpa border yang menampilkan UI web (WebView2). Tombol minimize / maximize / close
/// dan area judul untuk drag ada di dalam UI, dikirim lewat <c>window.chrome.webview.postMessage</c>.
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

    private readonly WebApplication _app;
    private readonly WebView2 _web = new() { Dock = DockStyle.Fill, DefaultBackgroundColor = Color.FromArgb(2, 16, 39) };
    private bool _closing;

    public MainForm(WebApplication app)
    {
        _app = app;
        Text = "SHARX Data Transfer";
        FormBorderStyle = FormBorderStyle.None;
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = Color.FromArgb(2, 16, 39);
        MinimumSize = new Size(1000, 720);
        var area = Screen.PrimaryScreen?.WorkingArea ?? new Rectangle(0, 0, 1280, 1024);
        Size = new Size(Math.Min(1240, area.Width), Math.Min(1290, area.Height));
        Padding = new Padding(ResizeBorder);
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
        Load += async (_, _) => await StartAsync();
        Resize += (_, _) => Padding = WindowState == FormWindowState.Maximized ? Padding.Empty : new Padding(ResizeBorder);
        Move += (_, _) => UpdateMaximizedBounds();
        FormClosing += OnFormClosing;
    }

    private async Task StartAsync()
    {
        UpdateMaximizedBounds();
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
            FormBorderStyle = FormBorderStyle.FixedSingle;
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
            case "minimize":
                WindowState = FormWindowState.Minimized;
                break;
            case "maximize":
                WindowState = WindowState == FormWindowState.Maximized ? FormWindowState.Normal : FormWindowState.Maximized;
                break;
            case "close":
                Close();
                break;
            case "drag" when WindowState != FormWindowState.Maximized:
                ReleaseCapture();
                SendMessage(Handle, WmNcLButtonDown, HtCaption, 0);
                break;
        }
    }

    private async void OnFormClosing(object? sender, FormClosingEventArgs e)
    {
        if (_closing)
        {
            return;
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
        catch (Exception ex) when (ex is OperationCanceledException or ObjectDisposedException)
        {
            // Tutup saja.
        }

        Close();
    }

    private void UpdateMaximizedBounds()
    {
        // Jendela tanpa border: saat maximize jangan menutupi taskbar.
        var screen = Screen.FromHandle(Handle).WorkingArea;
        MaximizedBounds = new Rectangle(0, 0, screen.Width, screen.Height);
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
