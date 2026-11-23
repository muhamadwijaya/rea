using System.Globalization;
using System.Text.Json;
using System.Text.RegularExpressions;
using Sharx.Core.Mapping;
using Sharx.Core.Printing;
using Sharx.Core.Text;

namespace Sharx.Core.Configuration;

/// <summary>Memuat dan menyimpan <c>settings.json</c> (dengan backup <c>settings.json.bak</c>).</summary>
public sealed class SettingsStore
{
    private readonly string _path;

    public SettingsStore(string path)
    {
        _path = path;
    }

    public string Path => _path;

    public (AppSettings Settings, string? Warning) Load()
    {
        var (settings, status, error) = JsonFile.Load(_path, () => new AppSettings());
        Normalize(settings);
        if (status == JsonFile.LoadStatus.Missing)
        {
            Save(settings);
        }

        return (settings, status == JsonFile.LoadStatus.Corrupt ? $"settings.json rusak, memakai default: {error}" : null);
    }

    public void Save(AppSettings settings) => JsonFile.Save(_path, settings, keepBackup: true);

    /// <summary>Salinan dalam (deep copy) supaya perubahan draft tidak memengaruhi pengaturan aktif.</summary>
    public static AppSettings Clone(AppSettings settings) =>
        JsonSerializer.Deserialize<AppSettings>(JsonSerializer.Serialize(settings, JsonFile.Options), JsonFile.Options)!;

    /// <summary>Isi nilai null dari JSON lama dengan default dan rapikan kunci field.</summary>
    public static void Normalize(AppSettings s)
    {
        s.Server ??= new ServerSettings();
        s.Mapping ??= new MappingSettings();
        s.Counter ??= new CounterSettings();
        s.Printer ??= new PrinterSettings();
        s.Feedback ??= new FeedbackSettings();
        s.General ??= new GeneralSettings();
        s.Mapping.Fields ??= [];
        s.Printer.Fields ??= [];
        s.Printer.Tij ??= new TijSettings();
        s.Printer.Generic ??= new GenericProtocolSettings();
        s.Printer.TemplateName ??= "";
        var g = s.Printer.Generic;
        g.TemplateCommand ??= "";
        g.ClearCommand ??= "";
        g.FieldCommand ??= "";
        g.DataCommand ??= "";
        g.AckPattern ??= "";
        g.NakPattern ??= "";
        g.PrintDonePattern ??= "";
        s.Feedback.Template ??= "";
        foreach (var field in s.Mapping.Fields)
        {
            field.Key = RecordParser.NormalizeKey(field.Key);
            field.Label ??= "";
        }

        foreach (var field in s.Printer.Fields)
        {
            field.Name ??= "";
            field.Value ??= "";
        }
    }
}

public static partial class SettingsValidator
{
    public static IReadOnlyList<string> Validate(AppSettings s)
    {
        var errors = new List<string>();
        SettingsStore.Normalize(s);

        if (string.IsNullOrWhiteSpace(s.Server.Host))
        {
            errors.Add("Server IP wajib diisi");
        }

        if (s.Server.Port is < 1 or > 65535)
        {
            errors.Add("Port server harus 1-65535");
        }

        if (s.Server.Framing == FrameMode.Custom && ControlTokens.Decode(s.Server.CustomTerminator ?? "").Length == 0)
        {
            errors.Add("Terminator custom wajib diisi");
        }

        if (!TextEncodings.IsSupported(s.Server.Encoding) || !TextEncodings.IsSupported(s.Printer.Encoding))
        {
            errors.Add($"Encoding harus salah satu dari: {string.Join(", ", TextEncodings.Supported)}");
        }

        if (s.Server.ReconnectDelaySeconds is < 1 or > 3600 || s.Server.ConnectTimeoutSeconds is < 1 or > 120)
        {
            errors.Add("Reconnect delay 1-3600 detik dan connect timeout 1-120 detik");
        }

        if (s.Server.MaxFrameBytes is < 16 or > 1_048_576)
        {
            errors.Add("Panjang maksimum data 16 - 1048576 byte");
        }

        ValidateMapping(s.Mapping, errors);
        ValidateCounter(s.Counter, errors);
        ValidatePrinter(s, errors);

        if (s.General.LogRetentionDays is < 0 or > 3650 || s.General.LiveRows is < 10 or > 2000)
        {
            errors.Add("Retensi log 0-3650 hari dan baris live 10-2000");
        }

        return errors;
    }

    private static void ValidateMapping(MappingSettings m, List<string> errors)
    {
        if (m.Mode == ParseMode.Delimited && ControlTokens.Decode(m.Delimiter ?? "").Length == 0)
        {
            errors.Add("Delimiter wajib diisi");
        }

        if (m.ExpectedFieldCount is < 0 or > 500)
        {
            errors.Add("Jumlah field 0-500");
        }

        var seen = new HashSet<string>(StringComparer.Ordinal);
        foreach (var f in m.Fields)
        {
            if (!KeyRegex().IsMatch(f.Key))
            {
                errors.Add($"Kunci field '{f.Key}' tidak valid (huruf besar, angka, garis bawah; diawali huruf)");
            }
            else if (OutputFormatter.BuiltIns.Contains(f.Key) || FieldIndexKey().IsMatch(f.Key) || ReservedKey().IsMatch(f.Key))
            {
                errors.Add($"Kunci field '{f.Key}' sudah dipakai placeholder bawaan");
            }
            else if (!seen.Add(f.Key))
            {
                errors.Add($"Kunci field '{f.Key}' dipakai lebih dari sekali");
            }

            if (m.Mode == ParseMode.Delimited && f.Position is < 1 or > 500)
            {
                errors.Add($"Posisi field {f.Key} harus 1-500");
            }

            if (m.Mode == ParseMode.FixedWidth && f.Start < 1)
            {
                errors.Add($"Karakter awal field {f.Key} harus >= 1");
            }

            if (f.Start < 0 || f.Length < 0)
            {
                errors.Add($"Karakter awal/panjang field {f.Key} tidak boleh negatif");
            }
        }

        if (!seen.Contains(FieldKeys.Template))
        {
            errors.Add("Field TEMPLATE wajib ada di Flow Mapping (dipakai untuk counter per template)");
        }
    }

