---
paths:
  - "**/*.cs"
---

# Jmodot Utilities — Consumer Reference

**Context:** Quick reference for Jmodot framework utilities consumed throughout {{PROJECT_NAME}} C# code. Auto-loads on `.cs` reads. For framework-internal authoring rules (2D/3D parity, framework boundary), see [`jmodot_framework_authoring.md`](jmodot_framework_authoring.md) which auto-loads only inside `Jmodot/**/*.cs`. For design-time Jmodot reference (subsystem index, BBDataSig keys), see [`../skills/jmodot/SKILL.md`](../skills/jmodot/SKILL.md).

## IComponent Initialization Gotcha

**Rule:** All `IComponent` implementations require explicit `Initialize(IBlackboard bb)` calls. Components **silently no-op** (early return) if `IsInitialized = false`.

- *Diagnostic:* If a component method does nothing with no error, check `IsInitialized` first.
- The blackboard parameter can be `null` if dependencies are optional.

**Three phases, run by `EntityNodeComponentsInitializer`** — each completes for ALL components before the next begins:

0. `IBlackboardProvider.Provision` → `bb.Set` (scanned independently of `IComponent`)
1. `IComponent.Initialize(bb)` — **resolve dependencies only**; order is arbitrary, so a sibling may be uninitialized
2. `OnPostInitialize()` — post-barrier; **sibling-event subscriptions belong here**, never in `Initialize`

Never self-call `OnPostInitialize()` from `Initialize` — the phase driver invokes it after the Phase-1 barrier; the house tail is `IsInitialized = true; Initialized(); return true;`. Required-dep `bool` policy and the bespoke-path taxonomy: [`../skills/architecture_philosophy/SKILL.md`](../skills/architecture_philosophy/SKILL.md) §Component Initialization Paths.

## Node & Scene Querying (NodeExts)

**Rule:** Prefer `NodeExts` extension methods over standard `GetNode()`. Most accept `bool includeSubChildren = true`. Method inventory + signatures: `<summary>` docs on `NodeExts`.

**When ambiguous:** if multiple nodes of the same type live under the same parent, `GetFirstChildOfType<T>()` is non-deterministic — use a direct `[Export]` reference instead.

## RequiredExport Validation

Canonical pattern + `pattern_enforcer.py` hook reference: [`csharp_patterns.md`](csharp_patterns.md) §"Nullability Convention". Same rule applies for `Resource` subclasses via `ResourceExts.ValidateRequiredExports()` — throws `ResourceConfigurationException` instead of `NodeConfigurationException`.

## IGodotNodeInterface / IGodotResourceInterface

**Purpose:** Expose the underlying `Node` or `Resource` when passing interfaces around.

```csharp
public class MyComponent : Node, IGodotNodeInterface {
    public Node GetUnderlyingNode() => this;  // ALWAYS return 'this'
}

public class MyResource : Resource, IGodotResourceInterface {
    public Resource GetUnderlyingResource() => this;
}
```

## IRuntimeCopyable&lt;T&gt;

**Purpose:** Interface to copy state — blueprint→instance pattern (e.g., Resource templates spawning per-instance runtime copies). Contract: `CopyStateFrom(T original)`.

## Map&lt;T1, T2&gt;

**Purpose:** Two-way dictionary (`.Forward` / `.Reverse` lookup) — exists; don't hand-roll one.

## JmoRng

**Rule:** `JmoRng` is an **instance class** wrapping a seeded `Godot.RandomNumberGenerator` (xoshiro256++, contract-stable across Godot versions). Every consumer holds its own instance; same seed → same sequence, always. The pre-refactor static `JmoRng.Rnd` singleton was retired by `arch-seed-system.md`.

**Runtime requirement (empirically confirmed 2026-05-17):** `JmoRng` construction allocates a `Godot.RandomNumberGenerator`, whose constructor runs `Godot.StringName..cctor` via native — SIGSEGVs the test host without engine bootstrap. Tests that construct `JmoRng` MUST carry `[RequireGodotRuntime]` (analyzer `GdUnit0501` enforces). Pure-Logic call sites that need randomness MUST either:
- **(a) inject the roll as a parameter** — `Lag.CalculateEffectiveDuration(duration, additiveVariation, float variationRoll)`. Caller (in a Godot-runtime-safe lifecycle hook like `OnEnter`/`_Ready`) supplies `_rng.GetRndFloat()`. Static helper stays pure-CLR-testable.
- **(b) decouple via delegate** — `ModifierPool.GetRandom(category, available, Func<int, int> nextIndex)`. Production passes `_rng.GetRndInt` (method group); tests pass `new Random(seed).Next` (legitimate fixture seam per `feedback_system_random_test_fixture_carveout.md`) and stay pure-CLR.

