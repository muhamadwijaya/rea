using System.Net;
using System.Net.Sockets;
using SmcTerminal.Gateway.Models;
using SmcTerminal.Gateway.Services;

namespace SmcTerminal.Gateway;

/// <summary>Builds the gateway web application. Used by Program (browser mode) and by the desktop app.</summary>
public static class GatewayHost
{
    public static WebApplication Build(string[] args, string? contentRoot = null)
    {
        var builder = WebApplication.CreateBuilder(new WebApplicationOptions { Args = args, ContentRootPath = contentRoot });
        var section = builder.Configuration.GetSection("Terminal");
        var options = section.Get<TerminalOptions>() ?? new TerminalOptions();
        var root = Path.GetFullPath(Path.Combine(builder.Environment.ContentRootPath, ".."));

        if (!string.IsNullOrWhiteSpace(options.LogDirectory))
        {
            builder.Logging.AddProvider(new FileLoggerProvider(Path.Combine(options.LogDirectory, "gateway.log")));
        }

        if (options.ManageEngine)
        {
            var preferred = Uri.TryCreate(options.EngineUrl, UriKind.Absolute, out var uri) ? uri.Port : 8765;
            var port = Ports.FreeOr(preferred);
            options.EngineUrl = $"http://127.0.0.1:{port}";
            builder.Configuration["Terminal:EngineUrl"] = options.EngineUrl;
            var python = options.EnginePython ?? DefaultPython(root);
            var logDir = options.LogDirectory ?? Path.Combine(root, "logs");
            builder.Services.AddSingleton(new EngineProcessOptions(python, Path.Combine(root, "engine"), port, Path.Combine(logDir, "engine.log")));
            builder.Services.AddSingleton<EngineSupervisor>();
            builder.Services.AddHostedService(sp => sp.GetRequiredService<EngineSupervisor>());
        }

        // Bound after the managed-engine port override above so every worker sees the final EngineUrl.
        builder.Services.Configure<TerminalOptions>(builder.Configuration.GetSection("Terminal"));
        builder.Services.AddSignalR();
        builder.Services.AddHttpClient<EngineClient>(client =>
        {
            client.BaseAddress = new Uri(options.EngineUrl.TrimEnd('/') + "/");
            client.Timeout = TimeSpan.FromSeconds(240); // AI analysis can take a few minutes
        });
        builder.Services.AddHttpClient(NewsWorker.HttpName, client =>
            {
                client.Timeout = TimeSpan.FromSeconds(20);
                client.DefaultRequestHeaders.UserAgent.ParseAdd(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36");
                client.DefaultRequestHeaders.Accept.ParseAdd(
                    "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.9, application/json;q=0.8, */*;q=0.5");
            })
            .ConfigurePrimaryHttpMessageHandler(() => new SocketsHttpHandler
            {
                AutomaticDecompression = DecompressionMethods.All,
                PooledConnectionLifetime = TimeSpan.FromMinutes(10),
            });

        builder.Services.AddSingleton<Broadcaster>();
        builder.Services.AddSingleton<MarketState>();
        builder.Services.AddSingleton<NewsStore>();
        builder.Services.AddSingleton<CalendarStore>();
        builder.Services.AddSingleton<StatsService>();
        builder.Services.AddSingleton<SymbolNewsService>();
        builder.Services.AddSingleton<SecretStore>();
        builder.Services.AddHostedService<NewsWorker>();
        builder.Services.AddHostedService<CalendarWorker>();
        builder.Services.AddHostedService<ScreenerWorker>();
        builder.Services.AddHostedService<QuotesWorker>();
        builder.Services.AddHostedService<FundamentalWorker>();

        var app = builder.Build();
        app.UseDefaultFiles();
        app.UseStaticFiles(new StaticFileOptions
        {
            OnPrepareResponse = ctx => ctx.Context.Response.Headers.CacheControl = "no-cache",
        });
        app.MapHub<MarketHub>("/hub/market");
        app.MapTerminalApi();
        app.Logger.LogInformation("SMC Terminal gateway — engine {Engine} ({Mode}), {Feeds} feed berita",
            options.EngineUrl, options.ManageEngine ? "dikelola gateway" : "eksternal", options.Feeds.Count);
        return app;
    }

    private static string DefaultPython(string root) => OperatingSystem.IsWindows()
        ? Path.Combine(root, "engine", ".venv", "Scripts", "python.exe")
        : Path.Combine(root, "engine", ".venv", "bin", "python");
}

public static class Ports
{
    /// <summary>The preferred loopback port when it is free, otherwise any free port.</summary>
    public static int FreeOr(int preferred)
    {
        foreach (var candidate in new[] { preferred, 0 })
        {
            try
            {
                var listener = new TcpListener(IPAddress.Loopback, candidate);
                listener.Start();
                var port = ((IPEndPoint)listener.LocalEndpoint).Port;
                listener.Stop();
                return port;
            }
            catch (SocketException) when (candidate != 0)
            {
                // preferred port busy, fall back to an ephemeral one
            }
        }
        throw new InvalidOperationException("Tidak ada port lokal yang bebas.");
    }
}
