using System.Diagnostics;
using Microsoft.AspNetCore.Builder;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using SmcTerminal.Gateway;
using SmcTerminal.Gateway.Services;

namespace SmcTerminal.Desktop;

/// <summary>
/// Native application window. Starts in borderless full screen (no Windows title bar or close button;
/// the UI has its own minimize / window / exit controls), hosts the C# gateway in-process (which
/// supervises the hidden Python engine) and renders the UI in an embedded WebView2.
/// Closing the window stops the gateway and the engine.
/// </summary>
internal sealed class MainForm : Form
{
    public const string WindowTitle = "SHARX INVEST — Saham · Forex · Kripto";
    private static readonly Color Background = Color.FromArgb(0, 27, 51);
    private static readonly Color Accent = Color.FromArgb(153, 255, 0);

    public static string DataDirectory => SmcTerminal.Gateway.AppPaths.DataDirectory;

    private readonly string _root;
    private readonly WebView2 _web = new() { Dock = DockStyle.Fill, DefaultBackgroundColor = Background };
    private readonly Panel _splash = new() { Dock = DockStyle.Fill, BackColor = Background };
    private readonly Label _status = new();
    private readonly Label _detail = new();
    private readonly Panel _track = new() { Height = 3, Width = 420, BackColor = Color.FromArgb(28, 38, 55) };
    private readonly Panel _bar = new() { Height = 3, Width = 120, BackColor = Accent };
    private readonly System.Windows.Forms.Timer _anim = new() { Interval = 16 };
    private readonly System.Windows.Forms.Timer _saveTimer = new() { Interval = 600 };
    private double _zoom = 1.0;
    private bool _restoring = true;
    private WebApplication? _gateway;
    private Uri? _home;
    private bool _fullScreen;
    private FormWindowState _windowedState = FormWindowState.Maximized;
    private bool _shuttingDown;
    private bool _shutdownComplete;

    public MainForm(string root)
    {
        _root = root;
        Text = WindowTitle;
        BackColor = Background;
        Icon = AppIcon.Create();
        MinimumSize = new Size(1100, 680);
        KeyPreview = true;
        RestoreWindow(WindowSettings.Load());
        BuildSplash();
        Controls.Add(_web);
        Controls.Add(_splash);
        _splash.BringToFront();
        Shown += async (_, _) => await StartAsync();
        FormClosing += OnFormClosing;
        KeyDown += OnKeyDown;
        // Any size / position / state change is saved automatically (debounced).
        ResizeEnd += (_, _) => QueueSave();
        Resize += (_, _) => QueueSave();
        _saveTimer.Tick += (_, _) => { _saveTimer.Stop(); SaveWindow(); };
        _restoring = false;
    }

    private void RestoreWindow(WindowSettings.State state)
    {
        StartPosition = FormStartPosition.Manual;
        Bounds = new Rectangle(state.X, state.Y, state.Width, state.Height);
        _windowedState = state.Maximized ? FormWindowState.Maximized : FormWindowState.Normal;
        _zoom = state.Zoom;
        if (state.FullScreen)
        {
            WindowState = _windowedState;
            EnterFullScreen();
        }
        else
        {
            WindowState = _windowedState;
        }
    }

    private void QueueSave()
    {
        if (_restoring || _shuttingDown || WindowState == FormWindowState.Minimized)
        {
            return;
        }
        _saveTimer.Stop();
        _saveTimer.Start();
    }

    private void SaveWindow()
    {
        var bounds = WindowState == FormWindowState.Normal && !_fullScreen ? Bounds : RestoreBounds;
        var windowed = _fullScreen ? _windowedState : WindowState;
        WindowSettings.Save(new WindowSettings.State(bounds.X, bounds.Y, bounds.Width, bounds.Height,
            windowed == FormWindowState.Maximized, _fullScreen, Math.Round(_zoom, 2)));
    }

    protected override void OnHandleCreated(EventArgs e)
    {
        base.OnHandleCreated(e);
        Win32.UseDarkTitleBar(Handle);
    }

    private string LogDirectory => Path.Combine(_root, "logs");

    // ------------------------------------------------------------------ splash (until the web UI takes over)

