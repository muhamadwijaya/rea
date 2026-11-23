using Sharx.Core.Configuration;
using Sharx.Core.Text;

namespace Sharx.Core.Transport;

/// <summary>Satu record mentah dari server.</summary>
public readonly record struct Frame(byte[] Data, bool Truncated);

/// <summary>Memotong aliran byte TCP menjadi record sesuai <see cref="FrameMode"/>.</summary>
public sealed class FrameDecoder
{
    private const byte Stx = 0x02;
    private const byte Etx = 0x03;
    private const byte Cr = 0x0D;
    private const byte Lf = 0x0A;

    private readonly FrameMode _mode;
    private readonly byte[] _terminator;
    private readonly int _maxBytes;
    private readonly List<byte> _buffer = [];
    private bool _insideFrame;
    private bool _discarding;

    public FrameDecoder(ServerSettings settings)
    {
        _mode = settings.Framing;
        _maxBytes = Math.Max(16, settings.MaxFrameBytes);
        _terminator = _mode == FrameMode.Custom
            ? TextEncodings.Resolve(settings.Encoding).GetBytes(ControlTokens.Decode(settings.CustomTerminator ?? ""))
            : [];
        if (_mode == FrameMode.Custom && _terminator.Length == 0)
        {
            throw new ArgumentException("Terminator custom kosong", nameof(settings));
        }
    }

    /// <summary>Ada byte yang sudah diterima tetapi belum menjadi record (menunggu terminator).</summary>
    public bool HasPending => !_discarding && _buffer.Count > 0 && (_mode != FrameMode.StxEtx || _insideFrame);

    /// <summary>Jadikan byte yang menunggu terminator sebagai satu record (dipakai setelah jeda tanpa data).</summary>
    public Frame? Flush()
    {
        if (!HasPending)
        {
            return null;
        }

        var frame = new Frame([.. _buffer], Truncated: false);
        _buffer.Clear();
        _insideFrame = false;
        return frame;
    }

    public IReadOnlyList<Frame> Push(ReadOnlySpan<byte> data)
    {
        var frames = new List<Frame>();
        if (_mode == FrameMode.Packet)
        {
            var packet = TrimLineEnd(data);
            if (packet.Length > 0)
            {
                var truncated = packet.Length > _maxBytes;
                frames.Add(new Frame(packet[..Math.Min(packet.Length, _maxBytes)].ToArray(), truncated));
            }

            return frames;
        }

        foreach (var b in data)
        {
            switch (_mode)
            {
                case FrameMode.Line:
                    if (b is Cr or Lf)
                    {
                        Emit(frames);
                    }
                    else
                    {
                        Append(b, frames);
                    }

                    break;
                case FrameMode.Etx:
                    if (b == Etx)
                    {
                        Emit(frames);
                    }
                    else if (!(b == Stx && _buffer.Count == 0))
                    {
                        Append(b, frames);
                    }

                    break;
                case FrameMode.StxEtx:
                    if (b == Stx)
                    {
                        _buffer.Clear();
                        _discarding = false;
                        _insideFrame = true;
                    }
                    else if (b == Etx && _insideFrame)
                    {
                        Emit(frames);
                        _insideFrame = false;
                    }
                    else if (_insideFrame)
                    {
                        Append(b, frames);
                    }

                    break;
                case FrameMode.Custom:
                    Append(b, frames);
                    if (EndsWithTerminator())
                    {
                        _buffer.RemoveRange(_buffer.Count - _terminator.Length, _terminator.Length);
                        Emit(frames);
                    }

                    break;
            }
        }

        return frames;
    }

    private void Append(byte b, List<Frame> frames)
    {
        if (_discarding)
        {
            // Simpan hanya ekor sepanjang terminator agar akhir record tetap terdeteksi.
            _buffer.Add(b);
            if (_buffer.Count > _terminator.Length)
            {
                _buffer.RemoveAt(0);
            }

            return;
        }

        _buffer.Add(b);
        if (_buffer.Count > _maxBytes + _terminator.Length)
        {
            // Record terlalu panjang tanpa terminator: kirim yang ada sebagai record terpotong,
            // lalu abaikan sisa byte sampai terminator berikutnya.
            var head = _buffer.GetRange(0, _maxBytes).ToArray();
            frames.Add(new Frame(head, Truncated: true));
            _buffer.Clear();
            _discarding = true;
        }
    }

    private void Emit(List<Frame> frames)
    {
        if (_discarding)
        {
            _discarding = false;
            _buffer.Clear();
            return;
        }

        if (_buffer.Count > 0)
        {
            frames.Add(new Frame([.. _buffer], Truncated: false));
            _buffer.Clear();
        }
    }

    private bool EndsWithTerminator()
    {
        if (_buffer.Count < _terminator.Length)
        {
            return false;
        }

        var offset = _buffer.Count - _terminator.Length;
        for (var i = 0; i < _terminator.Length; i++)
        {
            if (_buffer[offset + i] != _terminator[i])
            {
                return false;
            }
        }

        return true;
    }

    private static ReadOnlySpan<byte> TrimLineEnd(ReadOnlySpan<byte> data)
    {
        var end = data.Length;
        while (end > 0 && data[end - 1] is Cr or Lf)
        {
            end--;
        }

        return data[..end];
    }
}
