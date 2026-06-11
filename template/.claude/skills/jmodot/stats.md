# Stats & Modifiers System

## Why This Exists
Centralized, modifiable character stats with a predictable calculation pipeline. Modifiers can stack, cancel each other, and be cleaned up by owner - all without manual bookkeeping.

## Mental Model

```
Base Value → Modifiers (staged) → Final Value
```

Stats aren't just numbers - they're **modifiable properties** that recalculate when modifiers change.

## The Calculation Pipeline

Modifiers apply in strict order:

| Stage | Operation | Example |
|-------|-----------|---------|
| **BaseAdd** | Flat addition | +10 Damage from equipment |
| **PercentAdd** | Sum percentages, apply once | +20% MaxHealth (stacks additively) |
| **FinalMultiply** | Independent multipliers | ×2 for Critical, ×0 for Stun |

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

**Key insight:** Multiple PercentAdd modifiers are summed first (+10% and +20% = +30% total), then applied once. FinalMultiply modifiers are applied independently in priority order.

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
// GOOD - Attribute resource
stats.GetStatValue<float>(GlobalReg.MaxSpeedAttr, 5f);
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
