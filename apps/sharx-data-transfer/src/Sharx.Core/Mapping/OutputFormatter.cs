using System.Globalization;
using System.Text;
using System.Text.RegularExpressions;
using Sharx.Core.Configuration;
using Sharx.Core.Text;

namespace Sharx.Core.Mapping;

/// <summary>Data yang tersedia untuk placeholder output printer / feedback server.</summary>
public sealed record OutputContext(
    ParsedRecord Record,
    long Counter,
    string CounterText,
    string BusinessDate,
    DateTimeOffset Now,
    long Sequence,
    bool TemplateChanged,
    bool OperatorChanged);

/// <summary>Satu perintah yang akan/tidak dikirim ke printer.</summary>
public sealed record OutputCommand(int Step, string Name, StepTrigger Trigger, bool Send, string Text)
{
    /// <summary>Teks dengan karakter kontrol ditampilkan sebagai token (&lt;CR&gt; dll.).</summary>
    public string Display => ControlTokens.Visualize(Text);
}

/// <summary>Menyusun data ke printer CIJ dari langkah-langkah output (flow mapping).</summary>
public static partial class OutputFormatter
{
    public const string Counter = "COUNTER";
    public const string Raw = "RAW";
    public const string Date = "DATE";
    public const string Time = "TIME";
    public const string ProductionDate = "PROD_DATE";
    public const string Sequence = "SEQ";

    /// <summary>Placeholder bawaan (selain field dari Flow Mapping dan F1..Fn).</summary>
    public static IReadOnlyList<string> BuiltIns { get; } =
        [Counter, Raw, Date, Time, ProductionDate, Sequence];

    public static IReadOnlyList<OutputCommand> Build(IEnumerable<OutputStep> steps, OutputContext context)
    {
        var commands = new List<OutputCommand>();
        var index = 0;
        foreach (var step in steps)
        {
            index++;
            var send = step.Enabled && ShouldSend(step.Trigger, context);
            commands.Add(new OutputCommand(index, step.Name, step.Trigger, send, Render(step.Template, context)));
        }

        return commands;
    }

    public static bool ShouldSend(StepTrigger trigger, OutputContext context) => trigger switch
    {
        StepTrigger.TemplateChanged => context.TemplateChanged,
        StepTrigger.OperatorChanged => context.OperatorChanged,
        StepTrigger.JobChanged => context.TemplateChanged || context.OperatorChanged,
        _ => true,
    };

    /// <summary>Isi placeholder {KEY} / {KEY:FORMAT} dan token kontrol &lt;CR&gt; dsb.</summary>
    public static string Render(string template, OutputContext context)
    {
        var sb = new StringBuilder();
        foreach (var part in Tokenize(template ?? ""))
        {
            sb.Append(part.IsPlaceholder ? Resolve(part.Name, part.Format, context) : part.Text);
        }

        return sb.ToString();
    }

    /// <summary>Cek placeholder yang tidak dikenal atau kurung kurawal yang tidak lengkap.</summary>
    public static IReadOnlyList<string> Validate(string template, IReadOnlyCollection<string> fieldKeys)
    {
        var errors = new List<string>();
        if (string.IsNullOrEmpty(template))
        {
            errors.Add("Format output kosong");
            return errors;
        }

        List<Part> parts;
        try
        {
            parts = Tokenize(template);
        }
        catch (FormatException ex)
        {
            errors.Add(ex.Message);
            return errors;
        }

        foreach (var part in parts.Where(p => p.IsPlaceholder))
        {
            var known = BuiltIns.Contains(part.Name) || fieldKeys.Contains(part.Name) || FieldIndexRegex().IsMatch(part.Name);
            if (!known)
            {
                errors.Add($"Placeholder {{{part.Name}}} tidak dikenal");
                continue;
            }

            if (part.Format is not null && !IsValidFormat(part.Name, part.Format))
            {
                errors.Add($"Format '{part.Format}' tidak valid untuk {{{part.Name}}}");
            }
        }

        return errors;
    }

    private static bool IsValidFormat(string name, string format)
    {
        try
        {
            switch (name)
            {
                case Date or Time or ProductionDate:
                    _ = DateTime.Now.ToString(format, CultureInfo.InvariantCulture);
                    return true;
                case Counter or Sequence:
                    _ = 1L.ToString(format, CultureInfo.InvariantCulture);
                    return true;
                default:
                    return TextFormatRegex().IsMatch(format);
            }
        }
        catch (FormatException)
        {
            return false;
        }
    }

