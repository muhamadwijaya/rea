using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace SmcTerminal.Gateway.Services;

/// <summary>
/// Stores the user's Anthropic API key for the optional AI analysis.
/// Windows: encrypted with DPAPI (current user) in %LOCALAPPDATA%\SMCTerminal\secrets.json.
/// Other OS: ~/.config/smc-terminal/secrets.json with owner-only permissions.
/// The key is never logged or returned to the browser; ANTHROPIC_API_KEY in the environment also works.
/// </summary>
public sealed class SecretStore(ILogger<SecretStore> log)
{
    private readonly object _gate = new();
    private string? _cached;
    private bool _loaded;

    private static string FilePath => OperatingSystem.IsWindows()
        ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "SMCTerminal", "secrets.json")
        : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".config", "smc-terminal", "secrets.json");

    public string? AnthropicKey
    {
        get
        {
            lock (_gate)
            {
                if (!_loaded)
                {
                    _cached = Load();
                    _loaded = true;
                }
                return _cached;
            }
        }
    }

    public static bool EnvironmentKeyPresent => !string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("ANTHROPIC_API_KEY"));

    public void SaveAnthropicKey(string key)
    {
        var payload = OperatingSystem.IsWindows()
            ? new { scheme = "dpapi", value = Convert.ToBase64String(ProtectedData.Protect(Encoding.UTF8.GetBytes(key), null, DataProtectionScope.CurrentUser)) }
            : new { scheme = "plain", value = key };
        Directory.CreateDirectory(Path.GetDirectoryName(FilePath)!);
        File.WriteAllText(FilePath, JsonSerializer.Serialize(new { anthropic = payload }));
        if (!OperatingSystem.IsWindows())
        {
            File.SetUnixFileMode(FilePath, UnixFileMode.UserRead | UnixFileMode.UserWrite);
        }
        lock (_gate)
        {
            _cached = key;
            _loaded = true;
        }
    }

    public void DeleteAnthropicKey()
    {
        if (File.Exists(FilePath))
        {
            File.Delete(FilePath);
        }
        lock (_gate)
        {
            _cached = null;
            _loaded = true;
        }
    }

    private string? Load()
    {
        try
        {
            if (!File.Exists(FilePath))
            {
                return null;
            }
            using var doc = JsonDocument.Parse(File.ReadAllText(FilePath));
            var entry = doc.RootElement.GetProperty("anthropic");
            var value = entry.GetProperty("value").GetString();
            if (value is null)
            {
                return null;
            }
            return entry.GetProperty("scheme").GetString() == "dpapi" && OperatingSystem.IsWindows()
                ? Encoding.UTF8.GetString(ProtectedData.Unprotect(Convert.FromBase64String(value), null, DataProtectionScope.CurrentUser))
                : value;
        }
        catch (Exception ex) when (ex is IOException or JsonException or CryptographicException or KeyNotFoundException or FormatException)
        {
            log.LogWarning("Saved AI key could not be read: {Type}", ex.GetType().Name);
            return null;
        }
    }
}
