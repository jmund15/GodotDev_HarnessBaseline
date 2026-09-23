# `jmodot_utilities.md` — snippets and API inventory

Read a section when [`rules/jmodot_utilities.md`](../../rules/jmodot_utilities.md) names it. This file does not auto-load; the rule carries the traps and conventions, this carries the code shapes and the member lists a reader looks up while writing one call.

## Interface implementations

```csharp
public class MyComponent : Node, IGodotNodeInterface {
    public Node GetUnderlyingNode() => this;  // ALWAYS return 'this'
}

public class MyResource : Resource, IGodotResourceInterface {
    public Resource GetUnderlyingResource() => this;
}
```

## JmoRng pure-Logic seams

The two sanctioned ways a pure-Logic call site gets randomness without constructing a `JmoRng`:

**(a) Inject the roll as a parameter.** `Timing.CalculateEffectiveDuration(duration, additiveVariation, float variationRoll)`. The caller — sitting in a Godot-runtime-safe lifecycle hook such as `OnEnter` or `_Ready` — supplies `_rng.GetRndFloat()`. The static helper stays pure-CLR-testable.

**(b) Decouple via delegate.** `WeightedPool.GetRandom(category, available, Func<int, int> nextIndex)`. Production passes `_rng.GetRndInt` as a method group; tests pass `new Random(seed).Next` — the one legitimate use of `System.Random`, as a test fixture seam — and stay pure-CLR.

## JmoRng runtime mechanics

- **Generator:** `JmoRng` wraps xoshiro256++, which is contract-stable across Godot versions, so the same seed always yields the same sequence. The static `JmoRng.Rnd` singleton is retired.
- **Test-host crash:** construction allocates a `Godot.RandomNumberGenerator`, whose constructor runs `Godot.StringName..cctor` through native code; without engine bootstrap that SIGSEGVs.
- **Eager field initializer:** a `JmoRng.NonDeterministic()` field initializer on a `Resource`-derived type runs at Godot type registration and at `.tres` load, so it crashes any pure-CLR test that constructs the Resource, even one that never calls the entry method.
- **Lifetime:** cache the instance as a member field on the owning node or component, or as a method-local where the scope is one call. Drawing each sample from a fresh instance breaks xoshiro256++ spectral guarantees.
- **Migration backlog:** `JmoRng.NonDeterministic()` is Guid-seeded. `Grep "NonDeterministic\("` lists the current call sites.

## JmoRng construction

`JmoRng.FromRawStreamName(string streamName, int parentSeed)` is the deterministic factory that takes a *raw* string; it derives a per-stream child seed via `SeedManager.DeriveChild(parentSeed, streamName)`.

Consumers should not call it directly. Wrap it in a strongly-typed stream registry that pins each stream's key in one place, so a registry entry's `CreateRng(parentSeed)` cannot drift from its `GetSeed(parentSeed)` the way two raw string literals can.

## JmoRng instance methods

Full signatures live in the `<summary>` XML on `Jmodot/Implementation/Shared/JmoRng.cs`.

| Member | Returns |
|---|---|
| `GetRndFloat()` | float in [0, 1) |
| `GetRndInt(int max)` | int in [0, max), array-index style |
| `GetRndInRange(float min, float max)` | float in [min, max), max exclusive; **throws `ArgumentException` on min > max** |
| `GetRndInRange(int min, int max)` | int in [min, max] **inclusive** — no validation; the caller guards per `rules/jmodot_utilities.md` §JmoRng |
| `GetRndSign()` | +1f or -1f |
| `GetRndVector2()` / `GetRndVector3()` / `GetRndVector3PosY()` / `GetRndVector3ZeroY()` | random unit-length directions |
