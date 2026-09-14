---
paths:
  - "**/*.tscn"
  - "**/*.tres"
  - "**/*Decal*.cs"
  - "**/*PlacementPreview*.cs"
  - "Jmodot/Core/Shared/RenderLayers.cs"
---

# Visual Layers (Decal Projection)

**Context:** Godot 4 `Decal` nodes project onto every `VisualInstance3D` whose visual `layers` bitmask intersects the decal's `cull_mask`. Without an explicit convention, decals stamp onto unrelated actors, props, and transient effects in their projection volume. This rule defines an opt-in receiver convention.

## Convention

Two named bits. Typed constants live in `Jmodot/Core/Shared/RenderLayers.cs`:

| Layer         | Bit       | Constant                    | Used by                                                                          |
| ------------- | --------- | --------------------------- | -------------------------------------------------------------------------------- |
| Default       | `1u << 0` | `RenderLayers.Default`      | Every `VisualInstance3D` (Godot default — 1).                                    |
| DecalReceiver | `1u << 1` | `RenderLayers.DecalReceiver`| Ground / terrain / walls that should receive shadows or aiming previews.         |

## When authoring

- **Ground / terrain / floor / wall** meshes that should receive shadows or aiming-preview decals → set `layers = 3` (`Default | DecalReceiver`) on the `MeshInstance3D` in the `.tscn`.
- **Non-receiver meshes** → leave `layers = 1` default. **Do NOT** add them to layer 2; decals would project onto their own or unrelated geometry.
- **Decal** nodes for shadows or placement previews → set `cull_mask = 2` (`DecalReceiver` only). In C# code, reference `Jmodot.Core.Shared.RenderLayers.DecalReceiver`.

## Failure modes (by design)

- New floor mesh **authored in a `.tscn`** forgets to opt in → no shadow visible on it at playtest. **Loud, easy to fix.**
- New entity accidentally added to layer 2 → shadow stamps on the entity body. Visible immediately.
- New decal author forgets `cull_mask = 2` → projects on everything. Each decal class should set the mask in `_Ready()` as a safety net.

Within scene-authored geometry, the opt-in design (ground opts in) was chosen over opt-out (entities opt out) because the failure mode is louder. An opt-out scheme would silently re-introduce the original bug for any new entity that forgot to move off layer 1.

## Coverage boundary — the convention binds only geometry the project authors

A `.tscn`-level convention reaches only geometry declared in a scene file. Generated, vendored, or `RenderingServer`-level render paths are outside it **by construction**: there is no mesh node to opt in, so no scene sweep reaches them, and the failure mode inverts — the decal never lands, and nothing is missing from any file a reviewer reads. **Silent, not loud.** Audit every such path separately, at its own render call.

- Geometry created as raw instance RIDs (`RenderingServer.instance_create()`) has no `VisualInstance3D`. Its render layers are set with `RenderingServer.instance_set_layer_mask(instance, mask)` — *"Sets the render layers that this instance will be drawn to. Equivalent to [member VisualInstance3D.layers]."* (Godot 4.7.1 class reference). A receiver surface built this way needs `mask = 3`.
- Live instance of the class: a vendored tilemap/terrain addon that renders generated geometry through instance RIDs is governed by the addon's own render path, never by a scene-file opt-in.

## Layer-system distinction (CRITICAL — category error if confused)

These constants are for the **rendering** layer system:
- `VisualInstance3D.layers` — which layers a mesh renders to (decal projection target lookup).
- `Decal.cull_mask` — which layers a decal projects onto.

They are **NOT** physics collision layers:
- `CollisionObject3D.collision_layer` — physics body's layer membership.
- `PhysicsRayQueryParameters3D.collision_mask` — what a raycast hits.

The two layer systems are independent 32-bit (rendering: 20-bit) spaces with separate Project Settings sections. Passing a `RenderLayers` value to a physics query is a silent category error.

For physics layer masks on `[Export]` properties, use:
```csharp
[Export(PropertyHint.Layers3DPhysics)] public uint MyPhysicsMask { get; set; } = 2;
```
The hint gives the Godot Inspector the same 32-cell grid UI that `CollisionObject3D` uses for `collision_layer`/`collision_mask`.

## Touchpoints

- `Jmodot/Core/Shared/RenderLayers.cs` — typed constants.
- Scene-authored receiver meshes — `layers = 3`.
- Decal scenes and scripts — `cull_mask = 2`, with a code safety net when a script owns the node.

A scene sweep covers `.tscn`-authored meshes only; RID-level render paths need the separate audit above.
