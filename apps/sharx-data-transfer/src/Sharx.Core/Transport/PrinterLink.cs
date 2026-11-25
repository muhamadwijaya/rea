using System.IO.Ports;
using System.Net.Sockets;
using Sharx.Core.Configuration;

namespace Sharx.Core.Transport;

/// <summary>Koneksi fisik ke printer (TCP/IP atau serial RS232).</summary>
public interface IPrinterLink : IAsyncDisposable
{
    string Endpoint { get; }
    Task ConnectAsync(CancellationToken ct);
    Task SendAsync(byte[] data, CancellationToken ct);

    /// <summary>Baca data dari printer; 0 = koneksi ditutup.</summary>
    ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken ct);
}

public static class PrinterLinks
{
    public static IPrinterLink Create(PrinterSettings settings) => settings.Transport switch
    {
        PrinterTransport.Serial => new SerialPrinterLink(settings),
        _ => new TcpPrinterLink(settings),
    };

    /// <summary>Kunci koneksi; jika berubah, koneksi printer dibuat ulang.</summary>
    public static string ConnectionKey(PrinterSettings s) => s.Transport == PrinterTransport.Serial
        ? $"serial|{s.Protocol}|{s.SerialPort}|{s.BaudRate}|{s.DataBits}|{s.Parity}|{s.StopBits}|{s.Generic.PrintDonePattern}"
        : $"tcp|{s.Protocol}|{s.Host}|{s.Port}|{s.ConnectTimeoutSeconds}|{s.Generic.PrintDonePattern}";

    public static string Describe(PrinterSettings s) => s.Transport == PrinterTransport.Serial
        ? $"{s.SerialPort} @ {s.BaudRate}"
        : $"{s.Host}:{s.Port}";
}

public sealed class TcpPrinterLink(PrinterSettings settings) : IPrinterLink
{
    private TcpClient? _client;
    private NetworkStream? _stream;

    public string Endpoint { get; } = PrinterLinks.Describe(settings);

    public async Task ConnectAsync(CancellationToken ct)
    {
        var client = new TcpClient();
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeout.CancelAfter(TimeSpan.FromSeconds(Math.Max(1, settings.ConnectTimeoutSeconds)));
        try
        {
            await client.ConnectAsync(settings.Host, settings.Port, timeout.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException) when (!ct.IsCancellationRequested)
        {
            client.Dispose();
            throw new TimeoutException($"timeout koneksi ke {Endpoint}");
        }
        catch
        {
            client.Dispose();
            throw;
        }

        TcpTuning.Apply(client);
        _client = client;
        _stream = client.GetStream();
    }

    public async Task SendAsync(byte[] data, CancellationToken ct)
    {
        var stream = _stream ?? throw new IOException("Printer belum terhubung");
        await stream.WriteAsync(data, ct).ConfigureAwait(false);
        await stream.FlushAsync(ct).ConfigureAwait(false);
    }

    public ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken ct)
    {
        var stream = _stream ?? throw new IOException("Printer belum terhubung");
        return stream.ReadAsync(buffer, ct);
    }

    public ValueTask DisposeAsync()
    {
        _stream?.Dispose();
        _client?.Dispose();
        _stream = null;
        _client = null;
        return ValueTask.CompletedTask;
    }
}

public sealed class SerialPrinterLink(PrinterSettings settings) : IPrinterLink
{
    private SerialPort? _port;

    public string Endpoint { get; } = PrinterLinks.Describe(settings);

    public Task ConnectAsync(CancellationToken ct)
    {
        if (OperatingSystem.IsAndroid())
        {
            throw new IOException("Serial RS232 tidak tersedia di Android; gunakan koneksi TCP/IP (IP + port)");
        }

        var port = new SerialPort(
            settings.SerialPort,
            settings.BaudRate,
            Enum.TryParse<Parity>(settings.Parity, true, out var parity) ? parity : Parity.None,
            settings.DataBits,
            Enum.TryParse<StopBits>(settings.StopBits, true, out var stop) ? stop : StopBits.One)
        {
            WriteTimeout = 5000,
        };
        try
        {
            port.Open();
        }
        catch
        {
            port.Dispose();
            throw;
        }

        _port = port;
        return Task.CompletedTask;
    }

    public async Task SendAsync(byte[] data, CancellationToken ct)
    {
        var port = _port ?? throw new IOException("Printer belum terhubung");
        await port.BaseStream.WriteAsync(data, ct).ConfigureAwait(false);
        await port.BaseStream.FlushAsync(ct).ConfigureAwait(false);
    }

    public ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken ct)
    {
        var port = _port ?? throw new IOException("Printer belum terhubung");
        return port.BaseStream.ReadAsync(buffer, ct);
    }

    public ValueTask DisposeAsync()
    {
        _port?.Dispose();
        _port = null;
        return ValueTask.CompletedTask;
    }
}
