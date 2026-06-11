---
paths:
  - "**/*.tscn"
  - "**/*.tres"
  - "Visual/**/*.cs"
  - "Spells/Aiming/**/*.cs"
  - "Jmodot/Core/Shared/RenderLayers.cs"
---

# Visual Layers (Decal Projection)

**Context:** Godot 4 `Decal` nodes project onto every `VisualInstance3D` whose visual `layers` bitmask intersects the decal's `cull_mask`. Without an explicit convention, decals stamp onto everything in their projection volume — including entities, props, and projectiles — producing visible bugs (shadow-on-owner, locus-on-enemy). This rule codifies the project-wide opt-in convention.

## Convention

Two named bits. Typed constants live in `Jmodot/Core/Shared/RenderLayers.cs`:

| Layer         | Bit       | Constant                    | Used by                                                                          |
| ------------- | --------- | --------------------------- | -------------------------------------------------------------------------------- |
| Default       | `1u << 0` | `RenderLayers.Default`      | Every `VisualInstance3D` (Godot default — 1).                                    |
| DecalReceiver | `1u << 1` | `RenderLayers.DecalReceiver`| Ground / terrain / walls that should receive shadows or aiming previews.         |

## When authoring

- **Ground / terrain / floor / wall** meshes that should receive shadows or aiming-preview decals → set `layers = 3` (`Default | DecalReceiver`) on the `MeshInstance3D` in the `.tscn`.
- **Entities** (player, enemies, projectiles, items, props) → leave `layers = 1` default. **Do NOT** add them to layer 2 — that re-introduces the shadow-on-owner bug, since decals would project onto the entity's own mesh.
- **Decal** nodes that project shadows or placement previews → set `cull_mask = 2` (`DecalReceiver` only). In C# code, reference `Jmodot.Core.Shared.RenderLayers.DecalReceiver`.

## Failure modes (by design)

- New floor mesh forgets to opt in → no shadow visible on it at playtest. **Loud, easy to fix.**
- New entity accidentally added to layer 2 → shadow stamps on the entity body. Visible immediately.
- New decal author forgets `cull_mask = 2` → projects on everything. The `DropShadowDecal._Ready()` / `PlacementLocusPreview._Ready()` safety nets cover the two existing decal scripts; new decal classes should add the same safety-net line.

The opt-in design (ground opts in) was chosen over opt-out (entities opt out) because the failure mode is louder. An opt-out scheme would silently re-introduce the original bug for any new entity that forgot to move off layer 1.

## Layer-system distinction (CRITICAL — category error if confused)

These constants are for the **rendering** layer system:
- `VisualInstance3D.layers` — which layers a mesh renders to (decal projection target lookup).
- `Decal.cull_mask` — which layers a decal projects onto.

They are **NOT** physics collision layers:
- `CollisionObject3D.collision_layer` — physics body's layer membership.
- `PhysicsRayQueryParameters3D.collision_mask` — what a raycast hits.

The two layer systems are independent 32-bit (rendering: 20-bit) spaces with separate Project Settings sections. Passing a `RenderLayers` value to a physics query is a silent category error — the previous `DropShadowDecal` bug at line 80 (pre-fix) did exactly this, reusing `CullMask` as a raycast `collision_mask`.

For physics layer masks on `[Export]` properties, use:
```csharp
[Export(PropertyHint.Layers3DPhysics)] public uint MyPhysicsMask { get; set; } = 2;
```
The hint gives the Godot Inspector the same 32-cell grid UI that `CollisionObject3D` uses for `collision_layer`/`collision_mask`. Reference: `DropShadowDecal.GroundProbePhysicsMask`.

## Touchpoints

- `Jmodot/Core/Shared/RenderLayers.cs` — typed constants (source of truth).
- `Visual/DropShadowDecal.cs` — sets `CullMask = RenderLayers.DecalReceiver` in `_Ready()`. Authoritative for all scenes that embed it (`wizard.tscn`, `npc_template.tscn`, `potion.tscn`, `ingredient_template.tscn`, `sprite_fragment.tscn`).
- `Spells/Aiming/PlacementLocusPreview.cs` + `Spells/Aiming/placement_indicator.tscn` — both set the DecalReceiver-only cull_mask (C# safety net + scene primary).
- `Prototype/arena_floor.tscn` — floor `MeshInstance3D` opted in (`layers = 3`).

Other arena/environment scenes still on `layers = 1` default — broader sweep is on the worklog.
