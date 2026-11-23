using System.Text;

namespace Sharx.Core.Text;

public static class TextEncodings
{
    public static IReadOnlyList<string> Supported { get; } = ["utf-8", "ascii", "latin1"];

    public static bool IsSupported(string? name) =>
        name is not null && Supported.Contains(name.Trim().ToLowerInvariant());

    public static Encoding Resolve(string? name) => (name ?? "").Trim().ToLowerInvariant() switch
    {
        "ascii" => Encoding.ASCII,
        "latin1" => Encoding.Latin1,
        _ => new UTF8Encoding(encoderShouldEmitUTF8Identifier: false),
    };
}
