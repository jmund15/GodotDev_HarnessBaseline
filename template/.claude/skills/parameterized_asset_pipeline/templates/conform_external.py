"""TEMPLATE — parameterized_asset_pipeline (do not run in the baseline repo).

External-asset conform pipeline: posterize-to-ramp + relaxed lint profile + CREDITS ledger.
Adapt: source/output paths; keep the ledger gate.

Provenance: the reference project's art_pipeline (its test suites
verify the original of this file).
"""
# --- original file follows ---
"""conform_external.py -- curated external sprite sheet -> DW-compliant sheet.

Companion to ``generate_unit.py``. Where generate_unit DRAWS palette-correct
pixels from templates, conform_external PROJECTS arbitrary external pixel art
(e.g. a CC0 Luiz Melo sheet) onto the same contract: Resurrect-64 palette,
1px-feet-on-ground-line framing, and a tick-faithful manifest. The external
RGB never reaches the canvas -- only its luminance *level* (which ramp step) and
hue *material* (which ramp) survive, so palette compliance holds by construction
(art-direction.md section 3: ramp-lookup-only, no computed shades).

Pipeline (art-pipeline-uplift.md Track 2):
  1. posterize all opaque source pixels to N luminance levels (1-D k-means)
  2. map each level -> ONE ramp step per material (palette-compliant)
  3. hard-threshold alpha to 0/255
  4. re-slice source rows into the DW one-animation-per-row layout
  5. re-canvas each frame to the target 32x32 / 48x64 (nearest-neighbor)
  6. re-anchor the lowest opaque row to the ground line (frame_h-3 grounded,
     above it for airborne)
  7. emit a manifest whose attack contact_frame maps onto the foreswing share
     (NOT the source pack's native timing) via generate_unit.contact_frame_index

Mapping dict / JSON (the per-source spec):
    {
      "name": "luiz_wizard",
      "typeclass": "melee_biped",      # body_size floors come from this
      "element": "fire",
      "canvas": "32x32",               # target canvas (32x32 or 48x64)
      "levels": 4,                     # posterize value levels
      "materials": [                   # 1 entry -> whole body on one ramp;
        {"ramp": "fire"}               # >1 -> nearest-hue assignment per pixel
        # {"ramp": "tan", "hue": 30}, {"ramp": "frost", "hue": 210},
        # {"ramp": "mauve_grey", "neutral": true}  # desaturated px -> here
      ],
      "neutral_sat_max": 0.18,         # optional; sat below this -> neutral mat
      "body_fill": 0.85,               # optional; body height as fraction of the
                                       # canvas (decouples hero size from class)
      "body_height": 52,              # optional; absolute px, wins over body_fill
      "source_sheet": "external/sources/wizard.png",  # path (CLI only)
      "source_grid": {"frame_w": 64, "frame_h": 64},  # source cell size
      "rows": [                        # source row -> target animation
        {"animation": "idle",   "src_row": 0, "frames": 4, "fps": 7,  "loop": true},
        {"animation": "walk",   "src_row": 1, "frames": 8, "fps": 10, "loop": true},
        {"animation": "attack", "src_row": 2, "frames": 8, "fps": 12, "loop": false},
        {"animation": "death",  "src_row": 4, "frames": 7, "fps": 10, "loop": false}
      ],
      "foreswing_ticks": 8,
      "backswing_ticks": 12,
      "airborne": false                # optional; defaults true for aerial_flyer
    }

Determinism: no randomness anywhere (k-means uses a fixed percentile init, ties
break to the lower index, empty clusters keep their centroid) -- identical
source + mapping => identical bytes.

CLI:
    python art_pipeline/conform_external.py <mapping.json> [--outdir DIR]
"""

from __future__ import annotations

import argparse
import colorsys
import json
import sys
from pathlib import Path

from PIL import Image

from collections import Counter

