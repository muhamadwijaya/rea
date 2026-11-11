using System.Net;
using SmcTerminal.Gateway;
using SmcTerminal.Gateway.Models;
using SmcTerminal.Gateway.Services;

var builder = WebApplication.CreateBuilder(args);

var section = builder.Configuration.GetSection("Terminal");
builder.Services.Configure<TerminalOptions>(section);
var options = section.Get<TerminalOptions>() ?? new TerminalOptions();

builder.Services.AddSignalR();
builder.Services.AddHttpClient<EngineClient>(client =>
{
    client.BaseAddress = new Uri(options.EngineUrl.TrimEnd('/') + "/");
    client.Timeout = TimeSpan.FromSeconds(120);
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
builder.Services.AddHostedService<NewsWorker>();
builder.Services.AddHostedService<CalendarWorker>();
builder.Services.AddHostedService<ScreenerWorker>();

var app = builder.Build();

app.UseDefaultFiles();
app.UseStaticFiles(new StaticFileOptions
{
    OnPrepareResponse = ctx => ctx.Context.Response.Headers.CacheControl = "no-cache",
});
app.MapHub<MarketHub>("/hub/market");
app.MapTerminalApi();

app.Logger.LogInformation("SMC Terminal gateway — engine {Engine}, {Feeds} feed berita", options.EngineUrl, options.Feeds.Count);
app.Run();
