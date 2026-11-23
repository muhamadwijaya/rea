using System.Globalization;
using System.Text;

namespace Sharx.Core.Text;

/// <summary>
/// Mengubah teks yang bisa diedit manusia (mis. <c>&lt;STX&gt;ABC&lt;ETX&gt;&lt;CR&gt;&lt;LF&gt;</c> atau
/// <c>&lt;0x1B&gt;</c>) menjadi karakter kontrol asli, dan sebaliknya untuk tampilan log.
/// </summary>
public static class ControlTokens
{
    private static readonly (string Name, char Value)[] Named =
    [
        ("NUL", '\u0000'), ("SOH", '\u0001'), ("STX", '\u0002'), ("ETX", '\u0003'),
        ("EOT", '\u0004'), ("ENQ", '\u0005'), ("ACK", '\u0006'), ("BEL", '\u0007'),
        ("BS", '\u0008'), ("TAB", '\u0009'), ("LF", '\u000A'), ("VT", '\u000B'),
        ("FF", '\u000C'), ("CR", '\u000D'), ("SO", '\u000E'), ("SI", '\u000F'),
        ("DLE", '\u0010'), ("DC1", '\u0011'), ("DC2", '\u0012'), ("DC3", '\u0013'),
        ("DC4", '\u0014'), ("NAK", '\u0015'), ("SYN", '\u0016'), ("ETB", '\u0017'),
        ("CAN", '\u0018'), ("EM", '\u0019'), ("SUB", '\u001A'), ("ESC", '\u001B'),
        ("FS", '\u001C'), ("GS", '\u001D'), ("RS", '\u001E'), ("US", '\u001F'),
        ("DEL", '\u007F'),
    ];

    private static readonly Dictionary<string, char> ByName =
        Named.ToDictionary(n => n.Name, n => n.Value, StringComparer.OrdinalIgnoreCase);

    private static readonly Dictionary<char, string> ByValue =
        Named.ToDictionary(n => n.Value, n => n.Name);

    /// <summary>Daftar nama token yang didukung (untuk UI).</summary>
    public static IReadOnlyList<string> Names { get; } = Named.Select(n => n.Name).ToArray();

    /// <summary>Ganti token &lt;NAME&gt; / &lt;0xHH&gt; dengan karakter aslinya. Teks lain tidak diubah.</summary>
    public static string Decode(string text)
    {
        if (string.IsNullOrEmpty(text) || !text.Contains('<'))
        {
            return text ?? "";
        }

        var sb = new StringBuilder(text.Length);
        var i = 0;
        while (i < text.Length)
        {
            if (text[i] == '<' && TryReadToken(text, i, out var value, out var consumed))
            {
                sb.Append(value);
                i += consumed;
                continue;
            }

            sb.Append(text[i]);
            i++;
        }

        return sb.ToString();
    }

    /// <summary>Tampilkan karakter kontrol sebagai token, mis. "\r\n" menjadi "&lt;CR&gt;&lt;LF&gt;".</summary>
    public static string Visualize(string text)
    {
        if (string.IsNullOrEmpty(text))
        {
            return "";
        }

        StringBuilder? sb = null;
        for (var i = 0; i < text.Length; i++)
        {
            var c = text[i];
            var isControl = c < 0x20 || c == 0x7F;
            if (!isControl)
            {
                sb?.Append(c);
                continue;
            }

            sb ??= new StringBuilder(text, 0, i, text.Length + 16);
            sb.Append('<').Append(ByValue.TryGetValue(c, out var name) ? name : $"0x{(int)c:X2}").Append('>');
        }

        return sb?.ToString() ?? text;
    }

    private static bool TryReadToken(string text, int start, out char value, out int consumed)
    {
        value = '\0';
        consumed = 0;
        var end = text.IndexOf('>', start + 1);
        if (end < 0 || end - start > 8)
        {
            return false;
        }

        var name = text.Substring(start + 1, end - start - 1);
        if (ByName.TryGetValue(name, out value))
        {
            consumed = end - start + 1;
            return true;
        }

        if (name.Length == 4 && (name.StartsWith("0x", StringComparison.OrdinalIgnoreCase))
            && byte.TryParse(name.AsSpan(2), NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var b))
        {
            value = (char)b;
            consumed = end - start + 1;
            return true;
        }

        return false;
    }
}