    private void BuildSplash()
    {
        var mono = FontFamily.Families.Any(f => f.Name == "Cascadia Mono") ? "Cascadia Mono" : "Consolas";
        Control brand = new Label
        {
            Text = "SHARX INVEST", ForeColor = Color.FromArgb(230, 235, 243), Font = new Font(mono, 26, FontStyle.Bold), AutoSize = true,
        };
        var logoFile = Path.Combine(_root, "gateway", "wwwroot", "img", "sharx-logo.png");
        if (File.Exists(logoFile))
        {
            var logo = Image.FromFile(logoFile);
            brand = new PictureBox
            {
                Image = logo, SizeMode = PictureBoxSizeMode.Zoom, Size = new Size(420, (int)(420.0 * logo.Height / logo.Width)),
                BackColor = Background,
            };
        }
        var sub = new Label
        {
            Text = "SAHAM · FOREX · KRIPTO · SMART MONEY CONCEPT", ForeColor = Accent, Font = new Font(mono, 10), AutoSize = true,
        };
        _status.ForeColor = Color.FromArgb(195, 203, 216);
        _status.Font = new Font(mono, 11);
        _status.AutoSize = true;
        _detail.ForeColor = Color.FromArgb(133, 146, 168);
        _detail.Font = new Font(mono, 9);
        _detail.AutoSize = true;
        _detail.MaximumSize = new Size(900, 0);
        _track.Controls.Add(_bar);
        var stack = new FlowLayoutPanel
        {
            FlowDirection = FlowDirection.TopDown, AutoSize = true, BackColor = Background, WrapContents = false,
        };
        stack.Controls.AddRange(new Control[] { brand, sub, new Label { Height = 28 }, _track, new Label { Height = 10 }, _status, _detail });
        _splash.Controls.Add(stack);
        _splash.Resize += (_, _) => stack.Location = new Point(
            Math.Max(20, (_splash.Width - stack.Width) / 2), Math.Max(20, (_splash.Height - stack.Height) / 2 - 40));
        var x = 0;
        _anim.Tick += (_, _) =>
        {
            x = (x + 6) % (_track.Width + _bar.Width);
            _bar.Left = x - _bar.Width;
        };
        _anim.Start();
    }

    private void SetStatus(string status, string detail = "")
    {
        _status.Text = status;
        _detail.Text = detail;
        _splash.PerformLayout();
    }

    private void HideSplash()
    {
        _anim.Stop();
        _splash.Visible = false;
    }

    private void ShowSplash(string status, string detail)
    {
        _splash.Visible = true;
        _anim.Start();
        SetStatus(status, detail);
    }

    // ------------------------------------------------------------------ startup

    private async Task StartAsync()
    {
        try
        {
            SetStatus("Menyalakan gateway C# …");
            var port = Ports.FreeOr(5080);
            _home = new Uri($"http://127.0.0.1:{port}/");
            _gateway = GatewayHost.Build(new[]
            {
                $"--urls=http://127.0.0.1:{port}",
                "--Terminal:ManageEngine=true",
                $"--Terminal:LogDirectory={LogDirectory}",
            }, Path.Combine(_root, "gateway"));
            await _gateway.StartAsync();

            SetStatus("Menyiapkan tampilan …", "Engine Python, data pasar dan berita dimuat di layar berikutnya.");
            if (await InitWebView())
            {
                // The web UI shows its own boot screen and waits until engine, screener, news and calendar are ready.
                _web.CoreWebView2.Navigate(_home.ToString());
            }
        }
        catch (Exception ex)
        {
            ShowSplash("SHARX INVEST gagal dijalankan.", $"{ex.Message}\n\nLog: {LogDirectory}");
        }
    }

    private async Task<bool> InitWebView()
    {
        try
        {
            var env = await CoreWebView2Environment.CreateAsync(null, Path.Combine(DataDirectory, "WebView2"));
            await _web.EnsureCoreWebView2Async(env);
        }
        catch (WebView2RuntimeNotFoundException)
        {
            ExitFullScreen();
            ShowSplash(
                "Microsoft Edge WebView2 Runtime belum terpasang — aplikasi dibuka di browser untuk sementara.",
                "Install WebView2 (gratis): https://go.microsoft.com/fwlink/p/?LinkId=2124703 lalu jalankan ulang.\n" +
                "Biarkan jendela ini terbuka selama memakai SHARX INVEST; menutupnya akan menghentikan server.");
            OpenExternal(_home!);
            return false;
        }

        var core = _web.CoreWebView2;
        core.Settings.IsStatusBarEnabled = false;
        core.Settings.AreDevToolsEnabled = Environment.GetEnvironmentVariable("SMC_DEVTOOLS") == "1";
        core.Settings.IsZoomControlEnabled = true; // Ctrl + wheel, Ctrl +/-, Ctrl 0
        _web.ZoomFactor = _zoom;
        _web.ZoomFactorChanged += (_, _) =>
        {
            _zoom = _web.ZoomFactor;
            PostZoom();
            QueueSave();
        };
        core.DocumentTitleChanged += (_, _) => Text = WindowTitle;
        core.WebMessageReceived += OnWebMessage;
        core.NewWindowRequested += (_, e) =>
        {
            // Links with target=_blank (news, TradingView) open in the default browser.
            e.Handled = true;
            if (Uri.TryCreate(e.Uri, UriKind.Absolute, out var uri))
            {
                OpenExternal(uri);
            }
        };
        core.NavigationStarting += (_, e) =>
        {
            if (Uri.TryCreate(e.Uri, UriKind.Absolute, out var uri) && !IsHome(uri) && uri.Scheme is "http" or "https")
            {
                e.Cancel = true;
                OpenExternal(uri);
            }
        };
        core.NavigationCompleted += (_, e) =>
        {
            if (e.IsSuccess)
            {
                HideSplash();
                PostWindowState();
            }
            else
            {
                ShowSplash("Gagal memuat tampilan.", $"Kode: {e.WebErrorStatus}. Tekan F5 untuk mencoba lagi.");
            }
        };
        core.ProcessFailed += (_, _) => ShowSplash("Tampilan berhenti tak terduga.", "Tekan F5 untuk memuat ulang.");
        return true;
    }

