# Combat System

## Why This Exists
Data-driven combat where effects are defined as data, not behavior. An attack is a collection of effects (damage, knockback, status). Each effect knows how to apply itself and returns a result that other systems can react to.

## Mental Model

```
Attack → Payload (effects[]) → Combatant applies each → Results → Reactions
```

- **Payload**: Container of effects to apply
- **Effect**: Data struct that knows how to apply itself (damage, stun, etc.)
- **Result**: Snapshot of what happened (how much damage, what tags)
- **Reaction**: Response to result (play animation, transition state)

## The Combat Flow

1. **Hitbox** detects collision, creates `HitContext`
2. **Hurtbox** calls `combatant.ProcessPayload(payload, context)`
3. **Combatant** iterates effects, calls `effect.Apply()`
4. Each effect returns a `CombatResult` (or null if no-op)
5. **CombatResultEvent** fires for each result
6. **CombatLog** stores results for HSM queries

## Rules

1. **Effects are structs** - Value types for memory efficiency, no shared state
2. **Use factories for complex effects** - Factory resources configure effects via exports
3. **Tags categorize effects** - Use for reaction logic ("HeavyHit" triggers stagger)
4. **CombatLog enables HSM integration** - Query "was I hit this frame?"

## CombatLog - HSM Integration

The CombatLog stores results per-frame so HSM conditions can query them:

```csharp
// In a TransitionCondition
public override bool Check(Node agent, IBlackboard bb) {
    var log = bb.Get<CombatLog>(BBDataSig.CombatLog);
    return log.HasEvent<DamageResult>(r => r.Amount > 10f);
}
```

---

## Status Effect System

### Why This Exists
Temporal effects that persist over time. A StatusRunner manages the lifecycle - applying effects, showing visuals, and cleaning up when done.

### Mental Model

```
Factory creates Runner → Runner added to StatusEffectComponent → Runner ticks/expires → cleanup
```

### Runner Types

| Runner | Use Case |
|--------|----------|
| **Duration** | Ends after fixed time (stun, buff) |
| **Tick** | Repeats effect periodically (poison, regen) |
| **Delayed** | Triggers after delay (time bomb) |
| **Condition** | Ends when condition met |

### Tag System

StatusEffectComponent tracks which tags are currently active. This enables queries like "is this entity stunned?" without iterating all runners.

```csharp
var statusComp = bb.Get<StatusEffectComponent>(BBDataSig.StatusEffects);
if (statusComp.HasTag(CombatTags.Stun)) {
    // Can't act while stunned
}
```

### Rules

1. **Runners manage their own lifecycle** - They QueueFree themselves when done
2. **Tags are reference-counted** - Multiple stuns = stun tag still active
3. **Visuals are optional** - Runners can spawn persistent VFX or apply shader effects

---

## Health System

### Why This Exists
Unified health state with events for UI, AI, and game logic to react to.

### Key Interfaces

- **IHealth** (read-only): CurrentHealth, MaxHealth, IsDead, events
- **IDamageable**: TakeDamage()
- **IHealable**: Heal()

### Rules

1. **MaxHealth comes from IStatProvider** - Single source of truth
2. **Can't heal while dead** - State machine enforced
3. **Source parameter enables attribution** - Know what killed you

---

## Anti-Patterns

**Hardcoding damage in effects:**
```csharp
// BAD - not data-driven
return new DamageEffect { Amount = 50 };
```

```csharp
// GOOD - use factory with exports
[Export] public float BaseDamage { get; private set; }
```

**Checking status by iterating runners** - Use the tag system instead.

**Modifying combatant state in Apply()** - Effects should calculate, not directly modify. Return results and let the combatant handle it.
