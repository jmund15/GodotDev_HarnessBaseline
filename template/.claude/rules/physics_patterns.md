---
paths:
  - "**/Movement/**"
  - "**/*Strategy*.cs"
  - "**/*MovementProcessor*.cs"
  - "**/*Body3D*.cs"
---

# Physics & Movement Patterns

**Context:** Movement and physics body choices set the determinism contract for every gameplay entity. Multiplayer determinism, impulse-response behavior, and the strategy-vs-hook distinction all live here. Auto-loads on physics/movement-shaped files.

## Body Type Rules

| Body Type | Use For | Examples |
|-----------|---------|----------|
| **CharacterBody3D** | Gameplay entities requiring deterministic movement | Wizard, Ingredients, Environment objects (barrels, crates) |
| **RigidBody3D** | Non-gameplay-critical cosmetic physics | Sprite fragments, debris, visual-only particles |

- *Why CharacterBody3D for gameplay:* `MoveAndSlide()` is frame-deterministic (multiplayer safe), fully designer-tunable, and leverages the existing Jmodot `MovementProcessor3D` pipeline.
- *Why not RigidBody3D for gameplay:* Non-deterministic physics (jitter, stacking instability) breaks online multiplayer. Jolt doesn't fix determinism.
- *RigidBody3D is fine for cosmetics:* Sprite fragments, explosion debris, and other visual-only objects where non-determinism is acceptable and Godot's built-in physics saves work.

## Movement Pipeline

**Rule:** All gameplay entity movement flows through `MovementProcessor3D` → `ICharacterController3D` → `CharacterBody3D.MoveAndSlide()`.

- Impulses are single-frame: `ApplyImpulse()` stores, frame applies via `AddVelocity`, then clears.
- Push, knockback, and force zones all feed through the same impulse pipeline — consistent behavior.

## Movement Strategy Selection

**Rule:** Pushability is a strategy-choice concern, not a composable hook. The strategy IS the impulse-response model — pick the right one and impulse behavior follows for free.

- Use `ProjectileStrategy` for fixed-velocity, hitscan-like projectiles where impulses, wind, and knockback should NOT take effect. Returns `desiredDirection` wholesale (treated as pre-scaled velocity, not a unit vector).
- Use `MomentumProjectileMovementStrategy3D` when a projectile must respect external impulses — its horizontal velocity accelerates toward `desiredDirection * _maxSpeed` at `_acceleration` u/s while preserving Y for gravity, mirroring `LinearMovementStrategy3D`'s impulse-respecting feel for stock characters. Treats `desiredDirection` as a unit vector.
- Use `LinearMovementStrategy3D` for character-driven entities where designer-tunable acceleration/friction shape the feel.
- Use `ProjectileMovementStrategy3D` (in `SpellArchitecture/`, not Jmodot) only when the projectile needs `LaunchArc` decomposition for ballistic spells.

**Contract gotcha:** the family is split on what `desiredDirection` represents — `ProjectileStrategy` consumes it as a pre-scaled velocity, while `LinearMovementStrategy3D` and `MomentumProjectileMovementStrategy3D` consume it as a normalized unit vector and resolve speed from their own `BaseFloatValueDefinition` exports. Match the strategy to the consumer's contract; mixing conventions silently produces wrong-magnitude motion.

## Touchpoints

- `Jmodot/Core/Movement/MovementProcessor3D` — pipeline entry.
- `Jmodot/Core/Movement/Strategies/` — `LinearMovementStrategy3D`, `MomentumProjectileMovementStrategy3D`.
- `SpellArchitecture/Movement/ProjectileMovementStrategy3D.cs` — PP-specific ballistic strategy with `LaunchArc`.
- Companion: [`hsm_bt_patterns.md`](hsm_bt_patterns.md) covers the HSM-routes/physics-drives layering invariant — knockback magnitude × mass × `MovementStrategy` is the *drives* layer this rule selects.
