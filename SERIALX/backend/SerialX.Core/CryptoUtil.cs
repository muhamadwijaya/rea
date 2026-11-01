using System.Security.Cryptography;
using System.Text;

namespace SerialX.Core;

/// <summary>Utilitas kriptografi: HMAC signature serial, PBKDF2 password, RSA license.</summary>
public static class CryptoUtil
{
    /// <summary>sig = base32(HMAC-SHA256(secret, serial|lot))[:8] untuk verifikasi keaslian offline.</summary>
    public static string SignSerial(string secret, string serial, string lot)
    {
        using var h = new HMACSHA256(Encoding.UTF8.GetBytes(secret));
        byte[] mac = h.ComputeHash(Encoding.UTF8.GetBytes(serial + "|" + lot));
        return ToBase32(mac).Substring(0, 8);
    }

    /// <summary>Verifikasi signature (case-insensitive).</summary>
    public static bool VerifySerial(string secret, string serial, string lot, string sig) =>
        string.Equals(SignSerial(secret, serial, lot), sig.Trim().ToUpperInvariant(), StringComparison.Ordinal);

    public static string ToBase32(byte[] data)
    {
        const string alpha = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
        var sb = new StringBuilder((data.Length * 8 + 4) / 5);
        int bits = 0, value = 0;
        foreach (byte b in data)
        {
            value = (value << 8) | b;
            bits += 8;
            while (bits >= 5)
            {
                sb.Append(alpha[(value >> (bits - 5)) & 31]);
                bits -= 5;
            }
        }
        if (bits > 0) sb.Append(alpha[(value << (5 - bits)) & 31]);
        return sb.ToString();
    }

    public static string NewSalt()
    {
        byte[] buf = RandomNumberGenerator.GetBytes(16);
        return Convert.ToHexString(buf);
    }

    /// <summary>PBKDF2-HMAC-SHA256, 120.000 iterasi → "hexsalt$hexhash".</summary>
    public static string HashPassword(string password, string? salt = null, int iterations = 120_000)
    {
        salt ??= NewSalt();
        byte[] hash = Rfc2898DeriveBytes.Pbkdf2(
            Encoding.UTF8.GetBytes(password),
            Convert.FromHexString(salt),
            iterations,
            HashAlgorithmName.SHA256,
            32);
        return $"{salt}${Convert.ToHexString(hash)}${iterations}";
    }

    public static bool VerifyPassword(string password, string stored)
    {
        var parts = stored.Split('$');
        if (parts.Length != 3) return false;
        string expected = HashPassword(password, parts[0], int.Parse(parts[2]));
        return CryptographicOperations.FixedTimeEquals(
            Convert.FromHexString(expected.Split('$')[1]),
            Convert.FromHexString(parts[1]));
    }

    /// <summary>Buat lisensi: base64url(payload JSON) + "." + base64url(RSA-SHA256 sign).</summary>
    public static string IssueLicense(string publicKeyPemUnused, RSA priv, string payloadJson)
    {
        _ = publicKeyPemUnused;
        byte[] sig = priv.SignData(Encoding.UTF8.GetBytes(payloadJson), HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
        return B64Url(Encoding.UTF8.GetBytes(payloadJson)) + "." + B64Url(sig);
    }

    /// <summary>Verifikasi lisensi terhadap PEM public key yang di-embed aplikasi.</summary>
    public static bool VerifyLicense(string pemPublicKey, string token, out string payloadJson)
    {
        payloadJson = "";
        try
        {
            var dot = token.IndexOf('.');
            if (dot <= 0) return false;
            byte[] payload = FromB64Url(token[..dot]);
            byte[] sig = FromB64Url(token[(dot + 1)..]);
            using var rsa = RSA.Create();
            rsa.ImportFromPem(pemPublicKey);
            bool ok = rsa.VerifyData(payload, sig, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
            if (ok) payloadJson = Encoding.UTF8.GetString(payload);
            return ok;
        }
        catch { return false; }
    }

    private static string B64Url(byte[] data) =>
        Convert.ToBase64String(data).TrimEnd('=').Replace('+', '-').Replace('/', '_');

    private static byte[] FromB64Url(string s)
    {
        s = s.Replace('-', '+').Replace('_', '/');
        switch (s.Length % 4) { case 2: s += "=="; break; case 3: s += "="; break; }
        return Convert.FromBase64String(s);
    }
}
