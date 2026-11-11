using System.Text.Json;
using Microsoft.Extensions.Options;
using SmcTerminal.Gateway.Models;

namespace SmcTerminal.Gateway.Services;

public sealed class CalendarStore
{
    private readonly object _gate = new();
    private List<CalendarEvent> _events = new();

    public DateTimeOffset? LastUpdate { get; private set; }
    public string? LastError { get; set; }

    public void Replace(List<CalendarEvent> events, DateTimeOffset now)
    {
        lock (_gate)
        {
            _events = events.OrderBy(e => e.Date).ToList();
            LastUpdate = now;
            LastError = null;
        }
    }

    public List<CalendarEvent> All()
    {
        lock (_gate)
        {
            return _events.ToList();
        }
    }

    public List<CalendarEvent> Upcoming(DateTimeOffset now, TimeSpan window, string? minImpact = null)
    {
        lock (_gate)
        {
            return _events.Where(e => e.Date >= now.AddMinutes(-5) && e.Date <= now + window)
                .Where(e => minImpact is null || ImpactRank(e.Impact) >= ImpactRank(minImpact))
                .ToList();
        }
    }

    public static int ImpactRank(string impact) => impact.ToLowerInvariant() switch
    {
        "high" => 3,
        "medium" => 2,
        "low" => 1,
        _ => 0,
    };
}

/// <summary>Weekly economic calendar (ForexFactory JSON export) plus pre-event alerts.</summary>
public sealed class CalendarWorker(
    IHttpClientFactory httpFactory,
    CalendarStore store,
    MarketState state,
    Broadcaster broadcaster,
    IOptions<TerminalOptions> options,
    ILogger<CalendarWorker> log) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        var refresh = TimeSpan.FromMinutes(Math.Max(5, options.Value.CalendarRefreshMinutes));
        var lastFetch = DateTimeOffset.MinValue;
        using var timer = new PeriodicTimer(TimeSpan.FromMinutes(1));
        do
        {
            var now = DateTimeOffset.UtcNow;
            // Retry sooner after a failed download, otherwise respect the refresh interval.
            var due = store.LastError is not null ? TimeSpan.FromMinutes(5) : refresh;
            if (now - lastFetch >= due)
            {
                lastFetch = now;
                await Fetch(now, stoppingToken);
            }
            await CheckAlerts(now, stoppingToken);
        } while (await timer.WaitForNextTickAsync(stoppingToken));
    }

    private async Task Fetch(DateTimeOffset now, CancellationToken ct)
    {
        try
        {
            var json = await httpFactory.CreateClient(NewsWorker.HttpName).GetStringAsync(options.Value.CalendarUrl, ct);
            using var doc = JsonDocument.Parse(json);
            var events = new List<CalendarEvent>();
            foreach (var e in doc.RootElement.EnumerateArray())
            {
                var title = e.TryGetProperty("title", out var t) ? t.GetString() ?? "" : "";
                var country = e.TryGetProperty("country", out var c) ? c.GetString() ?? "" : "";
                var dateText = e.TryGetProperty("date", out var d) ? d.GetString() : null;
                if (string.IsNullOrEmpty(title) || !DateTimeOffset.TryParse(dateText, out var date))
                {
                    continue;
                }
                events.Add(new CalendarEvent(
                    FeedParser.HashId($"{title}|{country}|{dateText}"),
                    title,
                    country,
                    date.ToUniversalTime(),
                    e.TryGetProperty("impact", out var i) ? i.GetString() ?? "" : "",
                    e.TryGetProperty("forecast", out var f) ? f.GetString() ?? "" : "",
                    e.TryGetProperty("previous", out var p) ? p.GetString() ?? "" : ""));
            }
            store.Replace(events, now);
            log.LogInformation("Calendar: {Count} events", events.Count);
            await broadcaster.Send("calendar", new { count = events.Count, updated = now }, ct);
        }
        catch (Exception ex) when (!ct.IsCancellationRequested)
        {
            store.LastError = ex.Message;
            log.LogWarning("Calendar fetch failed: {Error}", ex.Message);
        }
    }

    private async Task CheckAlerts(DateTimeOffset now, CancellationToken ct)
    {
        var window = TimeSpan.FromMinutes(Math.Max(5, options.Value.CalendarAlertMinutes));
        foreach (var e in store.Upcoming(now, window, "high").Where(e => e.Date > now))
        {
            var minutes = (int)Math.Ceiling((e.Date - now).TotalMinutes);
            await state.AddAlert(new AlertItem(
                "cal" + e.Id, now, "calendar", "high", e.Currency,
                $"{minutes} menit lagi: {e.Currency} {e.Title}",
                $"Dampak TINGGI · forecast {Dash(e.Forecast)} · sebelumnya {Dash(e.Previous)}. Volatilitas {e.Currency} berpotensi melonjak.",
                null, null), ct);
        }
    }

    private static string Dash(string value) => string.IsNullOrWhiteSpace(value) ? "-" : value;
}
