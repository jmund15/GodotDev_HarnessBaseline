---
name: Sprite Authoring
description: >-
  Procedure for authoring prototype-grade visual assets as text — hand-written SVG,
  pixel grids baked via Godot's Image API, procedural .tres (StyleBoxFlat,
  GradientTexture2D) — covering in-game sprites (Sprite2D/3D), GUI icons, UI chrome,
  and animation frames/sheets, with a mandatory render→screenshot→critique loop. SKIP
  for production/final art (user-owned), 3D meshes/models, shader effects (use
  `shader_authoring`), and composing existing sprites into effects (use `vfx_patterns`).
---

# Prototype Sprite Authoring

Author sprites as **editable text sources** (SVG, pixel-grid scripts, .tres), bake/import them with Godot's own tooling, and judge every revision visually via screenshots. No image generators, no external image libraries — Godot is the only required tool, so the loop works anywhere the engine runs (local, cloud, CI).

**Quality bar:** prototype-grade — readable silhouette, immediately understandable role, workable for playtesting. Not production fidelity. Stop iterating when a playtester would recognize the object at game zoom; further polish is wasted.

## Track Selection

| Asset shape | Track | Source artifact |
|---|---|---|
| Items, props, icons, UI, flat-style characters (default) | **SVG** | `.svg` file, imported directly by Godot |
| Pixel-art aesthetic, tiles, tiny sprites (≤32px) | **Pixel grid** | generator `.gd` (ASCII grid + palette) → baked PNG |
| Glows, orbs, soft particles, noise blobs | **Procedural** | `.tres` (GradientTexture2D radial fill, NoiseTexture2D, GradientTexture1D ramps) |
| Animated/procedural surface looks (pulse, swirl, dissolve) | **Shader** | `.gdshader` — see `shader_authoring` |

Commit the text source next to the baked/imported asset under the owning path declared by `skills/project_subsystems/SKILL.md`. The source is the editable artifact; never hand-edit a baked PNG.

## Asset Classes

| Class | Sizing rule | Lands on | Notes |
|---|---|---|---|
| In-game art | world units via the project's px/m convention | Sprite2D / Sprite3D | scale judged at Rung 2 against neighbor assets |
| GUI icon | screen px, fixed tiers (16/24/32/64) | TextureRect, `Button.icon` | SVG track; `svg/scale` so the raster ≥ largest displayed size; mipmaps only if minified |
| GUI chrome (panels, buttons, bars) | Control layout | `theme_override_styles` / Theme | **no texture by default** — StyleBoxFlat `.tres` (bg color, borders, corner radii, content margins) covers prototype chrome; StyleBoxTexture + nine-patch margins only when flat styling can't express the look |

Scale and palette conventions are **per-project** — if not yet recorded, ask once and capture them (auto-memory or project doc) before authoring the first asset.

## Authoring Rules

**All tracks:**
- **Decompose into named parts** before writing coordinates (base / body / detail). One comment or group per part keeps edits tractable.
- **Compute coordinates on a small grid** (16/32/64 viewBox or canvas); no freehand values. Spatial coherence degrades fast with canvas size — this is the dominant LLM failure mode (symmetry drift, disconnected parts).
- **Style guardrails for cohesion:** flat fills, dark outline, 2–3 value steps per material, one limited project palette (e.g. project palette), fixed size tiers (16/32/64px). Strong silhouette and value contrast outrank detail.
- **Color-code the project's semantic role or state** when readability depends on it.

**SVG track:** Godot rasterizes via ThorVG ≈ SVG Tiny 1.2. Safe: shapes, paths, groups, linear/radial gradients, opacity, `clipPath`. Avoid: `<text>` (silently invisible — convert to paths), `<pattern>`, filters, CSS selectors beyond tag/class. Resolution comes from the `svg/scale` import param — never upscale the node (blur).

**Pixel-grid track:** ASCII grid + `{char: Color}` palette in a `.gd` script; bake via `Image.create` + `set_pixel` + `save_png` (CPU-side, works under `--headless`). Keep ≤32px per sprite. Also save an 8× `INTERPOLATE_NEAREST` preview for review — a 16px PNG is too small to judge.

## Render → Review Loop

Re-render after **every** edit — you cannot see what you wrote until it's rasterized. One named change per revision ("shorten neck 2px"), never whole-sprite rewrites of a mostly-working asset.