from generate_unit import contact_frame_index
from lint import CLASS_BODY_RULES, MIN_CLUSTER
from palette import Palette, get_palette
from skeletons import ground_row, parse_canvas

DEFAULT_OUTDIR = Path(__file__).resolve().parent / "output" / "external"
ALPHA_THRESHOLD = 128   # source alpha >= this -> opaque; below -> transparent
AIRBORNE_MARGIN = 4     # airborne feet sit this many px above the ground line


def _luma(rgb) -> float:
    """Rec.601 luma -- the value axis the posterize quantizes."""
    r, g, b = rgb
    return 0.299 * r + 0.587 * g + 0.114 * b


def kmeans_1d(values: list[float], k: int, iters: int = 30) -> list[float]:
    """Deterministic 1-D Lloyd's on luminance. Returns k ascending centers.

    Fixed percentile init; assignment ties break to the lower index; an empty
    cluster keeps its previous centroid. No RNG -> byte-stable.
    """
    vals = sorted(values)
    if not vals:
        return [0.0] * max(1, k)
    if k <= 1:
        return [sum(vals) / len(vals)]
    uniq = sorted(set(vals))
    if len(uniq) <= k:
        return (uniq + [uniq[-1]] * k)[:k]
    lo, hi = vals[0], vals[-1]
    centers = [lo + (hi - lo) * (i + 0.5) / k for i in range(k)]
    for _ in range(iters):
        buckets: list[list[float]] = [[] for _ in range(k)]
        for v in vals:
            best, bestd = 0, abs(v - centers[0])
            for ci in range(1, k):
                d = abs(v - centers[ci])
                if d < bestd:   # strict < keeps the lower index on a tie
                    best, bestd = ci, d
            buckets[best].append(v)
        new_centers = [sum(buckets[ci]) / len(buckets[ci]) if buckets[ci]
                       else centers[ci] for ci in range(k)]
        if new_centers == centers:
            break
        centers = new_centers
    return sorted(centers)


def _level_of(lum: float, centers: list[float]) -> int:
    """Nearest center index (tie -> lower index). centers are ascending."""
    best, bestd = 0, abs(lum - centers[0])
    for ci in range(1, len(centers)):
        d = abs(lum - centers[ci])
        if d < bestd:
            best, bestd = ci, d
    return best


def _level_to_index(level: int, levels: int, ramp_len: int) -> int:
    """Map posterize level 0..levels-1 across ramp steps 0..ramp_len-1."""
    if levels <= 1 or ramp_len <= 1:
        return 0
    idx = round(level / (levels - 1) * (ramp_len - 1))
    return max(0, min(ramp_len - 1, idx))


def _material_for(rgb, materials: list[dict], neutral: dict | None = None,
                  neutral_sat_max: float = 0.18) -> dict:
    """Pick the ramp for one source pixel.

    A desaturated pixel (near-white/grey: book pages, beard, highlights) has no
    meaningful hue, so hue-matching it against chromatic materials misfiles it
    onto whatever anchor is nearest 0deg (skin) -- which fused the wizard's book
    into his face. When a ``neutral`` material is declared, pixels below
    ``neutral_sat_max`` saturation route there instead. Otherwise: single
    material -> that ramp; else nearest chromatic material by circular hue."""
    if neutral is not None and colorsys.rgb_to_hsv(*(c / 255 for c in rgb))[1] < neutral_sat_max:
        return neutral
    if len(materials) == 1:
        return materials[0]
    hue = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))[0] * 360
    best, bestd = materials[0], 1e9
    for m in materials:
        if "hue" not in m:
            continue
        d = abs(((hue - m["hue"] + 180) % 360) - 180)
        if d < bestd:
            best, bestd = m, d
    return best


