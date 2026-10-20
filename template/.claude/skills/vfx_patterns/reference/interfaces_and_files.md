# Interfaces and file reference

The Key Interfaces and File Reference tables of `SKILL.md`, kept here for lookup.

## Key Interfaces

| Interface | Purpose | Location |
|-----------|---------|----------|
| `IVisualNodeProvider` | Producer of typed VisualNodeHandles + add/remove/visibility events | `Jmodot/Core/Visual/` |
| `IVisualEffectService` | Base-color storage + persistent tint registry | `Jmodot/Core/Visual/Effects/` |
| `IEffectApplier` | Per-effect lifecycle (Begin/End) — abstracts tween mechanics from controller | `Jmodot/Core/Visual/Effects/` |
| `ISpriteMaterialContributor` | Child node that writes its own uniforms into the materials a binder creates, at the binder's scope or one sprite's | `Jmodot/Core/Visual/Sprite/` |
| `IAnimComponent` | Play/query named animations (CompositeAnimatorComponent and leaves both implement) | `Jmodot/Core/Visual/Animation/Sprite/` |
| `ISpriteComponent` | Sprite dimension/texture queries | `Jmodot/Core/Visual/Sprite/` |

## File Reference

| System | Key Files |
|--------|-----------|
| **Composition** | `VisualComposer.cs`, `VisualSlotNode.cs`, `SlotKey.cs`, `VisualItemData.cs`, `VisualRig.cs`, `VisualPartBinding.cs`, `VisualNodeHandle.cs`, `VisualQuery.cs` |
| **Animation** | `AnimationOrchestrator.cs`, `CompositeAnimatorComponent.cs`, `AnimatedSprite3DComponent.cs`, `AnimationVisibilityCoordinator.cs` |
| **Effects** | `VisualEffectService.cs`, `VisualEffectController.cs`, `IEffectApplier.cs`, `Appliers/ModulateTweenApplier.cs`, `FlashEffect.cs`, `TintEffect.cs`, `VisualNodeAggregator.cs` |
| **Sprite material** | `SpriteMaterialBinder3D.cs`, `SpriteShaderVariant.cs`, `SpriteFrameRect.cs`, `sprite_material.gdshaderinc`, `EffectEmission.cs` |

Consumer-owned visual data and effect classes live under paths declared in `skills/project_subsystems/SKILL.md`.
