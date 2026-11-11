using System.Text.Json;
using System.Text.Json.Nodes;
using SmcTerminal.Gateway.Models;
using SmcTerminal.Gateway.Services;

namespace SmcTerminal.Gateway;

public static class Api
{
    private static readonly HashSet<string> Timeframes = new() { "15m", "1h", "4h", "1d", "1w" };
    private static readonly HashSet<string> MarketKeys = new(Markets.All.Append("all"));

    public static void MapTerminalApi(this WebApplication app)
    {
        var api = app.MapGroup("/api");

        api.MapGet("/health", (MarketState state, NewsStore news, CalendarStore calendar) => Results.Json(new
        {
            gateway = "ok",
            engine = new
            {
                online = EngineClient.Online, lastOk = EngineClient.LastOk, error = EngineClient.LastError,
                supervisor = EngineSupervisor.Status,
            },
            screenerAt = state.ScreenerAt,
            directionAt = state.DirectionAt,
            news = new { total = news.Total, updated = news.LastUpdate },
            calendar = new { updated = calendar.LastUpdate, error = calendar.LastError },
        }));

        api.MapGet("/stats", (StatsService stats) => Results.Json(stats.Build()));

        api.MapGet("/quotes", async (string? market, EngineClient engine, CancellationToken ct) =>
        {
            var m = string.IsNullOrWhiteSpace(market) ? "all" : market.ToLowerInvariant();
            if (!MarketKeys.Contains(m))
            {
                return Results.BadRequest(new { error = "invalid_argument", message = "market tidak dikenal" });
            }
            var response = await engine.GetAsync($"quotes?market={m}", ct);
            return Results.Content(response.Body, "application/json", statusCode: response.Status);
        });

        api.MapGet("/screener", async (string? market, string? tf, string? scope, MarketState state, EngineClient engine, CancellationToken ct) =>
        {
            market = string.IsNullOrWhiteSpace(market) ? "all" : market.ToLowerInvariant();
            tf = string.IsNullOrWhiteSpace(tf) ? state.ScreenerTf : tf.ToLowerInvariant();
            if (!MarketKeys.Contains(market) || !Timeframes.Contains(tf))
            {
                return Results.BadRequest(new { error = "invalid_argument", message = "market/tf tidak dikenal" });
            }
            if (scope == "all")
            {
                // Full universe (1D) is scanned in the engine's background job and served from its cache.
                var universe = await engine.GetAsync($"screener?market={market}&tf=1d&scope=all", ct);
                return Results.Content(universe.Body, "application/json", statusCode: universe.Status);
            }
            // The worker keeps the default snapshot warm; other combinations go straight to the engine cache.
            if (market == "all" && tf == state.ScreenerTf && state.ScreenerJson is { } cached)
            {
                return Results.Content(cached, "application/json");
            }
            var response = await engine.GetAsync($"screener?market={market}&tf={tf}", ct);
            return Results.Content(response.Body, "application/json", statusCode: response.Status);
        });

        api.MapGet("/analyze", async (string symbol, string? tf, MarketState state, EngineClient engine, Broadcaster broadcaster, CancellationToken ct) =>
        {
            tf = string.IsNullOrWhiteSpace(tf) ? "1h" : tf.ToLowerInvariant();
            if (!Timeframes.Contains(tf))
            {
                return Results.BadRequest(new { error = "invalid_timeframe", message = $"Timeframe {tf} tidak dikenal" });
            }
            var response = await engine.GetAsync($"analyze?symbol={Uri.EscapeDataString(symbol)}&tf={tf}&mtf=true", ct);
            if (response.Ok)
            {
                var record = ToRecord(response.Body);
                if (record is not null)
                {
                    state.RecordAnalysis(record);
                    await broadcaster.Send("analysis", record, ct);
                }
            }
            return Results.Content(response.Body, "application/json", statusCode: response.Status);
        });

        api.MapGet("/analyses", (MarketState state) => Results.Json(state.RecentAnalyses()));

        api.MapGet("/resolve", async (string q, EngineClient engine, CancellationToken ct) =>
        {
            var response = await engine.GetAsync($"resolve?q={Uri.EscapeDataString(q)}", ct);
            return Results.Content(response.Body, "application/json", statusCode: response.Status);
        });

        api.MapGet("/instruments", async (string? market, string? scope, EngineClient engine, CancellationToken ct) =>
        {
            var m = string.IsNullOrWhiteSpace(market) ? "all" : market;
            var sc = scope == "all" ? "all" : "core";
            var response = await engine.GetAsync($"instruments?market={Uri.EscapeDataString(m)}&scope={sc}", ct);
            return Results.Content(response.Body, "application/json", statusCode: response.Status);
        });

        api.MapGet("/direction", (MarketState state, NewsStore news, CalendarStore calendar) =>
        {
            if (state.DirectionJson is null)
            {
                return Results.Json(new
                {
                    error = "not_ready",
                    message = EngineClient.Online ? "Model arah market sedang dihitung…" : EngineClient.LastError ?? "Engine belum siap",
                }, statusCode: 503);
            }
            var now = DateTimeOffset.UtcNow;
            var root = JsonNode.Parse(state.DirectionJson)!.AsObject();
            root["news"] = JsonSerializer.SerializeToNode(news.SentimentSummary(TimeSpan.FromHours(12), now), JsonDefaults.Web);
            root["events"] = JsonSerializer.SerializeToNode(calendar.Upcoming(now, TimeSpan.FromHours(48), "high").Take(10), JsonDefaults.Web);
            root["updatedAt"] = state.DirectionAt;
            return Results.Content(root.ToJsonString(), "application/json");
        });

        api.MapGet("/news", (string? market, string? category, string? asset, string? sentiment, string? q, string? symbol,
                int? limit, int? offset, int? sinceMinutes, NewsStore news) =>
            Results.Json(new
            {
                total = news.Total,
                market,
                updated = news.LastUpdate,
                items = news.Query(market, category, asset, sentiment, q, limit ?? 80, offset ?? 0,
                    sinceMinutes is > 0 ? DateTimeOffset.UtcNow.AddMinutes(-sinceMinutes.Value) : null, symbol),
            }));

        api.MapGet("/news/sources", (string? market) => Results.Json(NewsWorker.Statuses
            .Where(s => !Markets.IsKnown(market) || s.Markets.Contains(market!))
            .Select(s => new
        {
            s.Name, s.Category, s.Markets, s.LastOk, s.LastError, s.LastItemCount, s.ConsecutiveFailures,
            host = Uri.TryCreate(s.Url, UriKind.Absolute, out var u) ? u.Host : s.Url,
        })));

        api.MapGet("/news/sentiment", (int? hours, string? market, NewsStore news) =>
            Results.Json(news.SentimentSummary(TimeSpan.FromHours(Math.Clamp(hours ?? 12, 1, 96)), DateTimeOffset.UtcNow,
                Markets.IsKnown(market) ? market : null)));

        api.MapGet("/symbol-news", async (string symbol, SymbolNewsService service, CancellationToken ct) =>
        {
            var (status, body) = await service.GetAsync(symbol, ct);
            return Results.Json(body, statusCode: status);
        });

        api.MapGet("/calendar", (CalendarStore calendar) => Results.Json(new
        {
            updated = calendar.LastUpdate,
            error = calendar.LastError,
            events = calendar.All(),
        }));

        api.MapGet("/alerts", (int? limit, MarketState state) => Results.Json(state.Alerts(limit ?? 50)));

        // ------------------------------------------------------------ fundamentals, outlook, AI
        api.MapGet("/fundamental", () => FundamentalWorker.LatestJson is { } json
            ? Results.Content(json, "application/json")
            : Results.Json(new { status = "warming", events = Array.Empty<object>() }));

        api.MapGet("/outlook", async (string symbols, NewsStore news, EngineClient engine, CancellationToken ct) =>
        {
            var list = symbols.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries).Take(60).ToList();
            var since = DateTimeOffset.UtcNow.AddHours(-24);
            var byNews = list.ToDictionary(s => s, s => news.Query(null, null, null, null, null, 30, 0, since, s));
            var response = await engine.PostJsonAsync("outlook", new { symbols = list, news = byNews }, ct);
            return Results.Content(response.Body, "application/json", statusCode: response.Status);
        });

