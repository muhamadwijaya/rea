using System.Text;
using System.Text.Json;
using Sharx.Core.Configuration;
using Sharx.Core.Logging;
using Sharx.Core.Mapping;
using Sharx.Core.Text;

namespace Sharx.App;

/// <summary>Jawaban API: JSON, atau isi file yang ditulis langsung ke response (download log).</summary>
public sealed class ApiResponse
{
    public int Status { get; init; } = 200;

    /// <summary>Isi JSON (diserialisasi dengan <see cref="SharxRuntime.JsonOptions"/>).</summary>
    public object? Json { get; init; }

    public string? ContentType { get; init; }

    /// <summary>Nama file untuk header Content-Disposition (download).</summary>
    public string? FileName { get; init; }

    /// <summary>Penulis isi response non-JSON.</summary>
    public Func<Stream, CancellationToken, Task>? WriteBody { get; init; }

    public static ApiResponse Ok(object? json) => new() { Json = json };

    public static ApiResponse Bad(params string[] errors) => new() { Status = 400, Json = new { ok = false, errors } };

    public static readonly ApiResponse NotFound = new() { Status = 404, Json = new { ok = false, errors = new[] { "Tidak ditemukan" } } };
}

/// <summary>
/// Semua endpoint <c>/api/*</c> tanpa ketergantungan web server, sehingga host desktop (ASP.NET Core)
/// dan Android (server lokal) memakai perilaku yang sama persis.
/// </summary>
public sealed class SharxApi(SharxRuntime runtime)
{
    private const int MaxBodyBytes = 4 * 1024 * 1024;

    private sealed record PreviewRequest(AppSettings Settings, string Sample);

    private sealed record CounterResetRequest(string? Template);

    private sealed record CounterSetRequest(string Template, long Value);

    /// <summary>Proses satu request. <paramref name="query"/> mengembalikan nilai parameter query (atau null).</summary>
    public async Task<ApiResponse> HandleAsync(
        string method,
        string path,
        Func<string, string?> query,
        Stream body,
        CancellationToken ct)
    {
        var service = runtime.Service;
        switch (method.ToUpperInvariant(), path)
        {
            case ("GET", "/api/state"):
                return ApiResponse.Ok(runtime.Snapshot());

            case ("POST", "/api/transfer/start"):
            {
                var started = service.Start();
                return ApiResponse.Ok(new { started, status = service.Status });
            }

            case ("POST", "/api/transfer/stop"):
                await service.StopAsync().ConfigureAwait(false);
                return ApiResponse.Ok(new { stopped = true, status = service.Status });

            case ("POST", "/api/printer/start"):
                return ApiResponse.Ok(await service.PrintControlAsync(true, ct).ConfigureAwait(false));

            case ("POST", "/api/printer/stop"):
                return ApiResponse.Ok(await service.PrintControlAsync(false, ct).ConfigureAwait(false));

            case ("GET", "/api/settings"):
                return ApiResponse.Ok(new
                {
                    settings = service.Settings,
                    meta = new
                    {
                        encodings = TextEncodings.Supported,
                        tokens = ControlTokens.Names,
                        builtIns = OutputFormatter.BuiltIns,
                        special = new[] { FieldKeys.Operator, FieldKeys.Template, FieldKeys.ServerCounter },
                        serialPorts = SerialPorts(),
                        serialSupported = SerialSupported,
                    },
                });

            case ("PUT", "/api/settings"):
            {
                var next = await ReadJsonAsync<AppSettings>(body, ct).ConfigureAwait(false);
                if (next is null)
                {
                    return ApiResponse.Bad("Body kosong");
                }

                var result = await service.ApplySettingsAsync(next).ConfigureAwait(false);
                if (result.Ok)
                {
                    runtime.Hub.Broadcast("counter", runtime.CounterView());
                }

                return new ApiResponse { Status = result.Ok ? 200 : 400, Json = result };
            }

            case ("POST", "/api/settings/preview"):
            {
                var request = await ReadJsonAsync<PreviewRequest>(body, ct).ConfigureAwait(false);
                return request?.Settings is null
                    ? ApiResponse.Bad("Settings kosong")
                    : ApiResponse.Ok(service.Preview(request.Settings, request.Sample));
            }

            case ("POST", "/api/settings/test-server"):
            {
                var server = await ReadJsonAsync<ServerSettings>(body, ct).ConfigureAwait(false);
                return server is null
                    ? ApiResponse.Bad("Settings server kosong")
                    : ApiResponse.Ok(await Core.Transfer.TransferService.TestServerAsync(server, ct).ConfigureAwait(false));
            }

            case ("POST", "/api/settings/test-printer"):
            {
                var printer = await ReadJsonAsync<PrinterSettings>(body, ct).ConfigureAwait(false);
                return printer is null
                    ? ApiResponse.Bad("Settings printer kosong")
                    : ApiResponse.Ok(await service.TestPrinterAsync(printer, ct).ConfigureAwait(false));
            }

            case ("POST", "/api/counter/reset"):
            {
                var request = await ReadJsonAsync<CounterResetRequest>(body, ct).ConfigureAwait(false);
                var template = string.IsNullOrWhiteSpace(request?.Template) ? null : request!.Template;
                var removed = service.ResetCounter(template);
                return ApiResponse.Ok(new { ok = true, removed, counter = runtime.CounterView() });
            }

            case ("POST", "/api/counter/set"):
            {
                var request = await ReadJsonAsync<CounterSetRequest>(body, ct).ConfigureAwait(false);
                if (request is null || string.IsNullOrWhiteSpace(request.Template) || request.Value < 0)
                {
                    return ApiResponse.Bad("Template dan nilai (>= 0) wajib diisi");
                }

                service.SetCounter(request.Template.Trim(), request.Value);
                return ApiResponse.Ok(new { ok = true, counter = runtime.CounterView() });
            }

            case ("GET", "/api/logs/dates"):
                return ApiResponse.Ok(service.Log.Dates());
        }

        if (method.Equals("GET", StringComparison.OrdinalIgnoreCase) && path.StartsWith("/api/logs/", StringComparison.Ordinal))
        {
            var rest = path["/api/logs/".Length..];
            if (rest.EndsWith("/download", StringComparison.Ordinal))
            {
                var date = Uri.UnescapeDataString(rest[..^"/download".Length]);
                return new ApiResponse
                {
                    ContentType = "text/plain; charset=utf-8",
                    FileName = $"sharx-log-{date}.txt",
                    WriteBody = (stream, token) => WriteLogFileAsync(service.Log.ReadAll(date), stream, token),
                };
            }

            if (!rest.Contains('/', StringComparison.Ordinal))
            {
                // Log History: hanya 50 baris terakhir yang cocok dengan filter (file lengkap lewat download).
                var date = Uri.UnescapeDataString(rest);
                LogStatus? filter = Enum.TryParse<LogStatus>(query("status"), true, out var parsed) ? parsed : null;
                return ApiResponse.Ok(service.Log.ReadDay(date, filter, query("q"), TransferLog.ViewLimit));
            }
        }

        return ApiResponse.NotFound;
    }

