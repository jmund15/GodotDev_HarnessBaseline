// TEMPLATE — parameterized_asset_pipeline (replace {{PROJECT_NAMESPACE}}).
// Engine-pure manifest parser / theme builder pair, lifted from the reference project
// (ui-art-system.md seams: "DW-namespace-pure -> lift to baseline as-is").
// Provenance: the reference project's Game/UI (its Logic + Integration suites verify the originals).

namespace {{PROJECT_NAMESPACE}}.UI;

using Godot;

/// <summary>The import-free texture idiom (UnitSpriteLibrary's): imported resource
/// when Godot knows the path, raw-file load as the fallback.</summary>
public static class AssetLoader
{
    public static Texture2D? LoadTexture(string path)
    {
        if (ResourceLoader.Exists(path))
        {
            return ResourceLoader.Load<Texture2D>(path);
        }
        var global = ProjectSettings.GlobalizePath(path);
        if (!System.IO.File.Exists(global))
        {
            return null;
        }
        var image = Image.LoadFromFile(global);
        return image is null ? null : ImageTexture.CreateFromImage(image);
    }
}

/// <summary>
/// Builds the master Theme from a parsed manifest (ui-art-system.md): 9-patch
/// StyleBoxTextures, pixel fonts, palette-resolved colors, and type variations.
/// </summary>
public sealed class ThemeBuilder
{
    public Theme Build(ThemeManifestRecord manifest)
    {
        var theme = new Theme();

        foreach (var entry in manifest.StyleBoxes)
        {
            var texture = AssetLoader.LoadTexture(entry.Png);
            if (texture is null)
            {
                continue; // a missing tile falls back to the default look, never crashes
            }
            var box = new StyleBoxTexture
            {
                Texture = texture,
                TextureMarginLeft = entry.Margins[0],
                TextureMarginTop = entry.Margins[1],
                TextureMarginRight = entry.Margins[2],
                TextureMarginBottom = entry.Margins[3],
                ContentMarginLeft = entry.ContentMargins[0],
                ContentMarginTop = entry.ContentMargins[1],
                ContentMarginRight = entry.ContentMargins[2],
                ContentMarginBottom = entry.ContentMargins[3],
            };
            theme.SetStylebox(entry.Item, entry.Type, box);
        }

        foreach (var (role, font) in manifest.Fonts)
        {
            if (!ResourceLoader.Exists(font.Path)
                && !System.IO.File.Exists(ProjectSettings.GlobalizePath(font.Path)))
            {
                continue;
            }
            var fontFile = ResourceLoader.Exists(font.Path)
                ? ResourceLoader.Load<FontFile>(font.Path)
                : LoadRawFont(font.Path);
            if (fontFile is null)
            {
                continue;
            }
            // Pixel fonts: no AA, nearest sampling.
            fontFile.Antialiasing = TextServer.FontAntialiasing.None;
            fontFile.SubpixelPositioning = TextServer.SubpixelPositioning.Disabled;
            if (role == "body")
            {
                theme.DefaultFont = fontFile;
                theme.DefaultFontSize = font.Size;
            }
            if (role == "header")
            {
                theme.SetFont("font", "HeaderLabel", fontFile);
                theme.SetFontSize("font_size", "HeaderLabel", font.Size);
            }
        }

        foreach (var (key, hex) in manifest.Colors)
        {
            var split = key.Split('/');
            if (split.Length == 2 && hex.Length >= 6)
            {
                theme.SetColor(split[1], split[0], Color.FromHtml(hex));
            }
        }

        foreach (var variation in manifest.TypeVariations)
        {
            theme.SetTypeVariation(variation.Variation, variation.Base);
        }
        return theme;
    }

    private static FontFile? LoadRawFont(string path)
    {
        var global = ProjectSettings.GlobalizePath(path);
        var font = new FontFile();
        var error = font.LoadDynamicFont(global);
        return error == Error.Ok ? font : null;
    }
}

/// <summary>
/// The master theme, lazily built once and applied per scene root (no autoload —
/// cross-scene state is static, the GameSession pattern). The theme only
/// propagates in Control trees, so non-Control scene roots apply it to each
/// top-level Control instead.
/// </summary>
public static class ThemeService
{
    private static Theme? _master;
    private static ThemeManifestRecord? _manifest;

    private static ThemeManifestRecord Manifest
    {
        get
        {
            if (_manifest is null)
            {
                var manifestText = FileAccess.FileExists("res://assets/ui/theme_manifest.json")
                    ? FileAccess.GetFileAsString("res://assets/ui/theme_manifest.json")
                    : "{}";
                _manifest = ThemeManifestParser.Parse(manifestText);
            }
            return _manifest;
        }
    }

    public static Theme Master => _master ??= new ThemeBuilder().Build(Manifest);

    // GAME-DOMAIN COLOUR ACCESSORS GO HERE — the single-source recipe:
    // expose Domain-enum -> manifest colour lookups (e.g. ElementColor(Element e)
    // => ManifestColor($"element_{...}")) so no hex literal ever lives in C#.
    // See the reference project's ThemeService.ElementColor/RarityColor for the worked example.

    private static Color ManifestColor(string key)
    {
        return Manifest.Colors.TryGetValue(key, out var hex) && hex.Length >= 6
            ? Color.FromHtml(hex)
            : Colors.White;
    }

    private static readonly System.Collections.Generic.Dictionary<string, Texture2D?> IconCache = new();

    /// <summary>Manifest icon lookup (arch-ui-followups §B; tiers 16/32/64).
    /// Null for unknown names/tiers — callers keep their no-icon layout.</summary>
    public static Texture2D? Icon(string name, int size = 16)
    {
        var cacheKey = $"{name}_{size}";
        if (IconCache.TryGetValue(cacheKey, out var cached))
        {
            return cached;
        }
        var texture = LoadIcon(name, size.ToString());
        IconCache[cacheKey] = texture;
        return texture;
    }

    private static Texture2D? LoadIcon(string name, string tier)
    {
        foreach (var entry in Manifest.Icons)
        {
            if (entry.Name != name)
            {
                continue;
            }
            return entry.Tiers.TryGetValue(tier, out var path)
                ? AssetLoader.LoadTexture(path)
                : null;
        }
        return null;
    }

    #region Test Helpers
#if TOOLS
    internal static void ResetForTesting()
    {
        _master = null;
        _manifest = null;
        IconCache.Clear();
    }
#endif
    #endregion

    public static void ApplyTo(Node sceneRoot)
    {
        if (sceneRoot is Control control)
        {
            control.Theme = Master;
            return;
        }
        foreach (var child in sceneRoot.GetChildren())
        {
            if (child is Control topLevel)
            {
                topLevel.Theme = Master;
            }
            else if (child is CanvasLayer layer)
            {
                ApplyTo(layer);
            }
        }
    }
}
