using Sharx.Core.Configuration;
using Sharx.Core.Counter;
using Sharx.Core.Mapping;

namespace Sharx.Core.Tests;

internal sealed class ManualTime(DateTimeOffset start) : TimeProvider
{
    public DateTimeOffset Now { get; set; } = start;
    public override DateTimeOffset GetUtcNow() => Now.ToUniversalTime();
    public override TimeZoneInfo LocalTimeZone => TimeZoneInfo.Utc;
}

public class CounterEngineTests
{
    private static readonly DateTimeOffset Morning = new(2026, 11, 23, 8, 0, 0, TimeSpan.Zero);

    private static ParsedRecord Job(string text) => RecordParser.Parse(text, new MappingSettings()).Record;

    /// <summary>Simulasi mode per data: setiap record langsung dipakai.</summary>
    private static string Use(CounterEngine engine, string text, CounterSettings settings)
    {
        var decision = engine.Evaluate(Job(text), settings);
        Assert.True(engine.Commit(decision));
        return decision.Text;
    }

    [Fact]
    public void Operator_change_keeps_counter_template_change_switches_counter()
    {
        var engine = new CounterEngine(null, new ManualTime(Morning));
        var s = new CounterSettings { IncrementOn = CounterIncrement.Record };

        Assert.Equal("001", Use(engine, "ANANDA02,867892,000", s));
        Assert.Equal("002", Use(engine, "ANANDA02,867892,000", s));
        Assert.Equal("003", Use(engine, "BUDI01,867892,000", s));   // operator ganti: lanjut
        Assert.Equal("001", Use(engine, "BUDI01,555111,000", s));   // template baru: counter baru
        Assert.Equal("002", Use(engine, "BUDI01,555111,000", s));
        Assert.Equal("004", Use(engine, "ANANDA02,867892,000", s)); // kembali: counter tersimpan
    }

    [Fact]
    public void New_template_starts_from_server_counter_when_enabled()
    {
        var engine = new CounterEngine(null, new ManualTime(Morning));
        Assert.Equal("051", Use(engine, "ANANDA02,867892,050", new CounterSettings()));

        var fromStart = new CounterEngine(null, new ManualTime(Morning));
        Assert.Equal("101", Use(fromStart, "ANANDA02,867892,050", new CounterSettings { UseServerValueAsStart = false, StartValue = 100 }));
    }

    [Fact]
    public void Day_change_resets_all_counters_using_day_start_time()
    {
        var time = new ManualTime(Morning);
        var engine = new CounterEngine(null, time);
        var s = new CounterSettings { DayStartTime = "06:00" };
        Use(engine, "ANANDA02,867892,000", s);
        Use(engine, "ANANDA02,867892,000", s);

        time.Now = new DateTimeOffset(2026, 11, 24, 5, 59, 0, TimeSpan.Zero); // masih hari produksi 23
        Assert.Equal("003", Use(engine, "ANANDA02,867892,000", s));

        time.Now = new DateTimeOffset(2026, 11, 24, 6, 0, 0, TimeSpan.Zero);
        Assert.Equal("2026-11-23", engine.RollDayIfNeeded(s));
        Assert.Empty(engine.Snapshot().Templates);
        Assert.Equal("001", Use(engine, "ANANDA02,867892,000", s));
    }

    [Fact]
    public void Print_counting_only_advances_on_print_and_ignores_prints_from_before_reset()
    {
        var engine = new CounterEngine(null, new ManualTime(Morning));
        var s = new CounterSettings();
        var decision = engine.Evaluate(Job("ANANDA02,867892,000"), s);
        Assert.True(engine.CommitJob(decision));
        Assert.Equal(1, engine.NextValue("867892", s));

        Assert.True(engine.CommitPrint("867892", "ANANDA02", 1, 3, engine.Epoch));
        Assert.Equal(2, engine.NextValue("867892", s));

        var staleEpoch = engine.Epoch;
        engine.Reset(null);
        Assert.False(engine.CommitPrint("867892", "ANANDA02", 2, 3, staleEpoch));
        Assert.Null(engine.NextValue("867892", s));
    }

    [Fact]
    public void Counters_survive_restart_through_file()
    {
        var path = Path.Combine(Path.GetTempPath(), $"sharx-counter-{Guid.NewGuid():N}.json");
        try
        {
            var time = new ManualTime(Morning);
            var s = new CounterSettings();
            Use(new CounterEngine(path, time), "ANANDA02,867892,000", s);

            var reloaded = new CounterEngine(path, time);
            Assert.Equal("002", Use(reloaded, "BUDI01,867892,000", s));
        }
        finally
        {
            File.Delete(path);
        }
    }

    [Fact]
    public void Counter_digits_follow_the_server_counter_when_not_fixed()
    {
        var engine = new CounterEngine(null, new ManualTime(Morning));
        var s = new CounterSettings(); // Digits = 0: ikuti server

        Assert.Equal("00006", Use(engine, "SITI AMINAH,TPL-A1,00005", s));
        Assert.Equal("1", Use(engine, "BUDI,X9,0", s));
        Assert.Equal("101", Use(engine, "BUDI,Y7,100", s));
        Assert.Equal("00007", Use(engine, "ANA,TPL-A1,", s)); // tanpa counter server: pakai digit tersimpan

        var fixedDigits = new CounterEngine(null, new ManualTime(Morning));
        Assert.Equal("0006", Use(fixedDigits, "SITI,TPL-A1,00005", new CounterSettings { Digits = 4 }));
    }

    [Fact]
    public void Rollover_returns_to_start_value()
    {
        var engine = new CounterEngine(null, new ManualTime(Morning));
        var s = new CounterSettings { MaxValue = 3 };
        Assert.Equal(["001", "002", "003", "001"], Enumerable.Range(0, 4).Select(_ => Use(engine, "A,T,000", s)).ToArray());
    }
}
