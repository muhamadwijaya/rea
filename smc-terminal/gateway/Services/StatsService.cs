using SmcTerminal.Gateway.Models;

namespace SmcTerminal.Gateway.Services;

/// <summary>KPI tiles shown on the dashboard.</summary>
public sealed class StatsService(MarketState state, NewsStore news, CalendarStore calendar)
{
    public object Build()
    {
        var now = DateTimeOffset.UtcNow;
        var rows = state.ScreenerRows.Where(r => !r.Failed).ToList();
        var feeds = NewsWorker.Statuses;
        var localNow = DateTimeOffset.Now;
        var todayEnd = new DateTimeOffset(localNow.Date.AddDays(1), localNow.Offset);
        return new
        {
            generatedAt = now,
            engineOnline = EngineClient.Online,
            engineError = EngineSupervisor.Status.Failure ?? EngineClient.LastError,
            screenerAt = state.ScreenerAt,
            screenerTf = state.ScreenerTf,
            instruments = rows.Count,
            failed = state.ScreenerRows.Count - rows.Count,
            bullish = rows.Count(r => r.Direction?.Contains("bullish") == true),
            bearish = rows.Count(r => r.Direction?.Contains("bearish") == true),
            neutral = rows.Count(r => r.Direction == "neutral"),
            chochRecent = rows.Count(r => r.EventKind == "CHoCH" && r.EventBarsAgo <= 6),
            nearOb = rows.Sum(r => r.NearOb),
            nearFvg = rows.Sum(r => r.NearFvg),
            inDiscount = rows.Count(r => r.ZoneArea == "discount"),
            inPremium = rows.Count(r => r.ZoneArea == "premium"),
            news24h = news.CountSince(now.AddHours(-24)),
            newsByMarket = Markets.All.ToDictionary(m => m, m => news.CountSince(now.AddHours(-24), m)),
            newsTotal = news.Total,
            newsAt = news.LastUpdate,
            feedsOk = feeds.Count(f => f.LastError is null && f.LastOk is not null),
            feedsTotal = feeds.Count,
            highImpactToday = calendar.All().Count(e => e.Impact == "High" && e.Date >= now && e.Date < todayEnd),
            highImpactWeek = calendar.All().Count(e => e.Impact == "High" && e.Date >= now),
            nextHighImpact = calendar.Upcoming(now, TimeSpan.FromDays(7), "high").FirstOrDefault(e => e.Date > now),
            alerts24h = state.AlertsSince(now.AddHours(-24)),
            analyses24h = state.AnalysesSince(now.AddHours(-24)),
            metrics = state.Metrics().TakeLast(60),
        };
    }
}
