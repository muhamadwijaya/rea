using System.Collections.Concurrent;
using System.Text.Json;
using SmcTerminal.Gateway.Models;

namespace SmcTerminal.Gateway.Services;

/// <summary>On-demand headlines for a single instrument (Yahoo Finance RSS or Google News), cached briefly.</summary>
public sealed class SymbolNewsService(IHttpClientFactory httpFactory, EngineClient engine, NewsStore store, ILogger<SymbolNewsService> log)
{
    private static readonly TimeSpan Ttl = TimeSpan.FromMinutes(5);
    private readonly ConcurrentDictionary<string, (DateTimeOffset Expires, List<NewsItem> Items)> _cache = new();

    public async Task<(int Status, object Body)> GetAsync(string symbol, CancellationToken ct)
    {
        var resolved = await engine.GetAsync($"resolve?q={Uri.EscapeDataString(symbol)}", ct);
        if (!resolved.Ok)
        {
            return (resolved.Status, JsonDocument.Parse(resolved.Body).RootElement.Clone());
        }
        using var doc = JsonDocument.Parse(resolved.Body);
        var inst = doc.RootElement;
        var key = inst.GetProperty("symbol").GetString()!;
        var now = DateTimeOffset.UtcNow;
        if (_cache.TryGetValue(key, out var hit) && hit.Expires > now)
        {
            return (200, new { symbol = key, items = hit.Items, cached = true });
        }

        var name = inst.GetProperty("name").GetString() ?? key;
        var yahoo = inst.GetProperty("yahoo").GetString() ?? key;
        var cls = inst.GetProperty("assetClass").GetString() ?? "us";
        var feed = FeedFor(key, name, yahoo, cls);
        var items = new List<NewsItem>();
        try
        {
            var xml = await httpFactory.CreateClient(NewsWorker.HttpName).GetStringAsync(feed.Url, ct);
            items = FeedParser.Parse(xml, feed, now).OrderByDescending(i => i.Published).Take(25).ToList();
        }
        catch (Exception ex) when (!ct.IsCancellationRequested)
        {
            log.LogWarning("Symbol news {Symbol} failed: {Error}", key, ex.Message);
        }

        // Headlines already in the live store that mention the symbol.
        var local = store.Query(null, null, null, key, 15);
        items = items.Concat(local).GroupBy(i => i.Id).Select(g => g.First())
            .OrderByDescending(i => i.Published).Take(30).ToList();
        items = await Score(items, ct);
        _cache[key] = (now + Ttl, items);
        return (200, new { symbol = key, source = feed.Name, items, cached = false });
    }

    private async Task<List<NewsItem>> Score(List<NewsItem> items, CancellationToken ct)
    {
        var pending = items.Where(i => i.Sentiment is null).ToList();
        if (pending.Count == 0)
        {
            return items;
        }
        var response = await engine.PostJsonAsync("sentiment",
            new { items = pending.Select(i => new { id = i.Id, title = i.Title, summary = i.Summary }) }, ct);
        if (!response.Ok)
        {
            return items;
        }
        using var doc = JsonDocument.Parse(response.Body);
        var scores = doc.RootElement.GetProperty("items").EnumerateArray()
            .ToDictionary(e => e.GetProperty("id").GetString()!, e => e.Clone());
        return items.Select(i => scores.TryGetValue(i.Id, out var s)
            ? i with
            {
                Sentiment = s.GetProperty("score").GetDouble(),
                SentimentLabel = s.GetProperty("label").GetString(),
                Terms = s.GetProperty("terms").EnumerateArray().Select(x => x.GetString() ?? "").ToList(),
                Assets = s.GetProperty("assets").EnumerateArray().Select(x => x.GetString() ?? "").ToList(),
            }
            : i).ToList();
    }

    private static FeedConfig FeedFor(string symbol, string name, string yahoo, string cls)
    {
        static string Google(string query, string lang) => lang == "id"
            ? $"https://news.google.com/rss/search?q={Uri.EscapeDataString(query)}+when:7d&hl=id&gl=ID&ceid=ID:id"
            : $"https://news.google.com/rss/search?q={Uri.EscapeDataString(query)}+when:7d&hl=en-US&gl=US&ceid=US:en";

        return cls switch
        {
            "us" or "crypto" => new FeedConfig
            {
                Name = "Yahoo Finance",
                Url = $"https://feeds.finance.yahoo.com/rss/2.0/headline?s={Uri.EscapeDataString(yahoo)}&region=US&lang=en-US",
                Category = cls == "us" ? "us" : "crypto",
            },
            "idx" => new FeedConfig { Name = "Google News", Url = Google($"saham {symbol} {name}", "id"), Category = "idx", Lang = "id" },
            "forex" when symbol.Length == 6 => new FeedConfig
            {
                Name = "Google News",
                Url = Google($"{symbol[..3]}/{symbol[3..]} OR {symbol}", "en"),
                Category = "forex",
            },
            "commodity" => new FeedConfig { Name = "Google News", Url = Google($"{name.Split(' ')[0]} price", "en"), Category = "commodity" },
            _ => new FeedConfig { Name = "Google News", Url = Google(name, "en"), Category = "macro" },
        };
    }
}
