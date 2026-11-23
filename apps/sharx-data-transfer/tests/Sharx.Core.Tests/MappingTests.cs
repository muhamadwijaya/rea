using System.Text;
using Sharx.Core.Configuration;
using Sharx.Core.Mapping;
using Sharx.Core.Transport;

namespace Sharx.Core.Tests;

public class MappingTests
{
    [Fact]
    public void Default_mapping_splits_operator_template_counter()
    {
        var result = RecordParser.Parse("ANANDA02,867892,000", new MappingSettings());

        Assert.True(result.Ok);
        Assert.Equal("ANANDA02", result.Record.Operator);
        Assert.Equal("867892", result.Record.Template);
        Assert.Equal("000", result.Record.ServerCounter);
    }

    [Fact]
    public void Positions_can_be_rearranged()
    {
        var mapping = new MappingSettings
        {
            Delimiter = "<TAB>",
            Fields =
            [
                new() { Key = FieldKeys.Template, Position = 1, Required = true },
                new() { Key = FieldKeys.ServerCounter, Position = 2 },
                new() { Key = FieldKeys.Operator, Position = 3, Start = 1, Length = 6 },
                new() { Key = "LINE", Position = 4 },
            ],
        };

        var result = RecordParser.Parse("867892\t015\tANANDA02\tL1", mapping);

        Assert.True(result.Ok);
        Assert.Equal("867892", result.Record.Template);
        Assert.Equal("015", result.Record.ServerCounter);
        Assert.Equal("ANANDA", result.Record.Operator);
        Assert.Equal("L1", result.Record.Values["LINE"]);
    }

    [Fact]
    public void Fixed_width_mode_uses_character_positions()
    {
        var mapping = new MappingSettings
        {
            Mode = ParseMode.FixedWidth,
            Fields =
            [
                new() { Key = FieldKeys.Operator, Start = 1, Length = 8 },
                new() { Key = FieldKeys.Template, Start = 9, Length = 6, Required = true },
                new() { Key = FieldKeys.ServerCounter, Start = 15 },
            ],
        };

        var result = RecordParser.Parse("ANANDA02867892000", mapping);

        Assert.Equal(("ANANDA02", "867892", "000"), (result.Record.Operator, result.Record.Template, result.Record.ServerCounter));
    }

    [Fact]
    public void Missing_required_field_is_rejected()
    {
        var result = RecordParser.Parse("ANANDA02", new MappingSettings());

        Assert.False(result.Ok);
        Assert.Contains(result.Errors, e => e.Contains("Template CIJ"));
    }

    [Fact]
    public void Output_template_expands_placeholders_formats_and_control_tokens()
    {
        var record = RecordParser.Parse("ANANDA02,867892,000", new MappingSettings()).Record;
        var context = new OutputContext(record, 7, "007", "2026-11-23", new DateTimeOffset(2026, 11, 23, 8, 5, 9, TimeSpan.Zero), 3, false, false);

        var text = OutputFormatter.Render("<STX>{TEMPLATE}|{OPERATOR:L10}|{COUNTER}|{COUNTER:D5}|{DATE:ddMMyy}|{F3}|{{x}}<ETX>", context);

        Assert.Equal("\u0002867892|ANANDA02  |007|00007|231126|000|{x}\u0003", text);
    }

    [Fact]
    public void Data_values_are_not_decoded_as_control_tokens()
    {
        var record = RecordParser.Parse("<CR>,867892,000", new MappingSettings()).Record;
        var context = new OutputContext(record, 1, "001", "2026-11-23", DateTimeOffset.UnixEpoch, 1, false, false);

        Assert.Equal("<CR>", OutputFormatter.Render("{OPERATOR}", context));
    }

    [Fact]
    public void Validate_reports_unknown_placeholder()
    {
        var errors = OutputFormatter.Validate("{OPERATOR},{NOPE}", [FieldKeys.Operator]);
        Assert.Single(errors);
    }

    [Theory]
    [InlineData(FrameMode.Line, "A,1,0\r\nB,2,0\rC,3,0\n", new[] { "A,1,0", "B,2,0", "C,3,0" })]
    [InlineData(FrameMode.Etx, "\u0002A,1\u0003\u0002B,2\u0003", new[] { "A,1", "B,2" })]
    [InlineData(FrameMode.StxEtx, "xx\u0002A,1\u0003junk\u0002B,2\u0003", new[] { "A,1", "B,2" })]
    [InlineData(FrameMode.Custom, "A,1##B,2##", new[] { "A,1", "B,2" })]
    public void Frame_decoder_splits_records_across_chunks(FrameMode mode, string input, string[] expected)
    {
        var decoder = new FrameDecoder(new ServerSettings { Framing = mode, CustomTerminator = "##" });
        var bytes = Encoding.UTF8.GetBytes(input);
        var frames = new List<string>();
        foreach (var chunk in bytes.Chunk(3))
        {
            frames.AddRange(decoder.Push(chunk).Select(f => Encoding.UTF8.GetString(f.Data)));
        }

        Assert.Equal(expected, frames);
    }

    [Fact]
    public void Frame_decoder_truncates_oversized_record_and_recovers()
    {
        var decoder = new FrameDecoder(new ServerSettings { MaxFrameBytes = 16 });
        var frames = decoder.Push(Encoding.UTF8.GetBytes(new string('X', 40) + "\nOK,1,0\n"));

        Assert.Equal(2, frames.Count);
        Assert.True(frames[0].Truncated);
        Assert.Equal("OK,1,0", Encoding.UTF8.GetString(frames[1].Data));
    }

    [Fact]
    public void Default_settings_are_valid_and_custom_text_requires_record_counting()
    {
        Assert.Empty(SettingsValidator.Validate(new AppSettings()));

        var custom = new AppSettings();
        custom.Printer.Protocol = PrinterProtocol.CustomText;
        Assert.Contains(SettingsValidator.Validate(custom), e => e.Contains("Custom text"));

        custom.Counter.IncrementOn = CounterIncrement.Record;
        Assert.Empty(SettingsValidator.Validate(custom));
    }
}
