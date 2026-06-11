# Movement System

## Why This Exists
Separates physics execution from movement logic. The Controller handles physics (MoveAndSlide), the Strategy calculates velocity. This lets you swap movement behaviors (walk, dash, ice) without changing physics code.

## Mental Model

```
Strategy calculates velocity → Controller executes physics
```

Two distinct layers:
- **Controller** = "How do I move?" (physics driver)
- **Strategy** = "What velocity should I have?" (game logic)

## The Key Decision: Force vs Velocity Offset

| Use Force when... | Use Velocity Offset when... |
|-------------------|----------------------------|
| Effect should decay over time | Effect should be constant |
| Friction matters | Friction shouldn't affect it |
| One-time impulse | Continuous environmental effect |
| Knockback, jump impulse | Conveyor belt, water current |

**Forces** are added to velocity and persist. Friction applies to them next frame.

**Velocity Offsets** are fresh each frame, added only during `Move()`, then removed. Friction never touches them.

### Equilibrium Formula (Forces)

When a constant force opposes friction:
```
V_equilibrium = sourceVel × ratio / (friction + ratio)
```

This is why forces eventually "cap out" while offsets don't.

## Rules

1. **Strategies are stateless** - Same inputs → same outputs. No per-instance data.
2. **Query stats, don't hardcode** - Get speed/accel from IStatProvider
3. **One Move() per frame** - Call it once at the end of all velocity calculations
4. **Use Processor for high-level** - Don't call Controller directly from states

## Movement Flow

```
1. State reads input direction
2. State picks appropriate Strategy
3. State calls processor.ProcessMovement(strategy, direction, delta)
4. Processor:
   - Runs strategy to get base velocity
   - Adds pending impulses
   - Queries ForceProviders
   - Queries VelocityOffsetProviders
   - Calls controller.Move()
```

## Strategies - Swapping Movement Behavior

States select which strategy to use:

```csharp
// Walk state
processor.ProcessMovement(_walkStrategy, moveDir, delta);

// Dash state
processor.ProcessMovement(_dashStrategy, dashDir, delta);

// Airborne state
processor.ProcessMovement(_airStrategy, moveDir, delta);
```

Each strategy is a Resource with its own logic. No code changes needed to add new movement types.

## Anti-Patterns

**State in strategies:**
```csharp
// BAD - strategy stores state
private float _timer;  // Shared across all users!
```

Strategies are shared resources. Store state in the State that uses them.

**Calling controller in _Process:**
```csharp
// BAD - physics in wrong callback
public override void _Process(double delta) {
    _controller.Move();  // Should be _PhysicsProcess
}
```

**Direct controller access:**
```csharp
// BAD - bypasses processor
_controller.SetVelocity(newVel);
_controller.Move();
```

Use the Processor - it handles forces and offsets correctly.

## Integration Points

- **HSM states** own strategies and call ProcessMovement
- **Stats** provide speed, acceleration, friction values
- **Combat** applies knockback via ApplyImpulse
- **Environment areas** implement IForceProvider or IVelocityOffsetProvider
