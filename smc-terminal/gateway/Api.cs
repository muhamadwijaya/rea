using System.Text.Json;
using System.Text.Json.Nodes;
using SmcTerminal.Gateway.Models;
using SmcTerminal.Gateway.Services;

namespace SmcTerminal.Gateway;

public static class Api
{
    private static readonly HashSet<string> Timeframes = new() { "1m", "5m", "10m", "15m", "1h", "4h", "1d", "1w" };
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
                var utf = tf == "4h" ? "4h" : "1d";
                var universe = await engine.GetAsync($"screener?market={market}&tf={utf}&scope=all", ct);
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

        api.MapPost("/ai/analyze", async (AiRequest body, NewsStore news, AiSettingsStore settings, EngineClient engine, CancellationToken ct) =>
        {
            var items = news.Query(null, null, null, null, null, 25, 0, DateTimeOffset.UtcNow.AddHours(-48), body.Symbol);
            var options = settings.Options;
            // The key travels only in a header to the local engine; without one the engine falls back to the provider's env var.
            var headers = settings.GetKey(options.Provider) is { } key ? new Dictionary<string, string> { ["X-AI-Key"] = key } : null;
            var response = await engine.PostJsonAsync("ai/analyze", new
            {
                symbol = body.Symbol,
                news = items,
                provider = options.Provider,
                model = options.ActiveModel,
                baseUrl = options.Provider == "custom" ? options.CustomBaseUrl : null,
            }, ct, headers);
            return Results.Content(response.Body, "application/json", statusCode: response.Status);
        });

        // One stored headline analysed before it is read aloud: the engine fetches the article from its link, the AI
        // judges its impact (key only in a header), and every timeframe with momentum gets its own signal.
        api.MapPost("/ai/news", async (NewsAiRequest body, NewsStore news, AiSettingsStore settings, EngineClient engine, CancellationToken ct) =>
        {
            if (news.Find(body.Id) is not { } item)
            {
                return Results.NotFound(new { error = "not_found", message = "Berita tidak ditemukan." });
            }
            var symbol = string.IsNullOrWhiteSpace(body.Symbol) ? item.Symbols.FirstOrDefault()?.Symbol : body.Symbol;
            if (symbol is null)
            {
                return Results.BadRequest(new { error = "invalid_argument", message = "Berita ini tidak terkait aset mana pun." });
            }
            var context = news.Query(null, null, null, null, null, 25, 0, DateTimeOffset.UtcNow.AddHours(-48), symbol);
            var options = settings.Options;
            var headers = settings.GetKey(options.Provider) is { } key ? new Dictionary<string, string> { ["X-AI-Key"] = key } : null;
            var response = await engine.PostJsonAsync("ai/news", new
            {
                symbol,
                item = new
                {
                    title = item.Title, summary = item.Summary, link = item.Link, source = item.Source, published = item.Published,
                    sentiment = item.Sentiment, symbols = item.Symbols.Select(t => new { symbol = t.Symbol, sign = t.Sign }),
                },
                news = context,
                provider = options.Provider,
                model = options.ActiveModel,
                baseUrl = options.Provider == "custom" ? options.CustomBaseUrl : null,
            }, ct, headers);
            return Results.Content(response.Body, "application/json", statusCode: response.Status);
        });

        // Voice assistant: speech synthesis (WAV), recorded answers and model downloads go to the engine as-is.
        api.MapMethods("/voice/{**rest}", new[] { "GET", "POST" }, async (string rest, HttpContext context, EngineClient engine, CancellationToken ct) =>
        {
            if (rest is not ("status" or "setup" or "tts" or "stt"))
            {
                return Results.NotFound();
            }
            await engine.ForwardAsync(context, $"voice/{rest}", ct);
            return Results.Empty;
        });

        // Full text of a stored headline's article (or its feed summary with the reason), for reading aloud.
        api.MapGet("/news/article", async (string id, NewsStore news, EngineClient engine, CancellationToken ct) =>
        {
            if (news.Find(id) is not { } item)
            {
                return Results.NotFound(new { error = "not_found", message = "Berita tidak ditemukan." });
            }
            var response = await engine.PostJsonAsync("news/article", new { url = item.Link, title = item.Title, summary = item.Summary }, ct);
            return Results.Content(response.Body, "application/json", statusCode: response.Status);
        });

        api.MapGet("/training/summary", async (EngineClient engine, CancellationToken ct) =>
        {
            var response = await engine.GetAsync("training/summary", ct);
            return Results.Content(response.Body, "application/json", statusCode: response.Status);
        });

