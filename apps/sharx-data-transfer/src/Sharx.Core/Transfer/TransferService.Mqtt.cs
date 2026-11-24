using System.Diagnostics;
using MQTTnet;
using MQTTnet.Client;
using MQTTnet.Protocol;
using Sharx.Core.Configuration;
using Sharx.Core.Logging;
using Sharx.Core.Transport;

namespace Sharx.Core.Transfer;

/// <summary>
/// Koneksi server lewat MQTT broker: setiap pesan di topic subscribe = data job,
/// feedback setelah cetak dikirim (publish) ke topic feedback.
/// </summary>
public sealed partial class TransferService
{
    private static MqttClientOptions MqttOptions(ServerSettings server)
    {
        var m = server.Mqtt;
        var builder = new MqttClientOptionsBuilder()
            .WithTcpServer(server.Host, server.Port)
            .WithClientId(m.ClientId)
            .WithCleanSession(m.CleanSession)
            .WithKeepAlivePeriod(TimeSpan.FromSeconds(Math.Clamp(m.KeepAliveSeconds, 5, 3600)))
            .WithTimeout(TimeSpan.FromSeconds(Math.Clamp(server.ConnectTimeoutSeconds, 1, 120)));
        if (!string.IsNullOrEmpty(m.Username))
        {
            builder.WithCredentials(m.Username, m.Password);
        }

        if (m.UseTls)
        {
            builder.WithTlsOptions(o => o.UseTls());
        }

        return builder.Build();
    }

    private static MqttQualityOfServiceLevel Qos(MqttSettings m) => (MqttQualityOfServiceLevel)Math.Clamp(m.Qos, 0, 2);

    /// <summary>Isi satu pesan MQTT → record. Beberapa baris dalam satu pesan = beberapa record.</summary>
    internal static IReadOnlyList<Frame> MqttFrames(byte[] payload, ServerSettings server)
    {
        var decoder = new FrameDecoder(new ServerSettings { Framing = FrameMode.Line, MaxFrameBytes = server.MaxFrameBytes });
        var frames = decoder.Push(payload).ToList();
        if (decoder.Flush() is { } last)
        {
            frames.Add(last);
        }

        return frames;
    }

    /// <summary>Terima job dari broker; kembali dengan penyebab saat koneksi broker terputus.</summary>
    private async Task<string> ReceiveMqttAsync(ServerSettings server, CancellationToken ct)
    {
        _serverState = ServerState.Connecting;
        RaiseStatus();

        var m = server.Mqtt;
        using var client = new MqttFactory().CreateMqttClient();
        var disconnected = new TaskCompletionSource<string>(TaskCreationOptions.RunContinuationsAsynchronously);
        client.DisconnectedAsync += e =>
        {
            disconnected.TrySetResult(e.Exception?.Message ?? e.Reason.ToString());
            return Task.CompletedTask;
        };

        var frames = _frames!.Writer;
        var signal = _workSignal!;
        client.ApplicationMessageReceivedAsync += async e =>
        {
            var payload = e.ApplicationMessage.PayloadSegment.ToArray();
            _stats.AddReceived(payload.Length);
            foreach (var frame in MqttFrames(payload, server))
            {
                // Antrean penuh = pesan berikutnya ditahan broker (backpressure), bukan dibuang.
                await frames.WriteAsync(frame, ct).ConfigureAwait(false);
                signal.Release();
            }
        };

        try
        {
            await client.ConnectAsync(MqttOptions(server), ct).ConfigureAwait(false);
        }
        catch (Exception ex) when (ex is not OperationCanceledException || !ct.IsCancellationRequested)
        {
            throw new IOException(ex.Message, ex);
        }

        await client.SubscribeAsync(
            new MqttClientSubscribeOptionsBuilder()
                .WithTopicFilter(f => f.WithTopic(m.SubscribeTopic).WithQualityOfServiceLevel(Qos(m)))
                .Build(),
            ct).ConfigureAwait(false);

        _serverSender = async (payload, token) =>
        {
            // Satu pesan MQTT = satu laporan; akhiran CR/LF dari format feedback tidak ikut dikirim.
            var length = payload.Length;
            while (length > 0 && payload[length - 1] is (byte)'\r' or (byte)'\n')
            {
                length--;
            }

            var message = new MqttApplicationMessageBuilder()
                .WithTopic(m.PublishTopic)
                .WithPayload(payload.AsSpan(0, length).ToArray())
                .WithQualityOfServiceLevel(Qos(m))
                .WithRetainFlag(m.Retain)
                .Build();
            var result = await client.PublishAsync(message, token).ConfigureAwait(false);
            if (!result.IsSuccess)
            {
                throw new IOException($"Broker menolak feedback: {result.ReasonCode} {result.ReasonString}");
            }
        };

        _serverState = ServerState.Connected;
        _connectedSince = _time.GetLocalNow();
        _lastError = null;
        _log.Write(LogStatus.Ok,
            $"Connected to MQTT broker {server.Host}:{server.Port} (data: {m.SubscribeTopic}, feedback: {m.PublishTopic})"
            + ServerReconnectedNote());
        RaiseStatus();

        try
        {
            return await disconnected.Task.WaitAsync(ct).ConfigureAwait(false);
        }
        finally
        {
            _serverSender = null;
            if (client.IsConnected)
            {
                try
                {
                    using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(3));
                    await client.DisconnectAsync(new MqttClientDisconnectOptionsBuilder().Build(), timeout.Token).ConfigureAwait(false);
                }
                catch (Exception)
                {
                    // Sudah terputus.
                }
            }
        }
    }

    private static async Task<ConnectionTestResult> TestMqttAsync(ServerSettings server, CancellationToken ct)
    {
        var watch = Stopwatch.StartNew();
        using var client = new MqttFactory().CreateMqttClient();
        var options = MqttOptions(server);
        options.ClientId = server.Mqtt.ClientId + "-test";
        try
        {
            await client.ConnectAsync(options, ct).ConfigureAwait(false);
            await client.DisconnectAsync(new MqttClientDisconnectOptionsBuilder().Build(), ct).ConfigureAwait(false);
            return new ConnectionTestResult(true, $"MQTT broker {server.Host}:{server.Port} dapat dihubungi", watch.ElapsedMilliseconds);
        }
        catch (Exception ex) when (ex is not OperationCanceledException || !ct.IsCancellationRequested)
        {
            return new ConnectionTestResult(false, $"Gagal ke MQTT broker {server.Host}:{server.Port}: {ex.Message}", watch.ElapsedMilliseconds);
        }
    }
}
