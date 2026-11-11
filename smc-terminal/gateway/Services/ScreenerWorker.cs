using System.Globalization;
using System.Text.Json;
using Microsoft.Extensions.Options;
using SmcTerminal.Gateway.Models;

namespace SmcTerminal.Gateway.Services;

/// <summary>
/// Periodically refreshes the SMC screener and the market-direction model from the
/// Python engine, turns fresh structure events into alerts and pushes everything live.
/// </summary>
public sealed class ScreenerWorker(
    EngineClient engine,
    MarketState state,
    Broadcaster broadcaster,
    StatsService stats,
    IOptions<TerminalOptions> options,
    ILogger<ScreenerWorker> log) : BackgroundService
{
    private DateTimeOffset _lastDirection = DateTimeOffset.MinValue;
    private bool _engineWasOnline;

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        var interval = TimeSpan.FromSeconds(Math.Max(30, options.Value.ScreenerRefreshSeconds));
        // Give the Python engine a moment to boot when both are started together.
        await Task.Delay(TimeSpan.FromSeconds(2), stoppingToken);
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(15));
        var lastScreener = DateTimeOffset.MinValue;
        do
        {
            var now = DateTimeOffset.UtcNow;
            var engineDown = !EngineClient.Online;
            if (now - lastScreener >= interval || (engineDown && now - lastScreener >= TimeSpan.FromSeconds(15)))
            {
                lastScreener = now;
                try
                {
                    await RefreshScreener(stoppingToken);
                    if (now - _lastDirection >= TimeSpan.FromSeconds(Math.Max(60, options.Value.DirectionRefreshSeconds)) || state.DirectionJson is null)
                    {
                        await RefreshDirection(stoppingToken);
                    }
                }
                catch (Exception ex) when (!stoppingToken.IsCancellationRequested)
                {
                    log.LogError(ex, "Screener refresh failed");
                }
                await PublishEngineStatus(stoppingToken);
                await broadcaster.Send("stats", stats.Build(), stoppingToken);
            }
        } while (await timer.WaitForNextTickAsync(stoppingToken));
    }

    private async Task PublishEngineStatus(CancellationToken ct)
    {
        var online = EngineClient.Online;
        if (online != _engineWasOnline)
        {
            _engineWasOnline = online;
            await broadcaster.Send("engine", new { online, error = EngineClient.LastError }, ct);
        }
    }

    private async Task RefreshScreener(CancellationToken ct)
    {
        var tf = options.Value.ScreenerTimeframe;
        var response = await engine.GetAsync($"screener?market=all&tf={Uri.EscapeDataString(tf)}", ct);
        if (!response.Ok)
        {
            return;
        }
        using var doc = JsonDocument.Parse(response.Body);
        var rows = doc.RootElement.GetProperty("rows").EnumerateArray().Select(ScreenerRowView.From).ToList();
        var previous = state.ScreenerRows.ToDictionary(r => r.Symbol);
        var firstSnapshot = state.ScreenerAt is null;
        var now = DateTimeOffset.UtcNow;
        state.SetScreener(response.Body, rows, tf, now);
        await broadcaster.Send("screener", doc.RootElement.Clone(), ct);

        foreach (var row in rows.Where(r => !r.Failed))
        {
            previous.TryGetValue(row.Symbol, out var before);
            await DetectAlerts(row, before, firstSnapshot, tf, ct);
        }
    }

    private async Task DetectAlerts(ScreenerRowView row, ScreenerRowView? before, bool firstSnapshot, string tf, CancellationToken ct)
    {
        var tfLabel = tf.ToUpperInvariant();
        var isNewEvent = row.EventKind is not null && row.EventTime is not null &&
                         (before is null ? firstSnapshot && row.EventBarsAgo <= 1 : before.EventTime != row.EventTime || before.EventKind != row.EventKind);
        var important = row.EventScope == "swing" || row.EventKind == "CHoCH";
        if (isNewEvent && important && row.EventBarsAgo <= 2)
        {
            var severity = row.EventKind == "CHoCH" ? (row.EventScope == "swing" ? "high" : "warn") : "info";
            var level = row.EventLevel?.ToString("G6", CultureInfo.InvariantCulture) ?? "-";
            await state.AddAlert(new AlertItem(
                FeedParser.HashId($"{row.Symbol}|{tf}|{row.EventKind}|{row.EventTime}"),
                DateTimeOffset.UtcNow, "structure", severity, row.Symbol,
                $"{row.EventKind} {row.EventDirection} · {row.Symbol} {tfLabel}",
                $"Struktur {row.EventScope} ditembus di {level}. Bias sekarang {row.Label} (skor {row.Score:+0;-0;0}).",
                row.EventDirection, row.Tv), ct);
        }

        if (before is not null && before.Label != row.Label && row.Label is not null && Math.Abs(row.Score - before.Score) >= 15)
        {
            await state.AddAlert(new AlertItem(
                FeedParser.HashId($"{row.Symbol}|{tf}|bias|{row.Label}|{DateTimeOffset.UtcNow:yyyyMMddHH}"),
                DateTimeOffset.UtcNow, "bias", Math.Abs(row.Score) >= 50 ? "warn" : "info", row.Symbol,
                $"Bias {row.Symbol} {tfLabel}: {before.Label} → {row.Label}",
                $"Skor berubah {before.Score:+0;-0;0} → {row.Score:+0;-0;0}.",
                row.Direction, row.Tv), ct);
        }
    }

    private async Task RefreshDirection(CancellationToken ct)
    {
        var response = await engine.GetAsync("direction", ct);
        if (!response.Ok)
        {
            return;
        }
        var now = DateTimeOffset.UtcNow;
        _lastDirection = now;
        state.SetDirection(response.Body, now);
        await broadcaster.Send("direction", new { updated = now }, ct);
    }
}
