namespace SmcTerminal.Gateway;

/// <summary>
/// Product name and the per-user data folder (settings, keys, caches, virtual-trading journal).
/// Windows: %LOCALAPPDATA%\SharxInvest; other OS: ~/.local/share/sharx-invest.
/// Files from the earlier "SMC Terminal" folders are copied over once (never deleted), so saved keys,
/// window size and caches survive the rename.
/// </summary>
public static class AppPaths
{
    public const string ProductName = "SHARX INVEST";

    private static readonly Lazy<string> Data = new(Resolve);

    public static string DataDirectory => Data.Value;

    public static string File(string name) => Path.Combine(DataDirectory, name);

    private static string Resolve()
    {
        var dir = OperatingSystem.IsWindows()
            ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "SharxInvest")
            : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".local", "share", "sharx-invest");
        Directory.CreateDirectory(dir);
        foreach (var legacy in LegacyFiles())
        {
            var target = Path.Combine(dir, Path.GetFileName(legacy));
            try
            {
                if (System.IO.File.Exists(legacy) && !System.IO.File.Exists(target))
                {
                    System.IO.File.Copy(legacy, target);
                }
            }
            catch (IOException)
            {
                // a legacy file that cannot be copied is simply recreated
            }
            catch (UnauthorizedAccessException)
            {
            }
        }
        return dir;
    }

    private static IEnumerable<string> LegacyFiles()
    {
        if (OperatingSystem.IsWindows())
        {
            var old = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "SMCTerminal");
            return new[] { "secrets.json", "window.json", "calendar.json" }.Select(n => Path.Combine(old, n));
        }
        var home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        return new[]
        {
            Path.Combine(home, ".config", "smc-terminal", "secrets.json"),
            Path.Combine(home, ".cache", "smc-terminal", "calendar.json"),
        };
    }
}
