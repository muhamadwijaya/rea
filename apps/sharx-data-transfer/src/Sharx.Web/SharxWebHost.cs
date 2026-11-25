using System.Net;
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
using Sharx.App;

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

/// <summary>
/// Host desktop / browser: ASP.NET Core (Kestrel) menyajikan UI, <c>/api/*</c> lewat <see cref="SharxApi"/>
/// dan update live <c>/ws</c> lewat <see cref="LiveHub"/>. Logika aplikasi ada di <see cref="SharxRuntime"/>.
/// </summary>
public static class SharxWebHost
{
    public static WebApplication Build(SharxHostOptions options, string[]? args = null)
    {
        var runtime = new SharxRuntime(options.DataDirectory);
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
        builder.Services.AddSingleton(runtime);
        builder.Services.AddSingleton(runtime.Service);
        builder.Services.AddSingleton(options);

        var app = builder.Build();
        app.Lifetime.ApplicationStarted.Register(() => runtime.Start(ListeningAddress(app)));
        app.Lifetime.ApplicationStopping.Register(() => runtime.DisposeAsync().AsTask().GetAwaiter().GetResult());

        app.Use((context, next) =>
        {
            var request = context.Request;
            if (ApiGuard.IsProtected(request.Path.Value ?? "")
                && !ApiGuard.Allows(request.Method, request.Host.Value ?? "", request.Headers.Origin, request.Headers["X-Sharx"], options.AllowRemote))
            {
                context.Response.StatusCode = StatusCodes.Status403Forbidden;
                return Task.CompletedTask;
            }

            return next(context);
        });
        app.UseWebSockets(new WebSocketOptions { KeepAliveInterval = TimeSpan.FromSeconds(20) });
        app.UseDefaultFiles();
        app.UseStaticFiles(new StaticFileOptions
        {
            FileProvider = new PhysicalFileProvider(webRoot),
            OnPrepareResponse = ctx => ctx.Context.Response.Headers.CacheControl = "no-cache",
        });

        var api = new SharxApi(runtime);
        app.Map("/api/{**rest}", async (HttpContext context) =>
        {
            var request = context.Request;
            var result = await api.HandleAsync(
                request.Method,
                request.Path.Value ?? "",
                name => request.Query.TryGetValue(name, out var value) ? value.ToString() : null,
                request.Body,
                context.RequestAborted);
            var response = context.Response;
            response.StatusCode = result.Status;
            response.Headers.CacheControl = "no-store";
            if (result.WriteBody is not null)
            {
                response.ContentType = result.ContentType ?? "application/octet-stream";
                if (result.FileName is not null)
                {
                    response.Headers.ContentDisposition = $"attachment; filename=\"{result.FileName}\"";
                }

                await result.WriteBody(response.Body, context.RequestAborted);
                return;
            }

            response.ContentType = "application/json; charset=utf-8";
            await JsonSerializer.SerializeAsync(response.Body, result.Json, SharxRuntime.JsonOptions, context.RequestAborted);
        });

        app.Map("/ws", async (HttpContext context) =>
        {
            if (!context.WebSockets.IsWebSocketRequest)
            {
                context.Response.StatusCode = StatusCodes.Status400BadRequest;
                return;
            }

            using var socket = await context.WebSockets.AcceptWebSocketAsync();
            await runtime.Hub.HandleAsync(socket, runtime.Snapshot(), context.RequestAborted);
        });

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
}
