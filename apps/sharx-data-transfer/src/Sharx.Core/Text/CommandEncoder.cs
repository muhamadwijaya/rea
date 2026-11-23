using System.Text;

namespace Sharx.Core.Text;

/// <summary>Mengubah perintah (hasil render) menjadi byte, termasuk menghitung token checksum.</summary>
public static class CommandEncoder
{
    /// <summary>
    /// &lt;SUM&gt; = 2's complement dari jumlah (mod 256) semua byte sebelumnya (seperti TIJ);
    /// &lt;XOR&gt; = XOR semua byte sebelumnya.
    /// </summary>
    public static byte[] Encode(string text, Encoding encoding)
    {
        var bytes = new List<byte>(text.Length + 4);
        var segment = new StringBuilder();
        foreach (var c in text)
        {
            if (c is not (ControlTokens.SumMarker or ControlTokens.XorMarker))
            {
                segment.Append(c);
                continue;
            }

            bytes.AddRange(encoding.GetBytes(segment.ToString()));
            segment.Clear();
            bytes.Add(c == ControlTokens.SumMarker ? Sum(bytes) : Xor(bytes));
        }

        bytes.AddRange(encoding.GetBytes(segment.ToString()));
        return [.. bytes];
    }

    private static byte Sum(List<byte> bytes)
    {
        var sum = 0;
        foreach (var b in bytes)
        {
            sum += b;
        }

        return (byte)((0x100 - (sum & 0xFF)) & 0xFF);
    }

    private static byte Xor(List<byte> bytes)
    {
        byte x = 0;
        foreach (var b in bytes)
        {
            x ^= b;
        }

        return x;
    }
}
