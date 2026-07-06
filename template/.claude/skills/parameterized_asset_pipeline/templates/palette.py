"""TEMPLATE — parameterized_asset_pipeline (do not run in the baseline repo).

Ramp+index palette contract (verbatim from the reference project — already project-agnostic).
Adapt: nothing but your palette.json (ramps, elements table, outline/clear rules).

Provenance: the reference project's art_pipeline (its test suites
verify the original of this file).
"""
# --- original file follows ---
"""palette.py -- the binding color contract for all Draconic Wars art generation.

Loads ``palette.json`` (Resurrect 64 + named ramps + element mapping). Every
generator selects colors EXCLUSIVELY through this API as (ramp, index) lookups
per art-direction.md section 3: ramp-lookup-only; computing shades via HSV math
is forbidden. ``palette.json`` defines the absolute set of allowed hex values.

Usage:
    from palette import get_palette
    pal = get_palette()
    rgb = pal.get("fire", 2)
    darker = pal.shade("fire", 2, -1)   # clamped ramp step
    pal.is_on_palette((234, 79, 54))    # True
    pal.element_primary("frost")        # "frost"
"""

from __future__ import annotations

import json
from pathlib import Path

_DEFAULT_PATH = Path(__file__).resolve().parent / "palette.json"


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    """'2e222f' or '#2e222f' -> (46, 34, 47)."""
    h = h.lstrip("#").lower()
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def rgb_to_hex(rgb) -> str:
    return "{:02x}{:02x}{:02x}".format(rgb[0], rgb[1], rgb[2])


class Palette:
    """In-memory view of palette.json. Immutable after load."""

    def __init__(self, data: dict):
        self._data = data
        self.name = data.get("name", "unnamed")
        self.colors_hex = [c.lower() for c in data["colors"]]
        self.colors_rgb = {hex_to_rgb(c) for c in self.colors_hex}
        self.ramps: dict[str, list[str]] = {
            name: [c.lower() for c in steps] for name, steps in data["ramps"].items()
        }
        self.elements: dict[str, dict] = data["elements"]
        self.outline_hex = data.get("outline_color", "2e222f").lower()
        self.outline_rgb = hex_to_rgb(self.outline_hex)

        for ramp, steps in self.ramps.items():
            for c in steps:
                if c not in self.colors_hex:
                    raise ValueError(
                        f"palette.json ramp '{ramp}' step {c} is not in the master color list"
                    )

    # ------------------------------------------------------------- loading

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Palette":
        p = Path(path) if path else _DEFAULT_PATH
        with open(p, "r", encoding="utf-8") as f:
            return cls(json.load(f))

    # ------------------------------------------------------------- lookups

    def ramp_len(self, ramp: str) -> int:
        return len(self._ramp(ramp))

    def clamp_index(self, ramp: str, index: int) -> int:
        return max(0, min(index, self.ramp_len(ramp) - 1))

    def get(self, ramp: str, index: int) -> tuple[int, int, int]:
        """RGB of ramp step ``index``. Raises on unknown ramp / out-of-range index."""
        steps = self._ramp(ramp)
        if not 0 <= index < len(steps):
            raise IndexError(f"ramp '{ramp}' has {len(steps)} steps; index {index} is out of range")
        return hex_to_rgb(steps[index])

    def hex_of(self, ramp: str, index: int) -> str:
        steps = self._ramp(ramp)
        if not 0 <= index < len(steps):
            raise IndexError(f"ramp '{ramp}' has {len(steps)} steps; index {index} is out of range")
        return steps[index]

    def shade(self, ramp: str, index: int, delta: int) -> tuple[int, int, int]:
        """RGB of ramp step ``index + delta`` clamped to the ramp ends."""
        return self.get(ramp, self.clamp_index(ramp, index + delta))

    def shade_index(self, ramp: str, index: int, delta: int) -> int:
        return self.clamp_index(ramp, index + delta)

    def is_on_palette(self, color) -> bool:
        """Accepts (r,g,b), (r,g,b,a) or a hex string. Alpha is ignored."""
        if isinstance(color, str):
            return color.lstrip("#").lower() in self.colors_hex
        return tuple(color[:3]) in self.colors_rgb

    # ------------------------------------------------------------- elements

    def element(self, name: str) -> dict:
        try:
            return self.elements[name]
        except KeyError:
            raise KeyError(
                f"unknown element '{name}'; palette.json defines: {sorted(self.elements)}"
            ) from None

    def element_primary(self, name: str) -> str:
        return self.element(name)["primary"]

    def element_secondary(self, name: str) -> str:
        return self.element(name)["secondary"]

    def element_accent(self, name: str) -> tuple[str, int]:
        """(ramp, index) of the element's accent color."""
        e = self.element(name)
        ramp = e["primary"]
        return ramp, self.clamp_index(ramp, e["accent_index"])

    # ------------------------------------------------------------- lint aid

    def darkest_step_hexes(self) -> set[str]:
        """Hexes valid as sel-out outline on lit edges (step 0 of every ramp) + ink."""
        out = {steps[0] for steps in self.ramps.values()}
        out.add(self.outline_hex)
        return out

    # ------------------------------------------------------------- internal

    def _ramp(self, ramp: str) -> list[str]:
        try:
            return self.ramps[ramp]
        except KeyError:
            raise KeyError(f"unknown ramp '{ramp}'; palette.json defines: {sorted(self.ramps)}") from None


_cached: Palette | None = None


def get_palette(path: str | Path | None = None) -> Palette:
    """Default-path singleton. Pass an explicit path to bypass the cache."""
    global _cached
    if path is not None:
        return Palette.load(path)
    if _cached is None:
        _cached = Palette.load()
    return _cached
