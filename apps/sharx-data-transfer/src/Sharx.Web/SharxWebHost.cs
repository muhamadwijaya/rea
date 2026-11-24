using System.Net;
using System.Reflection;
using System.Text;
using System.Text.Json;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Hosting.Server;
using Microsoft.AspNetCore.Hosting.Server.Features;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.FileProviders;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Sharx.Core.Configuration;
using Sharx.Core.Counter;
using Sharx.Core.Logging;
using Sharx.Core.Mapping;
using Sharx.Core.Text;
using Sharx.Core.Transfer;

namespace Sharx.Web;

public sealed class SharxHostOptions
{
    /// <summary>Folder settings.json, counters.json dan logs/.</summary>
    public string DataDirectory { get; set; } = DefaultDataDirectory();

    /// <summary>Port UI lokal. 0 = pilih port kosong otomatis.</summary>
    public int Port { get; set; } = 5080;

    /// <summary>Izinkan akses UI dari komputer lain di jaringan (default hanya komputer ini).</summary>
    public bool AllowRemote { get; set; }

    public static string DefaultDataDirectory()
    {
        // Paket publish: run.bat + app\ (program). Data disimpan di samping run.bat.
        var baseDirectory = AppContext.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        var parent = Directory.GetParent(baseDirectory)?.FullName;
        var root = parent is not null && File.Exists(Path.Combine(parent, "run.bat")) ? parent : baseDirectory;
        var portable = Path.Combine(root, "data");
        try
        {
            Directory.CreateDirectory(portable);
            var probe = Path.Combine(portable, ".write-test");
            File.WriteAllText(probe, "");
            File.Delete(probe);
            return portable;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            return Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "SharxDataTransfer");
        }
    }
}

public sealed class AlreadyRunningException(string dataDirectory)
    : Exception($"SHARX Data Transfer sudah berjalan (folder data: {dataDirectory}).");

public static class SharxWebHost
{
    /// <summary>
    /// Catat error yang tidak tertangani ke <c>crash.log</c> dan cegah task yang gagal mematikan aplikasi.
    /// </summary>
    public static void InstallCrashGuards(string dataDirectory)
    {
        TaskScheduler.UnobservedTaskException += (_, e) =>
        {
            WriteCrash(dataDirectory, "UnobservedTaskException", e.Exception);
            e.SetObserved();
        };
        AppDomain.CurrentDomain.UnhandledException += (_, e) =>
            WriteCrash(dataDirectory, "UnhandledException", e.ExceptionObject as Exception);
    }

    public static void WriteCrash(string dataDirectory, string kind, Exception? ex)
    {
        try
        {
            Directory.CreateDirectory(dataDirectory);
            File.AppendAllText(
                Path.Combine(dataDirectory, "crash.log"),
                $"{DateTimeOffset.Now:O}\t{kind}\t{ex}\n\n");
        }
        catch (Exception logError) when (logError is IOException or UnauthorizedAccessException)
        {
            // Tidak bisa menulis log crash; jangan menambah error baru.
        }
    }

    public static string Version { get; } =
        typeof(SharxWebHost).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion
            .Split('+')[0] ?? "1.0.0";

    public static JsonSerializerOptions JsonOptions { get; } = JsonFile.CreateOptions(indented: false);

