using System.Diagnostics;
using System.Runtime.InteropServices;
using Microsoft.AspNetCore.Builder;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using Sharx.Core.Transfer;
using Sharx.Web;

namespace Sharx.Desktop;

/// <summary>
/// Jendela Windows biasa (caption gelap, terbuka maximize) yang menampilkan UI web lewat WebView2.
/// Minimize / maximize / close memakai tombol jendela Windows.
/// </summary>
internal sealed class MainForm : Form
{
    private const int DwmUseImmersiveDarkMode = 20;
    private const int DwmCaptionColor = 35;
    private const int DwmTextColor = 36;

    private static readonly Color Background = Color.FromArgb(10, 18, 34);

    private readonly WebApplication _app;
    private readonly WebView2 _web = new() { Dock = DockStyle.Fill, DefaultBackgroundColor = Background };
    private bool _closing;

    public MainForm(WebApplication app)
    {
        _app = app;
        Text = "SHARX Data Transfer";
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = Background;
        MinimumSize = new Size(1024, 640);
        var area = Screen.PrimaryScreen?.WorkingArea ?? new Rectangle(0, 0, 1280, 800);
        Size = new Size(Math.Min(1600, area.Width), Math.Min(900, area.Height));
        WindowState = FormWindowState.Maximized;
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
                SharxWebHost.WriteCrash(SharxHostOptions.DefaultDataDirectory(), "Startup", ex);
                MessageBox.Show($"SHARX Data Transfer gagal dibuka:\n{ex.Message}", Text, MessageBoxButtons.OK, MessageBoxIcon.Error);
                Close();
            }
        };
        FormClosing += OnFormClosing;
    }

    protected override void OnHandleCreated(EventArgs e)
    {
        base.OnHandleCreated(e);
        ApplyDarkCaption();
    }

    private void ApplyDarkCaption()
    {
        // Windows 10 1809+: caption gelap; Windows 11: warna caption sama dengan latar aplikasi.
        // Versi Windows yang tidak mendukung atribut ini mengabaikannya.
        try
        {
            var on = 1;
            _ = DwmSetWindowAttribute(Handle, DwmUseImmersiveDarkMode, ref on, sizeof(int));
            var caption = ColorTranslator.ToWin32(Background);
            _ = DwmSetWindowAttribute(Handle, DwmCaptionColor, ref caption, sizeof(int));
            var text = ColorTranslator.ToWin32(Color.FromArgb(223, 230, 238));
            _ = DwmSetWindowAttribute(Handle, DwmTextColor, ref text, sizeof(int));
        }
        catch (Exception ex) when (ex is DllNotFoundException or EntryPointNotFoundException)
        {
            // dwmapi tidak tersedia: caption standar.
        }
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

    private async void OnFormClosing(object? sender, FormClosingEventArgs e)
    {
        if (_closing)
        {
            return;
        }

        if (e.CloseReason == CloseReason.UserClosing && TransferRunning())
        {
            var answer = MessageBox.Show(
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

    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(IntPtr hwnd, int attribute, ref int value, int size);
}
