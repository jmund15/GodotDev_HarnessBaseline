# Stats & Modifiers System

## Why This Exists
Centralized, modifiable character stats with a predictable calculation pipeline. Modifiers can stack, cancel each other, and be cleaned up by owner - all without manual bookkeeping.

## Mental Model

```
Base Value → Modifiers (staged) → Final Value
```

Stats aren't just numbers - they're **modifiable properties** that recalculate when modifiers change.

## The Calculation Pipeline (StageRule Resources)

Stages are **extensible `StageRule` Resources** (`Jmodot/Core/Modifiers/StageRules/`), not a fixed enum. Every modifier references a rule; the calculation groups modifiers by `StageId` and folds groups in ascending `Order`:

| Stage (`StageId`) | `Order` | Operation | Example |
|-------|------|-----------|---------|
| **BaseAdd** | 100 | Flat addition | +10 Damage from equipment |
| **PercentAdd** | 200 | Sum percentages, apply once | +20% MaxHealth (stacks additively) |
| **FinalMultiply** | 300 | Independent multipliers | ×2 for Critical, ×0 for Stun |
| **Override** | 400 | Replace value | Forced value effects |
| **Floor** | 490 | Lower bound (bound lives on the rule) | Min-speed clamp |
| **Cap** | 500 | Upper bound (bound lives on the rule) | Max-stack clamp |

Bool stages: `Flip` (350) / `Override` (400). `Int*` rules mirror the float set. Code-side modifiers use the shared `CanonicalStageRules` statics (stateless rules; Floor/Cap carry per-instance bounds — construct at use site). Data-authored modifiers reference the `.tres` equivalents under `Jmodot/Implementation/Modifiers/StageRules/`; both fold identically.

**Example calculation:**
```
Base: 100
+10 BaseAdd      → 110
+20% PercentAdd  → 132 (110 × 1.20)
×2 FinalMultiply → 264
```

## When to Use Which Stage

| Use this stage... | For this effect... |
|-------------------|-------------------|
| **BaseAdd** | Equipment bonuses, flat buffs |
| **PercentAdd** | Percentage buffs that should stack additively |
| **FinalMultiply** | Critical hits, stun (×0), damage reduction |
| **Floor / Cap** | Bounding the final result — don't fake clamps with FinalMultiply |

**Key insight:** Multiple PercentAdd modifiers are summed first (+10% and +20% = +30% total), then applied once. FinalMultiply modifiers are applied independently. Floor/Cap fold last so they bound everything upstream.

## Rules

1. **Attribute resources as keys** - Never strings. Create Attribute resources in editor.
2. **Ownership enables cleanup** - Pass `this` as owner when adding modifiers
3. **Handles for precision** - Use ModifierHandle when you need to remove specific modifiers
4. **Single source of truth** - MaxHealth, MaxSpeed, etc. all come from IStatProvider

## Modifier Ownership & Cleanup

```csharp
// Add modifier with this state as owner
stats.TryAddModifier(attr, buffMod, this, out _);

// On state exit - remove ALL modifiers from this owner
stats.RemoveAllModifiersFromSource(this);
```

This pattern ensures modifiers are cleaned up when the source is done, without tracking individual handles.

## Tag-Based Conflict Resolution

Modifiers can cancel each other via tags:

```csharp
// Slow effect
EffectTags: ["Slow"]

// Speed boost (cancels slow)
EffectTags: ["SpeedBoost"]
CancelsEffectTags: ["Slow"]
```

When both are active, the slow is excluded from calculation.

## Context Gating

Modifiers can require certain contexts to be active:

```csharp
// Fire resistance only applies in fire contexts
RequiredContextTags: ["Fire"]
```

Use `AddActiveContext()` / `RemoveActiveContext()` to enable/disable context-gated modifiers.

## Anti-Patterns

**Using strings for stat names:**
```csharp
// BAD
stats.GetStatValue<float>("MaxSpeed", 5f);
```

```csharp
// GOOD - Attribute resource (project-side accessor: GlobalRegistry.DB, Global/GlobalRegistry.cs)
stats.GetStatValue<float>(GlobalRegistry.DB.MaxSpeedAttr, 5f);
```

**Forgetting to clean up modifiers** - Always use ownership or handles.

**Wrong stage choice:**
- Stacking percentage buffs in FinalMultiply (they'll compound instead of add)
- Critical hit in PercentAdd (it'll add with other percentages instead of multiply)

## Integration Points

- **HealthComponent** reads MaxHealth from IStatProvider
- **MovementStrategy** reads speed/acceleration from IStatProvider
- **Combat effects** can apply modifiers via TryAddModifier
- **Status runners** own their modifiers for automatic cleanup
- **HSM states** apply a `StatContext` while active (`State.ActiveStatContext` — added on enter, removed on exit)
- project consumption seam (attributes via `GlobalRegistry.DB`): [SKILL.md](SKILL.md) §Framework/consumer seam

Stage roster verified 2026-07-04. Re-verify: `Grep "StageId =" Jmodot/Core/Modifiers/StageRules/ -n`.