        api.MapGet("/training/asset", async (string symbol, string? tf, EngineClient engine, CancellationToken ct) =>
        {
            tf = string.IsNullOrWhiteSpace(tf) ? "1h" : tf.ToLowerInvariant();
            if (!Timeframes.Contains(tf))
            {
                return Results.BadRequest(new { error = "invalid_timeframe", message = $"Timeframe {tf} tidak dikenal" });
            }
            var response = await engine.GetAsync($"training/asset?symbol={Uri.EscapeDataString(symbol)}&tf={tf}", ct);
            return Results.Content(response.Body, "application/json", statusCode: response.Status);
        });

        api.MapGet("/training/chart", async (string symbol, string? tf, int? bars, EngineClient engine, CancellationToken ct) =>
        {
            tf = string.IsNullOrWhiteSpace(tf) ? "1h" : tf.ToLowerInvariant();
            if (!Timeframes.Contains(tf))
            {
                return Results.BadRequest(new { error = "invalid_timeframe", message = $"Timeframe {tf} tidak dikenal" });
            }
            var window = bars is > 0 ? $"&bars={bars.Value}" : "";
            var response = await engine.GetAsync($"training/chart?symbol={Uri.EscapeDataString(symbol)}&tf={tf}{window}", ct);
            return Results.Content(response.Body, "application/json", statusCode: response.Status);
        });

        // Training lab: train chosen markets × timeframes on N candles in the engine, at full CPU speed.
        api.MapGet("/training/lab", async (EngineClient engine, CancellationToken ct) =>
        {
            var response = await engine.GetAsync("training/lab", ct);
            return Results.Content(response.Body, "application/json", statusCode: response.Status);
        });

        api.MapPost("/training/lab/start", async (LabRequest body, EngineClient engine, CancellationToken ct) =>
        {
            var response = await engine.PostJsonAsync("training/lab/start", new { markets = body.Markets, tfs = body.Tfs, bars = body.Bars }, ct);
            return Results.Content(response.Body, "application/json", statusCode: response.Status);
        });

        api.MapPost("/training/lab/stop", async (EngineClient engine, CancellationToken ct) =>
        {
            var response = await engine.PostJsonAsync("training/lab/stop", new { }, ct);
            return Results.Content(response.Body, "application/json", statusCode: response.Status);
        });

        api.MapGet("/benefits", async (string? market, EngineClient engine, CancellationToken ct) =>
        {
            var m = string.IsNullOrWhiteSpace(market) ? "all" : market.ToLowerInvariant();
            if (!MarketKeys.Contains(m))
            {
                return Results.BadRequest(new { error = "invalid_argument", message = "market tidak dikenal" });
            }
            var response = await engine.GetAsync($"benefits?market={m}", ct);
            return Results.Content(response.Body, "application/json", statusCode: response.Status);
        });

        api.MapGet("/company", async (string symbol, EngineClient engine, CancellationToken ct) =>
        {
            var response = await engine.GetAsync($"company?symbol={Uri.EscapeDataString(symbol)}", ct);
            return Results.Content(response.Body, "application/json", statusCode: response.Status);
        });

        api.MapGet("/live", async (string symbol, EngineClient engine, CancellationToken ct) =>
        {
            var response = await engine.GetAsync($"live?symbol={Uri.EscapeDataString(symbol)}", ct);
            return Results.Content(response.Body, "application/json", statusCode: response.Status);
        });

        api.MapGet("/ai/models", async (EngineClient engine, CancellationToken ct) =>
        {
            var response = await engine.GetAsync("ai/models?provider=openrouter", ct);
            return Results.Content(response.Body, "application/json", statusCode: response.Status);
        });

        api.MapGet("/settings/ai", (AiSettingsStore settings) => Results.Json(AiSettingsView(settings)));

