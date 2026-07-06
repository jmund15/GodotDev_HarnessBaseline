"""TEMPLATE — parameterized_asset_pipeline (do not run in the baseline repo).

Procedural UI icon generator: 16-grid glyphs as char->(ramp,index) pixel
grids (palette lookups ONLY), baked to integer-scaled tiers, with the
sourcing-dial attribution ledger gate.

Adapt: author your glyph grids (three examples kept below); wire
generate_icons() into your theme_gen so the manifest stays single-writer.
Provenance: the reference project's art_pipeline/icons.py (20-glyph reference
implementation, verified by test_icons.py there).
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from palette import get_palette

ROOT = Path(__file__).resolve().parent
ICON_SPEC_PATH = ROOT / "icon_spec.json"
PROJECT = ROOT.parent
ICONS_DIR = PROJECT / "assets" / "ui" / "icons"
CREDITS_PATH = ICONS_DIR / "CREDITS.md"

CREDITS_STUB = """# UI Icon Credits

Icons with `source` != "procedural" in `icon_spec.json` MUST add an entry
here naming the icon, site, author, and license -- the bake fails otherwise.
"""


def load_icon_spec() -> dict:
    return json.loads(ICON_SPEC_PATH.read_text(encoding="utf-8"))


def check_sources(spec: dict, credits_text: str) -> None:
    """The attribution ledger gate: external sourcing without a CREDITS entry
    fails the bake."""
    for icon in spec["icons"]:
        source = icon.get("source", "procedural")
        if source == "procedural":
            continue
        if icon["name"] not in credits_text:
            raise ValueError(
                f"icon '{icon['name']}' has an external source but no "
                f"CREDITS.md entry -- attribution is mandatory")


def _draw(grid: list[str], cmap: dict) -> Image.Image:
    if len(grid) != 16:
        raise ValueError(f"glyph grid has {len(grid)} rows, want 16")
    img = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    px = img.load()
    for y, row in enumerate(grid):
        if len(row) != 16:
            raise ValueError(f"glyph row {y} has {len(row)} chars, want 16")
        for x, ch in enumerate(row):
            if ch != ".":
                px[x, y] = (*cmap[ch], 255)
    return img


# ------------------------------------------------------------ example glyphs
CROSS = [
    "................",
    "................",
    "..31........13..",
    "..331......133..",
    "...331....133...",
    "....331..133....",
    ".....331133.....",
    "......3333......",
    "......3333......",
    ".....331133.....",
    "....331..133....",
    "...331....133...",
    "..331......133..",
    "..31........13..",
    "................",
    "................",
]

CHEVRON = [
    "................",
    "................",
    "..........33....",
    ".........333....",
    "........333.....",
    ".......333......",
    "......333.......",
    ".....333........",
    ".....333........",
    "......333.......",
    ".......333......",
    "........333.....",
    ".........333....",
    "..........33....",
    "................",
    "................",
]

PIP = [
    "................",
    "................",
    ".......r........",
    "......rhr.......",
    ".....rhhbr......",
    "....rhhbbbr.....",
    "...rhhbbbbbr....",
    "..rhbbbbbbbbr...",
    "...rbbbbbbbr....",
    "....rbbbbbr.....",
    ".....rbbbr......",
    "......rbr.......",
    ".......r........",
    "................",
    "................",
    "................",
]


def _masters(pal, theme_spec: dict) -> dict:
    """All 16px master glyphs. AUTHOR YOUR SET HERE — colours resolved via
    palette lookups only (ramps from your palette.json / theme_spec maps)."""
    neutral = "mauve_grey"  # pick your neutral ramp
    return {
        "nav_close": _draw(CROSS, {
            "1": pal.get(neutral, 1),
            "3": pal.get(neutral, 3),
        }),
        "nav_back": _draw(CHEVRON, {"3": pal.get(neutral, 3)}),
        # Tinted-pip family pattern: one grid, per-variant charmaps from a
        # theme_spec role map (rarity_roles in the DW reference):
        # for key, (ramp, idx) in theme_spec["rarity_roles"].items():
        #     masters[f"pip_{key}"] = _draw(PIP, {
        #         "r": pal.shade(ramp, idx, -1),
        #         "b": pal.get(ramp, idx),
        #         "h": pal.shade(ramp, idx, +1),
        #     })
    }


def generate_icons(pal, theme_spec: dict, spec: dict | None = None,
                   write: bool = True):
    """Returns (tiles: dict[f"{name}_{tier}" -> Image], manifest entries)."""
    spec = spec or load_icon_spec()
    credits_text = (
        CREDITS_PATH.read_text(encoding="utf-8") if CREDITS_PATH.exists() else "")
    check_sources(spec, credits_text)

    masters = _masters(pal, theme_spec)
    spec_names = {icon["name"] for icon in spec["icons"]}
    if spec_names != set(masters):
        missing = spec_names.symmetric_difference(masters)
        raise ValueError(f"icon_spec/glyph mismatch: {sorted(missing)}")

    tiles: dict[str, Image.Image] = {}
    entries = []
    for name in sorted(spec_names):
        entry = {"name": name, "tiers": {}}
        for tier in spec["tiers"]:
            img = (masters[name] if tier == spec["grid"]
                   else masters[name].resize((tier, tier), Image.NEAREST))
            tiles[f"{name}_{tier}"] = img
            entry["tiers"][str(tier)] = f"res://assets/ui/icons/{name}_{tier}.png"
        entries.append(entry)

    for key, img in tiles.items():
        for rgba in img.getdata():
            if rgba[3] != 0 and not pal.is_on_palette(rgba[:3]):
                raise ValueError(f"icon '{key}' off-palette pixel {rgba[:3]}")

    if write:
        ICONS_DIR.mkdir(parents=True, exist_ok=True)
        for key, img in tiles.items():
            img.save(ICONS_DIR / f"{key}.png")
        if not CREDITS_PATH.exists():
            CREDITS_PATH.write_text(CREDITS_STUB, encoding="utf-8")
    return tiles, entries
