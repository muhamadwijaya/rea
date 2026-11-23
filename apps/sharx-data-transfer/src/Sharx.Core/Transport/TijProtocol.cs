using System.Text;

namespace Sharx.Core.Transport;

/// <summary>
/// DOD TIJ Protocol 4.8.
/// Perintah : 1B 02 [slave] [cmd] [data...] 1B 03 [checksum]
/// Balasan  : 1B 06 [slave] [P_Status] [cmd] [data...] 1B 03 [checksum]   (ACK)
///            1B 15 [slave] [C_Status] [cmd] 1B 03 [checksum]             (NAK)
/// Checksum : 2's complement dari jumlah (mod 256) semua byte sebelum checksum.
/// </summary>
public static class TijProtocol
{
    public const byte Esc = 0x1B;
    public const byte Stx = 0x02;
    public const byte Etx = 0x03;
    public const byte Ack = 0x06;
    public const byte Nak = 0x15;

    public const byte CmdUpdatePrint = 0x01;
    public const byte CmdStartPrint = 0x11;
    public const byte CmdStopPrint = 0x12;
    public const byte CmdPrinterStatus = 0x14;

    /// <summary>P_Status: print belum di-start.</summary>
    public const byte StatusPrintNotStarted = 0x12;

    /// <summary>P_Status: buffer 1D masih penuh (data tidak diterima).</summary>
    public const byte StatusBufferStillFull = 0x43;
    public const byte CmdRemoteFields = 0x1D;
    public const byte CmdLoadMessage = 0x1E;
    public const byte CmdMessageNames = 0x1F;
    public const byte CmdClearBuffer = 0x21;

    /// <summary>Sinyal dari printer: photocell ter-trigger (print start).</summary>
    public const byte SignalPrintStart = 0xAA;

    /// <summary>Sinyal dari printer: cetak selesai (print end / after print).</summary>
    public const byte SignalPrintEnd = 0xAB;

    public static byte Checksum(ReadOnlySpan<byte> bytes)
    {
        var sum = 0;
        foreach (var b in bytes)
        {
            sum += b;
        }

        return (byte)((0x100 - (sum & 0xFF)) & 0xFF);
    }

    public static byte[] Command(byte slave, byte command, ReadOnlySpan<byte> data = default)
    {
        var frame = new byte[data.Length + 7];
        frame[0] = Esc;
        frame[1] = Stx;
        frame[2] = slave;
        frame[3] = command;
        data.CopyTo(frame.AsSpan(4));
        frame[^3] = Esc;
        frame[^2] = Etx;
        frame[^1] = Checksum(frame.AsSpan(0, frame.Length - 1));
        return frame;
    }

    /// <summary>1E Load Print Message: [panjang nama][nama].</summary>
    public static byte[] LoadMessage(byte slave, string name, Encoding encoding)
    {
        var bytes = LengthChecked(encoding.GetBytes(name), "Nama message");
        var data = new byte[bytes.Length + 1];
        data[0] = (byte)bytes.Length;
        bytes.CopyTo(data, 1);
        return Command(slave, CmdLoadMessage, data);
    }

    /// <summary>1D Download Remote Field Data: [jumlah field] ([id][panjang][isi])...</summary>
    public static byte[] RemoteFields(byte slave, IReadOnlyList<(byte Id, string Value)> fields, Encoding encoding)
    {
        if (fields.Count is 0 or > 255)
        {
            throw new ArgumentException("Jumlah remote field harus 1-255", nameof(fields));
        }

        var data = new List<byte> { (byte)fields.Count };
        foreach (var (id, value) in fields)
        {
            var bytes = LengthChecked(encoding.GetBytes(value), $"Isi field {id}");
            if (bytes.Length == 0)
            {
                // Protokol mensyaratkan minimal 1 byte; kirim spasi untuk nilai kosong.
                bytes = [0x20];
            }

            data.Add(id);
            data.Add((byte)bytes.Length);
            data.AddRange(bytes);
        }

        return Command(slave, CmdRemoteFields, data.ToArray());
    }

    private static byte[] LengthChecked(byte[] bytes, string what) =>
        bytes.Length <= 255 ? bytes : throw new ArgumentException($"{what} lebih dari 255 byte");

    /// <summary>Balasan 1F: [jumlah] ([panjang][nama])...</summary>
    public static IReadOnlyList<string> ParseMessageNames(ReadOnlySpan<byte> data, Encoding encoding)
    {
        var names = new List<string>();
        if (data.Length == 0)
        {
            return names;
        }

        var count = data[0];
        var i = 1;
        for (var n = 0; n < count && i < data.Length; n++)
        {
            var len = data[i++];
            if (i + len > data.Length)
            {
                break;
            }

            names.Add(encoding.GetString(data.Slice(i, len)));
            i += len;
        }

        return names;
    }

    /// <summary>Slave address = byte terakhir IP printer.</summary>
    public static byte SlaveFromHost(string host)
    {
        var parts = (host ?? "").Trim().Split('.');
        return parts.Length == 4 && byte.TryParse(parts[3], out var last) ? last : (byte)0;
    }

