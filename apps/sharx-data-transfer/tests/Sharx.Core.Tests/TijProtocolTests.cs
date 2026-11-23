using System.Text;
using Sharx.Core.Transport;

namespace Sharx.Core.Tests;

public class TijProtocolTests
{
    [Fact]
    public void Checksum_matches_protocol_document_example()
    {
        // TIJ Protocol 4.8, bab 2.4: checksum perintah ini adalah B0h.
        var bytes = Convert.FromHexString("1B02001D01010B54494A205072696E7465721B03");
        Assert.Equal(0xB0, TijProtocol.Checksum(bytes));
    }

    [Fact]
    public void RemoteFields_builds_the_documented_1D_command()
    {
        // TIJ Protocol 4.8, bab 3.4.17: field 01 = "TIJ Printer", field 03 = "04/27/2019".
        var frame = TijProtocol.RemoteFields(0x00, [(1, "TIJ Printer"), (3, "04/27/2019")], Encoding.UTF8);
        Assert.Equal("1B02001D02010B54494A205072696E746572030A30342F32372F323031391B03AB", Convert.ToHexString(frame));
    }

    [Fact]
    public void LoadMessage_prefixes_name_length()
    {
        var frame = TijProtocol.LoadMessage(0x05, "867892", Encoding.UTF8);
        Assert.Equal("1B02051E06383637383932", Convert.ToHexString(frame.AsSpan(0, frame.Length - 3)));
        Assert.Equal(0, frame.Sum(b => b) & 0xFF);
    }

    [Fact]
    public void Parser_reads_message_names_reply_from_document()
    {
        var parser = new TijFrameParser();
        var frames = parser.Push(Convert.FromHexString("1B0600001F01036E65771B0354"));
        var frame = Assert.Single(frames);
        Assert.Equal(PrinterFrameKind.Ack, frame.Kind);
        Assert.Equal(TijProtocol.CmdMessageNames, frame.Command);
        Assert.Equal(["new"], TijProtocol.ParseMessageNames(frame.Data, Encoding.UTF8));
    }

    [Fact]
    public void Parser_handles_split_frames_garbage_and_print_signals()
    {
        var parser = new TijFrameParser();
        var printEnd = Reply(TijProtocol.Ack, 0x00, TijProtocol.SignalPrintEnd);
        var nak = Reply(TijProtocol.Nak, 0x08, 0x1D);
        var stream = new byte[] { 0x41, 0x42 }.Concat(printEnd).Concat(nak).ToArray();

        var first = parser.Push(stream.AsSpan(0, 6));
        var rest = parser.Push(stream.AsSpan(6));
        var all = first.Concat(rest).ToList();

        Assert.Equal(2, all.Count);
        Assert.Equal(PrinterFrameKind.PrintEnd, all[0].Kind);
        Assert.Equal(PrinterFrameKind.Nak, all[1].Kind);
        Assert.Equal(0x08, all[1].Status);
    }

    [Fact]
    public void Parser_skips_frame_with_bad_checksum()
    {
        var parser = new TijFrameParser();
        var bad = Reply(TijProtocol.Ack, 0x00, 0x1E);
        bad[^1] ^= 0xFF;
        var good = Reply(TijProtocol.Ack, 0x00, TijProtocol.SignalPrintEnd);

        var frames = parser.Push(bad.Concat(good).ToArray());

        var frame = Assert.Single(frames);
        Assert.Equal(PrinterFrameKind.PrintEnd, frame.Kind);
    }

    [Fact]
    public void SlaveFromHost_uses_last_ip_byte()
    {
        Assert.Equal(5, TijProtocol.SlaveFromHost("192.168.1.5"));
        Assert.Equal(0, TijProtocol.SlaveFromHost("printer.local"));
    }

    internal static byte[] Reply(byte kind, byte status, byte command, params byte[] data)
    {
        var bytes = new List<byte> { 0x1B, kind, 0x00, status, command };
        bytes.AddRange(data);
        bytes.Add(0x1B);
        bytes.Add(0x03);
        bytes.Add(TijProtocol.Checksum(bytes.ToArray()));
        return [.. bytes];
    }
}

public class TextFrameParserTests
{
    [Fact]
    public void Text_parser_passes_replies_and_detects_split_print_done_signal()
    {
        var parser = new TextFrameParser(Encoding.ASCII.GetBytes("DONE\r"));

        var ack = parser.Push([0x06]);
        Assert.Equal(PrinterFrameKind.Text, Assert.Single(ack).Kind);

        Assert.Empty(parser.Push(Encoding.ASCII.GetBytes("DO")));
        var frames = parser.Push(Encoding.ASCII.GetBytes("NE\rOK"));
        Assert.Equal([PrinterFrameKind.PrintEnd, PrinterFrameKind.Text], frames.Select(f => f.Kind));
        Assert.Equal("OK", Encoding.ASCII.GetString(frames[1].Data));
    }
}
