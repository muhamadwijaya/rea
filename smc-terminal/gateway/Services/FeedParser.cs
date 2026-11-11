using System.Globalization;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Xml;
using System.Xml.Linq;
using SmcTerminal.Gateway.Models;

namespace SmcTerminal.Gateway.Services;

/// <summary>Namespace-tolerant parser for RSS 2.0, RSS 1.0 (RDF) and Atom feeds.</summary>
public static partial class FeedParser
{
    public static List<NewsItem> Parse(string xml, FeedConfig feed, DateTimeOffset now)
    {
        var settings = new XmlReaderSettings { DtdProcessing = DtdProcessing.Ignore, XmlResolver = null };
        using var reader = XmlReader.Create(new StringReader(xml.TrimStart('﻿', ' ', '\r', '\n', '\t')), settings);
        var doc = XDocument.Load(reader);
        var items = new List<NewsItem>();
        foreach (var node in doc.Descendants().Where(e => e.Name.LocalName is "item" or "entry"))
        {
            var item = ParseItem(node, feed, now);
            if (item is not null)
            {
                items.Add(item);
            }
        }
        return items;
    }

    private static NewsItem? ParseItem(XElement node, FeedConfig feed, DateTimeOffset now)
    {
        var title = Clean(Child(node, "title")?.Value);
        if (string.IsNullOrWhiteSpace(title))
        {
            return null;
        }

        var link = LinkOf(node);
        if (string.IsNullOrWhiteSpace(link) || !Uri.TryCreate(link, UriKind.Absolute, out var uri) ||
            (uri.Scheme != Uri.UriSchemeHttp && uri.Scheme != Uri.UriSchemeHttps))
        {
            return null;
        }

        var summary = Clean(Child(node, "description")?.Value ?? Child(node, "summary")?.Value ?? Child(node, "encoded")?.Value);
        if (summary.Length > 320)
        {
            summary = summary[..317].TrimEnd() + "…";
        }

        var source = feed.Name;
        var publisher = Child(node, "source")?.Value?.Trim();
        if (!string.IsNullOrEmpty(publisher))
        {
            source = publisher;
            var suffix = " - " + publisher;
            if (title.EndsWith(suffix, StringComparison.Ordinal))
            {
                title = title[..^suffix.Length];
            }
        }

        var dateText = Child(node, "pubDate")?.Value ?? Child(node, "published")?.Value ??
                       Child(node, "updated")?.Value ?? Child(node, "date")?.Value;
        var published = ParseDate(dateText) ?? now;
        if (published > now.AddMinutes(5))
        {
            published = now;
        }

        if (summary.Equals(title, StringComparison.OrdinalIgnoreCase) || summary.StartsWith(title, StringComparison.OrdinalIgnoreCase) && summary.Length < title.Length + 40)
        {
            summary = "";
        }

        return new NewsItem
        {
            Id = HashId(link),
            Title = title,
            Summary = summary,
            Link = link,
            Source = source,
            Feed = feed.Name,
            Category = feed.Category,
            Lang = feed.Lang,
            Published = published,
            Received = now,
            Image = ImageOf(node),
        };
    }

    private static XElement? Child(XElement node, string localName) =>
        node.Elements().FirstOrDefault(e => e.Name.LocalName == localName);

    private static string? LinkOf(XElement node)
    {
        foreach (var link in node.Elements().Where(e => e.Name.LocalName == "link"))
        {
            var href = link.Attribute("href")?.Value;
            var rel = link.Attribute("rel")?.Value;
            if (!string.IsNullOrWhiteSpace(href) && (rel is null || rel == "alternate"))
            {
                return href.Trim();
            }
            if (!string.IsNullOrWhiteSpace(link.Value))
            {
                return link.Value.Trim();
            }
        }
        var guid = Child(node, "guid");
        var permalink = guid?.Attribute("isPermaLink")?.Value;
        return guid is not null && permalink != "false" && guid.Value.StartsWith("http", StringComparison.OrdinalIgnoreCase)
            ? guid.Value.Trim()
            : null;
    }