        api.MapPost("/settings/ai", (AiSettingsRequest body, AiSettingsStore settings) =>
        {
            var current = settings.Options;
            var provider = body.Provider?.Trim().ToLowerInvariant() ?? current.Provider;
            if (!AiSettingsStore.IsProvider(provider))
            {
                return Results.BadRequest(new { error = "invalid_provider", message = "Provider AI tidak dikenal." });
            }
            var next = current with
            {
                Provider = provider,
                OpenRouterModel = body.OpenRouterModel?.Trim() is { Length: > 0 } m ? m : current.OpenRouterModel,
                CustomBaseUrl = body.CustomBaseUrl?.Trim().TrimEnd('/') ?? current.CustomBaseUrl,
                CustomModel = body.CustomModel?.Trim() ?? current.CustomModel,
            };
            if (InvalidOption(next) is { } optionError)
            {
                return Results.BadRequest(new { error = "invalid_option", message = optionError });
            }
            var keyProvider = body.KeyProvider?.Trim().ToLowerInvariant() ?? provider;
            if (!string.IsNullOrWhiteSpace(body.ApiKey))
            {
                if (!AiSettingsStore.IsProvider(keyProvider))
                {
                    return Results.BadRequest(new { error = "invalid_provider", message = "Provider AI tidak dikenal." });
                }
                if (InvalidKey(keyProvider, body.ApiKey.Trim()) is { } keyError)
                {
                    return Results.BadRequest(new { error = "invalid_key", message = keyError });
                }
                settings.SaveKey(keyProvider, body.ApiKey.Trim());
            }
            if (next != current)
            {
                settings.SaveOptions(next);
            }
            return Results.Json(AiSettingsView(settings));
        });

        api.MapDelete("/settings/ai", (string? provider, AiSettingsStore settings) =>
        {
            var target = provider?.Trim().ToLowerInvariant() ?? settings.Options.Provider;
            if (!AiSettingsStore.IsProvider(target))
            {
                return Results.BadRequest(new { error = "invalid_provider", message = "Provider AI tidak dikenal." });
            }
            settings.DeleteKey(target);
            return Results.Json(AiSettingsView(settings));
        });
    }

    public sealed record AiRequest(string Symbol);

    public sealed record NewsAiRequest(string Id, string? Symbol);

    public sealed record LabRequest(string[] Markets, string[] Tfs, int Bars);

    public sealed record AiSettingsRequest(string? Provider, string? ApiKey, string? KeyProvider, string? OpenRouterModel, string? CustomBaseUrl, string? CustomModel);

    /// <summary>Settings as shown to the browser: which keys exist and where from, never the keys themselves.</summary>
    private static object AiSettingsView(AiSettingsStore settings)
    {
        var options = settings.Options;
        var providers = AiSettingsStore.Providers.Select(p =>
        {
            var saved = settings.GetKey(p.Key) is not null;
            var env = AiSettingsStore.EnvironmentKeyPresent(p.Key);
            return new
            {
                id = p.Key,
                name = p.Value.Name,
                envVar = p.Value.EnvVar,
                configured = saved || env || p.Key == "custom" && options.CustomBaseUrl.Length > 0,
                source = saved ? "saved" : env ? "environment" : null,
            };
        }).ToList();
        return new
        {
            provider = options.Provider,
            configured = providers.First(p => p.id == options.Provider).configured,
            providers,
            options = new { openrouterModel = options.OpenRouterModel, customBaseUrl = options.CustomBaseUrl, customModel = options.CustomModel },
            encrypted = OperatingSystem.IsWindows(),
            anthropicModel = "claude-opus-5-5",
        };
    }

    private static string? InvalidKey(string provider, string key)
    {
        if (key.Length > 500 || key.Any(c => char.IsWhiteSpace(c) || char.IsControl(c)))
        {
            return "Format API key tidak valid.";
        }
        return provider switch
        {
            "openrouter" when !key.StartsWith("sk-or-", StringComparison.Ordinal) || key.Length < 20 => "Format API key OpenRouter tidak dikenali (harus diawali sk-or-).",
            "anthropic" when !key.StartsWith("sk-ant-", StringComparison.Ordinal) || key.Length < 20 => "Format API key Anthropic tidak dikenali (harus diawali sk-ant-).",
            "custom" when key.Length < 8 => "API key terlalu pendek.",
            _ => null,
        };
    }

    private static string? InvalidOption(AiOptions options)
    {
        static bool BadModel(string m) => m.Length > 200 || m.Any(c => char.IsWhiteSpace(c) || char.IsControl(c));
        if (BadModel(options.OpenRouterModel) || BadModel(options.CustomModel))
        {
            return "Nama model tidak valid.";
        }
        if (options.CustomBaseUrl.Length > 0 && (!Uri.TryCreate(options.CustomBaseUrl, UriKind.Absolute, out var uri) || uri.Scheme is not ("http" or "https")))
        {
            return "Base URL harus alamat http(s) lengkap, mis. https://api.example.com/v1.";
        }
        return null;
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
