using System.Net.Sockets;

namespace Sharx.Core.Transport;

/// <summary>Pengaturan soket TCP yang sama untuk koneksi printer dan server.</summary>
public static class TcpTuning
{
    /// <summary>Mulai probe keepalive setelah koneksi diam sekian detik.</summary>
    public const int KeepAliveIdleSeconds = 5;

    /// <summary>Jeda antar probe keepalive yang tidak dibalas.</summary>
    public const int KeepAliveIntervalSeconds = 2;

    /// <summary>Jumlah probe tanpa balasan sebelum koneksi dianggap mati (±11 detik total).</summary>
    public const int KeepAliveProbes = 3;

    /// <summary>
    /// Pasang setelah terhubung: tanpa delay Nagle dan dengan TCP keepalive, sehingga koneksi yang diam
    /// tetap terjaga di jaringan dan koneksi yang mati (kabel / perangkat) terdeteksi dalam hitungan detik.
    /// Probe keepalive tidak membawa data, jadi tidak terbaca sebagai perintah oleh printer / server.
    /// </summary>
    public static void Apply(TcpClient client)
    {
        client.NoDelay = true;
        var socket = client.Client;
        socket.SetSocketOption(SocketOptionLevel.Socket, SocketOptionName.KeepAlive, true);
        try
        {
            socket.SetSocketOption(SocketOptionLevel.Tcp, SocketOptionName.TcpKeepAliveTime, KeepAliveIdleSeconds);
            socket.SetSocketOption(SocketOptionLevel.Tcp, SocketOptionName.TcpKeepAliveInterval, KeepAliveIntervalSeconds);
            socket.SetSocketOption(SocketOptionLevel.Tcp, SocketOptionName.TcpKeepAliveRetryCount, KeepAliveProbes);
        }
        catch (Exception ex) when (ex is SocketException or PlatformNotSupportedException)
        {
            // Windows lama: keepalive tetap aktif dengan jeda bawaan sistem.
        }
    }

    /// <summary>Penyebab koneksi terputus untuk log. <paramref name="error"/> null = ditutup normal oleh lawan.</summary>
    public static string DescribeClose(Exception? error, string peer)
    {
        if (error is null)
        {
            return $"ditutup oleh {peer}";
        }

        for (var e = error; e is not null; e = e.InnerException)
        {
            if (e is SocketException socket)
            {
                return socket.SocketErrorCode switch
                {
                    SocketError.ConnectionReset => $"direset oleh {peer}",
                    SocketError.ConnectionAborted => $"dibatalkan Windows (data tidak dibalas {peer})",
                    SocketError.TimedOut => $"{peer} tidak merespon (keepalive)",
                    SocketError.NetworkDown or SocketError.NetworkUnreachable or SocketError.HostUnreachable =>
                        $"jaringan ke {peer} terputus",
                    _ => socket.Message,
                };
            }
        }

        return error.Message;
    }
}
