using System.Globalization;
using System.Net;
using System.Net.Sockets;
using System.Text;
using Sharx.Core.Transport;

// Simulator untuk uji tanpa mesin asli.
//
//   SharxSimulator server  [--port 9000] [--jobs "ANANDA02,867892,000;BUDI01,867892,000"] [--interval 15]
//       Server TCP: mengirim job (satu baris per job) ke SHARX dan menampilkan feedback after print.
//
//   SharxSimulator printer [--port 9100] [--messages 867892,555111] [--print-interval 2] [--buffer 1]
//                          [--started true|false]
//       Printer TIJ palsu (protokol 4.8): membalas 1E/1D/01/11/12/14/21/1F dan mengirim Print End (AB)
//       setiap kali "mencetak". --started false = printer belum start (balas P_Status 12) sampai
//       menerima 11 Start Print, atau tekan tombol S di jendela simulator.
var mode = args.FirstOrDefault() ?? "";
var options = ParseOptions(args.Skip(1).ToArray());
using var cts = new CancellationTokenSource();
Console.CancelKeyPress += (_, e) =>
{
    e.Cancel = true;
    cts.Cancel();
};

try
{
    switch (mode)
    {
        case "server":
            await RunServerAsync(options, cts.Token);
            break;
        case "printer":
            await RunPrinterAsync(options, cts.Token);
            break;
        default:
            Console.WriteLine("Pakai: SharxSimulator server|printer [opsi]  (lihat komentar di Program.cs)");
            return 1;
    }
}
catch (OperationCanceledException)
{
    // Ctrl+C.
}

return 0;

static Dictionary<string, string> ParseOptions(string[] args)
{
    var result = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
    for (var i = 0; i + 1 < args.Length; i += 2)
    {
        result[args[i].TrimStart('-')] = args[i + 1];
    }

    return result;
}

static void Log(string who, string message) =>
    Console.WriteLine($"[{DateTime.Now:HH:mm:ss.fff}] {who,-7} {message}");

static async Task RunServerAsync(Dictionary<string, string> o, CancellationToken ct)
{
    var port = int.Parse(o.GetValueOrDefault("port", "9000"), CultureInfo.InvariantCulture);
    var jobs = o.GetValueOrDefault("jobs", "ANANDA02,867892,000;BUDI01,867892,000;BUDI01,555111,000").Split(';');
    var interval = double.Parse(o.GetValueOrDefault("interval", "15"), CultureInfo.InvariantCulture);
    var listener = new TcpListener(IPAddress.Loopback, port);
    listener.Start();
    Log("SERVER", $"listen 127.0.0.1:{port}, {jobs.Length} job, interval {interval}s");
    while (!ct.IsCancellationRequested)
    {
        using var client = await listener.AcceptTcpClientAsync(ct);
        Log("SERVER", "SHARX terhubung");
        var stream = client.GetStream();
        using var linked = CancellationTokenSource.CreateLinkedTokenSource(ct);
        var reader = Task.Run(async () =>
        {
            var buffer = new byte[4096];
            var text = new StringBuilder();
            while (true)
            {
                var n = await stream.ReadAsync(buffer, linked.Token);
                if (n == 0)
                {
                    return;
                }

                text.Append(Encoding.UTF8.GetString(buffer, 0, n));
                string all;
                int newline;
                while ((newline = (all = text.ToString()).IndexOf('\n')) >= 0)
                {
                    Log("SERVER", $"<- feedback after print: {all[..newline].TrimEnd('\r')}");
                    text.Remove(0, newline + 1);
                }
            }
        });

        try
        {
            foreach (var job in jobs)
            {
                Log("SERVER", $"-> job: {job}");
                await stream.WriteAsync(Encoding.UTF8.GetBytes(job + "\r\n"), ct);
                await Task.WhenAny(reader, Task.Delay(TimeSpan.FromSeconds(interval), ct));
                if (reader.IsCompleted)
                {
                    break;
                }
            }

            await reader;
        }
        catch (Exception ex) when (ex is IOException or SocketException)
        {
            Log("SERVER", $"koneksi putus: {ex.Message}");
        }

        await linked.CancelAsync();
        Log("SERVER", "SHARX terputus");
    }
}

