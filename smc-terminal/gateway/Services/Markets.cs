using SmcTerminal.Gateway.Models;

namespace SmcTerminal.Gateway.Services;

/// <summary>
/// Owner of the market pages (forex, saham-as, saham-idx, kripto) on the news side:
/// a headline belongs to a market through its feed category or through the assets
/// the engine tagged in its text.
/// </summary>
public static class Markets
{
    public static readonly string[] All = { "forex", "saham-as", "saham-idx", "kripto" };

    private static readonly Dictionary<string, (string[] Categories, string[] Assets)> Rules = new()
    {
        ["forex"] = (new[] { "forex", "commodity", "macro" }, new[] { "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD", "GOLD", "OIL" }),
        ["saham-as"] = (new[] { "us", "macro" }, new[] { "US_EQ" }),
        ["saham-idx"] = (new[] { "idx" }, new[] { "IDX", "IDR" }),
        ["kripto"] = (new[] { "crypto" }, new[] { "CRYPTO" }),
    };

    public static bool IsKnown(string? market) => market is not null && Rules.ContainsKey(market);

    public static IReadOnlyList<string> Classify(string category, IReadOnlyList<string> assets, IReadOnlyList<SymbolTag>? symbols = null) =>
        All.Where(m => Rules[m].Categories.Contains(category) || assets.Any(a => Rules[m].Assets.Contains(a)) ||
                       (symbols?.Any(s => s.Market == m) ?? false)).ToList();

    public static IReadOnlyList<string> ForCategory(string category) =>
        All.Where(m => Rules[m].Categories.Contains(category)).ToList();

    public static IReadOnlyList<string> CategoriesOf(string market) => Rules[market].Categories;
}
