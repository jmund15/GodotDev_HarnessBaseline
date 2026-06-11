---
name: Shader Authoring
description: >-
  Procedure for authoring prototype-grade Godot shaders (.gdshader) for sprite and
  surface looks — pulse, swirl, glow, dissolve, distortion — with a deterministic
  multi-phase screenshot review loop. Triggers: "write a shader", "shader effect",
  "make X glow", "make X pulse", "dissolve effect", "procedural look", "gdshader".
  SKIP for authoring the underlying sprite/texture (use `sprite_authoring`), wiring
  effects into the runtime VFX pipeline (use `vfx_patterns`), and
  Environment/post-processing tuning.
---

# Prototype Shader Authoring

Shaders are the text-native track for **animated or procedural surface looks** — anything that would otherwise need many baked frames (pulse, shimmer, dissolve) or that must scale with parameters (intensity tiers, element colors). Same quality bar as `sprite_authoring`: readable at game scale, prototype-grade, judged via screenshots.

## Type Selection

| Target | `shader_type` | Carrier node |
|---|---|---|
| Sprite2D / Control / GUI | `canvas_item` | the sprite itself, or a duplicate overlay sprite when the base must render untouched |
| Sprite3D / meshes | `spatial` | ShaderMaterial on the node (`SpriteBase3D.material_override`) |
| Asset-free full-rect effect (vignette, panel background) | `canvas_item` | ColorRect |

## Authoring Rules

- **Every tunable is a `uniform`** (colors, speeds, strengths) with a sane default — iteration becomes parameter edits, not code rewrites, and gameplay code can drive them later.
- **Parameterize time as `uniform float phase` (0–1) during authoring, not `TIME`.** Review captures step `phase` explicitly, so every screenshot is reproducible. After visual sign-off, switch to `TIME`-driven or keep the uniform and drive it from a tween/code (required anyway for gameplay-synced effects).
- **Modulate the base art, don't bury it:** additive tints/distortions scaled by the texture's own alpha (`c.a *`) keep the silhouette readable. An effect that obscures the sprite's role fails the rubric regardless of how good it looks.
- **Compose from cheap primitives:** UV offsets with `sin`, `smoothstep` masks, distance fields, texture self-sampling. Prototype shaders should stay a handful of lines.

## Review Loop

1. Apply the shader in the review scene (see `sprite_authoring` Rung 2 harness); capture one screenshot per phase step:
```gdscript
for i in 4:
    (target.material as ShaderMaterial).set_shader_parameter("phase", i / 4.0)
    await RenderingServer.frame_post_draw
    await RenderingServer.frame_post_draw
    get_viewport().get_texture().get_image().save_png("res://.review/fx_f%d.png" % i)
```
2. **Check the run log for `SHADER ERROR` before judging frames** — shader compile errors surface at runtime, not import; a broken shader renders the node with the error fallback and the frames lie.
3. Rubric per sequence: effect readable at game zoom? base art still readable underneath? loops seamlessly when `phase` wraps 1→0? alpha edges clean? One named change per revision, re-capture every time.

Screenshots need a real rendering driver — `xvfb-run + --rendering-driver opengl3` on cloud/CI, plain run locally (`--headless` renders nothing).

## Gotchas

- `StandardMaterial3D` emission does NOT produce HDR on Sprite3D — bloom-driven glow needs a custom ShaderMaterial, and `render_mode unshaded` ignores `EMISSION`. The full bloom architecture (overlay sprite + edge emission + Environment settings) is canon in `vfx_patterns` §Bloom-Based Glow — extend it rather than re-deriving.
- A `spatial` shader replaces the whole material — re-declare billboard behavior if the node relied on material billboard mode.
- Uniform names are the public API once gameplay code drives them — name like exports (`glow_color`, not `c1`) and keep them stable.

## Cross-references

- [`sprite_authoring`](../sprite_authoring/SKILL.md) — the underlying sprite/texture assets and the shared review-scene harness.
- [`vfx_patterns`](../vfx_patterns/SKILL.md) — runtime integration: IEffectApplier, bloom/glow architecture, tint pipeline (never write `Modulate` around it).