static async Task RunPrinterAsync(Dictionary<string, string> o, CancellationToken ct)
{
    var port = int.Parse(o.GetValueOrDefault("port", "9100"), CultureInfo.InvariantCulture);
    var messages = o.GetValueOrDefault("messages", "")
        .Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
    var interval = double.Parse(o.GetValueOrDefault("print-interval", "2"), CultureInfo.InvariantCulture);
    var bufferSize = int.Parse(o.GetValueOrDefault("buffer", "1"), CultureInfo.InvariantCulture);
    var started = !string.Equals(o.GetValueOrDefault("started", "true"), "false", StringComparison.OrdinalIgnoreCase);
    var listener = new TcpListener(IPAddress.Loopback, port);
    listener.Start();
    Log("PRINTER", $"TIJ listen 127.0.0.1:{port}, message: {(messages.Length == 0 ? "(semua)" : string.Join(",", messages))}, buffer {bufferSize}");
    while (!ct.IsCancellationRequested)
    {
        using var client = await listener.AcceptTcpClientAsync(ct);
        Log("PRINTER", "host terhubung");
        await new FakeTijPrinter(client.GetStream(), messages, interval, bufferSize, started).RunAsync(ct);
        Log("PRINTER", "host terputus");
    }
}

/// <summary>Printer TIJ palsu: cukup untuk menguji alur 1E → 1D → AB.</summary>
internal sealed class FakeTijPrinter(NetworkStream stream, string[] messages, double interval, int bufferSize, bool started)
{
    private bool _started = started;
    private readonly object _gate = new();
    private readonly Queue<string> _buffer = new();
    private readonly SemaphoreSlim _write = new(1, 1);
    private string? _message;
    private string? _last;
    private byte _slave;

    public async Task RunAsync(CancellationToken ct)
    {
        using var linked = CancellationTokenSource.CreateLinkedTokenSource(ct);
        var printing = PrintLoopAsync(linked.Token);
        var keys = KeyLoopAsync(linked.Token);
        Log("PRINTER", _started ? "status: print started" : "status: print BELUM start (tekan S untuk start/stop)");
        var buffer = new byte[4096];
        var pending = new List<byte>();
        try
        {
            while (true)
            {
                var n = await stream.ReadAsync(buffer, linked.Token);
                if (n == 0)
                {
                    break;
                }

                pending.AddRange(buffer.AsSpan(0, n));
                while (TryTakeCommand(pending, out var frame))
                {
                    await HandleAsync(frame, linked.Token);
                }
            }
        }
        catch (Exception ex) when (ex is IOException or SocketException or OperationCanceledException)
        {
            // Putus.
        }

        await linked.CancelAsync();
        try
        {
            await Task.WhenAll(printing, keys);
        }
        catch (OperationCanceledException)
        {
            // Selesai.
        }
    }

    private static bool TryTakeCommand(List<byte> data, out byte[] frame)
    {
        frame = [];
        var start = -1;
        for (var i = 0; i + 1 < data.Count; i++)
        {
            if (data[i] == 0x1B && data[i + 1] == 0x02)
            {
                start = i;
                break;
            }
        }

        if (start < 0)
        {
            return false;
        }

        for (var i = start + 4; i + 2 < data.Count; i++)
        {
            if (data[i] != 0x1B || data[i + 1] != 0x03)
            {
                continue;
            }

            var candidate = data.GetRange(start, i + 3 - start).ToArray();
            if (TijProtocol.Checksum(candidate.AsSpan(0, candidate.Length - 1)) != candidate[^1])
            {
                continue;
            }

            data.RemoveRange(0, i + 3);
            frame = candidate;
            return true;
        }

        return false;
    }

