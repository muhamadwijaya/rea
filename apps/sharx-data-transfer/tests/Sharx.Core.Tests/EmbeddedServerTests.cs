using System.Net;
using System.Net.Http.Json;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using Sharx.App;

namespace Sharx.Core.Tests;

/// <summary>Server lokal host Android: UI, API, guard dan update live sama dengan host desktop.</summary>
public class EmbeddedServerTests
{
    private static async Task<JsonElement> ReceiveJsonAsync(ClientWebSocket socket, CancellationToken ct)
    {
        var buffer = new byte[256 * 1024];
        var total = 0;
        ValueWebSocketReceiveResult result;
        do
        {
            result = await socket.ReceiveAsync(buffer.AsMemory(total), ct);
            total += result.Count;
        }
        while (!result.EndOfMessage);

        return JsonDocument.Parse(buffer.AsMemory(0, total)).RootElement.Clone();
    }

    [Fact]
    public async Task Serves_the_same_ui_api_and_live_updates_as_the_desktop_host()
    {
        var dir = Path.Combine(Path.GetTempPath(), $"sharx-embedded-{Guid.NewGuid():N}");
        try
        {
            await using var runtime = new SharxRuntime(dir);
            var files = new Dictionary<string, string>
            {
                ["index.html"] = "<!doctype html><title>SHARX</title>",
                ["css/app.css"] = "body{}",
            };
            await using var server = new EmbeddedServer(
                runtime,
                path => files.TryGetValue(path, out var text) ? new MemoryStream(Encoding.UTF8.GetBytes(text)) : null);
            server.Start();
            runtime.Start(server.Address);
            using var http = new HttpClient { BaseAddress = new Uri(server.Address) };

            // File UI.
            Assert.Equal(files["index.html"], await http.GetStringAsync("/"));
            var css = await http.GetAsync("/css/app.css");
            Assert.Equal("text/css", css.Content.Headers.ContentType?.MediaType);
            Assert.Equal(HttpStatusCode.NotFound, (await http.GetAsync("/tidak-ada.js")).StatusCode);

            // API.
            using var state = JsonDocument.Parse(await http.GetStringAsync("/api/state"));
            Assert.Equal(SharxRuntime.Version, state.RootElement.GetProperty("version").GetString());
            Assert.Equal(dir, state.RootElement.GetProperty("dataDirectory").GetString());
            using var settings = JsonDocument.Parse(await http.GetStringAsync("/api/settings"));
            Assert.True(settings.RootElement.GetProperty("meta").GetProperty("serialSupported").GetBoolean());

            // Guard: perintah tanpa header X-Sharx atau dari situs lain ditolak.
            Assert.Equal(HttpStatusCode.Forbidden, (await http.PostAsync("/api/transfer/stop", null)).StatusCode);
            using (var foreign = new HttpRequestMessage(HttpMethod.Get, "/api/state"))
            {
                foreign.Headers.Add("Origin", "http://evil.example");
                Assert.Equal(HttpStatusCode.Forbidden, (await http.SendAsync(foreign)).StatusCode);
            }

            // Update live lewat WebSocket: snapshot dulu, lalu perubahan counter.
            using var socket = new ClientWebSocket();
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(15));
            await socket.ConnectAsync(new Uri($"ws://127.0.0.1:{server.Port}/ws"), timeout.Token);
            var first = await ReceiveJsonAsync(socket, timeout.Token);
            Assert.Equal("snapshot", first.GetProperty("type").GetString());

            using (var set = new HttpRequestMessage(HttpMethod.Post, "/api/counter/set"))
            {
                set.Headers.Add("X-Sharx", "1");
                set.Content = JsonContent.Create(new { template = "555111", value = 7 });
                var response = await http.SendAsync(set);
                Assert.Equal(HttpStatusCode.OK, response.StatusCode);
            }

            JsonElement counter;
            do
            {
                var message = await ReceiveJsonAsync(socket, timeout.Token);
                counter = message.GetProperty("type").GetString() == "batch"
                    && message.GetProperty("data").GetProperty("counter").ValueKind == JsonValueKind.Object
                        ? message.GetProperty("data").GetProperty("counter")
                        : default;
            }
            while (counter.ValueKind != JsonValueKind.Object);

            Assert.Contains(counter.GetProperty("templates").EnumerateArray(), t => t.GetProperty("template").GetString() == "555111");

            // Settings tidak valid: 400 dengan pesan.
            using (var put = new HttpRequestMessage(HttpMethod.Put, "/api/settings"))
            {
                put.Headers.Add("X-Sharx", "1");
                put.Content = new StringContent("""{"server":{"host":"","port":0}}""", Encoding.UTF8, "application/json");
                var response = await http.SendAsync(put);
                Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
                using var errors = JsonDocument.Parse(await response.Content.ReadAsStringAsync());
                Assert.NotEmpty(errors.RootElement.GetProperty("errors").EnumerateArray());
            }

            // Download log harian lengkap.
            var date = runtime.Log.Dates().First();
            var download = await http.GetAsync($"/api/logs/{date}/download");
            Assert.Equal("attachment", download.Content.Headers.ContentDisposition?.DispositionType);
            Assert.Contains("siap", await download.Content.ReadAsStringAsync(), StringComparison.Ordinal);

            await socket.CloseAsync(WebSocketCloseStatus.NormalClosure, "selesai", timeout.Token);
        }
        finally
        {
            if (Directory.Exists(dir))
            {
                Directory.Delete(dir, recursive: true);
            }
        }
    }

    [Fact]
    public void Guard_accepts_only_local_same_origin_requests()
    {
        Assert.True(ApiGuard.Allows("GET", "127.0.0.1:5080", null, null, allowRemote: false));
        Assert.True(ApiGuard.Allows("POST", "localhost:5080", "http://localhost:5080", "1", allowRemote: false));
        Assert.False(ApiGuard.Allows("POST", "127.0.0.1:5080", null, null, allowRemote: false));
        Assert.False(ApiGuard.Allows("GET", "evil.example:5080", null, null, allowRemote: false));
        Assert.False(ApiGuard.Allows("GET", "127.0.0.1:5080", "http://evil.example", null, allowRemote: false));
        Assert.True(ApiGuard.Allows("GET", "192.168.1.5:5080", null, null, allowRemote: true));
    }
}