def _target_body_height(typeclass: str, ground_y: int, airborne: bool,
                        override_h: int | None = None,
                        fill: float | None = None) -> int:
    """Visual body-height target, clamped to the space above the anchor line.

    Precedence: explicit ``body_height`` px > ``body_fill`` (fraction of the
    available height) > the typeclass band mid. The two override knobs decouple
    hero fidelity from the gameplay typeclass -- a hero can fill a tall canvas
    without being pinned to the small-unit band."""
    avail = ground_y + 1 - (AIRBORNE_MARGIN if airborne else 0)
    if override_h is not None:
        th = int(override_h)
    elif fill is not None:
        th = round(float(fill) * avail)
    else:
        rules = CLASS_BODY_RULES.get(typeclass, {})
        if "min_h" in rules and "max_h" in rules:
            th = (rules["min_h"] + rules["max_h"]) // 2
        elif "min_h" in rules:
            th = rules["min_h"] + 2
        else:
            th = avail
    return max(1, min(th, avail))


def _denoise_orphans(cell: Image.Image, min_cluster: int = MIN_CLUSTER) -> Image.Image:
    """Despeckle a palettized cell so it satisfies lint's orphan_pixels (HARD).

    Nearest-neighbor downscaling of detailed external art fragments thin features
    (staffs, glints, VFX motes) into sub-min_cluster exact-color regions. Each
    such region is absorbed into its dominant 8-neighbor color, or deleted when it
    floats in transparency (a stray downscale artifact). Iterates to a fixpoint.
    Palette-safe (only reassigns colors already present) and deterministic
    (regions and tie-broken neighbor colors are taken in sorted order)."""
    px = cell.load()
    w, h = cell.size
    for _ in range(32):                       # fixpoint cap; merges converge fast
        opaque = {(x, y): px[x, y][:3]
                  for y in range(h) for x in range(w) if px[x, y][3] == 255}
        seen: set[tuple[int, int]] = set()
        changed = False
        for start in sorted(opaque):          # deterministic region order
            if start in seen:
                continue
            rgb = opaque[start]
            region = [start]
            seen.add(start)
            qi = 0
            while qi < len(region):
                cx, cy = region[qi]
                qi += 1
                for nx in (cx - 1, cx, cx + 1):
                    for ny in (cy - 1, cy, cy + 1):
                        p = (nx, ny)
                        if p not in seen and opaque.get(p) == rgb:
                            seen.add(p)
                            region.append(p)
            if len(region) >= min_cluster:
                continue
            nbr: Counter = Counter()
            for (cx, cy) in region:
                for nx in (cx - 1, cx, cx + 1):
                    for ny in (cy - 1, cy, cy + 1):
                        c = opaque.get((nx, ny))
                        if c is not None and c != rgb:
                            nbr[c] += 1
            if nbr:
                best = max(sorted(nbr), key=lambda c: nbr[c])  # tie -> lowest color
                for (cx, cy) in region:
                    px[cx, cy] = (*best, 255)
                    opaque[(cx, cy)] = best   # live update: adjacent specks
            else:                             # collapse together, never swap
                for (cx, cy) in region:       # isolated speck -> drop
                    px[cx, cy] = (0, 0, 0, 0)
                    opaque.pop((cx, cy), None)
            changed = True
        if not changed:
            break
    return cell


