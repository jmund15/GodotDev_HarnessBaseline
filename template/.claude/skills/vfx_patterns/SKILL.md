---
name: VFX Patterns
description: >-
  Auto-load for VFX, animation, or visual composition work — particles, tweens, tints,
  flashes, explosions, or the VisualComposer/VisualSlotNode/VisualQuery/TintByQuery/
  VisualEffectController/AnimationOrchestrator surface. Also load when diagnosing why a
  tint isn't applying or a flash gets stomped. SKIP for authoring new sprite/texture
  assets (use `sprite_authoring`) or authoring new prototype shaders (use
  `shader_authoring`).
user-invocable: false
---

# VFX Patterns

Reference for Jmodot visual effects, animation, and composition.

The current contract uses `VisualSlotNode`, `SlotKey`, `VisualEffectService`, and `IVisualNodeProvider`. Slot configurations are scene children. Persistent tints use query-based APIs. Providers emit typed `NodeAdded` and `NodeRemoved` events.

## Architecture Overview

```
VisualComposer  (Node, IVisualNodeProvider — aggregates child slots)
  ├── VisualEffectService  (Node, owns base colors + persistent tints)
  ├── VisualSlotNode  (Node3D — Body)       SyncMode = Master
  ├── VisualSlotNode  (Node3D — Right Hand) SyncMode = Slave
  └── VisualSlotNode  (Node3D — Left Hand)  SyncMode = Slave
                  │  each slot:
                  ├── Key: SlotKey (.tres asset)
                  ├── DefaultItem: VisualItemData
                  ├── CurrentItem / CurrentInstance / Animator
                  ├── Push/Pop stack (transient swaps)
                  └── Builds VisualNodeHandle list from VisualRig bindings
                       OR fallback recursive sprite walk

CompositeAnimatorComponent  (sibling of VisualComposer)
  ├── Master (registered by Body slot, isMaster: SyncMode == Master)
  └── Slaves (registered by other slots) — follow normalized time

AnimationOrchestrator  (directional suffix: "run" → "run_left")
  └── delegates to IAnimComponent (CompositeAnimatorComponent or leaf)

VisualEffectController  (transient effects: flash, tint)
  ├── [Export] Composer  (single source of nodes via IVisualNodeProvider)
  ├── [Export] Root      (fallback for single-sprite props)
  └── Subscribes to Composer.NodeAdded/NodeRemoved + Effects.TintChanged
```

## Key Systems

### 1. VisualComposer + VisualSlotNode — Slot Graph

**Location**: `Jmodot/Implementation/Visual/Animation/Sprite/VisualComposer.cs`,
`VisualSlotNode.cs`

`VisualComposer` is a thin coordinator over `VisualSlotNode` scene-graph children. It discovers slots at `_Ready` (uniqueness check on `SlotKey.Id`), wires each slot's dependencies (`CompositeAnimator` + `VisualEffectService`), and forwards each slot's `IVisualNodeProvider` events 1:1 (D1/D2 forwarding — no aggregate cache; queries hit slots on demand).

**`VisualSlotNode` Inspector surface:**
- `Key: SlotKey` (RequiredExport) — typed identifier
- `SyncMode: AnimationSyncMode` — `Master` / `Slave` / `Independent`
- `IsOptional: bool` — false slots revert to `DefaultItem` on Unequip
- `DefaultItem: VisualItemData` — equipped automatically by composer
- `SlotTags: Array<StringName>` — applied to every handle this slot produces

**`SlotKey` (`.tres` asset)**:
- `Id: StringName` — identity (equality is on Id)
- `DisplayName: string` — inspector label
- IDs are referenced exclusively via `[Export]` fields wired to `.tres` assets — never constructed inline. Spaces in `Id` are fine (`"Right Hand"`).

**Composer typed API:**
```csharp
composer.Equip(slotKey, item);                           // returns VisualEquipResult
composer.Unequip(slotKey);
composer.Push(slotKey, item, options);                   // transient swap
composer.Pop(slotKey);                                   // restores prior item
composer.GetVisualNodes(VisualQuery.Slot(slotKey));      // typed query
composer.GetVisualNodes(VisualQuery.Tagged("TeamTinted"));
composer.GetVisualNodes(VisualQuery.AllExceptSlot(slotKey));
composer.GetVisibleNodes(VisualQuery.All);               // LIVE visibility — see Gotcha 8
composer.Effects;  // VisualEffectService for persistent-tint registration
```

