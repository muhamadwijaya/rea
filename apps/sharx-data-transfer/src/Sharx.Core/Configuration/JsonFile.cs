using System.Text.Json;
using System.Text.Json.Serialization;

namespace Sharx.Core.Configuration;

/// <summary>Baca/tulis file JSON secara atomik (tulis ke .tmp lalu ganti), dengan backup.</summary>
public static class JsonFile
{
    public static JsonSerializerOptions Options { get; } = CreateOptions(indented: true);

    public static JsonSerializerOptions CreateOptions(bool indented)
    {
        var options = new JsonSerializerOptions
        {
            PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
            DictionaryKeyPolicy = null,
            WriteIndented = indented,
            ReadCommentHandling = JsonCommentHandling.Skip,
            AllowTrailingCommas = true,
        };
        options.Converters.Add(new JsonStringEnumConverter());
        return options;
    }

    public enum LoadStatus
    {
        Loaded,
        Missing,
        /// <summary>File rusak; disalin ke <c>*.corrupt-*</c> dan nilai default dipakai.</summary>
        Corrupt,
    }

    public static (T Value, LoadStatus Status, string? Error) Load<T>(string path, Func<T> fallback)
    {
        if (!File.Exists(path))
        {
            return (fallback(), LoadStatus.Missing, null);
        }

        try
        {
            var json = File.ReadAllText(path);
            var value = JsonSerializer.Deserialize<T>(json, Options);
            if (value is null)
            {
                throw new JsonException("File kosong");
            }

            return (value, LoadStatus.Loaded, null);
        }
        catch (Exception ex) when (ex is JsonException or NotSupportedException or IOException)
        {
            var copy = $"{path}.corrupt-{DateTime.Now:yyyyMMddHHmmss}";
            try
            {
                File.Copy(path, copy, overwrite: true);
            }
            catch (IOException)
            {
                // Tetap lanjut dengan default; file asli tidak disentuh.
            }

            return (fallback(), LoadStatus.Corrupt, $"{ex.Message} (salinan: {Path.GetFileName(copy)})");
        }
    }

    /// <summary>Simpan atomik. Jika <paramref name="keepBackup"/>, versi sebelumnya disimpan sebagai *.bak.</summary>
    public static void Save<T>(string path, T value, bool keepBackup)
    {
        var directory = Path.GetDirectoryName(Path.GetFullPath(path));
        if (!string.IsNullOrEmpty(directory))
        {
            Directory.CreateDirectory(directory);
        }

        var temp = path + ".tmp";
        File.WriteAllText(temp, JsonSerializer.Serialize(value, Options));
        if (keepBackup && File.Exists(path))
        {
            File.Replace(temp, path, path + ".bak", ignoreMetadataErrors: true);
        }
        else
        {
            File.Move(temp, path, overwrite: true);
        }
    }
}