**Rung 1 — isolated render (fast, per-edit).** Bake the sprite alone and `Read` the PNG:

```gdscript
# rasterize.gd — godot --headless --path <proj> --script res://rasterize.gd
extends SceneTree
func _init() -> void:
    DirAccess.make_dir_recursive_absolute("res://.review")   # gitignore .review/
    var img := Image.new()
    img.load_svg_from_string(FileAccess.get_file_as_string("res://art/icon.svg"), 4.0)
    img.save_png("res://.review/item_x4.png")   # also save a 1.0-scale render = game size
    quit()
```

Critique rubric — fail any line, edit and re-render:
- [ ] Recognizable from silhouette alone at game-zoom render?
- [ ] Role/faction readable from color at a glance?
- [ ] No stray/disconnected shapes, no part misalignment, intended symmetry holds?
- [ ] Outline closed; alpha edges clean (no halo)?

**Rung 2 — scene-context screenshot (before integration).** Isolated renders hide scale and contrast errors — an asset on a flat card is a vacuum, and every variant looks fine in isolation. Judge the sprite next to existing assets, on real backgrounds, and (for Sprite3D) under lighting/billboard. Keep a review scene per project with a screenshot script:

```gdscript
# attach to review-scene root (2D or 3D)
func _ready() -> void:
    await RenderingServer.frame_post_draw
    get_viewport().get_texture().get_image().save_png("res://.review/scene.png")
    get_tree().quit()
```

```bash
godot --headless --path <proj> --import        # (re)import after adding/editing assets
xvfb-run --auto-servernum godot --path <proj> --rendering-driver opengl3 \
    --resolution 640x360 res://review/review_scene.tscn
```

`--headless` **cannot render scenes** (dummy RenderingServer → empty capture); rasterize/bake/import are fine headless, but any viewport screenshot needs a real driver — `xvfb-run + opengl3` on cloud/CI, plain run locally. Check: scale vs neighbors, contrast vs background, 3D lighting/billboard behavior.

**Rung 3 — in-game.** Wire into the real scene; screenshot or playtest. Subjective feel stays with the user.

## Godot Integration

**Import settings** (`.import` params; re-run `--headless --import` after editing):

| Param | Prototype default | Why |
|---|---|---|
| `svg/scale` | size so 1 game-zoom px ≈ 1 texture px (e.g. 4.0 for a 32-viewBox shown ~128px) | resolution lives here, not in node scale |
| `mipmaps/generate` | `true` if ever used on Sprite3D | distance shimmer; default is false |
| `compress/mode` | 0 (lossless) | VRAM compression visibly degrades flat color / pixel art |
| `detect_3d/compress_to` | 0 (disabled) | first 3D use silently re-imports as VRAM-compressed |

**Sprite3D:** `pixel_size` maps px→meters — pick one project-wide convention (e.g. 32px/m → `0.03125`) so all sprites agree on scale. Prototype defaults: `billboard = 1`, `alpha_cut = 1` (DISCARD — sidesteps transparency sorting), `shaded` off. Filter enums differ: CanvasItem `texture_filter` 1 = nearest, but SpriteBase3D uses BaseMaterial3D values — 0 = nearest (no mipmaps), 2 = nearest-with-mipmaps.

**Transparency sorting — `sorting_use_aabb_center` / `sorting_offset`.** Both are `GeometryInstance3D` properties affecting **only geometry in the transparent pass**; sprites at `alpha_cut = 1`/`2` are depth-buffer sorted per-pixel and ignore them entirely, which is why DISCARD is the default above. When they do apply, `sorting_use_aabb_center` (default `true`) selects the depth reference point — AABB center vs. the node's global origin — and `sorting_offset` is added to whichever point was selected. Under a pitched camera a tilted sprite's local `+Y` also carries world `+Z`, so the AABB center of a ground-anchored sprite sits *further from the camera* than its base: tall sprites sort as though standing behind their own footprint. **Set `sorting_use_aabb_center = false` on any world-space sprite whose origin is its ground-contact point** — that restores sort-by-feet, the 2.5D rule. Leave it default for center-anchored sprites (projectiles, pickups, stacked particle layers): the two points coincide and the flag buys nothing. The engine tooltip's "more accurate for 3D models" is about solid volumes, where the centroid is a fair depth proxy; for a flat quad standing at an angle it is not.

