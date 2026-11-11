using System.Text.Json;
using Microsoft.AspNetCore.SignalR;
using SmcTerminal.Gateway.Models;

namespace SmcTerminal.Gateway.Services;

/// <summary>SignalR hub. Clients only listen; all pushes originate from the background workers.</summary>
public sealed class MarketHub : Hub;

/// <summary>Fire-and-forget broadcast to every connected browser.</summary>
public sealed class Broadcaster(IHubContext<MarketHub> hub, ILogger<Broadcaster> log)
{
    public async Task Send(string method, object payload, CancellationToken ct)
    {
        try
        {
            await hub.Clients.All.SendAsync(method, payload, ct);
        }
        catch (Exception ex) when (!ct.IsCancellationRequested)
        {
            log.LogDebug(ex, "Broadcast {Method} failed", method);
        }
    }
}

/// <summary>Typed projection of one engine screener row (only the fields the gateway reasons about).</summary>
public sealed record ScreenerRowView(
    string Symbol,
    string Name,
    string Tv,
    string? Direction,
    string? Label,
    double Score,
    string? EventKind,
    string? EventDirection,
    string? EventScope,
    double? EventLevel,
    int? EventBarsAgo,
    long? EventTime,
    string? ZoneArea,
    int NearOb,
    int NearFvg,
    bool Failed)
{
    public static ScreenerRowView From(JsonElement row)
    {
        var inst = row.GetProperty("instrument");
        string? Str(JsonElement e, string name) =>
            e.ValueKind == JsonValueKind.Object && e.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;
        double? Num(JsonElement e, string name) =>
            e.ValueKind == JsonValueKind.Object && e.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.Number ? v.GetDouble() : null;
        JsonElement Obj(JsonElement e, string name) =>
            e.TryGetProperty(name, out var v) ? v : default;

        var ev = Obj(row, "lastEvent");
        var zone = Obj(row, "zone");
        var near = Obj(row, "nearby");
        return new ScreenerRowView(
            Str(inst, "symbol") ?? "?",
            Str(inst, "name") ?? "",
            Str(inst, "tv") ?? "",
            Str(row, "direction"),
            Str(row, "label"),
            Num(row, "score") ?? 0,
            Str(ev, "kind"),
            Str(ev, "direction"),
            Str(ev, "scope"),
            Num(ev, "level"),
            (int?)Num(ev, "barsAgo"),
            (long?)Num(ev, "t"),
            Str(zone, "area"),
            (int)(Num(near, "ob") ?? 0),
            (int)(Num(near, "fvg") ?? 0),
            row.TryGetProperty("error", out _));
    }
}

/// <summary>Live state shared by the workers and the HTTP API.</summary>
public sealed class MarketState(Broadcaster broadcaster)
{
    private readonly object _gate = new();
    private readonly LinkedList<AlertItem> _alerts = new();
    private readonly HashSet<string> _alertIds = new();
    private readonly LinkedList<AnalysisRecord> _recent = new();
    private readonly LinkedList<MetricPoint> _metrics = new();
    private readonly Queue<DateTimeOffset> _analysisTimes = new();

    public string? ScreenerJson { get; private set; }
    public IReadOnlyList<ScreenerRowView> ScreenerRows { get; private set; } = Array.Empty<ScreenerRowView>();
    public DateTimeOffset? ScreenerAt { get; private set; }
    public string ScreenerTf { get; private set; } = "4h";
    public string? DirectionJson { get; private set; }
    public DateTimeOffset? DirectionAt { get; private set; }

    public void SetScreener(string json, IReadOnlyList<ScreenerRowView> rows, string tf, DateTimeOffset now)
    {
        var valid = rows.Where(r => !r.Failed).ToList();
        lock (_gate)
        {
            ScreenerJson = json;
            ScreenerRows = rows;
            ScreenerAt = now;
            ScreenerTf = tf;
            if (valid.Count > 0)
            {
                _metrics.AddLast(new MetricPoint(
                    now,
                    Math.Round(100.0 * valid.Count(r => r.Direction?.Contains("bullish") == true) / valid.Count, 1),
                    Math.Round(100.0 * valid.Count(r => r.Direction?.Contains("bearish") == true) / valid.Count, 1),
                    valid.Count(r => r.EventKind == "CHoCH" && r.EventBarsAgo <= 6),
                    valid.Sum(r => r.NearOb),
                    valid.Sum(r => r.NearFvg),
                    Math.Round(100.0 * valid.Count(r => r.ZoneArea == "discount") / valid.Count, 1)));
                while (_metrics.Count > 240)
                {
                    _metrics.RemoveFirst();
                }
            }
        }
    }

    public void SetDirection(string json, DateTimeOffset now)
    {
        lock (_gate)
        {
            DirectionJson = json;
            DirectionAt = now;
        }
    }

    public async Task AddAlert(AlertItem alert, CancellationToken ct)
    {
        lock (_gate)
        {
            if (!_alertIds.Add(alert.Id))
            {
                return;
            }
            _alerts.AddFirst(alert);
            while (_alerts.Count > 200)
            {
                _alertIds.Remove(_alerts.Last!.Value.Id);
                _alerts.RemoveLast();
            }
        }
        await broadcaster.Send("alert", alert, ct);
    }

    public List<AlertItem> Alerts(int limit)
    {
        lock (_gate)
        {
            return _alerts.Take(Math.Clamp(limit, 1, 200)).ToList();
        }
    }

    public int AlertsSince(DateTimeOffset since)
    {
        lock (_gate)
        {
            return _alerts.Count(a => a.Time >= since);
        }
    }

    public void RecordAnalysis(AnalysisRecord record)
    {
        lock (_gate)
        {
            var existing = _recent.FirstOrDefault(r => r.Symbol == record.Symbol && r.Tf == record.Tf);
            if (existing is not null)
            {
                _recent.Remove(existing);
            }
            _recent.AddFirst(record);
            while (_recent.Count > 20)
            {
                _recent.RemoveLast();
            }
            _analysisTimes.Enqueue(record.Time);
        }
    }

    public List<AnalysisRecord> RecentAnalyses()
    {
        lock (_gate)
        {
            return _recent.ToList();
        }
    }

    public int AnalysesSince(DateTimeOffset since)
    {
        lock (_gate)
        {
            while (_analysisTimes.Count > 0 && _analysisTimes.Peek() < since)
            {
                _analysisTimes.Dequeue();
            }
            return _analysisTimes.Count;
        }
    }

    public List<MetricPoint> Metrics()
    {
        lock (_gate)
        {
            return _metrics.ToList();
        }
    }
}
