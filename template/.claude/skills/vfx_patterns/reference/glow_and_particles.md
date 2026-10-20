# Glow and GPU particles

The Sprite Effect Material and GPU Particles sections of `SKILL.md`. Their headings keep the section numbers `SKILL.md` and its citers use.

## 8. Sprite Effect Material — Bloom-Based Glow

**Location:** `Jmodot/Implementation/Visual/Sprite/SpriteMaterialBinder3D.cs` over `sprite_material.gdshaderinc`; the project supplies the shader body the binder requires and any glow node.

An entity instances the binder under its root. `SpriteMaterialBinder3D` gives every `SpriteBase3D` under its parent its own `ShaderMaterial`: the render state comes from that sprite's own draw flags, the body from the binder's `Body`, and the sprite's current frame texture and rect are bound and followed on frame, animation, texture and `SpriteFrames` changes. Hidden clip sprites and sprites a `VisualComposer` equips later are bound with no second reference to author.

Glow and other per-sprite uniforms ride `ISpriteMaterialContributor` children, at two scopes:
- Under the binder, a contributor writes its values into every sprite the binder binds, hidden clips and later-equipped sprites included, so a five-clip rig takes one halo.
- Under one bound sprite, a contributor with the same `Slot` replaces the binder's contribution there, whole. Use it when that sprite's art needs its own mask or a different glow.

A contributor writes only uniforms the binder's body declares, never its texture, frame or alpha uniforms, and never replaces the material.

Tint the source. The glow draws on the sprite itself, so the sprite's `Modulate` reaches it once; there is no separate glow node whose colour could square it.
A subtree `Modulate` writer applies `VisualNodeAggregator.InheritsModulate` to its own target set. `VisualEffectService` skips a nested handle's tint when a matching sprite ancestor already passes it down.

Only emission reaches the glow threshold. The project's environment owns the HDR glow threshold and bloom settings. A white-out that must stay below it rides the emission channel (`SKILL.md` §5).

See [`shader_authoring`](../../shader_authoring/SKILL.md) for shader authoring rules.

## 9. GPU Particles — Silent-Visibility Gates

A `GPUParticles3D` emitter can run its full simulation and render **nothing**, with no error or warning. Independent gates — every one must pass; a miss looks identical to "particles too subtle":

1. **`visibility_aabb` must enclose where particles actually GO, not where they spawn.** The default is a small box around the emitter origin; with world-space particles (`local_coords = false`) anything travelling outside it is frustum-culled invisibly. Derive it: emission extents + `maxSpeed × lifetime` reach + a draw margin. A set `GeometryInstance3D.custom_aabb` **overrides** `visibility_aabb` entirely — check both.
2. **`ParticleProcessMaterial` color output lands in VERTEX color; the draw-pass material discards it by default.** `Color`/color-ramp values only reach the screen when the pass material sets `vertex_color_use_as_albedo = true` (`BaseMaterial3D`, default **false**). All process-material tinting silently no-ops without it.
3. **Draw-pass transparency mode** must suit the texture (Alpha for soft particles); an opaque pass renders soft textures as hard quads or nothing legible.
4. **`layers` ∩ camera `cull_mask`** must be non-empty.
5. **A finished `one_shot` emitter does not re-emit on `Emitting = true`** — call `Restart()`.

Diagnosis order for "particles don't show": walk gates 1–5 before touching amount, scale, or lifetime. Tuning an emitter that is gated off is the time sink.
