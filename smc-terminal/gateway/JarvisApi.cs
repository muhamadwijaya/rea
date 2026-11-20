using System.Net;
using SmcTerminal.Gateway.Services;

namespace SmcTerminal.Gateway;

/// <summary>
/// /api/jarvis: install, start and stop Mark LVII on this PC and read its state for the JARVIS tab.
/// These requests download and run a program, so they are accepted only from the app's own page on the loopback
/// address: the Host must be loopback (a DNS name rebound to 127.0.0.1 is refused), the connection must come from this
/// PC, and changes need the <c>X-Sharx-Action</c> header, which a page of another site cannot send without a CORS
/// preflight that the gateway never approves.
/// </summary>
public static class JarvisApi
{
    public const string ActionHeader = "X-Sharx-Action";

    public static void MapJarvisApi(this WebApplication app)
    {
        var jarvis = app.MapGroup("/api/jarvis").AddEndpointFilter(LocalPageOnly);
        jarvis.MapGet("/status", async (long? after, JarvisService service, CancellationToken ct) =>
            Results.Json(await service.StatusAsync(after ?? 0, ct)));
        jarvis.MapPost("/install", async (JarvisService service, CancellationToken ct) => Reply(await service.BeginInstallAsync(ct)));
        jarvis.MapPost("/cancel", (JarvisService service) => Reply(service.CancelInstall()));
        jarvis.MapPost("/start", async (JarvisService service, CancellationToken ct) => Reply(await service.LaunchAsync(ct)));
        jarvis.MapPost("/stop", async (JarvisService service) => Reply(await service.StopJarvisAsync(TimeSpan.FromSeconds(10))));
    }

    private static IResult Reply(JarvisResult result) => result.Ok
        ? Results.Json(new { ok = true, message = result.Message })
        : Results.Json(new { error = result.Error, message = result.Message }, statusCode: StatusCodes.Status409Conflict);

    private static async ValueTask<object?> LocalPageOnly(EndpointFilterInvocationContext context, EndpointFilterDelegate next)
    {
        var http = context.HttpContext;
        var request = http.Request;
        var remote = http.Connection.RemoteIpAddress;
        var change = !HttpMethods.IsGet(request.Method) && !HttpMethods.IsHead(request.Method);
        var site = request.Headers["Sec-Fetch-Site"].ToString();
        if (!IsLoopbackHost(request.Host.Host) || remote is null || !IPAddress.IsLoopback(remote)
            || (change && (request.Headers[ActionHeader] != "1" || site is "cross-site" or "same-site")))
        {
            return Results.Json(new { error = "forbidden", message = "Kontrol JARVIS hanya dari aplikasi SHARX di PC ini." },
                statusCode: StatusCodes.Status403Forbidden);
        }
        return await next(context);
    }

    private static bool IsLoopbackHost(string host)
    {
        if (host.Equals("localhost", StringComparison.OrdinalIgnoreCase))
        {
            return true;
        }
        return IPAddress.TryParse(host.Trim('[', ']'), out var address) && IPAddress.IsLoopback(address);
    }
}
