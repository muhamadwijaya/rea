using System.Text.Json;

namespace SmcTerminal.Gateway.Services;

/// <summary>
/// Relays the engine's universe quotes (open / last / change of every asset) to the browsers.
/// The engine sweeps Yahoo in batches; this worker polls its snapshot and pushes a compact form:
/// rows of [symbol, last, open, changePct, openChangePct].
/// </summary>
public sealed class QuotesWorker(EngineClient engine, Broadcaster broadcaster, ILogger<QuotesWorker> log) : BackgroundService
{
    public static int Count { get; private set; }
    public static DateTimeOffset? UpdatedAt { get; private set; }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(30));
        do
        {
            try
            {
                var response = await engine.GetAsync("quotes?market=all", stoppingToken);
                if (!response.Ok)
                {
                    continue;
                }
                using var doc = JsonDocument.Parse(response.Body);
                var root = doc.RootElement;
                var rows = new List<object?[]>();
                foreach (var r in root.GetProperty("rows").EnumerateArray())
                {
                    rows.Add(new object?[]
                    {
                        r.GetProperty("symbol").GetString(), Num(r, "last"), Num(r, "open"), Num(r, "changePct"), Num(r, "openChangePct"),
                    });
                }
                Count = rows.Count;
                UpdatedAt = DateTimeOffset.UtcNow;
                await broadcaster.Send("quotes", new
                {
                    t = UpdatedAt,
                    sweeps = root.GetProperty("sweeps").GetInt32(),
                    progress = root.GetProperty("progress").Clone(),
                    rows,
                }, stoppingToken);
            }
            catch (Exception ex) when (!stoppingToken.IsCancellationRequested)
            {
                log.LogWarning("Quotes relay failed: {Message}", ex.Message);
            }
        } while (await timer.WaitForNextTickAsync(stoppingToken));
    }

    private static double? Num(JsonElement e, string name) =>
        e.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.Number ? Math.Round(v.GetDouble(), 6) : null;
}