Also forbidden by this rule: **eager field initializers** that allocate `JmoRng.NonDeterministic()` on `Resource`-derived types (gets called at Godot type-registration and at `.tres` load AND would SIGSEGV any pure-CLR test that constructs the Resource without calling the entry method). Field-init to `null!`; assign in the entry method.

**Construction (pick by need):**
- `new JmoRng(int seed)` — explicit seed (deterministic).
- `JmoRng.FromRawStreamName(string streamName, int parentSeed)` — deterministic factory taking a *raw* string; derives a per-stream child seed via `SeedManager.DeriveChild(parentSeed, streamName)`. {{PROJECT_NAME}} consumers prefer the strongly-typed registry, which pins each stream's key via `[SeedStreamKey]`: `SeedStreams.X.CreateRng(parentSeed)` / `SeedStreams.X.GetSeed(parentSeed)` (see `Global/SeedStreamsExtensions.cs`).
- `JmoRng.NonDeterministic()` — Guid-seeded, **migration debt marker**. Every call site is a tracked backlog item to be replaced with a seeded construction. `Grep "NonDeterministic\("` for the current backlog.

**Lifetime convention** (per `arch-seed-system.md §6`): per-scope materialization. Cache as a member field on the owning node/component, or as a method-local where appropriate. **Never** allocate per-call inside loops — drawing each sample from a fresh `NonDeterministic()` instance breaks xoshiro256++ spectral guarantees. **Never** use a static singleton `JmoRng` (the anti-pattern the audit retired).

**Instance methods** (full signatures in `<summary>` XML on `Jmodot/Implementation/Shared/JmoRng.cs`):
- `GetRndFloat()` — float in [0, 1)
- `GetRndInt(int max)` — int in [0, max), array-index style
- `GetRndInRange(float min, float max)` — float in [min, max), max exclusive; **throws `ArgumentException` on min > max**
- `GetRndInRange(int min, int max)` — int in [min, max] inclusive; **no validation** — caller must guard `Math.Min/Max` for inspector-driven ranges
- `GetRndSign()` — +1f or -1f
- `GetRndVector2()` / `GetRndVector3()` / `GetRndVector3PosY()` / `GetRndVector3ZeroY()` — random unit-length directions

## JmoMath

Pure math + geometry utilities (remap, Bezier, ring-point, enum-values). Full signatures in `<summary>` on the class — check before hand-rolling math helpers.

## MovementExtensions

Vector flatten/lift (3D↔2D) + weighted-gravity helpers on `CharacterBody3D`. Signatures in `<summary>` on the class.

## Configuration Exceptions

**Rule:** Throw these for configuration errors. Pass the actual object as the second argument — the constructor extracts the name automatically.

- `new NodeConfigurationException("message", this)` — Node missing/misconfigured `[Export]`
- `new ResourceConfigurationException("message", this)` — Resource missing/misconfigured `[Export]`

**Do not** pass a string for the second arg. **Do not** use `JmoLogger.Error` for fail-fast configuration — throw the exception.

## Touchpoints

- [`csharp_patterns.md`](csharp_patterns.md) — `[RequiredExport]` mechanics, nullability, test helpers, signals vs events.
- [`jmodot_framework_authoring.md`](jmodot_framework_authoring.md) — fires only on `Jmodot/**/*.cs` for framework-internal rules.
- [`../skills/jmodot/SKILL.md`](../skills/jmodot/SKILL.md) — design-time index, subsystem deep-dive routing, BBDataSig keys.
- [`../skills/architecture_philosophy/SKILL.md`](../skills/architecture_philosophy/SKILL.md) — Component Initialization Paths, Resource Strategy Hierarchies, Marker Interface as Capability Query.