    private async Task HandleAsync(byte[] frame, CancellationToken ct)
    {
        _slave = frame[2];
        var command = frame[3];
        var data = frame.AsSpan(4, frame.Length - 7).ToArray();
        byte status = 0x00;
        byte[] reply = [];
        if (!_started && command is TijProtocol.CmdLoadMessage or TijProtocol.CmdRemoteFields or TijProtocol.CmdPrinterStatus)
        {
            Log("PRINTER", $"perintah {command:X2} ditolak: print belum start (P_Status 12)");
            await SendAsync(TijProtocol.Ack, TijProtocol.StatusPrintNotStarted, command, [], ct);
            return;
        }

        switch (command)
        {
            case TijProtocol.CmdStartPrint:
                status = _started ? (byte)0x11 : (byte)0x00;
                _started = true;
                Log("PRINTER", "11 start print");
                break;
            case TijProtocol.CmdStopPrint:
                status = _started ? (byte)0x00 : TijProtocol.StatusPrintNotStarted;
                _started = false;
                Log("PRINTER", "12 stop print");
                break;
            case TijProtocol.CmdPrinterStatus:
                break;
            case TijProtocol.CmdLoadMessage:
                var name = Encoding.UTF8.GetString(data, 1, data[0]);
                if (messages.Length > 0 && !messages.Contains(name))
                {
                    status = 0x10;
                    Log("PRINTER", $"1E load '{name}' -> message tidak ada");
                }
                else
                {
                    lock (_gate)
                    {
                        _message = name;
                        _buffer.Clear();
                    }

                    Log("PRINTER", $"1E load message '{name}'");
                }

                break;
            case TijProtocol.CmdRemoteFields:
                var fields = new List<string>();
                var i = 1;
                for (var f = 0; f < data[0]; f++)
                {
                    var id = data[i];
                    var len = data[i + 1];
                    fields.Add($"F{id}={Encoding.UTF8.GetString(data, i + 2, len)}");
                    i += 2 + len;
                }

                var text = string.Join(" ", fields);
                lock (_gate)
                {
                    if (_buffer.Count >= bufferSize)
                    {
                        _buffer.Dequeue();
                        status = 0x43;
                    }

                    _buffer.Enqueue(text);
                    if (status == 0 && _buffer.Count == bufferSize)
                    {
                        status = 0x42;
                    }
                }

                Log("PRINTER", $"1D {text} (buffer {_buffer.Count}/{bufferSize})");
                break;
            case TijProtocol.CmdClearBuffer:
                lock (_gate)
                {
                    _buffer.Clear();
                }

                Log("PRINTER", "21 clear buffer");
                break;
            case TijProtocol.CmdMessageNames:
                var names = messages.Length > 0 ? messages : ["867892", "555111"];
                var list = new List<byte> { (byte)names.Length };
                foreach (var n in names)
                {
                    var bytes = Encoding.UTF8.GetBytes(n);
                    list.Add((byte)bytes.Length);
                    list.AddRange(bytes);
                }

                reply = [.. list];
                break;
            default:
                Log("PRINTER", $"perintah {command:X2}");
                break;
        }

        await SendAsync(TijProtocol.Ack, status, command, reply, ct);
    }

    private async Task PrintLoopAsync(CancellationToken ct)
    {
        while (true)
        {
            await Task.Delay(TimeSpan.FromSeconds(interval), ct);
            string? printed;
            lock (_gate)
            {
                if (_message is null || !_started)
                {
                    continue;
                }

                printed = _buffer.Count > 0 ? _buffer.Dequeue() : _last;
                if (printed is null)
                {
                    continue;
                }

                _last = printed;
            }

            Log("PRINTER", $"*** PRINT {_message}: {printed}");
            await SendAsync(TijProtocol.Ack, 0x00, TijProtocol.SignalPrintEnd, [], ct);
        }
    }

    /// <summary>Tombol S di jendela simulator = start / stop print dari "panel printer".</summary>
    private async Task KeyLoopAsync(CancellationToken ct)
    {
        if (Console.IsInputRedirected)
        {
            return;
        }

        while (!ct.IsCancellationRequested)
        {
            await Task.Delay(100, ct);
            if (Console.KeyAvailable && Console.ReadKey(true).Key == ConsoleKey.S)
            {
                _started = !_started;
                Log("PRINTER", _started ? "panel: START print" : "panel: STOP print");
            }
        }
    }

    private async Task SendAsync(byte kind, byte status, byte command, byte[] data, CancellationToken ct)
    {
        var frame = new List<byte> { 0x1B, kind, _slave, status, command };
        frame.AddRange(data);
        frame.Add(0x1B);
        frame.Add(0x03);
        frame.Add(TijProtocol.Checksum(frame.ToArray()));
        await _write.WaitAsync(ct);
        try
        {
            await stream.WriteAsync(frame.ToArray(), ct);
        }
        finally
        {
            _write.Release();
        }
    }

    private static void Log(string who, string message) =>
        Console.WriteLine($"[{DateTime.Now:HH:mm:ss.fff}] {who,-7} {message}");
}
