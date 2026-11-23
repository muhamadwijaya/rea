using System.Collections.Concurrent;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading.Channels;
using Sharx.Core.Configuration;
using Sharx.Core.Counter;
using Sharx.Core.Logging;
using Sharx.Core.Transfer;
using Sharx.Core.Transport;

namespace Sharx.Core.Tests;

/// <summary>Printer TIJ di memori: mencatat perintah, membalas ACK, dan bisa "mencetak" (kirim AB).</summary>
internal sealed class FakeTijLink(ConcurrentQueue<string> commands) : IPrinterLink
{
    private readonly Channel<byte[]> _toHost = Channel.CreateUnbounded<byte[]>();

    public string Endpoint => "fake";

    public Task ConnectAsync(CancellationToken ct) => Task.CompletedTask;

    public Task SendAsync(byte[] data, CancellationToken ct)
    {
        var command = data[3];
        var payload = data.AsSpan(4, data.Length - 7);
        commands.Enqueue(Describe(command, payload));
        _toHost.Writer.TryWrite(TijProtocolTests.Reply(TijProtocol.Ack, 0x00, command));
        return Task.CompletedTask;
    }

    public void Print() => _toHost.Writer.TryWrite(TijProtocolTests.Reply(TijProtocol.Ack, 0x00, TijProtocol.SignalPrintEnd));

    public async ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken ct)
    {
        try
        {
            var bytes = await _toHost.Reader.ReadAsync(ct);
            bytes.CopyTo(buffer);
            return bytes.Length;
        }
        catch (ChannelClosedException)
        {
            return 0;
        }
    }

    public ValueTask DisposeAsync()
    {
        _toHost.Writer.TryComplete();
        return ValueTask.CompletedTask;
    }

    private static string Describe(byte command, ReadOnlySpan<byte> data)
    {
        switch (command)
        {
            case TijProtocol.CmdLoadMessage:
                return "1E:" + Encoding.UTF8.GetString(data.Slice(1, data[0]));
            case TijProtocol.CmdRemoteFields:
                var parts = new List<string>();
                var i = 1;
                for (var f = 0; f < data[0]; f++)
                {
                    parts.Add($"{data[i]}={Encoding.UTF8.GetString(data.Slice(i + 2, data[i + 1]))}");
                    i += 2 + data[i + 1];
                }

                return "1D:" + string.Join(";", parts);
            default:
                return command.ToString("X2");
        }
    }
}

public class TransferServiceTests
{
    private static async Task WaitUntil(Func<bool> condition, string what)
    {
        var deadline = DateTime.UtcNow.AddSeconds(10);
        while (!condition())
        {
            if (DateTime.UtcNow > deadline)
            {
                throw new TimeoutException($"Menunggu: {what}");
            }

            await Task.Delay(20);
        }
    }

    [Fact]
    public async Task Tij_print_counting_flow_with_feedback_to_server()
    {
        var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        var port = ((IPEndPoint)listener.LocalEndpoint).Port;

        var commands = new ConcurrentQueue<string>();
        FakeTijLink? printer = null;
        var settings = new AppSettings();
        settings.Server.Host = "127.0.0.1";
        settings.Server.Port = port;
        settings.Printer.Host = "127.0.0.1";

        var time = TimeProvider.System;
        await using var service = new TransferService(
            settings,
            null,
            new CounterEngine(null, time),
            new TransferLog(null, time),
            new TransferStats(),
            time,
            _ => printer = new FakeTijLink(commands));
        service.Start();

        using var server = await listener.AcceptTcpClientAsync();
        var stream = server.GetStream();
        var reader = new StreamReader(stream, Encoding.UTF8);

        async Task Send(string line) => await stream.WriteAsync(Encoding.UTF8.GetBytes(line + "\r\n"));
        async Task<string> Feedback()
        {
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(10));
            return await reader.ReadLineAsync(timeout.Token) ?? "";
        }

        async Task Expect(params string[] expected)
        {
            await WaitUntil(() => commands.Count >= expected.Length, string.Join(", ", expected));
            var actual = new List<string>();
            while (actual.Count < expected.Length && commands.TryDequeue(out var c))
            {
                actual.Add(c);
            }

            Assert.Equal(expected, actual);
        }

        // Job pertama: load template, update, kosongkan buffer, field 0 = operator, field 1 = counter.
        await Send("ANANDA02,867892,000");
        await Expect("1E:867892", "01", "21", "1D:0=ANANDA02;1=001");

        printer!.Print();
        Assert.Equal("ANANDA02,867892,001", await Feedback());
        await Expect("1D:0=ANANDA02;1=002");

        // Operator ganti, template sama: counter lanjut (002 belum tercetak, dikirim ulang).
        await Send("BUDI01,867892,000");
        await Expect("21", "1D:0=BUDI01;1=002");
        printer.Print();
        Assert.Equal("BUDI01,867892,002", await Feedback());
        await Expect("1D:0=BUDI01;1=003");

        // Template ganti: pakai counter template baru.
        await Send("BUDI01,555111,000");
        await Expect("1E:555111", "01", "21", "1D:0=BUDI01;1=001");
        printer.Print();
        Assert.Equal("BUDI01,555111,001", await Feedback());
        await Expect("1D:0=BUDI01;1=002");