    public static WebApplication Build(SharxHostOptions options, string[]? args = null)
    {
        Directory.CreateDirectory(options.DataDirectory);
        var instanceLock = AcquireInstanceLock(options.DataDirectory);

        var webRoot = Path.Combine(AppContext.BaseDirectory, "wwwroot");
        var builder = WebApplication.CreateSlimBuilder(new WebApplicationOptions
        {
            Args = args ?? [],
            ContentRootPath = AppContext.BaseDirectory,
            WebRootPath = webRoot,
        });
        builder.Logging.ClearProviders();
        builder.WebHost.ConfigureKestrel(k =>
        {
            if (options.AllowRemote)
            {
                k.Listen(IPAddress.Any, options.Port);
            }
            else
            {
                k.Listen(IPAddress.Loopback, options.Port);
            }
        });
        builder.Services.ConfigureHttpJsonOptions(o =>
        {
            o.SerializerOptions.PropertyNamingPolicy = JsonOptions.PropertyNamingPolicy;
            foreach (var converter in JsonOptions.Converters)
            {
                o.SerializerOptions.Converters.Add(converter);
            }
        });

        var time = TimeProvider.System;
        var store = new SettingsStore(Path.Combine(options.DataDirectory, "settings.json"));
        var (settings, settingsWarning) = store.Load();
        var log = new TransferLog(Path.Combine(options.DataDirectory, "logs"), time);
        var counter = new CounterEngine(Path.Combine(options.DataDirectory, "counters.json"), time);
        var service = new TransferService(settings, store, counter, log, new TransferStats(), time);
        var hub = new LiveHub(JsonOptions);

        builder.Services.AddSingleton(service);
        builder.Services.AddSingleton(hub);
        builder.Services.AddSingleton(options);

        var app = builder.Build();
        app.Lifetime.ApplicationStopped.Register(instanceLock.Dispose);

        foreach (var warning in new[] { settingsWarning, counter.LoadWarning })
        {
            if (warning is not null)
            {
                log.Write(LogStatus.Warn, warning);
            }
        }

        var batcher = new LiveBatcher(service, hub, () => CounterView(service));
        app.Lifetime.ApplicationStarted.Register(() =>
        {
            log.Write(LogStatus.Info, $"SHARX Data Transfer v{Version} siap ({ListeningAddress(app)})");
            service.StartBackground();
            if (service.Settings.General.AutoStart)
            {
                service.Start();
            }
        });
        app.Lifetime.ApplicationStopping.Register(() =>
        {
            batcher.DisposeAsync().AsTask().GetAwaiter().GetResult();
            service.DisposeAsync().AsTask().GetAwaiter().GetResult();
            log.DisposeAsync().AsTask().GetAwaiter().GetResult();
        });

        app.Use(GuardAsync(options));
        app.UseWebSockets(new WebSocketOptions { KeepAliveInterval = TimeSpan.FromSeconds(20) });
        app.UseDefaultFiles();
        app.UseStaticFiles(new StaticFileOptions
        {
            FileProvider = new PhysicalFileProvider(webRoot),
            OnPrepareResponse = ctx => ctx.Context.Response.Headers.CacheControl = "no-cache",
        });

        MapApi(app, service, hub, options);
        return app;
    }

    /// <summary>Alamat UI setelah server berjalan (berguna jika Port = 0).</summary>
    public static string ListeningAddress(WebApplication app)
    {
        var address = app.Services.GetRequiredService<IServer>().Features.Get<IServerAddressesFeature>()?.Addresses.FirstOrDefault()
            ?? "http://127.0.0.1:5080";
        return address.Replace("0.0.0.0", "127.0.0.1", StringComparison.Ordinal)
            .Replace("[::]", "127.0.0.1", StringComparison.Ordinal);
    }

    private static FileStream AcquireInstanceLock(string dataDirectory)
    {
        try
        {
            return new FileStream(
                Path.Combine(dataDirectory, ".instance.lock"),
                FileMode.OpenOrCreate,
                FileAccess.ReadWrite,
                FileShare.None,
                1,
                FileOptions.DeleteOnClose);
        }
        catch (IOException)
        {
            throw new AlreadyRunningException(dataDirectory);
        }
    }

    /// <summary>
    /// UI ini mengontrol printer, jadi tolak request dari situs lain (CSRF) dan host asing (DNS rebinding).
    /// </summary>
    private static Func<HttpContext, RequestDelegate, Task> GuardAsync(SharxHostOptions options) => (context, next) =>
    {
        var path = context.Request.Path;
        var isApi = path.StartsWithSegments("/api") || path.StartsWithSegments("/ws");
        if (!isApi)
        {
            return next(context);
        }

        if (!options.AllowRemote && !IsLocalHost(context.Request.Host.Host))
        {
            context.Response.StatusCode = StatusCodes.Status403Forbidden;
            return Task.CompletedTask;
        }

        var origin = context.Request.Headers.Origin.ToString();
        if (origin.Length > 0)
        {
            var sameOrigin = Uri.TryCreate(origin, UriKind.Absolute, out var originUri)
                && string.Equals(originUri.Authority, context.Request.Host.Value, StringComparison.OrdinalIgnoreCase);
            if (!sameOrigin)
            {
                context.Response.StatusCode = StatusCodes.Status403Forbidden;
                return Task.CompletedTask;
            }
        }

        var mutating = !HttpMethods.IsGet(context.Request.Method) && !HttpMethods.IsHead(context.Request.Method);
        if (mutating && context.Request.Headers["X-Sharx"] != "1")
        {
            context.Response.StatusCode = StatusCodes.Status403Forbidden;
            return Task.CompletedTask;
        }

        return next(context);
    };