    /// <summary>Window commands sent by the UI (its own title-bar buttons and F11).</summary>
    private void OnWebMessage(object? sender, CoreWebView2WebMessageReceivedEventArgs e)
    {
        string message;
        try
        {
            message = e.TryGetWebMessageAsString();
        }
        catch (ArgumentException)
        {
            return;
        }
        switch (message)
        {
            case "window:minimize":
                WindowState = FormWindowState.Minimized;
                break;
            case "window:toggle-fullscreen":
                ToggleFullScreen();
                break;
            case "window:close":
                BeginInvoke(Close);
                break;
            case "ui:ready":
                PostWindowState();
                PostZoom();
                break;
            case "zoom:in":
                _web.ZoomFactor = Math.Min(2.0, Math.Round(_web.ZoomFactor + 0.1, 2));
                break;
            case "zoom:out":
                _web.ZoomFactor = Math.Max(0.5, Math.Round(_web.ZoomFactor - 0.1, 2));
                break;
            case "zoom:reset":
                _web.ZoomFactor = 1.0;
                break;
        }
    }

    private void PostWindowState() => _web.CoreWebView2?.PostWebMessageAsString(_fullScreen ? "fullscreen:1" : "fullscreen:0");

    private void PostZoom() => _web.CoreWebView2?.PostWebMessageAsString(
        "zoom:" + _web.ZoomFactor.ToString("0.00", System.Globalization.CultureInfo.InvariantCulture));

    private bool IsHome(Uri uri) => _home is not null && uri.Host == _home.Host && uri.Port == _home.Port;

    private static void OpenExternal(Uri uri)
    {
        if (uri.Scheme is not ("http" or "https"))
        {
            return;
        }
        try
        {
            Process.Start(new ProcessStartInfo(uri.ToString()) { UseShellExecute = true });
        }
        catch (Exception ex) when (ex is System.ComponentModel.Win32Exception or InvalidOperationException)
        {
            // no default browser registered
        }
    }

    // ------------------------------------------------------------------ full screen

    private void OnKeyDown(object? sender, KeyEventArgs e)
    {
        // Only reached while the native splash has focus; inside the UI the page forwards F11 itself.
        if (e.KeyCode == Keys.F11)
        {
            ToggleFullScreen();
            e.Handled = true;
        }
        else if (e.KeyCode == Keys.F5 && _web.CoreWebView2 is not null && _home is not null)
        {
            _web.CoreWebView2.Navigate(_home.ToString());
            e.Handled = true;
        }
    }

    private void ToggleFullScreen()
    {
        if (_fullScreen)
        {
            ExitFullScreen();
        }
        else
        {
            EnterFullScreen();
        }
        PostWindowState();
        QueueSave();
    }

    private void EnterFullScreen()
    {
        if (_fullScreen)
        {
            return;
        }
        if (WindowState != FormWindowState.Minimized)
        {
            _windowedState = WindowState;
        }
        _fullScreen = true;
        // Borderless + maximized covers the whole monitor, taskbar included, with no title bar or close button.
        FormBorderStyle = FormBorderStyle.None;
        WindowState = FormWindowState.Normal;
        WindowState = FormWindowState.Maximized;
    }

    private void ExitFullScreen()
    {
        if (!_fullScreen)
        {
            return;
        }
        _fullScreen = false;
        FormBorderStyle = FormBorderStyle.Sizable;
        WindowState = _windowedState == FormWindowState.Maximized ? FormWindowState.Maximized : FormWindowState.Normal;
    }

    // ------------------------------------------------------------------ shutdown

    private async void OnFormClosing(object? sender, FormClosingEventArgs e)
    {
        if (_shutdownComplete || _gateway is null)
        {
            return;
        }
        e.Cancel = true;
        if (_shuttingDown)
        {
            return;
        }
        _saveTimer.Stop();
        SaveWindow();
        _shuttingDown = true;
        Hide();
        try
        {
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(10));
            await _gateway.StopAsync(timeout.Token);
            await _gateway.DisposeAsync();
        }
        catch (Exception)
        {
            // The engine is in a kill-on-close job object, so it still ends with this process.
        }
        _shutdownComplete = true;
        Close();
    }
}
