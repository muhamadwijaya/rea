namespace SmcTerminal.Gateway.Models;

public sealed class TerminalOptions
{
    public string EngineUrl { get; set; } = "http://127.0.0.1:8765";
    public int NewsPollSeconds { get; set; } = 60;
    public int ScreenerRefreshSeconds { get; set; } = 90;
    public string ScreenerTimeframe { get; set; } = "4h";
    public int DirectionRefreshSeconds { get; set; } = 300;
    public int CalendarRefreshMinutes { get; set; } = 30;
    public int CalendarAlertMinutes { get; set; } = 30;
    public string CalendarUrl { get; set; } = "https://nfs.faireconomy.media/ff_calendar_thisweek.json";
    public int MaxNewsItems { get; set; } = 800;
    public int MaxNewsAgeHours { get; set; } = 96;
    /// <summary>Start and supervise the Python engine as a hidden child process.</summary>
    public bool ManageEngine { get; set; }
    /// <summary>Python interpreter for the managed engine; defaults to engine/.venv.</summary>
    public string? EnginePython { get; set; }
    /// <summary>When set, gateway and engine logs are written to files in this folder.</summary>
    public string? LogDirectory { get; set; }
    public List<FeedConfig> Feeds { get; set; } = new();
}

public sealed class FeedConfig
{
    public string Name { get; set; } = "";
    public string Url { get; set; } = "";
    public string Category { get; set; } = "macro";
    public string Lang { get; set; } = "en";
}

public sealed record NewsItem
{
    public required string Id { get; init; }
    public required string Title { get; init; }
    public string Summary { get; init; } = "";
    public required string Link { get; init; }
    public required string Source { get; init; }
    public required string Feed { get; init; }
    public required string Category { get; init; }
    public string Lang { get; init; } = "en";
    public DateTimeOffset Published { get; init; }
    public DateTimeOffset Received { get; init; }
    public string? Image { get; init; }
    public double? Sentiment { get; init; }
    public string? SentimentLabel { get; init; }
    public IReadOnlyList<string> Terms { get; init; } = Array.Empty<string>();
    public IReadOnlyList<string> Assets { get; init; } = Array.Empty<string>();
    public IReadOnlyList<string> Markets { get; init; } = Array.Empty<string>();
}

public sealed class FeedStatus
{
    public required string Name { get; init; }
    public required string Url { get; init; }
    public required string Category { get; init; }
    public IReadOnlyList<string> Markets { get; init; } = Array.Empty<string>();
    public DateTimeOffset? LastAttempt { get; set; }
    public DateTimeOffset? LastOk { get; set; }
    public string? LastError { get; set; }
    public int LastItemCount { get; set; }
    public int ConsecutiveFailures { get; set; }
    public string? ETag { get; set; }
    public string? LastModified { get; set; }
}

public sealed record CalendarEvent(
    string Id,
    string Title,
    string Currency,
    DateTimeOffset Date,
    string Impact,
    string Forecast,
    string Previous);

public sealed record AlertItem(
    string Id,
    DateTimeOffset Time,
    string Kind,
    string Severity,
    string? Symbol,
    string Title,
    string Detail,
    string? Direction,
    string? Tv);

public sealed record AnalysisRecord(
    string Symbol,
    string Name,
    string Tv,
    string Tf,
    double Score,
    string Label,
    string Direction,
    DateTimeOffset Time,
    string? Summary);

public sealed record MetricPoint(
    DateTimeOffset Time,
    double BullishPct,
    double BearishPct,
    int ChochRecent,
    int NearOb,
    int NearFvg,
    double DiscountPct);
