# Component & Blackboard System

## Why This Exists
Eliminates tight coupling between game systems. Instead of components holding direct references to each other, they query a shared Blackboard for what they need.

## Mental Model
Think of the Blackboard as a **shared dictionary** owned by an entity. During initialization, the entity populates it with services. Components then query the BB to get their dependencies - they never directly reference siblings.

```
Entity owns BB → populates with services → components query BB
```

## Rules

1. **Always use `BBDataSig` constants** - Never raw strings for BB keys
2. **Validate in `Initialize()`** - If a required dependency is missing, return `false` immediately
3. **Fail fast** - Log errors and abort if configuration is wrong
4. **Document required keys** - Add a class summary listing which `BBDataSig` keys your component needs

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

- **Entity** owns the BB and calls `Initialize()` on all components
- **States** receive BB in their `Init()` method
- **Combat effects** access target's BB via `ICombatant.Blackboard`
- **Subscriptions** allow reactive updates when BB values change