    public static string CommandName(byte command) => command switch
    {
        0x01 => "Update Print Contents (01)",
        0x02 => "Lock Printer (02)",
        0x03 => "Unlock Printer (03)",
        0x11 => "Start Print (11)",
        0x12 => "Stop Print (12)",
        0x13 => "Trigger Print (13)",
        0x14 => "Request Printer Status (14)",
        0x1D => "Download Remote Field Data (1D)",
        0x1E => "Load Print Message (1E)",
        0x1F => "Request Message Names (1F)",
        0x20 => "Set Print Mode (20)",
        0x21 => "Clear Buffers of 1D (21)",
        0xAA => "Print Start Signal (AA)",
        0xAB => "Print End Signal (AB)",
        _ => $"Command {command:X2}",
    };

    /// <summary>Arti P_Status (balasan ACK).</summary>
    public static string PrinterStatusText(byte code) => code switch
    {
        0x00 => "OK",
        0x01 => "Printer errors exist",
        0x0B => "Number of QR code's sub-modules",
        0x0D => "No the serial number",
        0x0E => "Too few fields",
        0x0F => "Wrong account/password",
        0x10 => "Message name doesn't exist",
        0x11 => "Print not idle",
        0x12 => "Print not started",
        0x13 => "Grouping mode",
        0x14 => "Print interval too short",
        0x15 => "Failed to save message",
        0x16 => "Column selection",
        0x42 => "Buffer now full",
        0x43 => "Buffer still full",
        0xFD => "Service expired",
        0xFE => "Printer unlocked",
        0xFF => "Printer locked",
        _ => "Unknown status",
    };

    /// <summary>Arti C_Status (balasan NAK).</summary>
    public static string CommandStatusText(byte code) => code switch
    {
        0x06 => "Invalid command start",
        0x07 => "Invalid command end",
        0x08 => "Invalid checksum",
        0x09 => "Invalid number of field",
        0x0A => "Invalid module type",
        0x11 => "Invalid command ID",
        0x3C => "Invalid print mode",
        _ => "Unknown command status",
    };
}

public enum PrinterFrameKind
{
    /// <summary>Balasan sukses (ACK) untuk perintah.</summary>
    Ack,
    /// <summary>Balasan gagal (NAK).</summary>
    Nak,
    /// <summary>Printer mulai mencetak (photocell).</summary>
    PrintStart,
    /// <summary>Printer selesai mencetak.</summary>
    PrintEnd,
    /// <summary>Teks bebas (mode CustomText).</summary>
    Text,
}

/// <summary>Satu pesan yang diterima dari printer.</summary>
public sealed record PrinterFrame(PrinterFrameKind Kind, byte Command, byte Status, byte[] Data, byte[] Raw)
{
    public string Describe() => Kind switch
    {
        PrinterFrameKind.Ack => $"ACK {TijProtocol.CommandName(Command)} status {Status:X2} ({TijProtocol.PrinterStatusText(Status)})",
        PrinterFrameKind.Nak => $"NAK {TijProtocol.CommandName(Command)} ({TijProtocol.CommandStatusText(Status)})",
        PrinterFrameKind.PrintStart => "Print start (AA)",
        PrinterFrameKind.PrintEnd => "Print end (AB)",
        _ => Text.ControlTokens.Visualize(Encoding.Latin1.GetString(Raw)),
    };
}

/// <summary>Mengubah aliran byte dari printer menjadi <see cref="PrinterFrame"/>.</summary>
public interface IPrinterFrameParser
{
    IReadOnlyList<PrinterFrame> Push(ReadOnlySpan<byte> data);
}

/// <summary>Parser balasan TIJ: ACK/NAK dan sinyal AA/AB. Byte yang tidak dikenal dibuang.</summary>
public sealed class TijFrameParser : IPrinterFrameParser
{
    private const int MaxFrame = 4096;
    private readonly List<byte> _buffer = [];

    /// <summary>Jumlah frame dengan checksum salah yang dibuang.</summary>
    public int ChecksumErrors { get; private set; }

    public IReadOnlyList<PrinterFrame> Push(ReadOnlySpan<byte> data)
    {
        _buffer.AddRange(data);
        var frames = new List<PrinterFrame>();
        while (TryTakeFrame(out var frame))
        {
            if (frame is not null)
            {
                frames.Add(frame);
            }
        }

        if (_buffer.Count > MaxFrame)
        {
            _buffer.Clear();
        }

        return frames;
    }

