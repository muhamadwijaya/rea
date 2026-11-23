namespace Sharx.Core.Counter;

/// <summary>Counter yang tersimpan di software. Disimpan sebagai <c>data/counters.json</c>.</summary>
public sealed class CounterState
{
    /// <summary>Tanggal hari produksi (yyyy-MM-dd) tempat counter ini berlaku.</summary>
    public string BusinessDate { get; set; } = "";

    public string? ActiveTemplate { get; set; }
    public string? ActiveOperator { get; set; }

    public Dictionary<string, TemplateCounter> Templates { get; set; } = new(StringComparer.Ordinal);

    /// <summary>Naik setiap kali state berubah; dipakai untuk mendeteksi reset di tengah pengiriman.</summary>
    public long Version { get; set; }

    /// <summary>Naik setiap Reset Counter / ganti hari / ubah manual; data cetak dari epoch lama tidak disimpan.</summary>
    public long Epoch { get; set; }

    public CounterState Clone() => new()
    {
        BusinessDate = BusinessDate,
        ActiveTemplate = ActiveTemplate,
        ActiveOperator = ActiveOperator,
        Version = Version,
        Epoch = Epoch,
        Templates = Templates.ToDictionary(
            kv => kv.Key,
            kv => kv.Value with { },
            StringComparer.Ordinal),
    };
}

public sealed record TemplateCounter
{
    /// <summary>Nilai counter terakhir yang dipakai/dikirim ke printer.</summary>
    public long Value { get; init; }

    public long Records { get; init; }

    /// <summary>Jumlah digit counter template ini (0 = tanpa zero padding).</summary>
    public int Width { get; init; }

    public string? LastOperator { get; init; }
    public DateTimeOffset UpdatedAt { get; init; }
}