**Critical patterns:**
- Default-item equip is `CallDeferred` from `_Ready` (parent tree may be locked at that point)
- Slot ordering matters when consumers call `Equip` synchronously from `_Ready`; those calls win over the deferred default pass. The composer's `_GetConfigurationWarnings` flags a missing `SyncMode = Master` slot.
- Atomic event firing: `Equip` clears the prior instance (firing `NodeRemoved` for each gone handle) BEFORE installing the new prefab and firing `NodeAdded` for each new handle. Subscribers querying inside event handlers see consistent state

### 2. CompositeAnimatorComponent — Time Sync

**Location**: `Jmodot/Implementation/Visual/Animation/Sprite/CompositeAnimatorComponent.cs`

Master/slave architecture for multi-part characters:
- Master sets duration; slaves follow normalized position
- If Body at 50% of 2s animation, Hat jumps to 50% of its 4s animation
- **Partial match**: Slots without matching animation name are silently skipped (intentional — allows independent animations)
- **No implicit master**: `_masterAnimator` stays null until an explicit `SyncMode = Master` claim arrives. Reads must stay null-guarded.
- **Master loss**: when the master unregisters, the composite elects an arbitrary remaining animator and re-issues `StartAnim(_lastRequestedAnim)` so subsequent sync calls compute against an actually-playing animator

### 3. AnimationOrchestrator — Directional Suffixes

**Location**: `Jmodot/Implementation/Visual/Animation/Sprite/AnimationOrchestrator.cs`

Adds directional suffixes to animation names:
- `SetDirection(moveVector)` → maps to nearest cardinal
- `"run"` + `"_left"` → plays `"run_left"` if exists, falls back to `"run"`
- Separator configurable (default: `"_"`)
- Uses `DirectionSet3D` resource for mapping
- Wraps any `IAnimComponent` (typically the `CompositeAnimatorComponent`)

### 4. VisualEffectService — Persistent Tints + Base Colors

**Location**: `Jmodot/Implementation/Visual/Effects/VisualEffectService.cs`

Replaces the legacy `BaseModulationTracker`. Owned by `VisualComposer` via `[Export, RequiredExport] VisualEffectService Effects`.

Two channels:

**Per-node base colors** (set on equip):
```csharp
service.RegisterBaseColor(node, color);
service.GetBaseColor(node);
service.UnregisterSprite(node);
```
`VisualSlotNode.ApplyOverrides` calls `RegisterBaseColor` for each prefab sprite using `VisualItemData.ModulateOverride`. The legacy `RegisterBaseColor` field is preserved as the base layer of the tint composition.

**Persistent tints** (set by gameplay code, layer over base):
```csharp
EffectId id = service.TintByQuery(VisualQuery.AllExceptSlot(itemSlot), teamColor);
service.RemoveTint(id);     // surgical removal — preserves other overlapping tints
```
`TintByQuery` applies to current matches and future matching handles through the service's `IVisualNodeProvider.NodeAdded` subscription. Use it for persistent identity, team, or status colors. Do not subscribe to `Composer.NodeAdded` and re-walk the composer per event.

**Layering rule**: effective color = base × product-of-matching-persistent-tints. Multiplication is commutative, so registration order doesn't change the result. `RemoveTint` recomputes effective color for matched nodes — overlapping tints survive removal.

### 5. VisualEffectController — Transient Effects

**Location**: `Jmodot/Implementation/Visual/Effects/VisualEffectController.cs`

Central hub for transient timed effects such as flashes and tint pulses. Unlike `VisualEffectService` persistent tints, controller effects have a finite duration and a tween.

**Inspector surface:**
- `[Export] VisualComposer? Composer` — primary node source
- `[Export] Node? Root` — fallback for single-sprite props (no composer)

**Blend Modes:**
- **Mix**: Multiply all effect colors together
- **Override**: Highest priority wins completely

