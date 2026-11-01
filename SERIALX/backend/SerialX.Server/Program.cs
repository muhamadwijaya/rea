using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using SerialX.Core;

// ============================================================================
// SERIALX BACKEND SERVER (C#) — JSON-RPC 2.0 via stdin/stdout.
// Protokol: satu baris = satu request UTF-8, satu baris = satu response.
// Method: ping, version, validate_pattern, render_serial, render_batch,
//         sign_serial, verify_serial, hash_password, verify_password,
//         zpl_label, tspl_label, agg_link, agg_export, license_verify, bench
// ============================================================================

Console.InputEncoding = Encoding.UTF8;
Console.OutputEncoding = new UTF8Encoding(false);

var agg = new AggregationTree();
var jsonOpts = new JsonSerializerOptions { WriteIndented = false };

while (true)
{
    string? line = Console.ReadLine();
    if (line is null) break;
    if (string.IsNullOrWhiteSpace(line)) continue;

    string id = "0";
    try
    {
        using var doc = JsonDocument.Parse(line);
        var root = doc.RootElement;
        id = root.TryGetProperty("id", out var e) ? e.ToString() : "0";
        var method = root.GetProperty("method").GetString() ?? "";
        var prm = root.TryGetProperty("params", out var p) ? p : default;
        var result = Handle(method, prm);
        Write(id, result, null);
    }
    catch (Exception ex)
    {
        Write(id, null, new JsonObject { ["code"] = -32000, ["message"] = ex.Message });
    }
}
return 0;

JsonNode Handle(string method, JsonElement prm)
{
    string S(string name) => prm.TryGetProperty(name, out var v) ? v.GetString() ?? "" : "";
    long L(string name, long dflt = 0) => prm.TryGetProperty(name, out var v) ? (v.ValueKind == JsonValueKind.Number ? v.GetInt64() : long.Parse(v.GetString()!)) : dflt;
    int I(string name, int dflt = 0) => (int)L(name, dflt);
    bool B(string name) => prm.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.True;

    switch (method)
    {
        case "ping":
            return JsonValue.Create("pong")!;

        case "version":
            return new JsonObject { ["name"] = "SerialX.Core", ["ver"] = "1.0.0", ["runtime"] = Environment.Version.ToString() };

        case "validate_pattern":
        {
            var (ok, msg) = SerialEngine.ValidatePattern(S("pattern"));
            return new JsonObject { ["ok"] = ok, ["message"] = msg };
        }

        case "render_serial":
        {
            var engine = new SerialEngine(B("avoid_ambiguous")) { };
            var ctx = MakeCtx(prm);
            string serial = engine.RenderFull(S("pattern"), L("seq"), ctx);
            return new JsonObject { ["serial"] = serial };
        }

        case "render_batch":
        {
            // Batch cepat: bangkitkan n serial mulai dari seq_start. Dipakai untuk benchmark & reserve block.
            var engine = new SerialEngine(B("avoid_ambiguous"));
            var ctx = MakeCtx(prm);
            int n = Math.Clamp(I("count", 1), 1, 100_000);
            long start = L("seq_start", 1);
            var arr = new JsonArray();
            for (long k = 0; k < n; k++)
                arr.Add(engine.RenderFull(S("pattern"), start + k, ctx));
            return new JsonObject { ["serials"] = arr, ["count"] = n };
        }

        case "sign_serial":
            return new JsonObject { ["sig"] = CryptoUtil.SignSerial(S("secret"), S("serial"), S("lot")) };

        case "verify_serial":
            return new JsonObject { ["valid"] = CryptoUtil.VerifySerial(S("secret"), S("serial"), S("lot"), S("sig")) };

        case "hash_password":
            return new JsonObject { ["stored"] = CryptoUtil.HashPassword(S("password")) };

        case "verify_password":
            return new JsonObject { ["valid"] = CryptoUtil.VerifyPassword(S("password"), S("stored")) };

        case "zpl_label":
            return new JsonObject { ["zpl"] = Zpl.BuildLabel(S("serial"), S("sku"), S("lot"), S("exp"), I("dpi", 203)) };

        case "tspl_label":
            return new JsonObject { ["tspl"] = Tspl.BuildLabel(S("serial"), S("sku")) };

        case "agg_link":
        {
            var r = agg.Link(S("child"), S("parent"), I("level", 1));
            return new JsonObject { ["result"] = r.ToString(), ["nodes"] = agg.NodeCount };
        }

        case "agg_descendants":
            return new JsonObject { ["list"] = new JsonArray(agg.Descendants(S("root")).Select(x => JsonValue.Create(x)!).ToArray()) };

        case "agg_export":
            return new JsonObject { ["json"] = agg.ToEpcisJson(S("gtin")) };

        case "license_verify":
        {
            bool ok = CryptoUtil.VerifyLicense(S("public_key_pem"), S("token"), out string payload);
            return new JsonObject { ["valid"] = ok, ["payload"] = payload };
        }

        case "bench":
        {
            // Benchmark murni CPU: throughput render + checksum.
            int n = Math.Clamp(I("count", 50_000), 1, 1_000_000);
            var engine = new SerialEngine();
            var ctx = MakeCtx(prm);
            var sw = System.Diagnostics.Stopwatch.StartNew();
            long len = 0;
            for (long k = 0; k < n; k++) len += engine.RenderFull(S("pattern"), k, ctx).Length;
            sw.Stop();
            return new JsonObject
            {
                ["count"] = n,
                ["elapsed_ms"] = sw.ElapsedMilliseconds,
                ["per_second"] = (long)(n / Math.Max(0.001, sw.Elapsed.TotalSeconds)),
                ["sample_len"] = len
            };
        }

        default:
            throw new NotSupportedException($"Method tidak dikenal: {method}");
    }
}

static SerialContext MakeCtx(JsonElement prm)
{
    string S(string name, string d = "") => prm.TryGetProperty(name, out var v) ? v.GetString() ?? d : d;
    long L(string name, long d = 0) => prm.TryGetProperty(name, out var v) ? (v.ValueKind == JsonValueKind.Number ? v.GetInt64() : long.Parse(v.GetString()!)) : d;
    return new SerialContext
    {
        Lot = S("lot"),
        Sku = S("sku"),
        Product = S("product"),
        Line = S("line", "L1"),
        Station = S("station", "ST01"),
        Step = (int)L("step", 1),
        Descending = prm.TryGetProperty("descending", out var d) && d.ValueKind == JsonValueKind.True,
        Now = DateTime.Now
    };
}

void Write(string id, JsonNode? result, JsonNode? error)
{
    var resp = new JsonObject { ["jsonrpc"] = "2.0", ["id"] = id };
    if (error is not null) resp["error"] = error; else resp["result"] = result;
    var text = resp.ToJsonString(jsonOpts).Replace("\n", " ").Replace("\r", " ");
    lock (Console.Out) { Console.Out.WriteLine(text); Console.Out.Flush(); }
}