def _conform_cell(src: Image.Image, sx: int, sy: int, cw: int, ch: int,
                  centers, materials, levels, pal, ramps_used,
                  W, H, ground_y, airborne, typeclass,
                  neutral=None, neutral_sat_max=0.18, override_h=None, fill=None):
    """Posterize+ramp-map+threshold+scale+anchor ONE source cell -> a W x H
    RGBA cell image. Returns (cell_img, (body_w, body_h)). Raises on empty."""
    cell = src.crop((sx, sy, sx + cw, sy + ch)).convert("RGBA")
    cpx = cell.load()
    # threshold alpha + palettize, collecting opaque source points
    pts: list[tuple[int, int, tuple[int, int, int]]] = []
    for cy in range(ch):
        for cx in range(cw):
            r, g, b, a = cpx[cx, cy]
            if a < ALPHA_THRESHOLD:
                continue
            level = _level_of(_luma((r, g, b)), centers)
            mat = _material_for((r, g, b), materials, neutral, neutral_sat_max)
            ramp = mat["ramp"]
            idx = _level_to_index(level, levels, pal.ramp_len(ramp))
            ramps_used.add(ramp)
            pts.append((cx, cy, pal.get(ramp, idx)))
    if not pts:
        raise ValueError("empty source cell (no opaque pixels above threshold)")

    minx = min(p[0] for p in pts)
    maxx = max(p[0] for p in pts)
    miny = min(p[1] for p in pts)
    maxy = max(p[1] for p in pts)
    sw, sh = maxx - minx + 1, maxy - miny + 1

    # palettized crop at source resolution
    crop = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
    crpx = crop.load()
    for (cx, cy, rgb) in pts:
        crpx[cx - minx, cy - miny] = (*rgb, 255)

    # fit-scale to the body-height target, width-bounded by the canvas
    th = _target_body_height(typeclass, ground_y, airborne, override_h, fill)
    scale = th / sh
    if sw * scale > W:
        scale = W / sw
    ow = max(1, round(sw * scale))
    oh = max(1, round(sh * scale))
    scaled = crop.resize((ow, oh), Image.NEAREST)  # NEAREST keeps binary alpha
    scaled = _denoise_orphans(scaled)              # despeckle BEFORE anchoring so
                                                   # dropping a foot speck can't
                                                   # lift the sprite off the line

    # re-anchor: lowest opaque row -> the ground line (or above, if airborne)
    spx = scaled.load()
    opaque_rows = [y for y in range(oh) for x in range(ow) if spx[x, y][3] == 255]
    if not opaque_rows:
        raise ValueError("source cell emptied by despeckle")
    bottom = max(opaque_rows)
    target_bottom = ground_y - (AIRBORNE_MARGIN if airborne else 0)
    offx = (W - ow) // 2
    offy = target_bottom - bottom

    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    out.paste(scaled, (offx, offy), scaled)   # paste clips to bounds
    return out, (ow, oh)