    private bool TryTakeFrame(out PrinterFrame? frame)
    {
        frame = null;
        var start = FindStart();
        if (start < 0)
        {
            // Simpan byte ESC terakhir (mungkin awal frame berikutnya).
            var keep = _buffer.Count > 0 && _buffer[^1] == TijProtocol.Esc ? 1 : 0;
            _buffer.RemoveRange(0, _buffer.Count - keep);
            return false;
        }

        if (start > 0)
        {
            _buffer.RemoveRange(0, start);
        }

        // Minimal: ESC kind slave status cmd ESC ETX checksum = 8 byte.
        int? badEnd = null;
        for (var i = 5; i + 1 < _buffer.Count; i++)
        {
            var isEnd = _buffer[i] == TijProtocol.Esc && _buffer[i + 1] == TijProtocol.Etx;
            if (isEnd && i + 2 < _buffer.Count)
            {
                var raw = _buffer.GetRange(0, i + 3).ToArray();
                if (TijProtocol.Checksum(raw.AsSpan(0, raw.Length - 1)) == raw[^1])
                {
                    _buffer.RemoveRange(0, raw.Length);
                    frame = ToFrame(raw);
                    return true;
                }

                // ESC ETX ini mungkin bagian dari data; cari penutup berikutnya.
                badEnd ??= i + 3;
                continue;
            }

            var isStart = _buffer[i] == TijProtocol.Esc && _buffer[i + 1] is TijProtocol.Ack or TijProtocol.Nak;
            if (isStart && badEnd is int end && i >= end)
            {
                // Frame sebelumnya rusak dan frame baru sudah mulai: buang yang rusak.
                ChecksumErrors++;
                _buffer.RemoveRange(0, i);
                return true;
            }
        }

        if (_buffer.Count > MaxFrame)
        {
            ChecksumErrors++;
            _buffer.RemoveRange(0, 2);
            return true;
        }

        return false;
    }

    private int FindStart()
    {
        for (var i = 0; i + 1 < _buffer.Count; i++)
        {
            if (_buffer[i] == TijProtocol.Esc && _buffer[i + 1] is TijProtocol.Ack or TijProtocol.Nak)
            {
                return i;
            }
        }

        return -1;
    }

    private static PrinterFrame ToFrame(byte[] raw)
    {
        var status = raw[3];
        var command = raw[4];
        var data = raw.AsSpan(5, raw.Length - 8).ToArray();
        if (raw[1] == TijProtocol.Nak)
        {
            return new PrinterFrame(PrinterFrameKind.Nak, command, status, data, raw);
        }

        var kind = command switch
        {
            TijProtocol.SignalPrintStart => PrinterFrameKind.PrintStart,
            TijProtocol.SignalPrintEnd => PrinterFrameKind.PrintEnd,
            _ => PrinterFrameKind.Ack,
        };
        return new PrinterFrame(kind, command, status, data, raw);
    }
}

/// <summary>Parser mode CustomText: deteksi sinyal selesai cetak, sisanya jadi balasan teks.</summary>
public sealed class TextFrameParser(byte[] printDoneSignal) : IPrinterFrameParser
{
    private readonly List<byte> _buffer = [];

    public IReadOnlyList<PrinterFrame> Push(ReadOnlySpan<byte> data)
    {
        var frames = new List<PrinterFrame>();
        if (printDoneSignal.Length == 0)
        {
            frames.Add(new PrinterFrame(PrinterFrameKind.Text, 0, 0, data.ToArray(), data.ToArray()));
            return frames;
        }

        _buffer.AddRange(data);
        int index;
        while ((index = IndexOf(_buffer, printDoneSignal)) >= 0)
        {
            if (index > 0)
            {
                var text = _buffer.GetRange(0, index).ToArray();
                frames.Add(new PrinterFrame(PrinterFrameKind.Text, 0, 0, text, text));
            }

            frames.Add(new PrinterFrame(PrinterFrameKind.PrintEnd, 0, 0, [], printDoneSignal));
            _buffer.RemoveRange(0, index + printDoneSignal.Length);
        }

        // Teks yang tidak mungkin jadi awal sinyal diteruskan sebagai balasan.
        var keep = PartialSignalLength();
        if (_buffer.Count > keep)
        {
            var text = _buffer.GetRange(0, _buffer.Count - keep).ToArray();
            frames.Add(new PrinterFrame(PrinterFrameKind.Text, 0, 0, text, text));
            _buffer.RemoveRange(0, _buffer.Count - keep);
        }

        return frames;
    }

    /// <summary>Panjang ekor buffer yang merupakan awalan sinyal (mungkin sinyal yang belum lengkap).</summary>
    private int PartialSignalLength()
    {
        for (var length = Math.Min(_buffer.Count, printDoneSignal.Length - 1); length > 0; length--)
        {
            var match = true;
            for (var i = 0; i < length && match; i++)
            {
                match = _buffer[_buffer.Count - length + i] == printDoneSignal[i];
            }

            if (match)
            {
                return length;
            }
        }

        return 0;
    }

    private static int IndexOf(List<byte> buffer, byte[] pattern)
    {
        for (var i = 0; i + pattern.Length <= buffer.Count; i++)
        {
            var match = true;
            for (var j = 0; j < pattern.Length && match; j++)
            {
                match = buffer[i + j] == pattern[j];
            }

            if (match)
            {
                return i;
            }
        }

        return -1;
    }
}
