using System.Text.Json.Nodes;

namespace SerialX.Core;

/// <summary>Status hasil agregasi (deteksi konflik unit masuk dua induk).</summary>
public enum AggResult { Ok = 0, Conflict = 1, NotFound = 2, Cycle = 3 }

/// <summary>Pohon agregasi Unit→Pack→Case→Pallet di memori dengan deteksi konflik &amp; siklus.</summary>
public sealed class AggregationTree
{
    private readonly Dictionary<string, string> _parent = new(StringComparer.OrdinalIgnoreCase);
    private readonly Dictionary<string, HashSet<string>> _children = new(StringComparer.OrdinalIgnoreCase);
    private readonly HashSet<string> _nodes = new(StringComparer.OrdinalIgnoreCase);

    public int NodeCount => _nodes.Count;

    /// <summary>Hubungkan child → parent pada level tertentu. Return Conflict bila child sudah punya induk berbeda.</summary>
    public AggResult Link(string child, string parent, int childLevel)
    {
        if (string.IsNullOrWhiteSpace(child) || string.IsNullOrWhiteSpace(parent)) return AggResult.NotFound;
        _nodes.Add(child); _nodes.Add(parent);
        if (_parent.TryGetValue(child, out var existing) && !string.Equals(existing, parent, StringComparison.OrdinalIgnoreCase))
            return AggResult.Conflict;
        // cek siklus: parent tidak boleh keturunan child
        var cur = parent;
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        while (_parent.TryGetValue(cur, out var p))
        {
            if (!seen.Add(cur)) break;
            if (string.Equals(p, child, StringComparison.OrdinalIgnoreCase)) return AggResult.Cycle;
            cur = p;
        }
        _parent[child] = parent;
        if (!_children.TryGetValue(parent, out var set))
            _children[parent] = set = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        set.Add(child);
        return AggResult.Ok;
    }

    public bool TryGetParent(string node, out string parent) => _parent.TryGetValue(node, out parent!);

    public IReadOnlyCollection<string> ChildrenOf(string node) =>
        _children.TryGetValue(node, out var s) ? s : Array.Empty<string>();

    /// <summary>Ambil seluruh keturunan (BFS), eksklusif node itu sendiri.</summary>
    public List<string> Descendants(string root)
    {
        var result = new List<string>();
        var queue = new Queue<string>();
        queue.Enqueue(root);
        var visited = new HashSet<string>(StringComparer.OrdinalIgnoreCase) { root };
        while (queue.Count > 0)
        {
            var cur = queue.Dequeue();
            foreach (var c in ChildrenOf(cur))
                if (visited.Add(c)) { result.Add(c); queue.Enqueue(c); }
        }
        return result;
    }

    /// <summary>Rantai leluhur dari node sampai puncak (unit → pack → case → pallet).</summary>
    public List<string> Ancestry(string node)
    {
        var chain = new List<string>();
        var cur = node;
        var guard = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        while (_parent.TryGetValue(cur, out var p) && guard.Add(cur)) { chain.Add(p); cur = p; }
        return chain;
    }

    /// <summary>Ekspor struktur menjadi JSON EPCIS-like sederhana.</summary>
    public string ToEpcisJson(string epcListGtin = "0890000000000")
    {
        var roots = _nodes.Where(n => !_parent.ContainsKey(n)).OrderBy(n => n).ToList();
        var evtList = new JsonArray();
        foreach (var r in roots)
        {
            evtList.Add(new JsonObject
            {
                ["type"] = "ObjectEvent",
                ["epc"] = r,
                ["parentChain"] = new JsonArray(Ancestry(r).Select(x => JsonValue.Create(x)!).ToArray()),
                ["childCount"] = Descendants(r).Count,
                ["gtin"] = epcListGtin
            });
        }
        var doc = new JsonObject
        {
            ["context"] = "https://ref.gs1.org/standards/epcis/",
            ["creationDate"] = DateTime.UtcNow.ToString("O"),
            ["events"] = evtList
        };
        return doc.ToJsonString(new System.Text.Json.JsonWriterOptions { Indented = true });
    }
}
