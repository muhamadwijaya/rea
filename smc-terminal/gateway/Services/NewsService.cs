using System.Net;
using System.Net.Http.Headers;
using System.Text.Json;
using Microsoft.Extensions.Options;
using SmcTerminal.Gateway.Models;

namespace SmcTerminal.Gateway.Services;

/// <summary>In-memory, bounded, de-duplicated news store. Owner of all news aggregation.</summary>
public sealed class NewsStore(IOptions<TerminalOptions> options)
{
    private readonly object _gate = new();
    private readonly Dictionary<string, NewsItem> _byId = new();
    private readonly Dictionary<string, string> _titleKeys = new();
    private List<NewsItem> _sorted = new();

    public DateTimeOffset? LastUpdate { get; private set; }

    public static readonly string[] Categories = { "forex", "us", "idx", "commodity", "macro", "crypto" };

    /// <summary>Adds unseen items; returns the ones that were actually new.</summary>
    public List<NewsItem> AddRange(IEnumerable<NewsItem> items, DateTimeOffset now)
    {
        var added = new List<NewsItem>();
        var cutoff = now.AddHours(-options.Value.MaxNewsAgeHours);
        lock (_gate)
        {
            foreach (var item in items)
            {
                if (item.Published < cutoff || _byId.ContainsKey(item.Id))
                {
                    continue;
                }
                // The same headline syndicated by several feeds is kept once.
                var titleKey = TitleKey(item.Title);
                if (_titleKeys.ContainsKey(titleKey))
                {
                    continue;
                }
                _byId[item.Id] = item;
                _titleKeys[titleKey] = item.Id;
                added.Add(item);
            }
            if (added.Count > 0)
            {
                Rebuild(cutoff);
                LastUpdate = now;
            }
        }
        return added;
    }

    public void ApplySentiment(IReadOnlyDictionary<string, SentimentScore> scores)
    {
        lock (_gate)
        {
            foreach (var (id, s) in scores)
            {
                if (_byId.TryGetValue(id, out var item))
                {
                    _byId[id] = item with { Sentiment = s.Score, SentimentLabel = s.Label, Terms = s.Terms, Assets = s.Assets };
                }
            }
            _sorted = _sorted.Select(i => _byId.TryGetValue(i.Id, out var updated) ? updated : i).ToList();
        }
    }

    public List<NewsItem> Unscored(int max)
    {
        lock (_gate)
        {
            return _sorted.Where(i => i.Sentiment is null).Take(max).ToList();
        }
    }

    public List<NewsItem> Query(string? category, string? asset, string? sentiment, string? q, int limit, int offset = 0)
    {
        lock (_gate)
        {
            IEnumerable<NewsItem> query = _sorted;
            if (!string.IsNullOrWhiteSpace(category) && category != "all")
            {
                query = query.Where(i => i.Category == category);
            }
            if (!string.IsNullOrWhiteSpace(asset))
            {
                query = query.Where(i => i.Assets.Contains(asset, StringComparer.OrdinalIgnoreCase));
            }
            if (!string.IsNullOrWhiteSpace(sentiment) && sentiment != "all")
            {
                query = query.Where(i => i.SentimentLabel == sentiment);
            }
            if (!string.IsNullOrWhiteSpace(q))
            {
                query = query.Where(i => i.Title.Contains(q, StringComparison.OrdinalIgnoreCase) ||
                                         i.Summary.Contains(q, StringComparison.OrdinalIgnoreCase));
            }
            return query.Skip(Math.Max(0, offset)).Take(Math.Clamp(limit, 1, 300)).ToList();
        }
    }

    public int CountSince(DateTimeOffset since)
    {
        lock (_gate)
        {
            return _sorted.Count(i => i.Published >= since);
        }
    }

    public int Total
    {
        get { lock (_gate) { return _sorted.Count; } }
    }

    /// <summary>Average headline sentiment per category and per tagged asset over a window.</summary>
    public object SentimentSummary(TimeSpan window, DateTimeOffset now)
    {
        List<NewsItem> recent;
        lock (_gate)
        {
            recent = _sorted.Where(i => i.Published >= now - window && i.Sentiment is not null).ToList();
        }

