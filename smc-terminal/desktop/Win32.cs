using System.Drawing.Drawing2D;
using System.Runtime.InteropServices;
using System.Text.Json;

namespace SmcTerminal.Desktop;

/// <summary>Win32 helpers: dark title bar and focusing an already running instance.</summary>
internal static class Win32
{
    public static void UseDarkTitleBar(IntPtr hwnd)
    {
        var on = 1;
        // DWMWA_USE_IMMERSIVE_DARK_MODE is 20 on Windows 10 20H1+ / 11 and 19 on older builds.
        if (DwmSetWindowAttribute(hwnd, 20, ref on, sizeof(int)) != 0)
        {
            DwmSetWindowAttribute(hwnd, 19, ref on, sizeof(int));
        }
    }

    public static void ActivateExisting(string title)
    {
        var hwnd = FindWindow(null, title);
        if (hwnd == IntPtr.Zero)
        {
            return;
        }
        ShowWindow(hwnd, 9 /* SW_RESTORE */);
        SetForegroundWindow(hwnd);
    }

    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(IntPtr hwnd, int attribute, ref int value, int size);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern IntPtr FindWindow(string? className, string windowName);

    [DllImport("user32.dll")]
    private static extern bool ShowWindow(IntPtr hwnd, int command);

    [DllImport("user32.dll")]
    private static extern bool SetForegroundWindow(IntPtr hwnd);
}

/// <summary>The application icon, drawn in code (candles + trend line) so no binary asset is needed.</summary>
internal static class AppIcon
{
    public static Icon Create()
    {
        using var bmp = new Bitmap(64, 64);
        using (var g = Graphics.FromImage(bmp))
        {
            g.SmoothingMode = SmoothingMode.AntiAlias;
            using var bg = new SolidBrush(Color.FromArgb(11, 17, 28));
            using var ring = new Pen(Color.FromArgb(38, 50, 74), 2);
            g.FillRectangle(bg, 0, 0, 64, 64);
            g.DrawEllipse(ring, 7, 7, 50, 50);
            DrawCandle(g, Color.FromArgb(255, 77, 94), 18, 18, 42, 22, 36);
            DrawCandle(g, Color.FromArgb(47, 210, 140), 32, 14, 46, 20, 38);
            DrawCandle(g, Color.FromArgb(107, 155, 255), 46, 12, 36, 16, 30);
            using var trend = new Pen(Color.FromArgb(242, 201, 76), 3) { StartCap = LineCap.Round, EndCap = LineCap.Round };
            g.DrawLines(trend, new[] { new Point(10, 50), new Point(24, 40), new Point(34, 44), new Point(54, 22) });
        }
        return Icon.FromHandle(bmp.GetHicon());
    }

    private static void DrawCandle(Graphics g, Color color, int x, int wickTop, int wickBottom, int bodyTop, int bodyBottom)
    {
        using var pen = new Pen(color, 2);
        using var brush = new SolidBrush(color);
        g.DrawLine(pen, x, wickTop, x, wickBottom);
        g.FillRectangle(brush, x - 4, bodyTop, 8, bodyBottom - bodyTop);
    }
}

/// <summary>Remembers window position/size between runs (%LOCALAPPDATA%\SMCTerminal\window.json).</summary>
internal static class WindowSettings
{
    private sealed record State(int X, int Y, int Width, int Height, bool Maximized);

    private static string FilePath => Path.Combine(MainForm.DataDirectory, "window.json");

    public static void Restore(Form form)
    {
        form.StartPosition = FormStartPosition.Manual;
        var area = Screen.PrimaryScreen?.WorkingArea ?? new Rectangle(0, 0, 1600, 900);
        form.Bounds = new Rectangle(area.X + 40, area.Y + 30, Math.Min(1600, area.Width - 80), Math.Min(950, area.Height - 60));
        form.WindowState = FormWindowState.Maximized;
        try
        {
            if (!File.Exists(FilePath))
            {
                return;
            }
            var s = JsonSerializer.Deserialize<State>(File.ReadAllText(FilePath));
            if (s is null)
            {
                return;
            }
            var bounds = new Rectangle(s.X, s.Y, s.Width, s.Height);
            if (Screen.AllScreens.Any(sc => sc.WorkingArea.IntersectsWith(bounds)) && s.Width >= 800 && s.Height >= 500)
            {
                form.Bounds = bounds;
            }
            form.WindowState = s.Maximized ? FormWindowState.Maximized : FormWindowState.Normal;
        }
        catch (Exception ex) when (ex is IOException or JsonException or UnauthorizedAccessException)
        {
            // fall back to defaults
        }
    }

    /// <summary>Saves the windowed (non full-screen) placement; <paramref name="windowedState"/> is the state to restore to.</summary>
    public static void Save(Form form, FormWindowState windowedState)
    {
        try
        {
            var b = form.WindowState == FormWindowState.Normal && form.FormBorderStyle != FormBorderStyle.None ? form.Bounds : form.RestoreBounds;
            Directory.CreateDirectory(MainForm.DataDirectory);
            File.WriteAllText(FilePath, JsonSerializer.Serialize(new State(b.X, b.Y, b.Width, b.Height, windowedState == FormWindowState.Maximized)));
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            // not critical
        }
    }
}
