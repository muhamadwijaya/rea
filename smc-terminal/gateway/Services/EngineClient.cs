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

    public async Task<EngineResponse> PostJsonAsync(string path, object payload, CancellationToken ct, IDictionary<string, string>? headers = null)
    {
        try
        {
            using var request = new HttpRequestMessage(HttpMethod.Post, path)
            {
                Content = new StringContent(JsonSerializer.Serialize(payload, JsonDefaults.Web), Encoding.UTF8, "application/json"),
            };
            foreach (var (name, value) in headers ?? new Dictionary<string, string>())
            {
                request.Headers.TryAddWithoutValidation(name, value);
            }
            using var response = await http.SendAsync(request, ct);
            var body = await response.Content.ReadAsStringAsync(ct);
            MarkOk();
            return new EngineResponse((int)response.StatusCode, body);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException && !ct.IsCancellationRequested)
        {
            return Fail(path, ex);
        }
    }

    /// <summary>Scores headlines through the engine; returns an empty map when the engine is unavailable.</summary>
    public async Task<Dictionary<string, SentimentScore>> ScoreSentimentAsync(IEnumerable<SmcTerminal.Gateway.Models.NewsItem> items, CancellationToken ct)
    {
        var batch = items.Select(i => new { id = i.Id, title = i.Title, summary = i.Summary, lang = i.Lang, category = i.Category, published = i.Published.ToString("o"), link = i.Link, source = i.Source }).ToList();
        var scores = new Dictionary<string, SentimentScore>();
        if (batch.Count == 0)
        {
            return scores;
        }
        var response = await PostJsonAsync("sentiment", new { items = batch }, ct);
        if (!response.Ok)
        {
            return scores;
        }
        using var doc = JsonDocument.Parse(response.Body);
        foreach (var e in doc.RootElement.GetProperty("items").EnumerateArray())
        {
            if (e.GetProperty("id").GetString() is not { } id)
            {
                continue;
            }
            scores[id] = new SentimentScore(
                e.GetProperty("score").GetDouble(),
                e.GetProperty("label").GetString() ?? "neutral",
                e.GetProperty("terms").EnumerateArray().Select(x => x.GetString() ?? "").ToList(),
                e.GetProperty("assets").EnumerateArray().Select(x => x.GetString() ?? "").ToList(),
                e.TryGetProperty("symbols", out var tags)
                    ? tags.EnumerateArray().Select(t => new SmcTerminal.Gateway.Models.SymbolTag(
                        t.GetProperty("symbol").GetString() ?? "", t.GetProperty("market").GetString() ?? "", t.GetProperty("sign").GetInt32())).ToList()
                    : new List<SmcTerminal.Gateway.Models.SymbolTag>());
        }
        return scores;
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
