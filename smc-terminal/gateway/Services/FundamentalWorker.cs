using System.Text.Json;

namespace SmcTerminal.Gateway.Services;

/// <summary>
/// Sends the calendar and recent headlines to the engine's fundamental model every few minutes
/// (the engine extracts actual values, predicts the currency move and checks the real reaction).
/// </summary>
public sealed class FundamentalWorker(
    EngineClient engine, CalendarStore calendar, NewsStore news, Broadcaster broadcaster, ILogger<FundamentalWorker> log) : BackgroundService
{
    public static string? LatestJson { get; private set; }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        await Task.Delay(TimeSpan.FromSeconds(20), stoppingToken);
        using var timer = new PeriodicTimer(TimeSpan.FromMinutes(1));
        var last = DateTimeOffset.MinValue;
        do
        {
            try
            {
                // Every 5 minutes, or as soon as the calendar first becomes available.
                if (DateTimeOffset.UtcNow - last >= TimeSpan.FromMinutes(5) || LatestJson is null)
                {
                    if (await Refresh(stoppingToken))
                    {
                        last = DateTimeOffset.UtcNow;
                    }
                }
            }
            catch (Exception ex) when (!stoppingToken.IsCancellationRequested)
            {
                log.LogWarning("Fundamental refresh failed: {Message}", ex.Message);
            }
        } while (await timer.WaitForNextTickAsync(stoppingToken));
    }

    private async Task<bool> Refresh(CancellationToken ct)
    {
        var events = calendar.All();
        if (events.Count == 0)
        {
            return false;
        }
        var headlines = news.Query(null, null, null, null, null, 500, 0, DateTimeOffset.UtcNow.AddDays(-8))
            .Select(i => new { id = i.Id, title = i.Title, published = i.Published, source = i.Source, link = i.Link });
        var response = await engine.PostJsonAsync("fundamental", new { events, news = headlines }, ct);
        if (!response.Ok)
        {
            return false;
        }
        LatestJson = response.Body;
        using var doc = JsonDocument.Parse(response.Body);
        await broadcaster.Send("fundamental", new { accuracy = doc.RootElement.GetProperty("accuracy").Clone() }, ct);
        return true;
    }
}
