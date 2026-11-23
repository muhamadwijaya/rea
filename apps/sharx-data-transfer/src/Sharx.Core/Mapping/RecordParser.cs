using Sharx.Core.Configuration;
using Sharx.Core.Text;

namespace Sharx.Core.Mapping;

/// <summary>Satu record dari server yang sudah dipecah menjadi field sesuai Flow Mapping.</summary>
public sealed class ParsedRecord
{
    public ParsedRecord(string raw, IReadOnlyList<string> fields, IReadOnlyDictionary<string, string> values)
    {
        Raw = raw;
        Fields = fields;
        Values = values;
    }

    /// <summary>Data asli (tanpa terminator).</summary>
    public string Raw { get; }

    /// <summary>Hasil split (mode Delimited) atau satu elemen berisi seluruh data (mode FixedWidth).</summary>
    public IReadOnlyList<string> Fields { get; }

    /// <summary>Nilai per kunci field (OPERATOR, TEMPLATE, SERVER_COUNTER, custom...).</summary>
    public IReadOnlyDictionary<string, string> Values { get; }

    public string Operator => Values.GetValueOrDefault(FieldKeys.Operator, "");
    public string Template => Values.GetValueOrDefault(FieldKeys.Template, "");
    public string? ServerCounter => Values.TryGetValue(FieldKeys.ServerCounter, out var v) ? v : null;
}

public sealed record MappedField(string Key, string Label, string Source, string? Value, bool Required, bool Missing);

public sealed class ParseResult
{
    public required ParsedRecord Record { get; init; }
    public required IReadOnlyList<MappedField> Mapped { get; init; }
    public required IReadOnlyList<string> Errors { get; init; }
    public bool Ok => Errors.Count == 0;
}

/// <summary>Memecah data dari server menjadi field berdasarkan pengaturan Flow Mapping.</summary>
public static class RecordParser
{
    public static ParseResult Parse(string raw, MappingSettings mapping)
    {
        raw ??= "";
        var errors = new List<string>();
        var fields = Split(raw, mapping);

        if (mapping.Mode == ParseMode.Delimited && mapping.ExpectedFieldCount > 0
            && fields.Count != mapping.ExpectedFieldCount)
        {
            errors.Add($"Jumlah field {fields.Count}, seharusnya {mapping.ExpectedFieldCount}");
        }

        var values = new Dictionary<string, string>(StringComparer.Ordinal);
        var mapped = new List<MappedField>(mapping.Fields.Count);
        foreach (var field in mapping.Fields)
        {
            var key = NormalizeKey(field.Key);
            var value = Extract(raw, fields, field, mapping);
            if (value is not null && mapping.TrimFields)
            {
                value = value.Trim();
            }

            var missing = string.IsNullOrEmpty(value);
            if (missing && field.Required)
            {
                errors.Add($"{Label(field)} ({DescribeSource(field, mapping.Mode)}) kosong / tidak ada");
            }

            if (value is not null)
            {
                values[key] = value;
            }

            mapped.Add(new MappedField(key, Label(field), DescribeSource(field, mapping.Mode), value, field.Required, missing));
        }

        return new ParseResult
        {
            Record = new ParsedRecord(raw, fields, values),
            Mapped = mapped,
            Errors = errors,
        };
    }

    public static string NormalizeKey(string? key) => (key ?? "").Trim().ToUpperInvariant();

    public static string DescribeSource(FieldMapping field, ParseMode mode)
    {
        if (mode == ParseMode.FixedWidth)
        {
            return field.Length > 0
                ? $"karakter {field.Start}-{field.Start + field.Length - 1}"
                : $"karakter {field.Start}-akhir";
        }

        var text = $"posisi {field.Position}";
        if (field.Start > 0)
        {
            text += field.Length > 0
                ? $", karakter {field.Start}-{field.Start + field.Length - 1}"
                : $", karakter {field.Start}-akhir";
        }

        return text;
    }

    private static string Label(FieldMapping field) =>
        string.IsNullOrWhiteSpace(field.Label) ? NormalizeKey(field.Key) : field.Label.Trim();

    private static List<string> Split(string raw, MappingSettings mapping)
    {
        if (mapping.Mode == ParseMode.FixedWidth)
        {
            return [raw];
        }

        var delimiter = ControlTokens.Decode(mapping.Delimiter ?? "");
        if (delimiter.Length == 0)
        {
            return [raw];
        }

        var parts = raw.Split(delimiter);
        if (mapping.TrimFields)
        {
            for (var i = 0; i < parts.Length; i++)
            {
                parts[i] = parts[i].Trim();
            }
        }

        return [.. parts];
    }

    private static string? Extract(string raw, List<string> fields, FieldMapping field, MappingSettings mapping)
    {
        if (mapping.Mode == ParseMode.FixedWidth)
        {
            return Slice(raw, field.Start, field.Length);
        }

        var index = field.Position - 1;
        if (index < 0 || index >= fields.Count)
        {
            return null;
        }

        var value = fields[index];
        return field.Start > 0 ? Slice(value, field.Start, field.Length) : value;
    }

    /// <summary>Ambil substring berbasis posisi 1; null jika posisi awal di luar data.</summary>
    private static string? Slice(string text, int start, int length)
    {
        var begin = Math.Max(start, 1) - 1;
        if (begin >= text.Length)
        {
            return null;
        }

        var available = text.Length - begin;
        return text.Substring(begin, length > 0 ? Math.Min(length, available) : available);
    }
}
