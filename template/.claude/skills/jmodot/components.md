# Component & Blackboard System

## Why This Exists
Eliminates tight coupling between game systems. Instead of components holding direct references to each other, they query a shared Blackboard for what they need.

## Mental Model
Think of the Blackboard as a **shared dictionary** owned by an entity. During initialization, the entity populates it with services. Components then query the BB to get their dependencies - they never directly reference siblings.

```
Entity owns BB → components self-publish → components query BB
```

## Initialization Mechanics

`EntityNodeComponentsInitializer` (ENCI) drives three subtree scans. Each phase completes for ALL components before the next starts.

| Phase | Scan | Call |
|---|---|---|
| 0 | `GetChildrenOfInterface<IBlackboardProvider>()` — **independent of `IComponent`** | read `Provision` → `bb.Set(key, value)` |
| 1 | `GetChildrenOfInterface<IComponent>()` | `Initialize(bb)` — resolve dependencies only |
| 2 | components that returned `true` | `OnPostInitialize()` — subscribe here |

- Provider-independence means a **passive publisher** (a node that only exposes something on the BB) implements `IBlackboardProvider` alone — no `IComponent`, no `Initialize`.
- Phase 0 is reusable outside ENCI: `EntityNodeComponentsInitializer.RunPhase0(Node entity, IBlackboard bb)`. Non-entity bootstrap paths call it instead of hand-rolling the scan.
- Never self-call `OnPostInitialize()` from `Initialize` — the phase driver invokes it after the Phase-1 barrier (ENCI, or `ComponentInitHelper` for delegated lifecycles).

## Rules

1. **Always use `BBDataSig` constants** - Never raw strings for BB keys
2. **Publish via `Provision`, not a hand-written `bb.Set` in the entity root** - Phase 0 does the wiring
3. **Resolve in `Initialize`, subscribe in `OnPostInitialize`** - a sibling's events are only safe after the Phase-1 barrier
4. **Return `false` only for a dependency without which the component serves no purpose** - ENCI logs `Error` and drops it; partial-behavior deps degrade and return `true`. Full policy + `[Export]` carve-outs + the bespoke-path taxonomy: `architecture_philosophy/SKILL.md` §Component Initialization Paths
5. **Document required keys** - Add a class summary listing which `BBDataSig` keys your component needs

## When to Use

| Use Blackboard when... | Use direct reference when... |
|------------------------|------------------------------|
| Cross-system communication | Parent-child in same system |
| Dependencies might change | Tight coupling is intentional |
| Multiple consumers need same service | Performance-critical inner loop |

## Anti-Patterns

**Direct component references:**
```csharp
// BAD - tight coupling
private HealthComponent _health;
public override void _Ready() {
    _health = GetNode<HealthComponent>("../HealthComponent");
}
```

```csharp
// GOOD - BB query
public bool Initialize(IBlackboard bb) {
    if (!bb.TryGet<IHealth>(BBDataSig.HealthComponent, out var health))
        return false;
    _health = health;
    return true;
}
```

**Using GetNode() for sibling components** - Components shouldn't know the scene tree structure. Query the BB instead.

**Skipping validation** - Always check `TryGet()` returns true before using the value.

## Integration Points

- **Entity** owns the BB and runs the three phases (driver: `Jmodot/Implementation/Components/EntityNodeComponentsInitializer.cs`)
- **States** receive BB in their `Init()` method
- **Combat effects** access target's BB via `ICombatant.Blackboard`
- **Subscriptions** allow reactive updates when BB values change
- Path taxonomy, required-dep policy, `[Export]` carve-outs, `[Tool]` warning convention: `architecture_philosophy/SKILL.md` §Component Initialization Paths. Silent no-op diagnostic: `rules/jmodot_utilities.md` §IComponent
- Key constants: the two-partial `BBDataSig` split — [SKILL.md](SKILL.md) §BBDataSig Quick Reference
