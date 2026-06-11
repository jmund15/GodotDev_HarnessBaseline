---
name: Jmodot_2D_Movement_Architecture
description: 2D parity surface for Jmodot movement + combat + actors — API shapes, BB key convention, strategy inventory, deferred debt. Authored 2026-04-19.
type: reference
originSessionId: 65fe3ebf-5342-45f2-b44f-e56a7c3d003f
---
## MovementProcessor2D

**Constructor (5-arg, at parity with MovementProcessor3D):**
```csharp
MovementProcessor2D(
    ICharacterController2D controller,
    IStatProvider stats,
    ExternalForceReceiver2D receiver,
    Node2D owner,
    Attribute? stabilityAttr = null)
```
When `stabilityAttr` is null, `ScaleByStability` is pass-through. `ProcessMovement` runs the turn-rate preprocessing block (equivalent to `MovementProcessor3D.cs:50-62`) — clamps `desiredDirection` through `BaseMovementStrategy2D.TurnProfile` before calling `strategy.CalculateVelocity`.

## BBDataSig keys shared across dimensions

**No `2D` suffix on keys.** Entities register 2D components under:
- `BBDataSig.HurtboxComponent` / `HitboxComponent` / `KnockbackComponent`
- `BBDataSig.MovementProcessor`
- `BBDataSig.ForceContext` / `ControlLost`

Consumers dispatch by entity dimension at cast time (e.g. `bb.TryGet<IMovementProcessor2D>(key)` vs `...<IMovementProcessor3D>(key)`). Precedent: `MovementProcessor` already used this pattern before this session.

## Concrete 2D movement strategies (2026-04-19)

| Strategy | Status | Purpose |
|---|---|---|
| `LinearMovementStrategy2D` | direct twin | MoveToward with maxSpeed + accel + friction |
| `AcceleratedMovementStrategy2D` | NEW | Asymmetric accel/decel via Dot-product sign check — Robber's run/walk |
| `IdleFrictionStrategy2D` | NEW | Ignores desiredDirection, friction decay with stop threshold |
| `RecoilAirborneStrategy2D` | NEW | Input-suppressed friction decay for hit/fall states |

## TurnRateProfile rename

`TurnRateProfile` was RENAMED to `TurnRateProfile3D` (commit `ba442b5`) to match the dimension-suffix convention used elsewhere (`MovementProcessor3D`, `CharacterBodyController3D`, etc). Dimension-parallel sibling `TurnRateProfile2D` authored with `UniformTurnRateProfile2D` + `SpeedScaledTurnRateProfile2D` concretes. `AISteeringProcessor2D.ApplyTurnRateLimit` is the new Vector2 helper — simpler than 3D because `Vector2.AngleTo` returns a **signed** angle, so `prev.Rotated(clampedSigned)` needs no near-antiparallel Slerp degenerate handling.

## Combat trio surface

- **HurtboxComponent2D** (Area2D, 215-line 3D twin). Emits `HitContext2D` through `OnHitReceived`.
- **HitboxComponent2D** (Area2D, `IPoolResetable`). Full 469-line 3D surface including `PendingOverlapRetryFrames=3` broadphase workaround. Clears event delegates on `OnPoolReset` — critical to prevent N-handlers-per-hit on reuse.
- **KnockbackComponent2D** (Node2D). **Omits** `FlattenKnockback` export (top-down 2D has no up-axis to flatten).
- **KnockbackComponentRigidBody2D**: intentionally skipped. The 3D version is itself a TODO with commented-out `ApplyCentralImpulse`. Author when a TR `RigidBody2D` consumer demands it.

## HitContext2D ↔ HitContext bridging

`HurtboxComponent2D` adapts `HitContext2D` → `HitContext` by mapping `Vector2(X,Y)` → `Vector3(X,0,Y)`. `KnockbackComponent2D` reads `DamageResult.Direction` as Vector3 and projects back via `(X,Z)` → `Vector2(X,Z)`. This lets the dimension-agnostic `ICombatant.ProcessPayload` API stay 3D-typed while 2D-aware effects read the richer `HitContext2D` directly from the `OnHitReceived` event.

## ForceControlLossDetector2D

Reuses the dimension-agnostic `ControlLossEvaluator` (scalar hysteresis — no Vector2/Vector3 knowledge needed). Writes `ForceContext2D` to `BBDataSig.ForceContext` + bool to `BBDataSig.ControlLost`. Consumer HSM states (CapturedState/WallHitState/GroundFallState equivalents) remain project-local — Jmodot provides only detector + flags, not state machinery.

## ICharacterController2D additions

Now extends `IVelocityProvider2D` with explicit interface implementation forwarding `LinearVelocity → Velocity` (mirrors 3D). Gained: `IsOnWall`, `GetWallNormal()`, `PreMoveVelocity`, `LastNonZeroVelocity`. `CharacterBodyController2D` tracks PreMove/LastNonZero inside `Move()`. **PP-local implementer `TestSpell.cs` required 4 explicit interface impls** to match — any other downstream implementer will break until it does the same.

## Deferred tech debt

- `AISteeringProcessor2D` only has `ApplyTurnRateLimit` — full steering/consideration pipeline NOT ported. Wait for 2D AI consumer.
- `KnockbackComponentRigidBody2D` pending a TR `RigidBody2D` consumer.
- `InstantMovementStrategy2D` previously had a `* delta` bug and a `{{PROJECT_NAME}}.Global` dependency — both fixed in commit `9bc55fc`.

## Known flake: Integration GC race

2026-04-19 Phase 9 regression gate: first Integration run died with `FATAL "gchandle.is_released()"` at `mono_object_disposed_baseref` in `GodotObject.Finalize` after `GC.RunFinalizers`. Classic Godot C# GC race on a previously-disposed GodotObject — **unrelated** to 2D work. Exit code -1, no test results. Clean re-run: 239/0 green.

**If this recurs** during CI/PR runs, investigate disposal paths in integration tests near the `SpellCrafter` / `HitboxComponent3D` hit pipeline — those were the last visible logs before the crash.