    private static string Resolve(string name, string? format, OutputContext ctx)
    {
        switch (name)
        {
            case Counter:
                return format is null ? ctx.CounterText : FormatNumber(ctx.Counter, format);
            case Sequence:
                return FormatNumber(ctx.Sequence, format);
            case Date:
                return FormatDate(ctx.Now.DateTime, format ?? "yyyy-MM-dd");
            case Time:
                return FormatDate(ctx.Now.DateTime, format ?? "HH:mm:ss");
            case ProductionDate:
                var prod = DateTime.TryParseExact(ctx.BusinessDate, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var d)
                    ? d
                    : ctx.Now.Date;
                return FormatDate(prod, format ?? "yyyy-MM-dd");
            case Raw:
                return FormatText(ctx.Record.Raw, format);
        }

        var match = FieldIndexRegex().Match(name);
        if (match.Success)
        {
            var i = int.Parse(match.Groups[1].Value, CultureInfo.InvariantCulture) - 1;
            var value = i >= 0 && i < ctx.Record.Fields.Count ? ctx.Record.Fields[i] : "";
            return FormatText(value, format);
        }

        return FormatText(ctx.Record.Values.GetValueOrDefault(name, ""), format);
    }

    private static string FormatNumber(long value, string? format)
    {
        try
        {
            return value.ToString(format ?? "", CultureInfo.InvariantCulture);
        }
        catch (FormatException)
        {
            return value.ToString(CultureInfo.InvariantCulture);
        }
    }

    private static string FormatDate(DateTime value, string format)
    {
        try
        {
            return value.ToString(format, CultureInfo.InvariantCulture);
        }
        catch (FormatException)
        {
            return value.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        }
    }

    /// <summary>Format teks: L10 = rata kiri lebar 10, R10 = rata kanan, Z10 = rata kanan isi nol, U/l = huruf besar/kecil.</summary>
    private static string FormatText(string value, string? format)
    {
        if (string.IsNullOrEmpty(format))
        {
            return value;
        }

        var m = TextFormatRegex().Match(format);
        if (!m.Success)
        {
            return value;
        }

        var kind = m.Groups[1].Success ? m.Groups[1].Value : m.Groups[3].Value;
        if (kind == "U")
        {
            return value.ToUpperInvariant();
        }

        if (kind == "l")
        {
            return value.ToLowerInvariant();
        }

        var width = int.Parse(m.Groups[2].Value, CultureInfo.InvariantCulture);
        if (value.Length > width)
        {
            return kind == "L" ? value[..width] : value[^width..];
        }

        return kind switch
        {
            "L" => value.PadRight(width),
            "Z" => value.PadLeft(width, '0'),
            _ => value.PadLeft(width),
        };
    }

    private readonly record struct Part(bool IsPlaceholder, string Text, string Name, string? Format);

    private static List<Part> Tokenize(string template)
    {
        var parts = new List<Part>();
        var literal = new StringBuilder();

        void FlushLiteral()
        {
            if (literal.Length > 0)
            {
                parts.Add(new Part(false, ControlTokens.Decode(literal.ToString()), "", null));
                literal.Clear();
            }
        }

        for (var i = 0; i < template.Length; i++)
        {
            var c = template[i];
            if (c == '{' && i + 1 < template.Length && template[i + 1] == '{')
            {
                literal.Append('{');
                i++;
                continue;
            }

            if (c == '}' && i + 1 < template.Length && template[i + 1] == '}')
            {
                literal.Append('}');
                i++;
                continue;
            }

            if (c == '{')
            {
                var end = template.IndexOf('}', i + 1);
                if (end < 0)
                {
                    throw new FormatException($"Kurung '{{' di karakter {i + 1} tidak ditutup");
                }

                var body = template.Substring(i + 1, end - i - 1);
                var colon = body.IndexOf(':');
                var name = RecordParser.NormalizeKey(colon < 0 ? body : body[..colon]);
                var format = colon < 0 ? null : body[(colon + 1)..];
                FlushLiteral();
                parts.Add(new Part(true, "", name, string.IsNullOrEmpty(format) ? null : format));
                i = end;
                continue;
            }

            literal.Append(c);
        }

        FlushLiteral();
        return parts;
    }

    [GeneratedRegex("^F([1-9][0-9]?)$")]
    private static partial Regex FieldIndexRegex();

    [GeneratedRegex("^(?:([LRZ])([1-9][0-9]{0,2})|([Ul]))$")]
    private static partial Regex TextFormatRegex();
}
