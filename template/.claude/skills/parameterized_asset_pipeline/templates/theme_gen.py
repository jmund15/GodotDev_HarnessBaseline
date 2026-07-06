"""TEMPLATE — parameterized_asset_pipeline (do not run in the baseline repo).

UI theme generator skeleton: theme_spec roles -> 9-patch chrome -> resolved manifest (+ variant axes, icon composition).
Adapt: theme_spec.json preset + variant axes; drop the icons import if the icon track is not instantiated.

Provenance: the reference project's art_pipeline (its test suites
verify the original of this file).
"""
# --- original file follows ---
"""theme_gen.py -- UI Art System theme-gen CORE (ui-art-system.md, Phase 1).

Bakes the neutral chrome: 24x24 9-patch StyleBoxTexture tiles from theme_spec.json
(Resurrect-64 ramp+index lookups ONLY -- lint_theme() enforces the palette
contract) plus theme_manifest.json with fully-resolved values (no palette logic
leaks to the runtime; ThemeBuilder consumes the manifest verbatim).

Usage:
    python theme_gen.py            # writes chrome PNGs + theme_manifest.json
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from icons import generate_icons
from palette import get_palette, rgb_to_hex

ROOT = Path(__file__).resolve().parent
SPEC_PATH = ROOT / "theme_spec.json"
PROJECT = ROOT.parent
CHROME_DIR = PROJECT / "assets" / "ui" / "chrome"
MANIFEST_PATH = PROJECT / "assets" / "ui" / "theme_manifest.json"


def load_spec() -> dict:
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


def role_rgb(pal, spec: dict, role: str):
    ramp, index = spec["roles"][role]
    return pal.get(ramp, index)


def draw_ninepatch(size, fill, border, highlight=None, border_px=1, highlight_px=1):
    """A 9-patch chrome tile: 1px border, optional 1px inner highlight, flat fill."""
    img = Image.new("RGBA", (size, size), (*fill, 255))
    px = img.load()
    for i in range(size):
        for b in range(border_px):
            px[i, b] = (*border, 255)
            px[i, size - 1 - b] = (*border, 255)
            px[b, i] = (*border, 255)
            px[size - 1 - b, i] = (*border, 255)
    if highlight is not None:
        lo = border_px
        hi = size - 1 - border_px
        for i in range(lo, hi + 1):
            for h in range(highlight_px):
                px[i, lo + h] = (*highlight, 255)
                px[lo + h, i] = (*highlight, 255)
    return img


def generate(spec: dict | None = None, write: bool = True):
    """Returns (tiles: dict[name -> Image], manifest: dict)."""
    pal = get_palette()
    spec = spec or load_spec()
    sb = spec["stylebox"]
    size = sb["size"]
    border_px = sb["border_px"]
    highlight_px = sb["inner_highlight_px"]

    panel = role_rgb(pal, spec, "panel")
    bg = role_rgb(pal, spec, "bg")
    border = role_rgb(pal, spec, "border")
    accent = role_rgb(pal, spec, "accent")
    text = role_rgb(pal, spec, "text")
    text_dim = role_rgb(pal, spec, "text_dim")
    mana = role_rgb(pal, spec, "mana")

    # Button states vary fill/border inside the same ramp discipline.
    hover_fill = pal.shade(*spec["roles"]["panel"], +1) if hasattr(pal, "shade") else panel
    tiles = {
        "button_normal": draw_ninepatch(size, panel, border, text_dim,
                                        border_px, highlight_px),
        "button_hover": draw_ninepatch(size, hover_fill, text_dim, text,
                                       border_px, highlight_px),
        "button_pressed": draw_ninepatch(size, bg, border, None,
                                         border_px, highlight_px),
        "button_disabled": draw_ninepatch(size, bg, bg, None,
                                          border_px, highlight_px),
        "button_focus": draw_ninepatch(size, panel, accent, text_dim,
                                       border_px, highlight_px),
        "panel": draw_ninepatch(size, panel, border, None, border_px, highlight_px),
        "progress_bg": draw_ninepatch(size, bg, border, None, border_px, highlight_px),
        "progress_fill": draw_ninepatch(size, mana, border, None,
                                        border_px, highlight_px),
    }

    # Element panel variants (arch-ui-followups §A): neutral geometry, element
    # accent border + one-step-darker inner highlight, resolved via the
    # palette.json elements table -- the single element-colour source.
    element_names = ["fire", "frost", "storm", "venom", "stone"]
    element_accent = {}
    for name in element_names:
        el = pal.element(name)
        accent = pal.get(el["primary"], el["accent_index"])
        el_highlight = pal.shade(el["primary"], el["accent_index"], -1)
        tiles[f"panel_{name}"] = draw_ninepatch(
            size, panel, accent, el_highlight, border_px, highlight_px)
        element_accent[name] = accent

    lint_theme(pal, tiles)

    margins = [sb["corner_margin"]] * 4
    content = sb["content_margin"]

    def sb_entry(kind, item, name):
        return {
            "type": kind,
            "item": item,
            "png": f"res://assets/ui/chrome/{name}.png",
            "margins": margins,
            "content_margins": content,
        }

    manifest = {
        "preset": spec["preset"],
        "fonts": {
            role: {
                "path": f"res://assets/fonts/{font['file']}.ttf",
                "size": font["size"],
            }
            for role, font in spec["fonts"].items()
        },
        "colors": {
            "Label/font_color": rgb_to_hex(text),
            "Button/font_color": rgb_to_hex(text),
            "Button/font_disabled_color": rgb_to_hex(text_dim),
            "TabContainer/font_selected_color": rgb_to_hex(text),
            "TabContainer/font_unselected_color": rgb_to_hex(text_dim),
            # Slash-less keys are runtime lookups (ThemeService), skipped by
            # ThemeBuilder's Type/item colour loop.
            **{f"element_{name}": rgb_to_hex(element_accent[name])
               for name in element_names},
            **{f"rarity_{name}": rgb_to_hex(pal.get(ramp, index))
               for name, (ramp, index) in spec["rarity_roles"].items()},
        },
        "styleboxes": [
            sb_entry("Button", "normal", "button_normal"),
            sb_entry("Button", "hover", "button_hover"),
            sb_entry("Button", "pressed", "button_pressed"),
            sb_entry("Button", "disabled", "button_disabled"),
            sb_entry("Button", "focus", "button_focus"),
            sb_entry("PanelContainer", "panel", "panel"),
            sb_entry("ProgressBar", "background", "progress_bg"),
            sb_entry("ProgressBar", "fill", "progress_fill"),
        ],
        "type_variations": [
            {"variation": "DeployCard", "base": "Button"},
            {"variation": "ManaBar", "base": "ProgressBar"},
        ],
        "constants": {},
    }

    # Per-element deploy-card variations: the panel variant tile overrides the
    # Button "normal" item; other states inherit the neutral chrome.
    for name in element_names:
        variation = f"DeployCard{name.capitalize()}"
        manifest["styleboxes"].append(sb_entry(variation, "normal", f"panel_{name}"))
        manifest["type_variations"].append({"variation": variation, "base": "Button"})

    # Icon set (arch-ui-followups §B): icons.py owns the glyph bake; the
    # manifest stays single-writer by composing its entries here.
    _, manifest["icons"] = generate_icons(pal, spec, write=write)

    if write:
        CHROME_DIR.mkdir(parents=True, exist_ok=True)
        for name, img in tiles.items():
            img.save(CHROME_DIR / f"{name}.png")
        MANIFEST_PATH.write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return tiles, manifest


def lint_theme(pal, tiles: dict) -> None:
    """The palette contract: every opaque pixel is an exact palette color."""
    for name, img in tiles.items():
        for rgba in img.getdata():
            if rgba[3] == 0:
                continue
            if not pal.is_on_palette(rgba[:3]):
                raise ValueError(
                    f"theme tile '{name}' contains off-palette pixel {rgba[:3]}")


if __name__ == "__main__":
    generated, manifest = generate()
    print(f"chrome tiles: {len(generated)} -> {CHROME_DIR}")
    print(f"manifest -> {MANIFEST_PATH}")
