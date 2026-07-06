// TEMPLATE — parameterized_asset_pipeline (replace {{PROJECT_NAMESPACE}}).
// Engine-pure manifest parser / theme builder pair, lifted from the reference project
// (ui-art-system.md seams: "DW-namespace-pure -> lift to baseline as-is").
// Provenance: the reference project's Game/UI (its Logic + Integration suites verify the originals).

namespace {{PROJECT_NAMESPACE}}.UI;

using System.Collections.Generic;
using System.Text.Json;

public sealed record FontEntry(string Path, int Size);

public sealed record StyleBoxEntry(
    string Type, string Item, string Png, int[] Margins, int[] ContentMargins);

public sealed record TypeVariationEntry(string Variation, string Base);

public sealed record IconEntry(
    string Name, IReadOnlyDictionary<string, string> Tiers);

public sealed record ThemeManifestRecord(
    string Preset,
    IReadOnlyDictionary<string, FontEntry> Fonts,
    IReadOnlyDictionary<string, string> Colors,
    IReadOnlyList<StyleBoxEntry> StyleBoxes,
    IReadOnlyList<TypeVariationEntry> TypeVariations,
    IReadOnlyDictionary<string, int> Constants,
    IReadOnlyList<IconEntry> Icons);

public sealed class ThemeManifestException : System.Exception
{
    public ThemeManifestException(string message)
        : base(message)
    {
    }
}

/// <summary>
/// Parses theme_manifest.json (ui-art-system.md): fully-RESOLVED values only — the
/// palette logic lives in theme_gen.py; the runtime consumes verbatim. Pure C# so
/// the Logic suite pins schema violations.
/// </summary>
public static class ThemeManifestParser
{
    public static ThemeManifestRecord Parse(string json)
    {
        using var doc = JsonDocument.Parse(json);
        var root = doc.RootElement;

        var preset = root.TryGetProperty("preset", out var presetEl)
            ? presetEl.GetString() ?? "unnamed"
            : "unnamed";

        var fonts = new Dictionary<string, FontEntry>();
        if (root.TryGetProperty("fonts", out var fontsEl))
        {
            foreach (var font in fontsEl.EnumerateObject())
            {
                if (!font.Value.TryGetProperty("path", out var pathEl)
                    || string.IsNullOrEmpty(pathEl.GetString()))
                {
                    throw new ThemeManifestException(
                        $"font '{font.Name}' is missing its path");
                }
                fonts[font.Name] = new FontEntry(
                    pathEl.GetString()!,
                    font.Value.GetProperty("size").GetInt32());
            }
        }

        var colors = new Dictionary<string, string>();
        if (root.TryGetProperty("colors", out var colorsEl))
        {
            foreach (var color in colorsEl.EnumerateObject())
            {
                colors[color.Name] = color.Value.GetString() ?? "";
            }
        }

        var styleBoxes = new List<StyleBoxEntry>();
        if (root.TryGetProperty("styleboxes", out var boxesEl))
        {
            foreach (var box in boxesEl.EnumerateArray())
            {
                var entry = new StyleBoxEntry(
                    Required(box, "type"),
                    Required(box, "item"),
                    Required(box, "png"),
                    IntArray(box, "margins"),
                    IntArray(box, "content_margins"));
                foreach (var margin in entry.Margins)
                {
                    if (margin < 0)
                    {
                        throw new ThemeManifestException(
                            $"stylebox {entry.Type}/{entry.Item} has a negative margin");
                    }
                }
                styleBoxes.Add(entry);
            }
        }

        var variations = new List<TypeVariationEntry>();
        if (root.TryGetProperty("type_variations", out var varsEl))
        {
            foreach (var variation in varsEl.EnumerateArray())
            {
                variations.Add(new TypeVariationEntry(
                    Required(variation, "variation"), Required(variation, "base")));
            }
        }

        var constants = new Dictionary<string, int>();
        if (root.TryGetProperty("constants", out var constsEl))
        {
            foreach (var constant in constsEl.EnumerateObject())
            {
                constants[constant.Name] = constant.Value.GetInt32();
            }
        }

        var icons = new List<IconEntry>();
        if (root.TryGetProperty("icons", out var iconsEl))
        {
            foreach (var icon in iconsEl.EnumerateArray())
            {
                if (!icon.TryGetProperty("name", out var nameEl)
                    || string.IsNullOrEmpty(nameEl.GetString()))
                {
                    throw new ThemeManifestException("icon entry missing 'name'");
                }
                var iconName = nameEl.GetString()!;
                if (!icon.TryGetProperty("tiers", out var tiersEl))
                {
                    throw new ThemeManifestException(
                        $"icon '{iconName}' is missing its tiers");
                }
                var tiers = new Dictionary<string, string>();
                foreach (var tier in tiersEl.EnumerateObject())
                {
                    if (string.IsNullOrEmpty(tier.Value.GetString()))
                    {
                        throw new ThemeManifestException(
                            $"icon '{iconName}' tier '{tier.Name}' is missing its path");
                    }
                    tiers[tier.Name] = tier.Value.GetString()!;
                }
                if (tiers.Count == 0)
                {
                    throw new ThemeManifestException(
                        $"icon '{iconName}' is missing its tiers");
                }
                icons.Add(new IconEntry(iconName, tiers));
            }
        }

        return new ThemeManifestRecord(
            preset, fonts, colors, styleBoxes, variations, constants, icons);

        static string Required(JsonElement element, string name)
        {
            if (!element.TryGetProperty(name, out var value)
                || string.IsNullOrEmpty(value.GetString()))
            {
                throw new ThemeManifestException($"stylebox entry missing '{name}'");
            }
            return value.GetString()!;
        }

        static int[] IntArray(JsonElement element, string name)
        {
            if (!element.TryGetProperty(name, out var value))
            {
                throw new ThemeManifestException($"stylebox entry missing '{name}'");
            }
            var list = new List<int>();
            foreach (var item in value.EnumerateArray())
            {
                list.Add(item.GetInt32());
            }
            return list.ToArray();
        }
    }
}