    private static bool IsLocalHost(string host) =>
        host is "localhost" or "127.0.0.1" or "[::1]" or "::1";

    private static object Snapshot(TransferService service, SharxHostOptions options) => new
    {
        Version,
        DataDirectory = options.DataDirectory,
        Status = service.Status,
        Stats = service.Stats.Snapshot(),
        Counter = CounterView(service),
        Records = service.RecentRecords(TransferLog.ViewLimit),
        Log = service.Log.Recent(TransferLog.ViewLimit),
        Feedback = service.RecentFeedback(TransferLog.ViewLimit),
    };

    private static object CounterView(TransferService service)
    {
        var state = service.Counter.Snapshot();
        var digits = service.Settings.Counter.Digits;
        var active = state.ActiveTemplate is not null && state.Templates.TryGetValue(state.ActiveTemplate, out var c) ? c : null;
        return new
        {
            state.BusinessDate,
            state.ActiveTemplate,
            state.ActiveOperator,
            ActiveValue = active is null ? null : CounterEngine.Format(active.Value, active.Width > 0 ? active.Width : digits),
            PersistError = service.Counter.LastPersistError,
            Templates = state.Templates
                .OrderByDescending(kv => kv.Value.UpdatedAt)
                .Select(kv => new
                {
                    Template = kv.Key,
                    kv.Value.Value,
                    Text = CounterEngine.Format(kv.Value.Value, kv.Value.Width > 0 ? kv.Value.Width : digits),
                    kv.Value.Records,
                    kv.Value.LastOperator,
                    kv.Value.UpdatedAt,
                }),
        };
    }

    private sealed record PreviewRequest(AppSettings Settings, string Sample);

    private sealed record CounterResetRequest(string? Template);

    private sealed record CounterSetRequest(string Template, long Value);

