using System.Diagnostics;
using Microsoft.AspNetCore.Builder;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using SmcTerminal.Gateway;
using SmcTerminal.Gateway.Services;

namespace SmcTerminal.Desktop;

/// <summary>
/// Native application window. Hosts the C# gateway in-process (which supervises the hidden
/// Python engine) and renders the UI in an embedded WebView2 — no browser, no console windows.
/// Closing the window stops the gateway and the engine.
/// </summary>
internal sealed class MainForm : Form
{
    public const string WindowTitle = "SMC Terminal — Saham · Forex · Kripto";
    private static readonly Color Background = Color.FromArgb(10, 14, 22);

    public static string DataDirectory => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "SMCTerminal");

    private readonly string _root;
    private readonly WebView2 _web = new() { Dock = DockStyle.Fill, DefaultBackgroundColor = Background };
    private readonly Panel _splash = new() { Dock = DockStyle.Fill, BackColor = Background };
    private readonly Label _status = new();
    private readonly Label _detail = new();
    private WebApplication? _gateway;
    private Uri? _home;
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
        WindowSettings.Restore(this);
        BuildSplash();
        Controls.Add(_web);
        Controls.Add(_splash);
        _splash.BringToFront();
        Shown += async (_, _) => await StartAsync();
        FormClosing += OnFormClosing;
        KeyDown += OnKeyDown;
    }

    protected override void OnHandleCreated(EventArgs e)
    {
        base.OnHandleCreated(e);
        Win32.UseDarkTitleBar(Handle);
    }

    private string LogDirectory => Path.Combine(_root, "logs");

    private void BuildSplash()
    {
        var mono = FontFamily.Families.Any(f => f.Name == "Cascadia Mono") ? "Cascadia Mono" : "Consolas";
        var brand = new Label
        {
            Text = "SMC TERMINAL", ForeColor = Color.FromArgb(230, 235, 243), Font = new Font(mono, 26, FontStyle.Bold),
            AutoSize = true,
        };
        var sub = new Label
        {
            Text = "SAHAM · FOREX · KRIPTO · SMART MONEY CONCEPT", ForeColor = Color.FromArgb(107, 155, 255),
            Font = new Font(mono, 10), AutoSize = true,
        };
        _status.ForeColor = Color.FromArgb(195, 203, 216);
        _status.Font = new Font(mono, 11);
        _status.AutoSize = true;
        _detail.ForeColor = Color.FromArgb(133, 146, 168);
        _detail.Font = new Font(mono, 9);
        _detail.AutoSize = true;
        _detail.MaximumSize = new Size(900, 0);
        var stack = new FlowLayoutPanel
        {
            FlowDirection = FlowDirection.TopDown, AutoSize = true, BackColor = Background, WrapContents = false,
        };
        stack.Controls.AddRange(new Control[] { brand, sub, new Label { Height = 24 }, _status, _detail });
        _splash.Controls.Add(stack);
        _splash.Resize += (_, _) => stack.Location = new Point(
            Math.Max(20, (_splash.Width - stack.Width) / 2), Math.Max(20, (_splash.Height - stack.Height) / 2 - 40));
    }

    private void SetStatus(string status, string detail = "")
    {
        _status.Text = status;
        _detail.Text = detail;
        _splash.PerformLayout();
    }

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

            SetStatus("Menyiapkan tampilan …");
            var ready = await InitWebView();

            var supervisor = _gateway.Services.GetService<EngineSupervisor>();
            if (supervisor is not null && !supervisor.Ready.IsCompleted)
            {
                SetStatus("Memulai engine analisa Python …", "Run pertama bisa butuh ±20–40 detik. Log: " + Path.Combine(LogDirectory, "engine.log"));
                await Task.WhenAny(supervisor.Ready, Task.Delay(TimeSpan.FromSeconds(60)));
                var failure = EngineSupervisor.Status.Failure;
                if (!supervisor.Ready.IsCompleted && failure is not null)
                {
                    SetStatus("Engine Python belum siap — membuka aplikasi dengan data terbatas …", failure);
                    await Task.Delay(2500);
                }
            }

            if (ready)
            {
                _web.CoreWebView2.Navigate(_home.ToString());
            }
        }
        catch (Exception ex)
        {
            SetStatus("SMC Terminal gagal dijalankan.", $"{ex.Message}\n\nLog: {LogDirectory}");
        }
    }

    private async Task<bool> InitWebView()
    {
        try
        {
            var options = new CoreWebView2EnvironmentOptions("--autoplay-policy=no-user-gesture-required");
            var env = await CoreWebView2Environment.CreateAsync(null, Path.Combine(DataDirectory, "WebView2"), options);
            await _web.EnsureCoreWebView2Async(env);
        }
        catch (WebView2RuntimeNotFoundException)
        {
            SetStatus(
                "Microsoft Edge WebView2 Runtime belum terpasang — aplikasi dibuka di browser untuk sementara.",
                "Install WebView2 (gratis): https://go.microsoft.com/fwlink/p/?LinkId=2124703 lalu jalankan ulang.\n" +
                "Biarkan jendela ini terbuka selama memakai SMC Terminal; menutupnya akan menghentikan server.");
            OpenExternal(_home!);
            return false;
        }

        var core = _web.CoreWebView2;
        core.Settings.IsStatusBarEnabled = false;
        core.Settings.AreDevToolsEnabled = Environment.GetEnvironmentVariable("SMC_DEVTOOLS") == "1";
        core.Settings.IsZoomControlEnabled = true;
        core.DocumentTitleChanged += (_, _) => Text = WindowTitle;
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
                _splash.Visible = false;
            }
            else
            {
                _splash.Visible = true;
                SetStatus("Gagal memuat tampilan.", $"Kode: {e.WebErrorStatus}. Tekan F5 untuk mencoba lagi.");
            }
        };
        core.ProcessFailed += (_, _) =>
        {
            _splash.Visible = true;
            SetStatus("Tampilan berhenti tak terduga.", "Tekan F5 untuk memuat ulang.");
        };
        return true;
    }

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

    private void OnKeyDown(object? sender, KeyEventArgs e)
    {
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

    private FormWindowState _beforeFullScreen = FormWindowState.Maximized;

    private void ToggleFullScreen()
    {
        if (FormBorderStyle == FormBorderStyle.None)
        {
            FormBorderStyle = FormBorderStyle.Sizable;
            WindowState = _beforeFullScreen;
        }
        else
        {
            _beforeFullScreen = WindowState;
            FormBorderStyle = FormBorderStyle.None;
            WindowState = FormWindowState.Normal;
            WindowState = FormWindowState.Maximized;
        }
    }

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
        _shuttingDown = true;
        if (FormBorderStyle != FormBorderStyle.None)
        {
            WindowSettings.Save(this);
        }
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