    private static void ValidateCounter(CounterSettings c, List<string> errors)
    {
        if (c.StartValue < 0)
        {
            errors.Add("Nilai awal counter tidak boleh negatif");
        }

        if (c.Step < 1)
        {
            errors.Add("Step counter minimal 1");
        }

        if (c.Digits is < 1 or > 18)
        {
            errors.Add("Digit counter 1-18");
        }

        if (c.MaxValue < 0 || (c.MaxValue > 0 && c.MaxValue <= c.StartValue))
        {
            errors.Add("Batas maksimum counter harus 0 (tanpa batas) atau lebih besar dari nilai awal");
        }

        if (!TimeSpan.TryParseExact(c.DayStartTime?.Trim(), [@"hh\:mm", @"h\:mm"], CultureInfo.InvariantCulture, out var t)
            || t >= TimeSpan.FromDays(1))
        {
            errors.Add("Jam ganti hari harus format HH:mm (00:00 - 23:59)");
        }
    }

    private static void ValidatePrinter(AppSettings s, List<string> errors)
    {
        var p = s.Printer;
        if (p.Transport == PrinterTransport.Tcp)
        {
            if (string.IsNullOrWhiteSpace(p.Host))
            {
                errors.Add("IP printer wajib diisi");
            }

            if (p.Port is < 1 or > 65535)
            {
                errors.Add("Port printer harus 1-65535");
            }
        }
        else if (string.IsNullOrWhiteSpace(p.SerialPort) || p.BaudRate < 300 || p.DataBits is < 5 or > 8)
        {
            errors.Add("Pengaturan serial printer tidak valid (port, baud rate >= 300, data bits 5-8)");
        }

        if (p.ResponseTimeoutMs is < 50 or > 60000 || p.ConnectTimeoutSeconds is < 1 or > 120
            || p.ReconnectDelaySeconds is < 1 or > 3600)
        {
            errors.Add("Timeout balasan 50-60000 ms, connect timeout 1-120 detik, reconnect printer 1-3600 detik");
        }

        var keys = s.Mapping.Fields.Select(f => f.Key).ToArray();
        ValidatePrinterJob(s, keys, errors);

        if (s.Feedback.Enabled)
        {
            foreach (var error in OutputFormatter.Validate(s.Feedback.Template, keys))
            {
                errors.Add($"Format feedback ke server: {error}");
            }
        }

        if (s.Feedback.MaxQueued is < 1 or > 1_000_000)
        {
            errors.Add("Antrean feedback 1-1000000");
        }
    }

    private static void ValidatePrinterJob(AppSettings s, string[] keys, List<string> errors)
    {
        var p = s.Printer;
        var driver = PrinterDrivers.For(p);
        if (p.Lookahead is < 1 or > 10)
        {
            errors.Add("Jumlah antrean data di printer harus 1-10");
        }

        foreach (var error in OutputFormatter.Validate(p.TemplateName, keys))
        {
            errors.Add($"Nama template (change template): {error}");
        }

        if (p.Fields.Count == 0)
        {
            errors.Add("Minimal satu field data ke printer");
        }

        var ids = new HashSet<int>();
        foreach (var field in p.Fields)
        {
            if (field.FieldId is < 0 or > 255)
            {
                errors.Add($"Nomor field {field.FieldId} harus 0-255");
            }
            else if (!ids.Add(field.FieldId))
            {
                errors.Add($"Nomor field {field.FieldId} dipakai lebih dari sekali");
            }

            foreach (var error in OutputFormatter.Validate(field.Value, keys))
            {
                errors.Add($"Field {field.FieldId}: {error}");
            }
        }

        errors.AddRange(driver.Validate(s, keys));
        if (s.Counter.IncrementOn == CounterIncrement.Print && !driver.HasPrintFeedback(p))
        {
            errors.Add("Counter per cetak butuh sinyal selesai cetak dari printer (isi 'Sinyal selesai cetak' atau pilih counter per data)");
        }
    }

    [GeneratedRegex("^[A-Z][A-Z0-9_]{0,31}$")]
    private static partial Regex KeyRegex();

    [GeneratedRegex("^F[0-9]+$")]
    private static partial Regex FieldIndexKey();

    /// <summary>Placeholder khusus perintah printer Generic.</summary>
    [GeneratedRegex("^(TEMPLATE_NAME|FIELD_ID|FIELD_NAME|FIELD_VALUE|FIELD_[0-9]+)$")]
    private static partial Regex ReservedKey();
}