Because the offset is measured from the selected point, flipping the flag on a rig of hand-tuned siblings re-bases every `sorting_offset` at once. Equal-size siblings survive because the AABB delta is constant; mixed-height comparisons shift. Flip per rig with a visual check, never by project-wide replacement.

**Texture filtering follows the project's art direction.** Set `texture_filter` explicitly on every sprite or spritesheet node instead of relying on the engine default. Pixel art normally uses the Nearest variant; smooth art may use a filtered variant. CanvasItem and BaseMaterial3D use different enum values, so verify the node type.

**Godot 4.5+ `DPITexture`** (SVG kept as source, auto re-rasterized) exists but ignores oversampling on SpriteBase3D — for shared 2D/3D assets stay on the classic texture importer + `svg/scale`.

## Animation (one-shot + looping)

- **Author frames as variants of the base source:** grid edits, SVG part transforms (rotate an arm group), or derived transforms (bottom-anchored vertical resize = squash/stretch). 2–4 frames read as alive at prototype grade.
- **Sheet contract:** columns = frames of one clip (X), rows = skin/style variants (Y). Assemble headlessly via `Image.blit_rect`; play via `Sprite2D.hframes/vframes` + keying `frame`, or build `SpriteFrames` in script for named clips with per-clip FPS and loop flags.
- **One-shot vs loop is an end-state decision.** Loop: the last frame must flow into the first — review the filmstrip as a cycle. One-shot: decide what shows after the final frame (hide / free / revert to idle) and wire it (`animation_finished` or AnimationPlayer queue) — a frozen last frame is the default bug.
- **Review:** Rung 1 per frame plus an 8× nearest-upscaled filmstrip of the whole sheet; Rung 2 steps `frame` (and any shader `phase` uniform) deterministically in the review scene — one capture per step, never timed waits.

**Jmodot handoff** (integration canon: `vfx_patterns`):
- Clip names use the DirectionSet vocabulary — `run`, `run_left`, `run_downLeft` (camelCase diagonals). Partial directional coverage is safe: `AnimationOrchestrator` falls back nearest-direction → undirected base.
- AnimationPlayer keys ONLY `frame_coords:x`; the Y row belongs to `VisualItemData.SpriteSheetRowOverride` — author sheets so rows are swappable variants of the same frame columns.
- Never key `Modulate` in authored animations — tints/flashes flow through `VisualEffectService`/`VisualEffectController` exclusively.

## Project-specific style

Read the consuming project's `game_vision` skill before authoring. Keep palette, silhouette language, clip roster, frame sizes, directional coverage, and asset output paths there or in the owning subsystem docs. This baseline defines the authoring loop, not a game's visual taxonomy.

## Anti-patterns

- Scaling the node to fix a blurry SVG — raise `svg/scale` and reimport.
- Screenshot under `--headless` — silently empty; use xvfb/real driver.
- Pixel grids >32px or freehand SVG coordinates — spatial coherence collapses; decompose or drop resolution.
- Editing baked PNGs or `.godot/imported/` output — edit the text source, re-bake.
- Iterating blind (multiple edits between renders) — every edit gets a render + rubric pass.
- Polishing past the quality bar — prototype art ships when readable, not when pretty.
- Drawing textures for UI chrome that StyleBoxFlat can express — flat styleboxes first, images last.
- Timed waits in motion captures — step `frame`/`phase` deterministically per screenshot.
- Dropping in a downloaded asset when the text track is slow — sourcing external art leaves this skill entirely; clear the license and write the ledger entry first (`parameterized_asset_pipeline` → `references/provenance.md`).

## Cross-references

- [`shader_authoring`](../shader_authoring/SKILL.md) — animated/procedural surface looks on top of (or instead of) baked sprites.
- [`vfx_patterns`](../vfx_patterns/SKILL.md) — composing authored sprites into effects, tints, flashes; animation/visual-slot runtime systems.
- [`testing`](../testing/SKILL.md) — ISceneRunner if a review scene grows into an automated visual check.
- [`parameterized_asset_pipeline`](../parameterized_asset_pipeline/references/provenance.md) — sourcing rules, license ledger, and verified CC0 endpoints when an asset comes from outside this repo.
