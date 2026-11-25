using System.Net;
using System.Net.Sockets;
using System.Text.Json;

namespace Sharx.App;

/// <summary>
/// Server HTTP + WebSocket lokal (127.0.0.1) untuk host tanpa ASP.NET Core (Android). Menyajikan file UI
/// yang sama, endpoint <c>/api/*</c> lewat <see cref="SharxApi"/> dan update live <c>/ws</c> lewat
/// <see cref="LiveHub"/>, sehingga UI dan perilakunya identik dengan aplikasi desktop.
/// </summary>
public sealed class EmbeddedServer : IAsyncDisposable
{
    private static readonly Dictionary<string, string> ContentTypes = new(StringComparer.OrdinalIgnoreCase)
    {
        [".html"] = "text/html; charset=utf-8",
        [".css"] = "text/css; charset=utf-8",
        [".js"] = "text/javascript; charset=utf-8",
        [".json"] = "application/json; charset=utf-8",
        [".svg"] = "image/svg+xml",
        [".png"] = "image/png",
        [".jpg"] = "image/jpeg",
        [".ico"] = "image/x-icon",
        [".woff2"] = "font/woff2",
        [".txt"] = "text/plain; charset=utf-8",
    };

    private readonly SharxRuntime _runtime;
    private readonly SharxApi _api;
    private readonly Func<string, Stream?> _openStatic;
    private readonly HttpListener _listener = new();
    private readonly CancellationTokenSource _stop = new();
    private Task _acceptLoop = Task.CompletedTask;

    /// <param name="openStatic">Buka file UI berdasarkan path relatif (mis. "css/app.css"); null = tidak ada.</param>
    public EmbeddedServer(SharxRuntime runtime, Func<string, Stream?> openStatic, int port = 0)
    {
        _runtime = runtime;
        _api = new SharxApi(runtime);
        _openStatic = openStatic;
        Port = port > 0 ? port : FreePort();
        _listener.Prefixes.Add($"http://127.0.0.1:{Port}/");
    }

    public int Port { get; }

    public string Address => $"http://127.0.0.1:{Port}";

    public void Start()
    {
        _listener.Start();
        _acceptLoop = Task.Run(() => AcceptLoopAsync(_stop.Token));
    }

    public async ValueTask DisposeAsync()
    {
        await _stop.CancelAsync().ConfigureAwait(false);
        try
        {
            _listener.Stop();
            _listener.Close();
        }
        catch (ObjectDisposedException)
        {
            // Sudah ditutup.
        }

        try
        {
            await _acceptLoop.ConfigureAwait(false);
        }
        catch (Exception ex) when (ex is HttpListenerException or ObjectDisposedException or OperationCanceledException)
        {
            // Berhenti.
        }

        _stop.Dispose();
    }

    private static int FreePort()
    {
        var probe = new TcpListener(IPAddress.Loopback, 0);
        probe.Start();
        var port = ((IPEndPoint)probe.LocalEndpoint).Port;
        probe.Stop();
        return port;
    }

    private async Task AcceptLoopAsync(CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            HttpListenerContext context;
            try
            {
                context = await _listener.GetContextAsync().ConfigureAwait(false);
            }
            catch (Exception ex) when (ex is HttpListenerException or ObjectDisposedException or InvalidOperationException)
            {
                return;
            }

            // Setiap request ditangani terpisah: WebSocket yang lama terbuka tidak menahan request lain.
            _ = Task.Run(() => HandleSafeAsync(context, ct), CancellationToken.None);
        }
    }

    private async Task HandleSafeAsync(HttpListenerContext context, CancellationToken ct)
    {
        try
        {
            await HandleAsync(context, ct).ConfigureAwait(false);
        }
        catch (Exception ex) when (ex is not OutOfMemoryException)
        {
            SharxRuntime.WriteCrash(_runtime.DataDirectory, "EmbeddedServer", ex);
            try
            {
                context.Response.StatusCode = 500;
                context.Response.Close();
            }
            catch (Exception closeError) when (closeError is HttpListenerException or ObjectDisposedException or InvalidOperationException)
            {
                // Klien sudah pergi.
            }
        }
    }

    private async Task HandleAsync(HttpListenerContext context, CancellationToken ct)
    {
        var request = context.Request;
        var response = context.Response;
        var path = request.Url?.AbsolutePath ?? "/";

        if (ApiGuard.IsProtected(path)
            && !ApiGuard.Allows(request.HttpMethod, request.UserHostName ?? "", request.Headers["Origin"], request.Headers["X-Sharx"], allowRemote: false))
        {
            response.StatusCode = 403;
            response.Close();
            return;
        }

        if (path == "/ws")
        {
            if (!request.IsWebSocketRequest)
            {
                response.StatusCode = 400;
                response.Close();
                return;
            }

            var socket = await context.AcceptWebSocketAsync(null, TimeSpan.FromSeconds(20)).ConfigureAwait(false);
            using (socket.WebSocket)
            {
                await _runtime.Hub.HandleAsync(socket.WebSocket, _runtime.Snapshot(), ct).ConfigureAwait(false);
            }

            return;
        }

        if (path.StartsWith("/api/", StringComparison.Ordinal))
        {
            var result = await _api.HandleAsync(
                request.HttpMethod,
                path,
                name => request.QueryString[name],
                request.InputStream,
                ct).ConfigureAwait(false);
            await WriteApiAsync(response, result, ct).ConfigureAwait(false);
            return;
        }

        await WriteStaticAsync(response, path, ct).ConfigureAwait(false);
    }

    private static async Task WriteApiAsync(HttpListenerResponse response, ApiResponse result, CancellationToken ct)
    {
        response.StatusCode = result.Status;
        response.Headers["Cache-Control"] = "no-store";
        if (result.WriteBody is not null)
        {
            response.ContentType = result.ContentType ?? "application/octet-stream";
            if (result.FileName is not null)
            {
                response.Headers["Content-Disposition"] = $"attachment; filename=\"{result.FileName}\"";
            }

            response.SendChunked = true;
            await result.WriteBody(response.OutputStream, ct).ConfigureAwait(false);
        }
        else
        {
            var bytes = JsonSerializer.SerializeToUtf8Bytes(result.Json, SharxRuntime.JsonOptions);
            response.ContentType = "application/json; charset=utf-8";
            response.ContentLength64 = bytes.Length;
            await response.OutputStream.WriteAsync(bytes, ct).ConfigureAwait(false);
        }

        response.Close();
    }

    private async Task WriteStaticAsync(HttpListenerResponse response, string path, CancellationToken ct)
    {
        var relative = Uri.UnescapeDataString(path).TrimStart('/');
        if (relative.Length == 0)
        {
            relative = "index.html";
        }

        // Hanya file di dalam folder UI.
        var file = relative.Contains("..", StringComparison.Ordinal) ? null : _openStatic(relative);
        if (file is null)
        {
            response.StatusCode = 404;
            response.Close();
            return;
        }

        await using (file)
        {
            response.ContentType = ContentTypes.GetValueOrDefault(Path.GetExtension(relative), "application/octet-stream");
            response.Headers["Cache-Control"] = "no-cache";
            await file.CopyToAsync(response.OutputStream, ct).ConfigureAwait(false);
        }

        response.Close();
    }
}