    private static string? ImageOf(XElement node)
    {
        foreach (var e in node.Elements())
        {
            var name = e.Name.LocalName;
            var url = e.Attribute("url")?.Value;
            if (string.IsNullOrEmpty(url))
            {
                continue;
            }
            if (name is "thumbnail" or "content" && (e.Attribute("medium")?.Value is null or "image" ||
                                                    e.Attribute("type")?.Value?.StartsWith("image") == true))
            {
                return url;
            }
            if (name == "enclosure" && e.Attribute("type")?.Value?.StartsWith("image") == true)
            {
                return url;
            }
        }
        return null;
    }

    public static string Clean(string? html)
    {
        if (string.IsNullOrEmpty(html))
        {
            return "";
        }
        var text = TagRegex().Replace(html, " ");
        text = WebUtility.HtmlDecode(WebUtility.HtmlDecode(text));
        return SpaceRegex().Replace(text, " ").Trim();
    }

    public static string HashId(string value)
    {
        var bytes = SHA1.HashData(Encoding.UTF8.GetBytes(value.Trim()));
        return Convert.ToHexString(bytes, 0, 8).ToLowerInvariant();
    }

    private static readonly Dictionary<string, string> Zones = new(StringComparer.OrdinalIgnoreCase)
    {
        ["GMT"] = "+00:00", ["UT"] = "+00:00", ["UTC"] = "+00:00", ["Z"] = "+00:00",
        ["EST"] = "-05:00", ["EDT"] = "-04:00", ["CST"] = "-06:00", ["CDT"] = "-05:00",
        ["MST"] = "-07:00", ["MDT"] = "-06:00", ["PST"] = "-08:00", ["PDT"] = "-07:00",
        ["WIB"] = "+07:00", ["WITA"] = "+08:00", ["WIT"] = "+09:00",
    };

    private static readonly string[] RfcFormats =
    {
        "ddd, d MMM yyyy HH:mm:ss zzz", "d MMM yyyy HH:mm:ss zzz", "ddd, d MMM yyyy HH:mm zzz",
        "d MMM yyyy HH:mm zzz", "ddd, dd MMM yyyy HH:mm:ss zzz", "ddd, d MMMM yyyy HH:mm:ss zzz",
    };

    public static DateTimeOffset? ParseDate(string? text)
    {
        if (string.IsNullOrWhiteSpace(text))
        {
            return null;
        }
        var value = text.Trim();
        if (DateTimeOffset.TryParse(value, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out var iso) &&
            value.Contains('T'))
        {
            return iso.ToUniversalTime();
        }

        var parts = value.Split(' ', StringSplitOptions.RemoveEmptyEntries).ToList();
        if (parts.Count >= 2)
        {
            var zone = parts[^1];
            if (Zones.TryGetValue(zone, out var offset))
            {
                parts[^1] = offset;
            }
            else if (OffsetRegex().IsMatch(zone))
            {
                parts[^1] = zone[..3] + ":" + zone[3..];
            }
            var normalized = string.Join(' ', parts);
            if (DateTimeOffset.TryParseExact(normalized, RfcFormats, CultureInfo.InvariantCulture,
                    DateTimeStyles.AllowWhiteSpaces, out var rfc))
            {
                return rfc.ToUniversalTime();
            }
        }

        return DateTimeOffset.TryParse(value, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal, out var any)
            ? any.ToUniversalTime()
            : null;
    }

    [GeneratedRegex("<[^>]+>")]
    private static partial Regex TagRegex();

    [GeneratedRegex(@"\s+")]
    private static partial Regex SpaceRegex();

    [GeneratedRegex(@"^[+-]\d{4}$")]
    private static partial Regex OffsetRegex();
}
