using System.Collections.Concurrent;
using System.Net.WebSockets;
using System.Text.Json;
using System.Threading.Channels;

namespace Sharx.App;

/// <summary>Mengirim event realtime ke semua tab UI melalui WebSocket.</summary>
public sealed class LiveHub
{
    private readonly ConcurrentDictionary<Guid, Channel<byte[]>> _clients = new();
    private readonly JsonSerializerOptions _json;

    public LiveHub(JsonSerializerOptions json)
    {
        _json = json;
    }

    public int ClientCount => _clients.Count;

    public void Broadcast(string type, object? data)
    {
        if (_clients.IsEmpty)
        {
            return;
        }

        var payload = JsonSerializer.SerializeToUtf8Bytes(new { type, data }, _json);
        foreach (var channel in _clients.Values)
        {
            channel.Writer.TryWrite(payload);
        }
    }

    public async Task HandleAsync(WebSocket socket, object snapshot, CancellationToken ct)
    {
        var id = Guid.NewGuid();
        var channel = Channel.CreateBounded<byte[]>(new BoundedChannelOptions(512)
        {
            FullMode = BoundedChannelFullMode.DropOldest,
            SingleReader = true,
        });
        channel.Writer.TryWrite(JsonSerializer.SerializeToUtf8Bytes(new { type = "snapshot", data = snapshot }, _json));
        _clients[id] = channel;

        using var linked = CancellationTokenSource.CreateLinkedTokenSource(ct);
        var sender = SendLoopAsync(socket, channel.Reader, linked.Token);
        try
        {
            var buffer = new byte[1024];
            while (socket.State == WebSocketState.Open)
            {
                var result = await socket.ReceiveAsync(buffer, linked.Token).ConfigureAwait(false);
                if (result.MessageType == WebSocketMessageType.Close)
                {
                    break;
                }
            }
        }
        catch (Exception ex) when (ex is WebSocketException or OperationCanceledException)
        {
            // Tab ditutup / koneksi terputus.
        }
        finally
        {
            _clients.TryRemove(id, out _);
            channel.Writer.TryComplete();
            await linked.CancelAsync().ConfigureAwait(false);
            try
            {
                await sender.ConfigureAwait(false);
            }
            catch (Exception ex) when (ex is WebSocketException or OperationCanceledException)
            {
                // Sudah ditutup.
            }

            if (socket.State is WebSocketState.Open or WebSocketState.CloseReceived)
            {
                try
                {
                    await socket.CloseAsync(WebSocketCloseStatus.NormalClosure, "bye", CancellationToken.None).ConfigureAwait(false);
                }
                catch (WebSocketException)
                {
                    // Abaikan.
                }
            }
        }
    }

    private static async Task SendLoopAsync(WebSocket socket, ChannelReader<byte[]> reader, CancellationToken ct)
    {
        await foreach (var payload in reader.ReadAllAsync(ct).ConfigureAwait(false))
        {
            if (socket.State != WebSocketState.Open)
            {
                return;
            }

            await socket.SendAsync(payload, WebSocketMessageType.Text, endOfMessage: true, ct).ConfigureAwait(false);
        }
    }
}