        static SentimentBucket Summarize(string key, IEnumerable<NewsItem> items)
        {
            var list = items.ToList();
            return new SentimentBucket(
                key,
                list.Count,
                list.Count == 0 ? 0 : Math.Round(list.Average(i => i.Sentiment!.Value), 3),
                list.Count(i => i.SentimentLabel == "bullish"),
                list.Count(i => i.SentimentLabel == "bearish"),
                list.Count(i => i.SentimentLabel == "neutral"));
        }

        return new
        {
            windowHours = window.TotalHours,
            total = recent.Count,
            overall = Summarize("all", recent),
            categories = Categories.Select(c => Summarize(c, recent.Where(i => i.Category == c))).ToList(),
            assets = recent.SelectMany(i => i.Assets).Distinct()
                .Select(a => Summarize(a, recent.Where(i => i.Assets.Contains(a))))
                .OrderByDescending(x => x.Count)
                .ToList(),
        };
    }

    private void Rebuild(DateTimeOffset cutoff)
    {
        var max = options.Value.MaxNewsItems;
        var keep = _byId.Values.Where(i => i.Published >= cutoff)
            .OrderByDescending(i => i.Published).ThenByDescending(i => i.Received)
            .Take(max).ToList();
        var keepIds = keep.Select(i => i.Id).ToHashSet();
        foreach (var id in _byId.Keys.Where(id => !keepIds.Contains(id)).ToList())
        {
            _byId.Remove(id);
        }
        foreach (var key in _titleKeys.Where(kv => !keepIds.Contains(kv.Value)).Select(kv => kv.Key).ToList())
        {
            _titleKeys.Remove(key);
        }
        _sorted = keep;
    }

    private static string TitleKey(string title) =>
        new string(title.ToLowerInvariant().Where(char.IsLetterOrDigit).Take(90).ToArray());
}

public sealed record SentimentBucket(string Key, int Count, double Score, int Bullish, int Bearish, int Neutral);

public sealed record SentimentScore(double Score, string Label, IReadOnlyList<string> Terms, IReadOnlyList<string> Assets);

