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
internal sealed class FakeTijLink(ConcurrentQueue<string> commands, bool started = true) : IPrinterLink
{
    private readonly Channel<byte[]> _toHost = Channel.CreateUnbounded<byte[]>();

    /// <summary>false = print belum start: 1E/1D/14 dibalas P_Status 12.</summary>
    public bool Started { get; set; } = started;

    public string Endpoint => "fake";

    public Task ConnectAsync(CancellationToken ct) => Task.CompletedTask;

    public Task SendAsync(byte[] data, CancellationToken ct)
    {
        var command = data[3];
        var payload = data.AsSpan(4, data.Length - 7);
        byte status = 0x00;
        if (!Started && command is TijProtocol.CmdLoadMessage or TijProtocol.CmdRemoteFields or TijProtocol.CmdPrinterStatus)
        {
            status = TijProtocol.StatusPrintNotStarted;
        }
        else
        {
            if (command == TijProtocol.CmdStartPrint)
            {
                Started = true;
            }

            if (command == TijProtocol.CmdStopPrint)
            {
                Started = false;
            }

            if (command != TijProtocol.CmdPrinterStatus)
            {
                commands.Enqueue(Describe(command, payload));
            }
        }

        _toHost.Writer.TryWrite(TijProtocolTests.Reply(TijProtocol.Ack, status, command));
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
    public async Task Records_without_line_terminator_are_processed_after_idle_gap()
    {
        // Seperti NetAssist tanpa "Auto Append Bytes": data dikirim tanpa CR/LF.
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
        await server.GetStream().WriteAsync(Encoding.UTF8.GetBytes("ANANDA02,78787900,000"));
        await WaitUntil(() => records.Count >= 1, "record pertama");
        await server.GetStream().WriteAsync(Encoding.UTF8.GetBytes("BUDI01,78787900,000"));
        await WaitUntil(() => records.Count >= 2, "record kedua");

        Assert.Equal(["ANANDA02,78787900,000", "BUDI01,78787900,000"], records.Select(r => r.Data));
        Assert.Equal("78787900", records.First().Template);
        await WaitUntil(() => commands.Contains("1D:0=BUDI01;1=001"), "field data operator baru");
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

public class PrintNotStartedTests
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

    private sealed class Rig : IAsyncDisposable
    {
        private readonly TcpListener _listener = new(IPAddress.Loopback, 0);

        public Rig(bool autoStart)
        {
            _listener.Start();
            var settings = new AppSettings();
            settings.Server.Host = "127.0.0.1";
            settings.Server.Port = ((IPEndPoint)_listener.LocalEndpoint).Port;
            settings.Printer.AutoStartPrint = autoStart;
            settings.Printer.ReadyPollSeconds = 1;
            var time = TimeProvider.System;
            Service = new TransferService(settings, null, new CounterEngine(null, time), new TransferLog(null, time),
                new TransferStats(), time, _ => Printer = new FakeTijLink(Commands, started: false));
            Service.RecordProcessed += Records.Enqueue;
        }

        public ConcurrentQueue<string> Commands { get; } = new();
        public ConcurrentQueue<TransferRecord> Records { get; } = new();
        public FakeTijLink? Printer { get; private set; }
        public TransferService Service { get; }

        public async Task SendJobAsync(string line)
        {
            Service.StartBackground();
            Service.Start();
            var server = await _listener.AcceptTcpClientAsync();
            await server.GetStream().WriteAsync(Encoding.UTF8.GetBytes(line + "\r\n"));
        }

        public async ValueTask DisposeAsync()
        {
            await Service.DisposeAsync();
            _listener.Stop();
        }
    }

    [Fact]
    public async Task Job_waits_without_error_and_is_sent_when_printer_is_started_from_panel()
    {
        await using var rig = new Rig(autoStart: false);
        await rig.SendJobAsync("ANANDA02,867892,000");

        await WaitUntil(() => !rig.Records.IsEmpty, "record");
        Assert.Equal(RecordStatus.Waiting, rig.Records.Single().Status);
        Assert.Equal(PrintState.NotStarted, rig.Service.Status.PrintState);
        Assert.True(rig.Service.Status.WaitingForPrinter);
        Assert.DoesNotContain(rig.Commands, c => c.StartsWith("1D", StringComparison.Ordinal));

        rig.Printer!.Started = true; // operator menekan start di panel printer
        await WaitUntil(() => rig.Commands.Contains("1D:0=ANANDA02;1=001"), "job terkirim otomatis");
        Assert.Contains("1E:867892", rig.Commands);
        Assert.Equal(PrintState.Started, rig.Service.Status.PrintState);
    }

    [Fact]
    public async Task Start_print_button_starts_printer_and_sends_waiting_job()
    {
        await using var rig = new Rig(autoStart: false);
        await rig.SendJobAsync("ANANDA02,867892,000");
        await WaitUntil(() => !rig.Records.IsEmpty, "record");

        var result = await rig.Service.PrintControlAsync(true, CancellationToken.None);

        Assert.True(result.Ok, result.Message);
        await WaitUntil(() => rig.Commands.Contains("1D:0=ANANDA02;1=001"), "job terkirim");
        Assert.Equal("11", rig.Commands.First());
    }

    [Fact]
    public async Task Auto_start_sends_start_print_before_loading_template()
    {
        await using var rig = new Rig(autoStart: true);
        await rig.SendJobAsync("ANANDA02,867892,000");

        await WaitUntil(() => !rig.Records.IsEmpty, "record selesai diproses");
        Assert.Equal(["11", "1E:867892", "01", "21", "1D:0=ANANDA02;1=001"], rig.Commands.ToArray());
        Assert.Equal(RecordStatus.Ok, rig.Records.Single().Status);
    }
}

public class RobustnessTests
{
    private static async Task WaitUntil(Func<bool> condition, string what, int seconds = 20)
    {
        var deadline = DateTime.UtcNow.AddSeconds(seconds);
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
    public async Task Flood_of_server_records_is_processed_completely_without_buffer_growth()
    {
        var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        var commands = new ConcurrentQueue<string>();
        var settings = new AppSettings();
        settings.Server.Host = "127.0.0.1";
        settings.Server.Port = ((IPEndPoint)listener.LocalEndpoint).Port;
        settings.Counter.IncrementOn = CounterIncrement.Record;
        var time = TimeProvider.System;
        await using var service = new TransferService(settings, null, new CounterEngine(null, time),
            new TransferLog(null, time), new TransferStats(), time, _ => new FakeTijLink(commands));
        service.Start();

        using var server = await listener.AcceptTcpClientAsync();
        const int total = 3000;
        var payload = new StringBuilder();
        for (var i = 0; i < total; i++)
        {
            payload.Append(i % 2 == 0 ? "ANANDA02" : "BUDI01").Append(",867892,000\n");
        }

        await server.GetStream().WriteAsync(Encoding.UTF8.GetBytes(payload.ToString()));
        await WaitUntil(() => service.Stats.Snapshot().RecordsForwarded == total, "semua record terkirim");

        Assert.Equal(total, commands.Count(c => c.StartsWith("1D", StringComparison.Ordinal)));
        Assert.Contains($"1D:0=BUDI01;1={total:D3}", commands);
        Assert.Equal(50, service.RecentRecords(1000).Count);
        await service.StopAsync();
        listener.Stop();
    }

    [Fact]
    public async Task Feedback_is_queued_while_server_is_down_and_delivered_after_reconnect()
    {
        var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        var commands = new ConcurrentQueue<string>();
        FakeTijLink? printer = null;
        var settings = new AppSettings();
        settings.Server.Host = "127.0.0.1";
        settings.Server.Port = ((IPEndPoint)listener.LocalEndpoint).Port;
        settings.Server.ReconnectDelaySeconds = 1;
        var time = TimeProvider.System;
        await using var service = new TransferService(settings, null, new CounterEngine(null, time),
            new TransferLog(null, time), new TransferStats(), time, _ => printer = new FakeTijLink(commands));
        service.Start();

        using (var first = await listener.AcceptTcpClientAsync())
        {
            await first.GetStream().WriteAsync(Encoding.UTF8.GetBytes("ANANDA02,867892,000\n"));
            await WaitUntil(() => commands.Contains("1D:0=ANANDA02;1=001"), "job terkirim");
        }

        await WaitUntil(() => service.Status.Server != ServerState.Connected, "server terputus");
        printer!.Print();
        printer.Print();
        await WaitUntil(() => service.Status.FeedbackQueued == 2, "feedback diantrikan");

        using var second = await listener.AcceptTcpClientAsync();
        var reader = new StreamReader(second.GetStream(), Encoding.UTF8);
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(10));
        Assert.Equal("ANANDA02,867892,001", await reader.ReadLineAsync(timeout.Token));
        Assert.Equal("ANANDA02,867892,002", await reader.ReadLineAsync(timeout.Token));
        await service.StopAsync();
        listener.Stop();
    }

    [Fact]
    public async Task Log_history_returns_only_last_50_matching_lines()
    {
        var dir = Path.Combine(Path.GetTempPath(), $"sharx-log-{Guid.NewGuid():N}");
        try
        {
            var log = new TransferLog(dir, TimeProvider.System);
            for (var i = 1; i <= 120; i++)
            {
                log.Write(i % 3 == 0 ? LogStatus.Error : LogStatus.Ok, $"baris {i}");
            }

            await log.DisposeAsync();
            var date = log.Dates().Single();

            var page = log.ReadDay(date);
            Assert.Equal(120, page.Matched);
            Assert.Equal(50, page.Entries.Count);
            Assert.Equal("baris 120", page.Entries[^1].Message);

            var errors = log.ReadDay(date, LogStatus.Error);
            Assert.Equal(40, errors.Matched);
            Assert.All(errors.Entries, e => Assert.Equal(LogStatus.Error, e.Status));

            var search = log.ReadDay(date, null, "baris 11");
            Assert.Equal(11, search.Matched); // baris 11 dan 110-119
            Assert.Equal("baris 11", search.Entries[0].Message);
            Assert.Equal(120, log.ReadAll(date).Count());
        }
        finally
        {
            Directory.Delete(dir, recursive: true);
        }
    }
}