**Effect Lifecycle:**
1. `PlayEffect(VisualEffect)` → constructs an `IEffectApplier`, which owns the Godot `Tween` and `VisualEffectHandle` lifetime
2. Each frame: composite all active effect colors via single-pass foreach (no LINQ allocation) → apply to tracked sprites
3. `FinalColor = BaseColor * EffectColor`
4. On finish: `applier.End()`, remove effect, reset to base

**IEffectApplier**: future effect kinds (glow shaders, particles, gradients) ship their own appliers; the controller composes blend modes and tracks sprites.

**Built-in Effects:**
- `FlashEffect` — on/off flash cycles
- `TintEffect` — gradual color shift through an easing curve

**Node tracking:**
- Subscribes to `Composer.NodeAdded` / `NodeRemoved` for incremental updates (no full re-scan per handle event)
- Subscribes to `Composer.Effects.TintChanged` to keep base colors in sync after persistent-tint changes
- `RefreshVisualNodes()` is the public API for forced rescans (editor + external mutation cases)

### 6. VisualNodeHandle + VisualQuery — Typed Identity

**Location**: `Jmodot/Core/Visual/`

```csharp
public record VisualNodeHandle(
    SlotKey SlotId,                  // which slot owns this handle
    StringName? PartId,              // optional rig-binding part identifier
    IReadOnlySet<StringName> Tags,   // slot tags + per-binding tags
    Node Node,                       // the actual visual node
    IVisualNodeProvider OwningProvider,
    bool IsVisible);
```

`VisualQuery` is the filter primitive — composable, query the composer:
```csharp
VisualQuery.All
VisualQuery.VisibleOnly
VisualQuery.Slot(slotKey)
VisualQuery.Part(partId)
VisualQuery.Tagged(tag)
VisualQuery.Tagged(t1, t2, ...)        // any-of
VisualQuery.AllExceptSlot(slotKey)
VisualQuery.Handles(h1, h2, ...)
queryA.And(queryB)
queryA.Or(queryB)
```

### 7. VisualRig + VisualPartBinding — Prefab Visual Contract

**Location**: `Jmodot/Core/Visual/Animation/Sprite/VisualRig.cs`, `VisualPartBinding.cs`

`VisualItemData` has an optional `[Export] VisualRig? Rig`. When set, `VisualSlotNode` uses the rig's `Bindings` to:
- Produce typed handles with `PartId` and `Tags`
- Apply overrides only to bindings flagged `ReceivesTextureOverride` / `ReceivesRowOverride` / `ReceivesModulateOverride`

When `Rig` is null, the slot falls back to a recursive sprite walk (tagless, partless handles) and applies overrides to the first sprite found. Both paths use `VisualNodeAggregator.CollectSprites` for the recursive walk (no type-NAME string match — that bug was fixed in the refactor).

## Consumer-owned visual systems

Ability identities, one-shot effects, bloom helpers, and authored visual data belong to the consuming project. Locate their roots through `skills/project_subsystems/SKILL.md`; do not copy another project's classes or folder layout into this framework reference.

## Conventions

### Animation Naming
```
Base: "run"
Directional: "run_left", "run_up", "run_downRight"
Style variant: "armored_run_left" or "run_left_armored"
Fallback chain: "run_left" → "run" → (skip)
```

### Sprite Sheet Rows
- AnimationPlayer keys ONLY `frame_coords:x` (horizontal frame)
- VisualItemData sets `frame_coords:y` via `SpriteSheetRowOverride` (vertical row)
- Never key full `frame_coords` in AnimationPlayer — conflicts with row selection

### Color Tinting
**For permanent tints** (identity color, team tint, status overlay):
```
gameplay code → effects.TintByQuery(query, color) → returns EffectId
auto-applies to current AND future matching handles via NodeAdded
remove via effects.RemoveTint(id) — preserves overlapping tints
```

**For per-item base color**:
```
VisualItemData.ModulateOverride → VisualSlotNode.ApplyOverrides
  → effects.RegisterBaseColor(sprite, color) + sprite.Modulate = color
```

**For transient effects**:
```
controller.PlayEffect(VisualEffect) → IEffectApplier.Begin (Tween + handle)
each frame: Final = BaseColor * effectColor (mix product or override winner)
```

