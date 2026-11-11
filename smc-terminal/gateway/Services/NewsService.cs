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
                var classified = item with { Markets = Markets.Classify(item.Category, item.Assets) };
                _byId[item.Id] = classified;
                _titleKeys[titleKey] = item.Id;
                added.Add(classified);
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
                    _byId[id] = item.WithSentiment(s);
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

    public NewsItem? Find(string id)
    {
        lock (_gate)
        {
            return _byId.GetValueOrDefault(id);
        }
    }

    public List<NewsItem> Query(string? market, string? category, string? asset, string? sentiment, string? q, int limit,
        int offset = 0, DateTimeOffset? since = null, string? symbol = null)
    {
        lock (_gate)
        {
            IEnumerable<NewsItem> query = _sorted;
            if (since is { } from)
            {
                query = query.Where(i => i.Published >= from);
            }
            if (!string.IsNullOrWhiteSpace(symbol))
            {
                query = query.Where(i => i.Symbols.Any(t => t.Symbol.Equals(symbol, StringComparison.OrdinalIgnoreCase)));
            }
            if (Markets.IsKnown(market))
            {
                query = query.Where(i => i.Markets.Contains(market!));
            }
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
            return query.Skip(Math.Max(0, offset)).Take(Math.Clamp(limit, 1, 500)).ToList();
        }
    }

    public int CountSince(DateTimeOffset since, string? market = null)
    {
        lock (_gate)
        {
            return _sorted.Count(i => i.Published >= since && (market is null || i.Markets.Contains(market)));
        }
    }

    public int Total
    {
        get { lock (_gate) { return _sorted.Count; } }
    }

    /// <summary>Average headline sentiment per category and per tagged asset over a window.</summary>
    public object SentimentSummary(TimeSpan window, DateTimeOffset now, string? market = null)
    {
        List<NewsItem> recent;
        lock (_gate)
        {
            recent = _sorted.Where(i => i.Published >= now - window && i.Sentiment is not null &&
                                        (market is null || i.Markets.Contains(market))).ToList();
        }
        var categories = Markets.IsKnown(market) ? Markets.CategoriesOf(market!) : Categories;

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
            market,
            total = recent.Count,
            overall = Summarize("all", recent),
            categories = categories.Select(c => Summarize(c, recent.Where(i => i.Category == c))).ToList(),
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

public sealed record SentimentScore(
    double Score, string Label, IReadOnlyList<string> Terms, IReadOnlyList<string> Assets, IReadOnlyList<SymbolTag> Symbols);

public static class NewsItemExtensions
{
    /// <summary>Applies an engine sentiment result; market membership follows the newly tagged assets.</summary>
    public static NewsItem WithSentiment(this NewsItem item, SentimentScore s) => item with
    {
        Sentiment = s.Score,
        SentimentLabel = s.Label,
        Terms = s.Terms,
        Assets = s.Assets,
        Symbols = s.Symbols,
        Markets = Markets.Classify(item.Category, s.Assets, s.Symbols),
    };
}

/// <summary>Polls every configured feed on its own loop, scores new headlines through the engine and pushes them live.</summary>
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
            _status[feed.Url] = new FeedStatus
            {
                Name = feed.Name, Url = feed.Url, Category = feed.Category, Markets = Markets.ForCategory(feed.Category),
            };
        }
        Statuses = _status.Values.ToList();

        // History first (all feeds once, one reset event), then every feed on its own loop: a new headline
        // is scored and pushed as soon as its feed returns it, never waiting for slower feeds.
        try
        {
            await PollAll(stoppingToken);
        }
        catch (Exception ex) when (!stoppingToken.IsCancellationRequested)
        {
            log.LogError(ex, "Initial news poll failed");
        }
        var baseInterval = TimeSpan.FromSeconds(Math.Max(10, options.Value.NewsPollSeconds));
        await Task.WhenAll(options.Value.Feeds.Select(feed => FeedLoop(feed, baseInterval, stoppingToken)));
    }

    private async Task PollAll(CancellationToken ct)
    {
        var now = DateTimeOffset.UtcNow;
        using var gate = new SemaphoreSlim(8);
        var batches = await Task.WhenAll(options.Value.Feeds.Select(async feed =>
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
        }));
        Statuses = _status.Values.ToList();
        var added = store.AddRange(batches.SelectMany(b => b), now);
        await ScoreUnscored(ct);
        log.LogInformation("News: {Count} headlines loaded (store {Total})", added.Count, store.Total);
        await broadcaster.Send("newsReset", new { total = store.Total }, ct);
    }

    private async Task FeedLoop(FeedConfig feed, TimeSpan baseInterval, CancellationToken ct)
    {
        var interval = feed.PollSeconds > 0 ? TimeSpan.FromSeconds(Math.Max(10, feed.PollSeconds)) : baseInterval;
        try
        {
            // spread the feeds over the interval so requests do not all fire together
            await Task.Delay(TimeSpan.FromMilliseconds(Random.Shared.NextDouble() * interval.TotalMilliseconds), ct);
            while (!ct.IsCancellationRequested)
            {
                try
                {
                    await PollFeed(feed, ct);
                }
                catch (Exception ex) when (!ct.IsCancellationRequested)
                {
                    log.LogError(ex, "News poll of {Feed} failed", feed.Name);
                }
                var failures = _status[feed.Url].ConsecutiveFailures;
                var wait = failures == 0 ? interval : TimeSpan.FromSeconds(Math.Min(300, interval.TotalSeconds * Math.Pow(2, Math.Min(5, failures))));
                await Task.Delay(wait, ct);
            }
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested)
        {
        }
    }

    private async Task PollFeed(FeedConfig feed, CancellationToken ct)
    {
        var now = DateTimeOffset.UtcNow;
        var items = await FetchFeed(feed, now, ct);
        Statuses = _status.Values.ToList();
        var added = store.AddRange(items, now);
        if (added.Count == 0)
        {
            return;
        }
        await ScoreUnscored(ct);
        var ids = added.Select(a => a.Id).ToHashSet();
        var fresh = store.Query(null, null, null, null, null, 300).Where(i => ids.Contains(i.Id)).ToList();
        if (fresh.Count == 0)
        {
            return;
        }
        log.LogInformation("News: {Count} new from {Feed} (store {Total})", fresh.Count, feed.Name, store.Total);
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
        if (pending.Count > 0)
        {
            store.ApplySentiment(await engine.ScoreSentimentAsync(pending, ct));
        }
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
