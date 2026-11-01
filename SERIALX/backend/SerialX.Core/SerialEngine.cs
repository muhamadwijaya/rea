using System.Globalization;
using System.Text;

namespace SerialX.Core;

/// <summary>
/// Mesin aturan serial berperforma tinggi (C#).
/// Mendukung token: {YYYY} {YY} {MM} {DD} {HH} {MIN} {SEQ:n} {LOT} {SKU} {PROD}
/// {LINE} {RAND:n} {BASE32:n} {LuhnCheck} {CRC2} {CRC1} {MOD10} {CHECKSUM}
/// {GS1AI:xx} {DATE11}.
/// </summary>
public sealed class SerialEngine
{
    private static readonly char[] Base32Alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567".ToCharArray();

    /// <summary>Karakter ambigu yang bisa dihindari (I O 0 1).</summary>
    public bool AvoidAmbiguous { get; set; }

    public SerialEngine(bool avoidAmbiguous = false) => AvoidAmbiguous = avoidAmbiguous;

    /// <summary>Menghitung checksum Luhn atas digit yang ada dalam string (modulo-10, hasil 1 digit).</summary>
    public static int LuhnDigit(string input)
    {
        var digits = new List<int>(input.Length);
        foreach (var c in input)
            if (char.IsAsciiDigit(c)) digits.Add(c - '0');
        int sum = 0;
        bool dbl = true; // mulai dari kanan, digit terakhir digandakan
        for (int i = digits.Count - 1; i >= 0; i--)
        {
            int d = digits[i];
            if (dbl) { d *= 2; if (d > 9) d -= 9; }
            sum += d;
            dbl = !dbl;
        }
        return (10 - (sum % 10)) % 10;
    }

    /// <summary>CRC-8 (poly 0x07) dipangkas ke 1 karakter base32-ish uppercase.</summary>
    public static char Crc1(string input)
    {
        byte crc = 0;
        foreach (byte b in Encoding.UTF8.GetBytes(input))
        {
            crc ^= b;
            for (int i = 0; i < 8; i++)
                crc = (byte)((crc & 0x80) != 0 ? ((crc << 1) ^ 0x07) : (crc << 1));
        }
        return Base32Alphabet[crc % 32];
    }

    /// <summary>CRC-8 dua karakter base32.</summary>
    public static string Crc2(string input)
    {
        byte crc = 0;
        foreach (byte b in Encoding.UTF8.GetBytes(input))
        {
            crc ^= b;
            for (int i = 0; i < 8; i++)
                crc = (byte)((crc & 0x80) != 0 ? ((crc << 1) ^ 0x07) : (crc << 1));
        }
        Span<char> buf = stackalloc char[2];
        buf[0] = Base32Alphabet[(crc >> 5) & 0x1F];
        buf[1] = Base32Alphabet[crc & 0x1F];
        return new string(buf);
    }

    /// <summary>Modulo-10 sederhana atas seluruh byte UTF-8.</summary>
    public static int Mod10(string input)
    {
        int sum = 0;
        foreach (byte b in Encoding.UTF8.GetBytes(input)) sum += b;
        return sum % 10;
    }

    /// <summary>Base32 dari nilai long (tanpa padding), panjang n (di-left-pad 'A').</summary>
    public static string Base32Of(ulong value, int len)
    {
        var buf = new char[len];
        for (int i = len - 1; i >= 0; i--)
        {
            buf[i] = Base32Alphabet[value % 32];
            value /= 32;
        }
        return new string(buf);
    }

    /// <summary>Date11 ala GS1: YYWWDDD — tahun 2 digit, minggu ISO 2 digit, hari 1-7.</summary>
    public static string Date11(DateTime dt)
    {
        int isoWeek = ISOWeek.GetWeekOfYear(dt);
        int dow = (int)ISOWeek.GetDayOfWeek(dt);
        return string.Format(CultureInfo.InvariantCulture, "{0:yy}{1:D2}{2:D1}", dt, isoWeek, dow);
    }

    /// <summary>
    /// Render pola serial. <paramref name="seq"/> dipakai oleh {SEQ:n};
    /// <paramref name="rng"/> dipakai {RAND:n}/{BASE32:n}. Token lain diisi dari context.
    /// </summary>
    public string Render(string pattern, long seq, SerialContext ctx, Random? rng = null)
    {
        rng ??= Random.Shared;
        var sb = new StringBuilder(pattern.Length + 16);
        int i = 0;
        while (i < pattern.Length)
        {
            char c = pattern[i];
            if (c != '{') { sb.Append(c); i++; continue; }
            int end = pattern.IndexOf('}', i);
            if (end < 0) { sb.Append(c); i++; continue; }
            string tokenRaw = pattern[(i + 1)..end];
            sb.Append(ExpandToken(tokenRaw, seq, ctx, rng));
            i = end + 1;
        }
        var result = sb.ToString().ToUpperInvariant();
        return AvoidAmbiguous ? Scrub(result) : result;
    }

