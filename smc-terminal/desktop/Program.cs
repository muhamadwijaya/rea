namespace SmcTerminal.Desktop;

internal static class Program
{
    [STAThread]
    private static void Main()
    {
        using var mutex = new Mutex(true, @"Local\SharxInvest.Desktop", out var firstInstance);
        if (!firstInstance)
        {
            Win32.ActivateExisting(MainForm.WindowTitle);
            return;
        }

        ApplicationConfiguration.Initialize();
        var root = AppRoot.Find();
        if (root is null)
        {
            MessageBox.Show(
                "Folder SHARX INVEST tidak ditemukan.\n\nJalankan SharxInvest.exe dari folder 'app' di dalam folder smc-terminal " +
                "(yang berisi folder engine, gateway dan config), atau jalankan start.bat.",
                "SHARX INVEST", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return;
        }
        Application.Run(new MainForm(root));
    }
}

/// <summary>Locates the smc-terminal folder (engine/, gateway/, config/) above the executable.</summary>
internal static class AppRoot
{
    public static string? Find()
    {
        var overrideRoot = Environment.GetEnvironmentVariable("SMC_TERMINAL_ROOT");
        if (!string.IsNullOrEmpty(overrideRoot) && IsRoot(overrideRoot))
        {
            return Path.GetFullPath(overrideRoot);
        }
        for (var dir = new DirectoryInfo(AppContext.BaseDirectory); dir is not null; dir = dir.Parent)
        {
            if (IsRoot(dir.FullName))
            {
                return dir.FullName;
            }
        }
        return null;
    }

    private static bool IsRoot(string path) =>
        File.Exists(Path.Combine(path, "gateway", "appsettings.json")) &&
        File.Exists(Path.Combine(path, "engine", "app", "main.py"));
}