    /// <summary>Serial RS232 tidak tersedia di Android.</summary>
    public static bool SerialSupported => !OperatingSystem.IsAndroid();

    private static string[] SerialPorts()
    {
        if (!SerialSupported)
        {
            return [];
        }

        try
        {
            return System.IO.Ports.SerialPort.GetPortNames().Order().ToArray();
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or PlatformNotSupportedException)
        {
            return [];
        }
    }

    private static async Task WriteLogFileAsync(IEnumerable<LogEntry> entries, Stream body, CancellationToken ct)
    {
        await using var writer = new StreamWriter(body, new UTF8Encoding(false), 64 * 1024, leaveOpen: true);
        foreach (var e in entries)
        {
            ct.ThrowIfCancellationRequested();
            await writer.WriteAsync($"[{e.Time:yyyy-MM-dd HH:mm:ss}] {e.Status.ToString().ToUpperInvariant(),-5} {e.Message}\r\n")
                .ConfigureAwait(false);
        }
    }

    /// <summary>Body JSON kosong / rusak / terlalu besar = null (dijawab 400 oleh pemanggil).</summary>
    private static async Task<T?> ReadJsonAsync<T>(Stream body, CancellationToken ct)
        where T : class
    {
        using var buffer = new MemoryStream();
        var chunk = new byte[16 * 1024];
        int read;
        while ((read = await body.ReadAsync(chunk, ct).ConfigureAwait(false)) > 0)
        {
            if (buffer.Length + read > MaxBodyBytes)
            {
                return null;
            }

            buffer.Write(chunk, 0, read);
        }

        if (buffer.Length == 0)
        {
            return null;
        }

        try
        {
            return JsonSerializer.Deserialize<T>(buffer.GetBuffer().AsSpan(0, (int)buffer.Length), SharxRuntime.JsonOptions);
        }
        catch (Exception ex) when (ex is JsonException or NotSupportedException)
        {
            return null;
        }
    }
}

/// <summary>
/// UI ini mengontrol printer, jadi tolak request dari situs lain (CSRF) dan host asing (DNS rebinding).
/// Dipakai oleh semua host.
/// </summary>
public static class ApiGuard
{
    public static bool IsProtected(string path) =>
        path.StartsWith("/api/", StringComparison.Ordinal) || path is "/api" or "/ws";

    /// <summary>true = request boleh diproses.</summary>
    public static bool Allows(string method, string host, string? origin, string? sharxHeader, bool allowRemote)
    {
        if (!allowRemote && !IsLocalHost(HostName(host)))
        {
            return false;
        }

        if (!string.IsNullOrEmpty(origin))
        {
            var sameOrigin = Uri.TryCreate(origin, UriKind.Absolute, out var originUri)
                && string.Equals(originUri.Authority, host, StringComparison.OrdinalIgnoreCase);
            if (!sameOrigin)
            {
                return false;
            }
        }

        var mutating = !method.Equals("GET", StringComparison.OrdinalIgnoreCase)
            && !method.Equals("HEAD", StringComparison.OrdinalIgnoreCase);
        return !mutating || sharxHeader == "1";
    }

    /// <summary>"127.0.0.1:5080" → "127.0.0.1", "[::1]:5080" → "[::1]".</summary>
    private static string HostName(string host)
    {
        if (host.StartsWith('['))
        {
            var end = host.IndexOf(']', StringComparison.Ordinal);
            return end > 0 ? host[..(end + 1)] : host;
        }

        var colon = host.LastIndexOf(':');
        return colon > 0 ? host[..colon] : host;
    }

    private static bool IsLocalHost(string host) =>
        host is "localhost" or "127.0.0.1" or "[::1]" or "::1";
}