        // Kembali ke template lama: counter tersimpan dilanjutkan.
        await Send("ANANDA02,867892,000");
        await Expect("1E:867892", "01", "21", "1D:0=ANANDA02;1=003");

        // Reset Counter: printer disinkronkan ulang dengan counter awal.
        service.ResetCounter(null);
        await Expect("21", "1D:0=ANANDA02;1=001");
        printer.Print();
        Assert.Equal("ANANDA02,867892,001", await Feedback());

        await service.StopAsync();
        listener.Stop();
    }

    [Fact]
    public async Task Rejected_record_is_not_sent_to_printer()
    {
        var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        var commands = new ConcurrentQueue<string>();
        var settings = new AppSettings();
        settings.Server.Host = "127.0.0.1";
        settings.Server.Port = ((IPEndPoint)listener.LocalEndpoint).Port;
        var time = TimeProvider.System;
        await using var service = new TransferService(settings, null, new CounterEngine(null, time),
            new TransferLog(null, time), new TransferStats(), time, _ => new FakeTijLink(commands));
        var records = new ConcurrentQueue<TransferRecord>();
        service.RecordProcessed += records.Enqueue;
        service.Start();

        using var server = await listener.AcceptTcpClientAsync();
        await server.GetStream().WriteAsync(Encoding.UTF8.GetBytes("ONLYONEFIELD\n"));

        await WaitUntil(() => !records.IsEmpty, "record ditolak");
        Assert.Equal(RecordStatus.Rejected, Assert.Single(records).Status);
        Assert.DoesNotContain(commands, c => c.StartsWith("1D", StringComparison.Ordinal));
        await service.StopAsync();
        listener.Stop();
    }
}

/// <summary>Printer teks di memori: membalas &lt;ACK&gt; dan bisa mengirim sinyal selesai cetak.</summary>
internal sealed class FakeTextLink(ConcurrentQueue<string> commands) : IPrinterLink
{
    private readonly Channel<byte[]> _toHost = Channel.CreateUnbounded<byte[]>();

    public string Endpoint => "fake-text";

    public Task ConnectAsync(CancellationToken ct) => Task.CompletedTask;

    public Task SendAsync(byte[] data, CancellationToken ct)
    {
        commands.Enqueue(Sharx.Core.Text.ControlTokens.Visualize(Encoding.ASCII.GetString(data)));
        _toHost.Writer.TryWrite([0x06]);
        return Task.CompletedTask;
    }

    public void Print() => _toHost.Writer.TryWrite(Encoding.ASCII.GetBytes("DONE\r"));

    public async ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken ct)
    {
        try
        {
            var bytes = await _toHost.Reader.ReadAsync(ct);
            bytes.CopyTo(buffer);
            return bytes.Length;
        }
        catch (ChannelClosedException)
        {
            return 0;
        }
    }

    public ValueTask DisposeAsync()
    {
        _toHost.Writer.TryComplete();
        return ValueTask.CompletedTask;
    }
}

public class GenericProtocolTests
{
    [Fact]
    public async Task Generic_commands_change_template_send_fields_and_report_prints()
    {
        var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        var commands = new ConcurrentQueue<string>();
        FakeTextLink? printer = null;
        var settings = new AppSettings();
        settings.Server.Host = "127.0.0.1";
        settings.Server.Port = ((IPEndPoint)listener.LocalEndpoint).Port;
        settings.Printer.Protocol = PrinterProtocol.Generic;
        settings.Printer.Generic = new GenericProtocolSettings
        {
            TemplateCommand = "<STX>SEL:{TEMPLATE_NAME}<ETX>",
            ClearCommand = "<STX>CLR<ETX>",
            FieldCommand = "<STX>F{FIELD_ID}={FIELD_VALUE}<ETX>",
            AckPattern = "<ACK>",
            PrintDonePattern = "DONE<CR>",
        };
        Assert.Empty(SettingsValidator.Validate(settings));

        var time = TimeProvider.System;
        await using var service = new TransferService(settings, null, new CounterEngine(null, time),
            new TransferLog(null, time), new TransferStats(), time, _ => printer = new FakeTextLink(commands));
        service.Start();
        using var server = await listener.AcceptTcpClientAsync();
        var stream = server.GetStream();
        var reader = new StreamReader(stream, Encoding.UTF8);
        await stream.WriteAsync(Encoding.UTF8.GetBytes("ANANDA02,867892,000\r\n"));

        var deadline = DateTime.UtcNow.AddSeconds(10);
        while (commands.Count < 4 && DateTime.UtcNow < deadline)
        {
            await Task.Delay(20);
        }

        Assert.Equal(
            ["<STX>SEL:867892<ETX>", "<STX>CLR<ETX>", "<STX>F0=ANANDA02<ETX>", "<STX>F1=001<ETX>"],
            commands.ToArray());

        printer!.Print();
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(10));
        Assert.Equal("ANANDA02,867892,001", await reader.ReadLineAsync(timeout.Token));
        await service.StopAsync();
        listener.Stop();
    }
}