def conform_external(mapping: dict, src_sheet: str | Path,
                     outdir: str | Path | None = None,
                     pal: Palette | None = None) -> dict:
    """Conform ``src_sheet`` per ``mapping`` -> DW sheet + manifest. Returns
    paths + the manifest dict."""
    pal = pal or get_palette()
    outdir = Path(outdir) if outdir else DEFAULT_OUTDIR
    outdir.mkdir(parents=True, exist_ok=True)

    name = mapping["name"]
    typeclass = mapping["typeclass"]
    W, H = parse_canvas(mapping.get("canvas", "32x32"))
    levels = int(mapping.get("levels", 4))
    materials = mapping["materials"]
    neutral = next((m for m in materials if m.get("neutral")), None)
    chromatic = [m for m in materials if not m.get("neutral")] or materials
    neutral_sat_max = float(mapping.get("neutral_sat_max", 0.18))
    body_h = mapping.get("body_height")
    body_fill = mapping.get("body_fill")
    grid = mapping["source_grid"]
    cw, ch = int(grid["frame_w"]), int(grid["frame_h"])
    rows = mapping["rows"]
    fore = int(mapping["foreswing_ticks"])
    back = int(mapping["backswing_ticks"])
    airborne = bool(mapping.get("airborne", typeclass == "aerial_flyer"))
    gy = ground_row(H)

    src = Image.open(src_sheet).convert("RGBA")

    # (1) global posterize: k-means over EVERY opaque source pixel's luminance,
    # so the value->ramp mapping is consistent across all frames.
    spx = src.load()
    lums = [_luma(spx[x, y][:3])
            for y in range(src.size[1]) for x in range(src.size[0])
            if spx[x, y][3] >= ALPHA_THRESHOLD]
    centers = kmeans_1d(lums, levels)

    ramps_used: set[str] = set()
    body_sizes: dict[str, list[tuple[int, int]]] = {}
    max_frames = max(int(r["frames"]) for r in rows)
    sheet = Image.new("RGBA", (W * max_frames, H * len(rows)), (0, 0, 0, 0))

    for out_row, r in enumerate(rows):
        anim = r["animation"]
        src_row = int(r["src_row"])
        for col in range(int(r["frames"])):
            sx, sy = col * cw, src_row * ch
            cell_img, (bw, bh) = _conform_cell(
                src, sx, sy, cw, ch, centers, chromatic, levels, pal,
                ramps_used, W, H, gy, airborne, typeclass,
                neutral, neutral_sat_max, body_h, body_fill)
            body_sizes.setdefault(anim, []).append((bw, bh))
            sheet.alpha_composite(cell_img, (col * W, out_row * H))

    # body mass: max over idle + the locomotion animation (mirrors generate_unit)
    move = "fly" if airborne else "walk"
    sizes = body_sizes.get("idle", []) + body_sizes.get(move, [])
    if not sizes:                       # fall back to whatever rows exist
        sizes = [s for v in body_sizes.values() for s in v]
    body_size = {"width": max(s[0] for s in sizes),
                 "height": max(s[1] for s in sizes)}
    if airborne:
        body_size["wingspan"] = max(s[0] for s in body_sizes.get(move, sizes))

    attack_frames = next((int(r["frames"]) for r in rows
                          if r["animation"] == "attack"), 0)
    contact = contact_frame_index(fore, back, attack_frames) if attack_frames else 0

    animations = []
    for out_row, r in enumerate(rows):
        entry = {"name": r["animation"], "row": out_row, "frames": int(r["frames"]),
                 "fps": int(r.get("fps", 10)), "loop": bool(r.get("loop", False))}
        if r["animation"] == "attack":
            entry["contact_frame"] = int(r.get("contact_frame", contact))
        animations.append(entry)

    manifest = {
        "name": name,
        "typeclass": typeclass,
        "element": mapping.get("element", "fire"),
        "size_class": mapping.get("size_class", "medium"),
        "source": "external",
        "frame_w": W,
        "frame_h": H,
        "animations": animations,
        "foreswing_ticks": fore,
        "backswing_ticks": back,
        "ramps_used": sorted(ramps_used),
        "pivot": "bottom_center",
        "ground_y": gy,
        "facing": "right",
        "body_size": body_size,
        "lint": {
            "whitelist_colors": [],
            "prop_colors": [],
            "airborne_animations": [a["name"] for a in animations] if airborne else [],
        },
    }

    sheet_path = outdir / f"{name}_sheet.png"
    sheet.save(sheet_path)
    manifest_path = outdir / f"{name}.manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    return {"sheet": str(sheet_path), "manifest": str(manifest_path), "data": manifest}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Conform an external sprite sheet to the DW pipeline.")
    ap.add_argument("mapping", help="path to a conform mapping .json file")
    ap.add_argument("--outdir", default=None, help=f"output directory (default {DEFAULT_OUTDIR})")
    ap.add_argument("--source", default=None, help="override mapping.source_sheet")
    args = ap.parse_args(argv)

    with open(args.mapping, "r", encoding="utf-8") as f:
        mapping = json.load(f)
    src = args.source or mapping.get("source_sheet")
    if not src:
        print("error: no source sheet (mapping.source_sheet or --source)", file=sys.stderr)
        return 2
    src = Path(src)
    if not src.is_absolute():
        src = Path(args.mapping).resolve().parent / src
    if not src.exists():
        print(f"error: no such source sheet: {src}", file=sys.stderr)
        return 2

    result = conform_external(mapping, src, args.outdir)
    print(f"sheet:    {result['sheet']}")
    print(f"manifest: {result['manifest']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
