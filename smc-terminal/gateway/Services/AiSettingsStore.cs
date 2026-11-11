using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace SmcTerminal.Gateway.Services;

/// <summary>Non-secret AI choices: the active provider and per-provider model / endpoint.</summary>
public sealed record AiOptions(string Provider, string OpenRouterModel, string CustomBaseUrl, string CustomModel)
{
    public static readonly AiOptions Default = new("openrouter", "auto", "", "");

    /// <summary>The model the engine should use for the active provider (null = provider default).</summary>
    public string? ActiveModel => Provider switch
    {
        "openrouter" => OpenRouterModel,
        "custom" => CustomModel,
        _ => null,
    };
}

/// <summary>
/// The AI settings and per-provider API keys.
/// Windows: keys encrypted with DPAPI (current user) in %LOCALAPPDATA%\SMCTerminal\secrets.json.
/// Other OS: ~/.config/smc-terminal/secrets.json with owner-only permissions.
/// Keys are never logged or returned to the browser; the provider's environment variable also works.
/// Reads the earlier single-key format ({"anthropic": {...}}) and keeps that key.
/// </summary>
public sealed class AiSettingsStore(ILogger<AiSettingsStore> log)
{
    public static readonly IReadOnlyDictionary<string, (string Name, string EnvVar)> Providers = new Dictionary<string, (string, string)>
    {
        ["openrouter"] = ("OpenRouter — model gratis, rotasi otomatis", "OPENROUTER_API_KEY"),
        ["anthropic"] = ("Anthropic Claude", "ANTHROPIC_API_KEY"),
        ["custom"] = ("API lain (OpenAI-compatible)", "AI_CUSTOM_API_KEY"),
    };

    private readonly object _gate = new();
    private Dictionary<string, string>? _keys;
    private AiOptions _options = AiOptions.Default;

    private static string FilePath => OperatingSystem.IsWindows()
        ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "SMCTerminal", "secrets.json")
        : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".config", "smc-terminal", "secrets.json");

    public static bool IsProvider(string? provider) => provider is not null && Providers.ContainsKey(provider);

    public static bool EnvironmentKeyPresent(string provider) =>
        Providers.TryGetValue(provider, out var p) && !string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable(p.EnvVar));

    public AiOptions Options
    {
        get
        {
            lock (_gate)
            {
                EnsureLoaded();
                return _options;
            }
        }
    }

    public string? GetKey(string provider)
    {
        lock (_gate)
        {
            EnsureLoaded();
            return _keys!.GetValueOrDefault(provider);
        }
    }

    public void SaveKey(string provider, string key) => Mutate(keys => keys[provider] = key, null);

    public void DeleteKey(string provider) => Mutate(keys => keys.Remove(provider), null);

    public void SaveOptions(AiOptions options) => Mutate(null, options);

    private void Mutate(Action<Dictionary<string, string>>? keys, AiOptions? options)
    {
        lock (_gate)
        {
            EnsureLoaded();
            var nextKeys = new Dictionary<string, string>(_keys!);
            keys?.Invoke(nextKeys);
            var nextOptions = options ?? _options;
            Persist(nextKeys, nextOptions);
            _keys = nextKeys;
            _options = nextOptions;
        }
    }

    private void EnsureLoaded()
    {
        if (_keys is not null)
        {
            return;
        }
        _keys = new Dictionary<string, string>();
        try
        {
            if (!File.Exists(FilePath))
            {
                return;
            }
            var root = JsonNode.Parse(File.ReadAllText(FilePath)) as JsonObject;
            if (root is null)
            {
                return;
            }
            if (root["keys"] is JsonObject saved)
            {
                foreach (var (provider, entry) in saved)
                {
                    if (IsProvider(provider) && Decode(entry) is { } key)
                    {
                        _keys[provider] = key;
                    }
                }
            }
            else if (Decode(root["anthropic"]) is { } legacy)
            {
                _keys["anthropic"] = legacy; // earlier single-key format
            }
            var provider0 = root["provider"]?.GetValue<string>();
            _options = new AiOptions(
                IsProvider(provider0) ? provider0! : AiOptions.Default.Provider,
                root["openrouterModel"]?.GetValue<string>() ?? AiOptions.Default.OpenRouterModel,
                root["customBaseUrl"]?.GetValue<string>() ?? "",
                root["customModel"]?.GetValue<string>() ?? "");
        }
        catch (Exception ex) when (ex is IOException or JsonException or CryptographicException or FormatException or InvalidOperationException or UnauthorizedAccessException)
        {
            log.LogWarning("Saved AI settings could not be read: {Type}", ex.GetType().Name);
        }
    }

    private string? Decode(JsonNode? entry)
    {
        var value = entry?["value"]?.GetValue<string>();
        if (value is null)
        {
            return null;
        }
        try
        {
            return entry!["scheme"]?.GetValue<string>() == "dpapi"
                ? OperatingSystem.IsWindows()
                    ? Encoding.UTF8.GetString(ProtectedData.Unprotect(Convert.FromBase64String(value), null, DataProtectionScope.CurrentUser))
                    : null
                : value;
        }
        catch (Exception ex) when (ex is CryptographicException or FormatException)
        {
            log.LogWarning("A saved AI key could not be decrypted: {Type}", ex.GetType().Name);
            return null;
        }
    }

    private static JsonObject Encode(string key) => OperatingSystem.IsWindows()
        ? new JsonObject { ["scheme"] = "dpapi", ["value"] = Convert.ToBase64String(ProtectedData.Protect(Encoding.UTF8.GetBytes(key), null, DataProtectionScope.CurrentUser)) }
        : new JsonObject { ["scheme"] = "plain", ["value"] = key };

    private static void Persist(Dictionary<string, string> keys, AiOptions options)
    {
        var encoded = new JsonObject();
        foreach (var (provider, key) in keys)
        {
            encoded[provider] = Encode(key);
        }
        var root = new JsonObject
        {
            ["version"] = 2,
            ["provider"] = options.Provider,
            ["openrouterModel"] = options.OpenRouterModel,
            ["customBaseUrl"] = options.CustomBaseUrl,
            ["customModel"] = options.CustomModel,
            ["keys"] = encoded,
        };
        Directory.CreateDirectory(Path.GetDirectoryName(FilePath)!);
        var temp = FilePath + ".tmp";
        File.WriteAllText(temp, root.ToJsonString());
        if (!OperatingSystem.IsWindows())
        {
            File.SetUnixFileMode(temp, UnixFileMode.UserRead | UnixFileMode.UserWrite);
        }
        File.Move(temp, FilePath, overwrite: true);
    }
}