### Slot Wiring Pattern (designer-facing)
1. Author `SlotKey` `.tres` (one per logical slot — body, right_hand, etc.)
2. Add `VisualSlotNode` children under the entity's `VisualComposer` node
3. Wire `Key`, `SyncMode`, `IsOptional`, `DefaultItem` per slot in inspector
4. Consumer code wires `[Export, RequiredExport] SlotKey ...SlotKey` to the same `.tres`

## Key Interfaces

| Interface | Purpose | Location |
|-----------|---------|----------|
| `IVisualNodeProvider` | Producer of typed VisualNodeHandles + add/remove/visibility events | `Jmodot/Core/Visual/` |
| `IVisualEffectService` | Base-color storage + persistent tint registry | `Jmodot/Core/Visual/Effects/` |
| `IEffectApplier` | Per-effect lifecycle (Begin/End) — abstracts tween mechanics from controller | `Jmodot/Core/Visual/Effects/` |
| `IAnimComponent` | Play/query named animations (CompositeAnimatorComponent and leaves both implement) | `Jmodot/Core/Visual/Animation/Sprite/` |
| `ISpriteComponent` | Sprite dimension/texture queries | `Jmodot/Core/Visual/Sprite/` |

## File Reference

| System | Key Files |
|--------|-----------|
| **Composition** | `VisualComposer.cs`, `VisualSlotNode.cs`, `SlotKey.cs`, `VisualItemData.cs`, `VisualRig.cs`, `VisualPartBinding.cs`, `VisualNodeHandle.cs`, `VisualQuery.cs` |
| **Animation** | `AnimationOrchestrator.cs`, `CompositeAnimatorComponent.cs`, `AnimatedSprite3DComponent.cs`, `AnimationVisibilityCoordinator.cs` |
| **Effects** | `VisualEffectService.cs`, `VisualEffectController.cs`, `IEffectApplier.cs`, `Appliers/ModulateTweenApplier.cs`, `FlashEffect.cs`, `TintEffect.cs`, `VisualNodeAggregator.cs` |

Consumer-owned visual data and effect classes live under paths declared in `skills/project_subsystems/SKILL.md`.

## Gotchas

1. **Default-item equip is deferred**: `VisualComposer._Ready` schedules `EquipAllDefaults` through `CallDeferred`. Synchronous consumer equips fire first. A master slot must explicitly use `SyncMode = Master`; registration order is not ownership.
2. **Use `TintByQuery` for permanent tints**: do not subscribe to `Composer.NodeAdded` and re-walk the composer per event.
3. **Sprite sheet rows**: never key `frame_coords` as full vector in AnimationPlayer — VisualItemData's `SpriteSheetRowOverride` writes the Y component, AnimationPlayer keys only X.
4. **Master animator**: only ONE `SyncMode = Master` per composite. The composer's `_GetConfigurationWarnings` flags the no-master case at edit time.
5. **Time seeking**: `AnimatedSprite3D` uses `frame / FPS` for time conversion — mismatched FPS breaks sync.
6. **Slot composition is `_Ready`-time only**: reparenting a `VisualSlotNode` away from a composer at runtime is unsupported — the composer keeps event subscriptions and slot dictionary entries until `_ExitTree`. Add `ChildExitingTree` handling if runtime composition becomes a need.
7. **Push/Pop semantics**: `Push(item, AsAnimationIndependent)` saves the prior item and options; `Pop` restores both. `AsAnimationIndependent` suppresses composite-animator registration only for the pushed item.
8. **`GetVisualNodes(VisualQuery.VisibleOnly)` is not the live-visibility query — use `GetVisibleNodes(query)`.** `VisualQuery.VisibleOnly` uses the handle's creation-time visibility snapshot. `GetVisibleNodes` checks the node's current state. Animation-driven visibility can therefore make the snapshot query return an empty set without error.
9. **Never size an attached visual off an arbitrary composer sprite.** A composer keeps every animation of every slot resident, and their frames span art regimes — hidden legacy frames can measure ~2× the shown body's height. A tree walk that takes the first (or tallest) `SpriteBase3D` is silently wrong by that factor. Measure only live-visible nodes; if none, return neutral rather than guessing.
