using System.Net;
using System.Text;
using System.Text.Json;

namespace SmcTerminal.Gateway.Services;

/// <summary>Result of a call to the Python engine: raw JSON body plus HTTP status.</summary>
public sealed record EngineResponse(int Status, string Body)
{
    public bool Ok => Status is >= 200 and < 300;

    public static EngineResponse Unavailable(string message) =>
        new(503, JsonSerializer.Serialize(new { error = "engine_unavailable", message }));
}

/// <summary>Typed HTTP client for the Python analysis engine (localhost only).</summary>
public sealed class EngineClient(HttpClient http, ILogger<EngineClient> log)
{
    public static DateTimeOffset? LastOk { get; private set; }
    public static string? LastError { get; private set; }
    public static bool Online => LastOk is { } ok && DateTimeOffset.UtcNow - ok < TimeSpan.FromMinutes(5) && LastError is null;

    public async Task<EngineResponse> GetAsync(string pathAndQuery, CancellationToken ct)
    {
        try
        {
            using var response = await http.GetAsync(pathAndQuery, ct);
            var body = await response.Content.ReadAsStringAsync(ct);
            MarkOk();
            return new EngineResponse((int)response.StatusCode, body);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException && !ct.IsCancellationRequested)
        {
            return Fail(pathAndQuery, ex);
        }
    }

    public async Task<EngineResponse> PostJsonAsync(string path, object payload, CancellationToken ct)
    {
        try
        {
            using var content = new StringContent(JsonSerializer.Serialize(payload, JsonDefaults.Web), Encoding.UTF8, "application/json");
            using var response = await http.PostAsync(path, content, ct);
            var body = await response.Content.ReadAsStringAsync(ct);
            MarkOk();
            return new EngineResponse((int)response.StatusCode, body);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException && !ct.IsCancellationRequested)
        {
            return Fail(path, ex);
        }
    }

    private static void MarkOk()
    {
        LastOk = DateTimeOffset.UtcNow;
        LastError = null;
    }

    private EngineResponse Fail(string path, Exception ex)
    {
        var message = ex is TaskCanceledException
            ? "Engine Python tidak merespons (timeout)."
            : $"Engine Python tidak dapat dihubungi: {ex.Message}";
        if (LastError is null)
        {
            log.LogWarning("Engine call {Path} failed: {Message}", path, ex.Message);
        }
        LastError = message;
        return EngineResponse.Unavailable(message);
    }
}

public static class JsonDefaults
{
    public static readonly JsonSerializerOptions Web = new(JsonSerializerDefaults.Web);
}
