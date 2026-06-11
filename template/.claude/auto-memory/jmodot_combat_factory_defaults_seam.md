---
name: Jmodot_CombatFactoryDefaults_Seam
description: How Jmodot's DamageEffectFactory + DistanceScaledDamageEffectFactory resolve project-wide crit attribute defaults without reaching into {{PROJECT_NAME}}.Global. Seam class CombatFactoryDefaults is set from PP's GlobalRegistry autoload at boot. Authored 2026-04-19, revised 2026-04-20 to reflect hybrid reconciliation with parallel user commits.
type: reference
originSessionId: 65fe3ebf-5342-45f2-b44f-e56a7c3d003f
---
## The seam (crit attrs only)

Jmodot's damage-effect factories need project-wide crit defaults — default crit chance attribute and crit multiplier attribute — but Jmodot cannot know about PP's `PushinPotionRegistry`. The seam:

**`Jmodot/Core/Combat/CombatFactoryDefaults.cs`** — static class with 2 nullable static fields:
- `Attribute? DefaultCritChanceAttr`
- `Attribute? DefaultCritMultiplierAttr`
- `Reset()` method — clears both fields, intended for test teardown

## Who sets it

**`{{PROJECT_NAME}}/Global/GlobalRegistry.cs`**, private static `WireCombatFactoryDefaults()` called from:
- `_EnterTree` (after `DB.RebuildRegistry()`)
- `EnsureInitializedForTests` (after same)
- Mirror cleanup in `ResetForTests` (calls `CombatFactoryDefaults.Reset()`)

## Who reads it

2 factories in `Jmodot/Implementation/Combat/EffectFactories/`:
- `DamageEffectFactory` — resolution: `CritChanceAttrOverride ?? CombatFactoryDefaults.DefaultCritChanceAttr`; then same for mult → `DefaultCritMultiplier` literal
- `DistanceScaledDamageEffectFactory` — same pattern

Each factory pattern: `var x = PerFactoryOverride ?? CombatFactoryDefaults.DefaultX;` then null-check before use. Both null = crit disabled (graceful; no exception).

## Status runners are NOT on the seam

A **separate parallel approach** (user commit `7e9d627`) governs the 4 status runner factories (`TickEffectFactory`, `DelayedEffectFactory`, `DurationEffectFactory`, `DurationRevertibleEffectFactory`):
- Runner export promoted from `RunnerOverride` (nullable) to `Runner` with `[Export, RequiredExport]` = `null!`
- Consumer `.tres` MUST Inspector-assign `Runner` — validated at scene load via `ValidateRequiredExports()`
- No seam fallback; no runtime throw-guard needed. Inspector-time enforcement is stronger.

**Why split the strategy?** Crit is graceful (null attr → crit disabled, game still works). Runner is non-graceful (null runner → effect cannot run). The stronger the fail-at contract needed, the earlier the check should fire.

## Why the seam exists

Before this seam, Jmodot factories had `using {{PROJECT_NAME}}.Global;` or fully-qualified `{{PROJECT_NAME}}.Global.GlobalRegistry.DB.*` references, making Jmodot un-portable to any other game. The seam preserves PP's crit behavior exactly (19 PP damage factory `.tres` files that don't set crit overrides still roll crit) while keeping Jmodot framework-agnostic.

## Don't confuse with `GlobalRegistryLIB`

`Jmodot/Implementation/Registry/` had an abandoned extraction: `GlobalRegistryLIB : Node` + parallel `GameRegistry : Resource` (458 lines) that PP's `PushinPotionRegistry` duplicated rather than inherited. **Deleted 2026-04-19** as dead code. Refactor to proper inheritance deferred — revisit when a second framework consumer (TR) materializes. `ResourceCollection.cs` preserved — actively used.
