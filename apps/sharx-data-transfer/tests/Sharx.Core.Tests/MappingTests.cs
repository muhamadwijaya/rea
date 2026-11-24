using System.Text;
using Sharx.Core.Configuration;
using Sharx.Core.Mapping;
using Sharx.Core.Text;
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

    [Theory]
    [InlineData("ANANDA02,867892,000", "ANANDA02", "867892", "000")]
    [InlineData("SITI AMINAH,TPL-A1/2026,00015", "SITI AMINAH", "TPL-A1/2026", "00015")]
    [InlineData("B,7,1", "B", "7", "1")]
    [InlineData(" OPR_99 , 1234567890ABC , 42 ", "OPR_99", "1234567890ABC", "42")]
    public void Delimited_fields_have_no_fixed_length(string raw, string op, string template, string counter)
    {
        var result = RecordParser.Parse(raw, new MappingSettings());

        Assert.True(result.Ok);
        Assert.Equal((op, template, counter), (result.Record.Operator, result.Record.Template, result.Record.ServerCounter));
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
        var context = new OutputContext(record, 7, "007", "2026-11-23", new DateTimeOffset(2026, 11, 23, 8, 5, 9, TimeSpan.Zero), 3);

        var text = OutputFormatter.Render("<STX>{TEMPLATE}|{OPERATOR:L10}|{COUNTER}|{COUNTER:D5}|{DATE:ddMMyy}|{F3}|{{x}}<ETX>", context);

        Assert.Equal("\u0002867892|ANANDA02  |007|00007|231126|000|{x}\u0003", text);
    }

    [Fact]
    public void Data_values_are_not_decoded_as_control_tokens()
    {
        var record = RecordParser.Parse("<CR>,867892,000", new MappingSettings()).Record;
        var context = new OutputContext(record, 1, "001", "2026-11-23", DateTimeOffset.UnixEpoch, 1);

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
    public void Frame_decoder_flushes_record_sent_without_terminator()
    {
        var decoder = new FrameDecoder(new ServerSettings());
        Assert.Empty(decoder.Push(Encoding.UTF8.GetBytes("ANANDA02,78787900,000")));
        Assert.True(decoder.HasPending);

        var frame = decoder.Flush();
        Assert.Equal("ANANDA02,78787900,000", Encoding.UTF8.GetString(frame!.Value.Data));
        Assert.False(decoder.HasPending);
        Assert.Null(decoder.Flush());
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
    public void Default_settings_are_valid_and_generic_print_counting_needs_print_done_signal()
    {
        Assert.Empty(SettingsValidator.Validate(new AppSettings()));

        var generic = new AppSettings();
        generic.Printer.Protocol = PrinterProtocol.Generic;
        Assert.Contains(SettingsValidator.Validate(generic), e => e.Contains("sinyal selesai cetak"));

        generic.Printer.Generic.PrintDonePattern = "<EOT>";
        Assert.Empty(SettingsValidator.Validate(generic));
    }

    [Fact]
    public void Command_encoder_computes_checksum_tokens()
    {
        // Contoh dokumen TIJ 4.8 bab 2.4 ditulis sebagai perintah Generic: checksum <SUM> = B0.
        var text = ControlTokens.Decode("<ESC><STX><0x00><0x1D><0x01><0x01><0x0B>TIJ Printer<ESC><ETX><SUM>");
        Assert.Equal("1B02001D01010B54494A205072696E7465721B03B0", Convert.ToHexString(CommandEncoder.Encode(text, Encoding.ASCII)));

        var xor = CommandEncoder.Encode(ControlTokens.Decode("<STX>AB<XOR>"), Encoding.ASCII);
        Assert.Equal(0x02 ^ 0x41 ^ 0x42, xor[^1]);
    }
}

public class FeedbackSettingsTests
{
    internal static AppSettings LoadJson(string json)
    {
        var dir = Path.Combine(Path.GetTempPath(), $"sharx-settings-{Guid.NewGuid():N}");
        Directory.CreateDirectory(dir);
        try
        {
            var path = Path.Combine(dir, "settings.json");
            File.WriteAllText(path, json);
            var (settings, warning) = new SettingsStore(path).Load();
            Assert.Null(warning);
            return settings;
        }
        finally
        {
            Directory.Delete(dir, recursive: true);
        }
    }

    [Fact]
    public void Legacy_default_template_becomes_selectable_fields()
    {
        var s = LoadJson("""{"feedback":{"enabled":true,"template":"{OPERATOR},{TEMPLATE},{COUNTER}<CR><LF>"}}""");
        Assert.Equal(FeedbackFormat.Fields, s.Feedback.Format);
        Assert.Equal(["OPERATOR", "TEMPLATE", "COUNTER"], s.Feedback.Fields.Select(f => f.Key));
        Assert.Equal("{OPERATOR},{TEMPLATE},{COUNTER}<CR><LF>", s.Feedback.EffectiveTemplate());
    }

    [Fact]
    public void Legacy_custom_template_is_kept_as_custom()
    {
        var s = LoadJson("""{"feedback":{"enabled":true,"template":"PRINT;{OPERATOR};{COUNTER:D6}<CR>"}}""");
        Assert.Equal(FeedbackFormat.Custom, s.Feedback.Format);
        Assert.Equal("PRINT;{OPERATOR};{COUNTER:D6}<CR>", s.Feedback.EffectiveTemplate());
        Assert.Empty(SettingsValidator.Validate(s));
    }

    [Fact]
    public void Selected_fields_build_the_feedback_in_order()
    {
        var s = new AppSettings();
        s.Feedback.Format = FeedbackFormat.Fields;
        s.Feedback.Fields = [new() { Key = "counter", Format = "D5" }, new() { Key = "DATE", Format = "yyMMdd" }];
        s.Feedback.Delimiter = "<TAB>";
        s.Feedback.Terminator = "<CR>";
        Assert.Empty(SettingsValidator.Validate(s)); // kunci dinormalkan ke huruf besar
        Assert.Equal("{COUNTER:D5}<TAB>{DATE:yyMMdd}<CR>", s.Feedback.EffectiveTemplate());
    }

    [Fact]
    public void Feedback_field_selection_is_validated()
    {
        var s = new AppSettings();
        s.Feedback.Format = FeedbackFormat.Fields;
        s.Feedback.Fields = [];
        Assert.Contains(SettingsValidator.Validate(s), e => e.Contains("minimal satu data", StringComparison.Ordinal));

        s.Feedback.Fields = [new() { Key = "TIDAK_ADA" }];
        Assert.Contains(SettingsValidator.Validate(s), e => e.Contains("{TIDAK_ADA}", StringComparison.Ordinal));

        s.Feedback.Fields = [new() { Key = "COUNTER", Format = "Q" }];
        Assert.Contains(SettingsValidator.Validate(s), e => e.Contains("Format 'Q'", StringComparison.Ordinal));

        s.Feedback.Enabled = false; // feedback nonaktif: susunan data tidak diperiksa
        Assert.Empty(SettingsValidator.Validate(s));
    }
}

public class PrinterMachineTests
{
    [Fact]
    public void Old_settings_show_the_default_tij_machine_name()
    {
        var s = FeedbackSettingsTests.LoadJson("""{"printer":{"host":"10.0.0.5","port":9100}}""");
        Assert.Equal(PrinterMachineType.Tij, s.Printer.MachineType);
        Assert.Equal("TIJ Printer 4.8", s.Printer.DisplayName());
    }

    [Fact]
    public void Machine_type_and_name_are_configurable()
    {
        var s = FeedbackSettingsTests.LoadJson("""{"printer":{"machineType":"Cij","machineName":"  Line 2 "}}""");
        Assert.Equal("CIJ Line 2", s.Printer.DisplayName());

        s.Printer.MachineName = "";
        Assert.Equal("CIJ", s.Printer.DisplayName());

        s.Printer.MachineName = new string('x', PrinterSettings.MachineNameMaxLength + 1);
        Assert.Contains(SettingsValidator.Validate(s), e => e.StartsWith("Nama mesin", StringComparison.Ordinal));
    }
}
