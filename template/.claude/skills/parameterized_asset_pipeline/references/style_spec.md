# Style-Spec Schema — the parameterization interface

Fill this BEFORE instantiating any pipeline code. A style spec is a frozen declaration of the project's art direction as machine-consumable axes; every generator selects colors and shapes ONLY through it. The reference project's instances (`art_pipeline/palette.json`, `art_pipeline/theme_spec.json`, `art_pipeline/icon_spec.json`) are the worked example.

## Axis sets

**Sprite / entity axes (Track A):**

| Axis | What it decides | DW worked example |
|---|---|---|
| Palette discipline | ramp+index lookups only vs freer | Resurrect-64, computed shades forbidden |
| Projection | side / top-down / isometric | side-view, facing right |
| Resolution & pixel density | canvas sizes per unit class | 32–96px by size tier, 640×360 native |
| Fidelity target | chunky / detailed / painterly | chunky pixel, HD-2D finish stack |
| Era idiom | 8-bit / 16-bit / modern-pixel | 16-bit+ with HDR bloom |
| Sourcing policy | procedural / conform / hand-authored per tier | pure-procedural locked (hero bake-off verdict) |
| Effects stack | what rides on top of baked pixels | per-element emissive VFX, view-layer shaders |

**UI axes (Track C):**

| Axis | What it decides | DW worked example |
|---|---|---|
| Chrome style | flat / bevel / ornate | 1px border + inner highlight, flat fill |
| Corner treatment | square / rounded / notched | square, 8px 9-patch corner margin |
| Border profile | width, highlight | 1px border, 1px highlight |
| Elevation/depth | shadows, layering | none (flat dark) |
| Theme palette | role map → ramps | bg/panel/border/accent/text/mana/danger roles |
| Icon style | glyph grid, tiers | 16-grid masters, 16/32/64 integer tiers |
| Motion level | juice intensity | HoverPop/PressDepress/Pulse (UiMotion layer) |
| Sourcing dial (per widget/icon) | procedural vs curated | procedural default, ledger-gated external |

## Spec carriers (file contracts)

- **`palette.json`** — colors list + named ramps + domain tables (e.g. `elements`: primary/secondary ramp + accent_index) + outline/clear rules. Consumed via `palette.py`'s `get(ramp, index)` / `shade` / `is_on_palette` / `element(name)`. The CONTRACT is universal; the CONTENTS are per-project.
- **`theme_spec.json`** — preset name, `roles` (role → [ramp, index]), variant maps (e.g. `rarity_roles`), stylebox geometry (size/margins/border/highlight), fonts, `interactive_roles` + `states`. Consumed by `theme_gen.py`; resolved values flow to the runtime via `theme_manifest.json` (C# never holds palette logic).
- **`icon_spec.json`** — `grid`, `tiers`, per-icon `{name, source}`. `source != "procedural"` REQUIRES a `CREDITS.md` ledger entry (bake-enforced).

## Rules that ride the schema

1. **Single source** — a color exists in exactly one place (palette.json or a spec role map); C# reads resolved hexes from the manifest, never literals.
2. **State axis is role-conditioned** — interactive roles bake all states; static roles bake `normal` only.
3. **Integer scaling only** — non-integer tier scaling breaks the pixel grid; slots that don't match a tier consume the nearest smaller tier centered.
4. **Variant axes are loops, not forks** — element/rarity/faction chrome variants iterate a spec map over the same geometry.
