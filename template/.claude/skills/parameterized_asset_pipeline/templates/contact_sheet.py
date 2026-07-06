"""TEMPLATE — parameterized_asset_pipeline (do not run in the baseline repo).

Contact-sheet compose/review helper (verbatim from the reference project).
Adapt: sheet layout constants to your canvas sizes.

Provenance: the reference project's art_pipeline (its test suites
verify the original of this file).
"""
# --- original file follows ---
"""contact_sheet.py -- review composites over the battle background.

Art-direction.md section 9 rule 12: every sheet is judged at 1x and 3x over a
real battle backdrop, never as an isolated PNG. This module produces both
review PNGs: all animation rows visible, labeled with unit name + animation
metadata, attack contact frame marked.

CLI:
    python art_pipeline/contact_sheet.py <sheet.png> [--manifest m.json]
        [--bg battle_bg.png] [--outdir DIR]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import backgrounds
from palette import get_palette

DEFAULT_OUTDIR = Path(__file__).resolve().parent / "output" / "contact_sheets"


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # older Pillow
        return ImageFont.load_default()


def _label(draw: ImageDraw.ImageDraw, xy, text, fill, shadow, font):
    draw.text((xy[0] + 1, xy[1] + 1), text, fill=shadow, font=font)
    draw.text(xy, text, fill=fill, font=font)


def _compose(sheet: Image.Image, manifest: dict, bg: Image.Image,
             scale: int, canvas_size: tuple[int, int]) -> Image.Image:
    pal = get_palette()
    text_rgb = pal.get("mauve_grey", 3)
    shadow_rgb = pal.get("ink", 0)
    mark_rgb = pal.get("fire", 4)

    canvas = bg.resize(canvas_size, Image.NEAREST).convert("RGBA")
    draw = ImageDraw.Draw(canvas)
    font = _font(10 * scale if scale > 1 else 11)
    small = _font(8 * scale if scale > 1 else 10)

    fw, fh = manifest["frame_w"], manifest["frame_h"]
    title = (f"{manifest['name']}  [{manifest['typeclass']} / {manifest['element']}]  "
             f"{scale}x   fore {manifest['foreswing_ticks']}t / back {manifest['backswing_ticks']}t")
    _label(draw, (8 * scale, 5 * scale), title, text_rgb, shadow_rgb, font)

    label_w = 92 * scale + 40
    gap = 6 * scale
    y = 22 * scale
    for anim in manifest["animations"]:
        meta = f"{anim['name']} {anim['frames']}f @{anim['fps']}"
        if anim.get("loop"):
            meta += " loop"
        if "contact_frame" in anim:
            meta += f" c@{anim['contact_frame']}"
        _label(draw, (8 * scale, y + (fh * scale) // 2 - 5 * scale), meta,
               text_rgb, shadow_rgb, small)
        row = anim["row"]
        for col in range(anim["frames"]):
            cell = sheet.crop((col * fw, row * fh, (col + 1) * fw, (row + 1) * fh))
            if scale != 1:
                cell = cell.resize((fw * scale, fh * scale), Image.NEAREST)
            x = label_w + col * (fw * scale + gap)
            canvas.alpha_composite(cell, (x, y))
            if anim.get("contact_frame") == col:
                draw.rectangle([x, y + fh * scale + 1, x + fw * scale - 1,
                                y + fh * scale + 1 + scale], fill=mark_rgb)
        y += fh * scale + 8 * scale
    return canvas


def make_contact_sheets(sheet_path: str | Path, manifest_path: str | Path | None = None,
                        bg_path: str | Path | None = None,
                        outdir: str | Path | None = None) -> list[str]:
    """Returns [path_1x, path_3x]."""
    sheet_path = Path(sheet_path)
    if manifest_path is None:
        stem = sheet_path.stem.removesuffix("_sheet")
        manifest_path = sheet_path.parent / f"{stem}.manifest.json"
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    if bg_path is None:
        bg_path = backgrounds.DEFAULT_OUT
        if not Path(bg_path).exists():
            backgrounds.generate_battle_background(bg_path)
    bg = Image.open(bg_path).convert("RGB")
    sheet = Image.open(sheet_path).convert("RGBA")

    outdir = Path(outdir) if outdir else DEFAULT_OUTDIR
    outdir.mkdir(parents=True, exist_ok=True)
    name = manifest["name"]

    out = []
    for scale in (1, 3):
        rows = len(manifest["animations"])
        fh = manifest["frame_h"]
        needed_h = 22 * scale + rows * (fh * scale + 8 * scale) + 12 * scale
        canvas_w = max(640, 640 * scale // 1) if scale == 1 else 1280
        canvas_h = max(360 if scale == 1 else 720, needed_h)
        img = _compose(sheet, manifest, bg, scale, (canvas_w, canvas_h))
        p = outdir / f"{name}_contact_{scale}x.png"
        img.save(p)
        out.append(str(p))
    return out


def make_silhouette_strip(units: list[tuple[str, str]], outdir: str | Path | None = None,
                          name: str = "silhouette_strip") -> list[str]:
    """1-bit silhouette comparison strip (art rule 1 gate at 1x).

    ``units`` is a list of (sheet_path, manifest_path). Two rows per unit
    column: idle frame 0 and move (walk/fly) frame 0, rendered white-on-ink.
    The TypeClass silhouettes (melee / ranged / sniper / aerial / siege /
    support) must be mutually distinguishable at 1x; the 4x copy exists for
    review notes only. Returns [path_1x, path_4x].
    """
    pal = get_palette()
    ink = pal.get("ink", 0)
    cells = []  # (unit_name, [idle_img, move_img])
    max_fw = max_fh = 0
    for sheet_path, manifest_path in units:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        sheet = Image.open(sheet_path).convert("RGBA")
        fw, fh = manifest["frame_w"], manifest["frame_h"]
        max_fw, max_fh = max(max_fw, fw), max(max_fh, fh)
        frames = []
        for anim_row in (0, 1):  # idle row, move row
            cell = sheet.crop((0, anim_row * fh, fw, (anim_row + 1) * fh))
            mask = cell.split()[3].point(lambda a: 255 if a > 0 else 0)
            sil = Image.new("RGBA", (fw, fh), ink + (255,))
            sil.paste((255, 255, 255, 255), (0, 0), mask)
            frames.append(sil)
        cells.append((manifest["name"], frames))

    gap = 4
    strip_w = len(cells) * (max_fw + gap) + gap
    strip_h = 2 * (max_fh + gap) + gap
    strip = Image.new("RGBA", (strip_w, strip_h), ink + (255,))
    for col, (_, frames) in enumerate(cells):
        for row, sil in enumerate(frames):
            strip.alpha_composite(sil, (gap + col * (max_fw + gap),
                                        gap + row * (max_fh + gap)))

    outdir = Path(outdir) if outdir else DEFAULT_OUTDIR
    outdir.mkdir(parents=True, exist_ok=True)
    out = []
    p1 = outdir / f"{name}_1x.png"
    strip.save(p1)
    out.append(str(p1))

    big = strip.resize((strip_w * 4, strip_h * 4), Image.NEAREST)
    labeled = Image.new("RGBA", (big.width, big.height + 14), ink + (255,))
    labeled.alpha_composite(big, (0, 0))
    draw = ImageDraw.Draw(labeled)
    font = _font(10)
    for col, (unit_name, _) in enumerate(cells):
        draw.text((4 * (gap + col * (max_fw + gap)), big.height + 1),
                  unit_name[:14], fill=pal.get("mauve_grey", 3), font=font)
    p4 = outdir / f"{name}_4x.png"
    labeled.save(p4)
    out.append(str(p4))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Composite a unit sheet over the battle background at 1x and 3x.")
    ap.add_argument("sheet", help="path to <name>_sheet.png")
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--bg", default=None, help="battle background png (generated if missing)")
    ap.add_argument("--outdir", default=None, help=f"output dir (default {DEFAULT_OUTDIR})")
    args = ap.parse_args(argv)
    for p in make_contact_sheets(args.sheet, args.manifest, args.bg, args.outdir):
        print(f"contact sheet: {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