        api.MapPost("/ai/analyze", async (AiRequest body, NewsStore news, SecretStore secrets, EngineClient engine, CancellationToken ct) =>
        {
            var items = news.Query(null, null, null, null, null, 25, 0, DateTimeOffset.UtcNow.AddHours(-48), body.Symbol);
            var headers = secrets.AnthropicKey is { } key ? new Dictionary<string, string> { ["X-Anthropic-Key"] = key } : null;
            var response = await engine.PostJsonAsync("ai/analyze", new { symbol = body.Symbol, news = items }, ct, headers);
            return Results.Content(response.Body, "application/json", statusCode: response.Status);
        });

        api.MapGet("/settings/ai", (SecretStore secrets) => Results.Json(new
        {
            configured = secrets.AnthropicKey is not null || SecretStore.EnvironmentKeyPresent,
            source = secrets.AnthropicKey is not null ? "saved" : SecretStore.EnvironmentKeyPresent ? "environment" : null,
            encrypted = OperatingSystem.IsWindows(),
            model = "claude-opus-5-5",
        }));

        api.MapPost("/settings/ai", (AiKeyRequest body, SecretStore secrets) =>
        {
            var key = body.ApiKey?.Trim() ?? "";
            if (!key.StartsWith("sk-ant-", StringComparison.Ordinal) || key.Length < 20 || key.Any(char.IsWhiteSpace))
            {
                return Results.BadRequest(new { error = "invalid_key", message = "Format API key tidak dikenali (harus diawali sk-ant-)." });
            }
            secrets.SaveAnthropicKey(key);
            return Results.Json(new { configured = true, source = "saved" });
        });

        api.MapDelete("/settings/ai", (SecretStore secrets) =>
        {
            secrets.DeleteAnthropicKey();
            return Results.Json(new { configured = SecretStore.EnvironmentKeyPresent });
        });
    }

    public sealed record AiRequest(string Symbol);

    public sealed record AiKeyRequest(string? ApiKey);

    private static AnalysisRecord? ToRecord(string body)
    {
        try
        {
            using var doc = JsonDocument.Parse(body);
            var root = doc.RootElement;
            var inst = root.GetProperty("instrument");
            var bias = root.GetProperty("bias");
            var narrative = root.TryGetProperty("narrative", out var n) && n.GetArrayLength() > 0 ? n[n.GetArrayLength() - 1].GetString() : null;
            return new AnalysisRecord(
                inst.GetProperty("symbol").GetString() ?? "?",
                inst.GetProperty("name").GetString() ?? "",
                inst.GetProperty("tv").GetString() ?? "",
                root.GetProperty("tf").GetString() ?? "",
                bias.GetProperty("score").GetDouble(),
                bias.GetProperty("label").GetString() ?? "",
                bias.GetProperty("direction").GetString() ?? "neutral",
                DateTimeOffset.UtcNow,
                narrative);
        }
        catch (Exception ex) when (ex is JsonException or KeyNotFoundException or InvalidOperationException)
        {
            return null;
        }
    }
}