    private string ExpandToken(string token, long seq, SerialContext ctx, Random rng)
    {
        var now = ctx.Now;
        int colon = token.IndexOf(':');
        string name = (colon < 0 ? token : token[..colon]).Trim().ToUpperInvariant();
        string arg = colon < 0 ? "" : token[(colon + 1)..].Trim();

        switch (name)
        {
            case "YYYY": return now.Year.ToString("D4", CultureInfo.InvariantCulture);
            case "YY": return now.ToString("yy", CultureInfo.InvariantCulture);
            case "MM": return now.Month.ToString("D2", CultureInfo.InvariantCulture);
            case "DD": return now.Day.ToString("D2", CultureInfo.InvariantCulture);
            case "HH": return now.Hour.ToString("D2", CultureInfo.InvariantCulture);
            case "MIN": return now.Minute.ToString("D2", CultureInfo.InvariantCulture);
            case "SEQ":
            {
                int width = int.TryParse(arg, out var w) ? Math.Max(1, w) : 6;
                long v = seq * Math.Max(1, ctx.Step);
                if (ctx.Descending) v = Math.Max(0, ctx.MaxValue - v);
                return v.ToString("D" + width, CultureInfo.InvariantCulture);
            }
            case "LOT": return ctx.Lot;
            case "SKU": return ctx.Sku;
            case "PROD": return ctx.Product;
            case "LINE": return ctx.Line;
            case "STATION": return ctx.Station;
            case "RAND":
            {
                int len = int.TryParse(arg, out var l) ? Math.Clamp(l, 1, 16) : 4;
                return Base32Of((ulong)rng.NextInt64(0, long.MaxValue), len);
            }
            case "BASE32":
            {
                int len = int.TryParse(arg, out var l) ? Math.Clamp(l, 1, 12) : 6;
                return Base32Of((ulong)seq, len);
            }
            case "DATE11": return Date11(now);
            case "GS1AI": return "(" + (string.IsNullOrEmpty(arg) ? "01" : arg) + ")";
            case "LUHNCHECK":
            case "MOD10":
            case "CRC1":
            case "CRC2":
            case "CHECKSUM":
                return ""; // diselesaikan pada pass kedua (butuh string penuh)
            default: return "{" + token + "}";
        }
    }

    /// <summary>Selesaikan token checksum yang bergantung pada seluruh string.</summary>
    public string FinalizeChecksums(string pattern, string partial)
    {
        var outSb = new StringBuilder(partial.Length + 4);
        // Hitung ulang posisi token: cara paling andal adalah render dengan placeholder lalu ganti.
        // Kami sisipkan marker \u0001 untuk tiap token checksum saat render pertama.
        foreach (char c in partial)
        {
            if (c == '\u0001')
            {
                var built = outSb.ToString();
                outSb.Append(LuhnDigit(built));
            }
            else if (c == '\u0002')
            {
                var built = outSb.ToString();
                outSb.Append(Crc1(built));
            }
            else if (c == '\u0003')
            {
                var built = outSb.ToString();
                outSb.Append(Crc2(built));
            }
            else if (c == '\u0004')
            {
                var built = outSb.ToString();
                outSb.Append(Mod10(built));
            }
            else outSb.Append(c);
        }
        _ = pattern;
        return outSb.ToString();
    }

    /// <summary>Render lengkap termasuk token checksum.</summary>
    public string RenderFull(string pattern, long seq, SerialContext ctx, Random? rng = null)
    {
        // Pass 1: ganti token checksum dgn marker unik.
        string replaced = pattern
            .Replace("{LuhnCheck}", "\u0001", StringComparison.OrdinalIgnoreCase)
            .Replace("{CRC1}", "\u0002", StringComparison.OrdinalIgnoreCase)
            .Replace("{CRC2}", "\u0003", StringComparison.OrdinalIgnoreCase)
            .Replace("{MOD10}", "\u0004", StringComparison.OrdinalIgnoreCase)
            .Replace("{CHECKSUM}", "\u0001", StringComparison.OrdinalIgnoreCase);
        string partial = Render(replaced, seq, ctx, rng);
        return FinalizeChecksums(pattern, partial);
    }

    /// <summary>Ganti karakter ambigu: I→J, O→K, 0→9, 1→8.</summary>
    public static string Scrub(string s)
    {
        var chars = s.ToCharArray();
        for (int i = 0; i < chars.Length; i++)
            chars[i] = chars[i] switch { 'I' => 'J', 'O' => 'K', '0' => '9', '1' => '8', _ => chars[i] };
        return new string(chars);
    }

    /// <summary>Validasi pola: semua token dikenal &amp; {SEQ:n} ada.</summary>
    public static (bool Ok, string Message) ValidatePattern(string pattern)
    {
        if (string.IsNullOrWhiteSpace(pattern)) return (false, "Pola kosong.");
        var known = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "YYYY","YY","MM","DD","HH","MIN","SEQ","LOT","SKU","PROD","LINE","STATION",
            "RAND","BASE32","DATE11","GS1AI","LUHNCHECK","MOD10","CRC1","CRC2","CHECKSUM"
        };
        int depth = 0;
        for (int i = 0; i < pattern.Length; i++)
        {
            char c = pattern[i];
            if (c == '{') depth++;
            else if (c == '}')
            {
                depth--;
                if (depth < 0) return (false, "Tanda '}' tanpa pasangan '{'.");
                string tok = pattern[(pattern.LastIndexOf('{', i) + 1)..i];
                string nm = tok.Split(':')[0].Trim();
                if (!known.Contains(nm)) return (false, $"Token tidak dikenal: {{{nm}}}");
            }
        }
        if (depth != 0) return (false, "Tanda '{' belum ditutup.");
        if (!pattern.Contains("{SEQ", StringComparison.OrdinalIgnoreCase))
            return (false, "Pola wajib memuat {SEQ:n} agar unik.");
        return (true, "OK");
    }
}

/// <summary>Konteks data master untuk render satu serial.</summary>
public sealed record SerialContext
{
    public DateTime Now { get; init; } = DateTime.Now;
    public string Lot { get; init; } = "";
    public string Sku { get; init; } = "";
    public string Product { get; init; } = "";
    public string Line { get; init; } = "L1";
    public string Station { get; init; } = "ST01";
    public int Step { get; init; } = 1;
    public bool Descending { get; init; }
    public long MaxValue { get; init; } = 999_999_999L;
}