    private static void MapApi(WebApplication app, TransferService service, LiveHub hub, SharxHostOptions options)
    {
        app.MapGet("/api/state", () => Results.Json(Snapshot(service, options), JsonOptions));

        app.MapPost("/api/transfer/start", () =>
        {
            var started = service.Start();
            return Results.Json(new { started, status = service.Status }, JsonOptions);
        });

        app.MapPost("/api/transfer/stop", async () =>
        {
            await service.StopAsync();
            return Results.Json(new { stopped = true, status = service.Status }, JsonOptions);
        });

        app.MapPost("/api/printer/start", async (CancellationToken ct) =>
            Results.Json(await service.PrintControlAsync(true, ct), JsonOptions));

        app.MapPost("/api/printer/stop", async (CancellationToken ct) =>
            Results.Json(await service.PrintControlAsync(false, ct), JsonOptions));

        app.MapGet("/api/settings", () => Results.Json(new
        {
            settings = service.Settings,
            meta = new
            {
                encodings = TextEncodings.Supported,
                tokens = ControlTokens.Names,
                builtIns = OutputFormatter.BuiltIns,
                special = new[] { FieldKeys.Operator, FieldKeys.Template, FieldKeys.ServerCounter },
                serialPorts = SafeSerialPorts(),
            },
        }, JsonOptions));

        app.MapPut("/api/settings", async (HttpRequest request) =>
        {
            var next = await ReadJsonAsync<AppSettings>(request);
            if (next is null)
            {
                return Results.BadRequest(new { ok = false, errors = new[] { "Body kosong" } });
            }

            var result = await service.ApplySettingsAsync(next);
            if (result.Ok)
            {
                hub.Broadcast("counter", CounterView(service));
            }

            return Results.Json(result, JsonOptions, statusCode: result.Ok ? 200 : 400);
        });

        app.MapPost("/api/settings/preview", async (HttpRequest request) =>
        {
            var body = await ReadJsonAsync<PreviewRequest>(request);
            return body?.Settings is null
                ? Results.BadRequest()
                : Results.Json(service.Preview(body.Settings, body.Sample), JsonOptions);
        });

        app.MapPost("/api/settings/test-server", async (HttpRequest request, CancellationToken ct) =>
        {
            var server = await ReadJsonAsync<ServerSettings>(request, ct);
            return server is null
                ? Results.BadRequest()
                : Results.Json(await TransferService.TestServerAsync(server, ct), JsonOptions);
        });

        app.MapPost("/api/settings/test-printer", async (HttpRequest request, CancellationToken ct) =>
        {
            var printer = await ReadJsonAsync<PrinterSettings>(request, ct);
            return printer is null
                ? Results.BadRequest()
                : Results.Json(await service.TestPrinterAsync(printer, ct), JsonOptions);
        });

        app.MapPost("/api/counter/reset", async (HttpRequest request) =>
        {
            var body = request.ContentLength > 0 ? await ReadJsonAsync<CounterResetRequest>(request) : null;
            var template = string.IsNullOrWhiteSpace(body?.Template) ? null : body!.Template;
            var removed = service.ResetCounter(template);
            return Results.Json(new { ok = true, removed, counter = CounterView(service) }, JsonOptions);
        });

        app.MapPost("/api/counter/set", async (HttpRequest request) =>
        {
            var body = await ReadJsonAsync<CounterSetRequest>(request);
            if (body is null || string.IsNullOrWhiteSpace(body.Template) || body.Value < 0)
            {
                return Results.BadRequest(new { ok = false, errors = new[] { "Template dan nilai (>= 0) wajib diisi" } });
            }

            service.SetCounter(body.Template.Trim(), body.Value);
            return Results.Json(new { ok = true, counter = CounterView(service) }, JsonOptions);
        });

        app.MapGet("/api/logs/dates", () => Results.Json(service.Log.Dates(), JsonOptions));

        // Log History: hanya 50 baris terakhir yang cocok dengan filter (file lengkap lewat download).
        app.MapGet("/api/logs/{date}", (string date, string? status, string? q) =>
        {
            LogStatus? filter = Enum.TryParse<LogStatus>(status, true, out var parsed) ? parsed : null;
            return Results.Json(service.Log.ReadDay(date, filter, q, TransferLog.ViewLimit), JsonOptions);
        });

        app.MapGet("/api/logs/{date}/download", (string date, HttpContext context) =>
        {
            context.Response.ContentType = "text/plain; charset=utf-8";
            context.Response.Headers.ContentDisposition = $"attachment; filename=\"sharx-log-{date}.txt\"";
            return WriteLogFileAsync(service.Log.ReadAll(date), context.Response.Body, context.RequestAborted);
        });

        app.Map("/ws", async (HttpContext context) =>
        {
            if (!context.WebSockets.IsWebSocketRequest)
            {
                context.Response.StatusCode = StatusCodes.Status400BadRequest;
                return;
            }

            using var socket = await context.WebSockets.AcceptWebSocketAsync();
            await hub.HandleAsync(socket, Snapshot(service, options), context.RequestAborted);
        });
    }

    private static async Task WriteLogFileAsync(IEnumerable<LogEntry> entries, Stream body, CancellationToken ct)
    {
        await using var writer = new StreamWriter(body, new UTF8Encoding(false), 64 * 1024, leaveOpen: true);
        foreach (var e in entries)
        {
            ct.ThrowIfCancellationRequested();
            await writer.WriteAsync($"[{e.Time:yyyy-MM-dd HH:mm:ss}] {e.Status.ToString().ToUpperInvariant(),-5} {e.Message}\r\n");
        }
    }

    private static async Task<T?> ReadJsonAsync<T>(HttpRequest request, CancellationToken ct = default)
        where T : class
    {
        try
        {
            return await request.ReadFromJsonAsync<T>(JsonOptions, ct);
        }
        catch (Exception ex) when (ex is JsonException or InvalidOperationException or NotSupportedException)
        {
            return null;
        }
    }

    private static string[] SafeSerialPorts()
    {
        try
        {
            return System.IO.Ports.SerialPort.GetPortNames().Order().ToArray();
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or PlatformNotSupportedException)
        {
            return [];
        }
    }
}
