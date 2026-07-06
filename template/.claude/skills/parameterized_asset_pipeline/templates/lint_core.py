"""TEMPLATE — parameterized_asset_pipeline (do not run in the baseline repo).

Core lint gates every generated asset must pass, distilled from the
reference-project pipeline (its full lint.py adds project-specific role/whitelist
caps on top — add your own equivalents as your style spec demands).

Adapt: banding threshold factor if your resolution axis differs; contrast
floor per your readability target.
"""

from __future__ import annotations

from PIL import Image


def lint_on_palette(pal, name: str, img: Image.Image) -> None:
    """The palette contract: every opaque pixel is an exact palette colour."""
    for rgba in img.getdata():
        if rgba[3] == 0:
            continue
        if not pal.is_on_palette(rgba[:3]):
            raise ValueError(f"'{name}' contains off-palette pixel {rgba[:3]}")


def lint_banding(name: str, img: Image.Image, max_run_factor: float = 6 / 32) -> None:
    """No axis-aligned same-colour runs longer than round(factor * height) —
    flat banding reads as unfinished at chunky-pixel scale."""
    width, height = img.size
    max_run = max(3, round(max_run_factor * height))
    px = img.load()
    for y in range(height):
        run, prev = 0, None
        for x in range(width):
            cur = px[x, y]
            if cur[3] != 0 and cur == prev:
                run += 1
                if run > max_run:
                    raise ValueError(
                        f"'{name}' horizontal banding run > {max_run} at y={y}")
            else:
                run = 0
            prev = cur


def lint_ninepatch_margins(name: str, size: int, margins: list[int]) -> None:
    """9-patch margins must leave a live centre (left+right and top+bottom
    each strictly under the texture size)."""
    if margins[0] + margins[2] >= size or margins[1] + margins[3] >= size:
        raise ValueError(f"'{name}' 9-patch margins {margins} overflow size {size}")


def lint_contrast(name: str, fg, bg, floor: float = 3.0) -> None:
    """WCAG-style relative-luminance contrast >= floor (text/danger vs bg)."""
    def channel(c: float) -> float:
        c /= 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    def luminance(rgb) -> float:
        r, g, b = (channel(v) for v in rgb[:3])
        return (0.2126 * r) + (0.7152 * g) + (0.0722 * b)

    hi, lo = sorted((luminance(fg), luminance(bg)), reverse=True)
    ratio = (hi + 0.05) / (lo + 0.05)
    if ratio < floor:
        raise ValueError(f"'{name}' contrast {ratio:.2f} below floor {floor}")