/// <summary>Polls every configured feed, scores new headlines through the engine and pushes them live.</summary>
public sealed class NewsWorker(
    IHttpClientFactory httpFactory,
    NewsStore store,
    EngineClient engine,
    Broadcaster broadcaster,
    MarketState state,
    IOptions<TerminalOptions> options,
    ILogger<NewsWorker> log) : BackgroundService
{
    public const string HttpName = "feeds";
    private readonly Dictionary<string, FeedStatus> _status = new();

    public static IReadOnlyList<FeedStatus> Statuses { get; private set; } = Array.Empty<FeedStatus>();

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        foreach (var feed in options.Value.Feeds)
        {
            _status[feed.Url] = new FeedStatus { Name = feed.Name, Url = feed.Url, Category = feed.Category };
        }
        Statuses = _status.Values.ToList();

        var interval = TimeSpan.FromSeconds(Math.Max(20, options.Value.NewsPollSeconds));
        using var timer = new PeriodicTimer(interval);
        var first = true;
        do
        {
            try
            {
                await PollOnce(first, stoppingToken);
                first = false;
            }
            catch (Exception ex) when (!stoppingToken.IsCancellationRequested)
            {
                log.LogError(ex, "News poll failed");
            }
        } while (await timer.WaitForNextTickAsync(stoppingToken));
    }

    private async Task PollOnce(bool first, CancellationToken ct)
    {
        var now = DateTimeOffset.UtcNow;
        using var gate = new SemaphoreSlim(6);
        var tasks = options.Value.Feeds.Select(async feed =>
        {
            await gate.WaitAsync(ct);
            try
            {
                return await FetchFeed(feed, now, ct);
            }
            finally
            {
                gate.Release();
            }
        });
        var batches = await Task.WhenAll(tasks);
        Statuses = _status.Values.ToList();

        var added = store.AddRange(batches.SelectMany(b => b), now);
        await ScoreUnscored(ct);

        if (added.Count == 0)
        {
            return;
        }
        log.LogInformation("News: {Count} new headlines (store {Total})", added.Count, store.Total);
        if (first)
        {
            await broadcaster.Send("newsReset", new { total = store.Total }, ct);
            return;
        }
        var ids = added.Select(a => a.Id).ToHashSet();
        var fresh = store.Query(null, null, null, null, 300).Where(i => ids.Contains(i.Id)).ToList();
        await broadcaster.Send("news", fresh, ct);
        foreach (var item in fresh.Where(i => Math.Abs(i.Sentiment ?? 0) >= 0.75 && i.Published > now.AddMinutes(-30)).Take(3))
        {
            await state.AddAlert(new AlertItem(
                FeedParser.HashId("news" + item.Id), DateTimeOffset.UtcNow, "news",
                "info", item.Assets.FirstOrDefault(), $"Berita {item.SentimentLabel}: {item.Source}", item.Title,
                item.SentimentLabel, null), ct);
        }
    }

    private async Task ScoreUnscored(CancellationToken ct)
    {
        var pending = store.Unscored(400);
        if (pending.Count == 0)
        {
            return;
        }
        var payload = new { items = pending.Select(i => new { id = i.Id, title = i.Title, summary = i.Summary }) };
        var response = await engine.PostJsonAsync("sentiment", payload, ct);
        if (!response.Ok)
        {
            return;
        }
        using var doc = JsonDocument.Parse(response.Body);
        var scores = new Dictionary<string, SentimentScore>();
        foreach (var e in doc.RootElement.GetProperty("items").EnumerateArray())
        {
            var id = e.GetProperty("id").GetString();
            if (id is null)
            {
                continue;
            }
            scores[id] = new SentimentScore(
                e.GetProperty("score").GetDouble(),
                e.GetProperty("label").GetString() ?? "neutral",
                e.GetProperty("terms").EnumerateArray().Select(x => x.GetString() ?? "").ToList(),
                e.GetProperty("assets").EnumerateArray().Select(x => x.GetString() ?? "").ToList());
        }
        store.ApplySentiment(scores);
    }

    private async Task<List<NewsItem>> FetchFeed(FeedConfig feed, DateTimeOffset now, CancellationToken ct)
    {
        var status = _status[feed.Url];
        status.LastAttempt = now;
        try
        {
            using var request = new HttpRequestMessage(HttpMethod.Get, feed.Url);
            if (status.ETag is not null && EntityTagHeaderValue.TryParse(status.ETag, out var etag))
            {
                request.Headers.IfNoneMatch.Add(etag);
            }
            if (status.LastModified is not null && DateTimeOffset.TryParse(status.LastModified, out var modified))
            {
                request.Headers.IfModifiedSince = modified;
            }
            using var response = await httpFactory.CreateClient(HttpName).SendAsync(request, ct);
            if (response.StatusCode == HttpStatusCode.NotModified)
            {
                status.LastOk = now;
                status.LastError = null;
                status.ConsecutiveFailures = 0;
                return new List<NewsItem>();
            }
            if (!response.IsSuccessStatusCode)
            {
                throw new HttpRequestException($"HTTP {(int)response.StatusCode}");
            }
            status.ETag = response.Headers.ETag?.ToString();
            status.LastModified = response.Content.Headers.LastModified?.ToString("R");
            var xml = await response.Content.ReadAsStringAsync(ct);
            var items = FeedParser.Parse(xml, feed, now);
            status.LastOk = now;
            status.LastError = null;
            status.LastItemCount = items.Count;
            status.ConsecutiveFailures = 0;
            return items;
        }
        catch (Exception ex) when (!ct.IsCancellationRequested)
        {
            status.ConsecutiveFailures++;
            status.LastError = ex.Message.Length > 160 ? ex.Message[..160] : ex.Message;
            if (status.ConsecutiveFailures == 1)
            {
                log.LogWarning("Feed {Feed} failed: {Error}", feed.Name, status.LastError);
            }
            return new List<NewsItem>();
        }
    }
}
