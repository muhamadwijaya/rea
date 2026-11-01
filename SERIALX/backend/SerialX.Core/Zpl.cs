using System.Text;

namespace SerialX.Core;

/// <summary>ZPL II generator — satu label berisi text, barcode Code128, dan QR dari payload serial.</summary>
public static class Zpl
{
    /// <summary>Buat dokumen ZPL 100x150mm untuk serial + metadata.</summary>
    public static string BuildLabel(string serial, string sku, string lot, string exp, int dpi = 203)
    {
        double s = dpi / 203.0; // scaling dot
        int Px(double mm) => (int)Math.Round(mm * dpi / 25.4);
        var sb = new StringBuilder();
        sb.Append("^XA\n");
        sb.Append($"^PW{Px(100)}\n^LL{Px(150)}\n^CI28\n");
        sb.Append($"^FO{Px(6)},{Px(8)}^AAN,{(int)(24 * s)},0^FD{Esc(serial)}^FS\n");
        sb.Append($"^FO{Px(6)},{Px(20)}^AAN,{(int)(14 * s)},0^FDArtikel: {Esc(sku)}  LOT: {Esc(lot)}  EXP: {Esc(exp)}^FS\n");
        // Code128 horizontal
        sb.Append($"^FO{Px(6)},{Px(30)}^BY{(int)(2 * s)}^BCN,{(int)(60 * s)},Y,N,A^FD>;{Esc(serial)}^FS\n");
        // QR pojok kanan
        sb.Append($"^FO{Px(70)},{Px(95)}^BQN,2,{Math.Max(2, (int)(5 * s))}^FDLA,{Esc(serial)}^FS\n");
        sb.Append($"^FO{Px(6)},{Px(120)}^AAN,{(int)(12 * s)},0^FDSERIALX :: TERVERIFIKASI^FS\n");
        sb.Append("^PQ1\n^XZ\n");
        return sb.ToString();
    }

    private static string Esc(string v) => v.Replace("^", " ").Replace("~", " ").Replace("\r", "").Replace("\n", " ");
}

/// <summary>TSPL (TSC) generator alternatif.</summary>
public static class Tspl
{
    public static string BuildLabel(string serial, string sku)
    {
        return string.Join('\n',
            "SIZE 100 mm,150 mm", "GAP 2 mm,2 mm", "CLS",
            $"TEXT 20,20,\"TTF-EN\",1,1,0,\"{serial}\"",
            $"TEXT 20,60,\"TTF-EN\",1,1,0,\"SKU {sku}\"",
            $"BARCODE 20,100,\"128\",100,1,0,2.0, \"{serial}\"",
            $"BARCODE 400,100,\"QRCODE\",6,A,0,\"{serial}\"",
            "PRINT 1,1", "");
    }
}
