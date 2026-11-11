using System.Text.Json;
using System.Text.Json.Nodes;
using SmcTerminal.Gateway.Models;
using SmcTerminal.Gateway.Services;

namespace SmcTerminal.Gateway;

public static class Api
{
    private static readonly HashSet<string> Timeframes = new() { "15m", "1h", "4h", "1d", "1w" };
    private static readonly HashSet<string> Groups = new() { "all", "forex", "us", "idx", "macro" };

    public static void MapTerminalApi(this WebApplication app)
    {
        var api = app.MapGroup("/api");

        api.MapGet("/health", (MarketState state, NewsStore news, CalendarStore calendar) => Results.Json(new
        {
            gateway = "ok",
            engine = new { online = EngineClient.Online, lastOk = EngineClient.LastOk, error = EngineClient.LastError },
            screenerAt = state.ScreenerAt,
            directionAt = state.DirectionAt,
            news = new { total = news.Total, updated = news.LastUpdate },
            calendar = new { updated = calendar.LastUpdate, error = calendar.LastError },
        }));

        api.MapGet("/stats", (StatsService stats) => Results.Json(stats.Build()));

        api.MapGet("/screener", async (string? group, string? tf, MarketState state, EngineClient engine, CancellationToken ct) =>
        {
            group = string.IsNullOrWhiteSpace(group) ? "all" : group.ToLowerInvariant();
            tf = string.IsNullOrWhiteSpace(tf) ? state.ScreenerTf : tf.ToLowerInvariant();
            if (!Groups.Contains(group) || !Timeframes.Contains(tf))
            {
                return Results.BadRequest(new { error = "invalid_argument", message = "group/tf tidak dikenal" });
            }
            // The worker keeps the default snapshot warm; other combinations go straight to the engine cache.
            if (group == "all" && tf == state.ScreenerTf && state.ScreenerJson is { } cached)
            {
                return Results.Content(cached, "application/json");
            }
            var response = await engine.GetAsync($"screener?group={group}&tf={tf}", ct);
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

        api.MapGet("/instruments", async (string? group, EngineClient engine, CancellationToken ct) =>
        {
            var g = string.IsNullOrWhiteSpace(group) ? "all" : group;
            var response = await engine.GetAsync($"instruments?group={Uri.EscapeDataString(g)}", ct);
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

        api.MapGet("/news", (string? category, string? asset, string? sentiment, string? q, int? limit, int? offset, NewsStore news) =>
            Results.Json(new
            {
                total = news.Total,
                updated = news.LastUpdate,
                items = news.Query(category, asset, sentiment, q, limit ?? 80, offset ?? 0),
            }));

        api.MapGet("/news/sources", () => Results.Json(NewsWorker.Statuses.Select(s => new
        {
            s.Name, s.Category, s.LastOk, s.LastError, s.LastItemCount, s.ConsecutiveFailures,
            host = Uri.TryCreate(s.Url, UriKind.Absolute, out var u) ? u.Host : s.Url,
        })));

        api.MapGet("/news/sentiment", (int? hours, NewsStore news) =>
            Results.Json(news.SentimentSummary(TimeSpan.FromHours(Math.Clamp(hours ?? 12, 1, 96)), DateTimeOffset.UtcNow)));

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
    }

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
