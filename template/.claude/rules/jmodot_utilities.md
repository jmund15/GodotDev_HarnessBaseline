---
paths:
  - "**/*.cs"
---

# Jmodot Utilities — Consumer Reference

**Context:** the traps and conventions for {{PROJECT_NAME}} C# code that consumes Jmodot framework utilities. Auto-loads on `.cs` reads. Snippets and member inventories: [`reference/rules/jmodot_utilities_examples.md`](../reference/rules/jmodot_utilities_examples.md). Framework-internal authoring rules (2D/3D parity, framework boundary) live in [`jmodot_framework_authoring.md`](jmodot_framework_authoring.md), which auto-loads only inside `Jmodot/**/*.cs`; the design-time index and BBDataSig keys live in [`../skills/jmodot/SKILL.md`](../skills/jmodot/SKILL.md).

## IComponent Initialization Gotcha

**Rule:** All `IComponent` implementations require an explicit `Initialize(IBlackboard bb)` call. Components **silently no-op** (early return) if `IsInitialized = false`.

- *Diagnostic:* if a component method does nothing with no error, check `IsInitialized` first.
- The blackboard parameter can be `null` if dependencies are optional.

**Three phases, run by `EntityNodeComponentsInitializer`** — each completes for ALL components before the next begins:

0. `IBlackboardProvider.Provision` → `bb.Set` (scanned independently of `IComponent`)
1. `IComponent.Initialize(bb)` — **resolve dependencies only**; order is arbitrary, so a sibling may be uninitialized
2. `OnPostInitialize()` — post-barrier; **sibling-event subscriptions belong here**, never in `Initialize`

Never self-call `OnPostInitialize()` from `Initialize` — the phase driver invokes it after the Phase-1 barrier; the house tail of `Initialize` is `IsInitialized = true; Initialized(); return true;`. Required-dep `bool` policy and the bespoke-path taxonomy: [`../skills/architecture_philosophy/SKILL.md`](../skills/architecture_philosophy/SKILL.md) §Component Initialization Paths.

## Node & Scene Querying (NodeExts)

**Rule:** Prefer `NodeExts` extension methods over standard `GetNode()`. Most accept `bool includeSubChildren = true`. Method inventory + signatures: `<summary>` docs on `NodeExts`.

**When ambiguous:** if multiple nodes of the same type live under the same parent, `GetFirstChildOfType<T>()` is non-deterministic — use a direct `[Export]` reference instead.

## RequiredExport Validation

Canonical pattern + `pattern_enforcer.py` hook reference: [`csharp_patterns.md`](csharp_patterns.md) §"Nullability Convention". Same rule applies for `Resource` subclasses via `ResourceExts.ValidateRequiredExports()` — throws `ResourceConfigurationException` instead of `NodeConfigurationException`.

## Interfaces and Data Structures

Each of these exists — check it before hand-rolling an equivalent.

- **`IGodotNodeInterface` / `IGodotResourceInterface`** — expose the underlying `Node` or `Resource` when passing interfaces around. `GetUnderlyingNode()` / `GetUnderlyingResource()` ALWAYS return `this`. Snippet: `reference/rules/jmodot_utilities_examples.md` §Interface implementations.
- **`IRuntimeCopyable<T>`** — copies state for the blueprint→instance pattern (a Resource template spawning per-instance runtime copies). Contract: `CopyStateFrom(T original)`.
- **`Map<T1, T2>`** — two-way dictionary with `.Forward` / `.Reverse` lookup.
- **`JmoMath`** — pure math and geometry (remap, Bezier, ring-point, enum-values). Full signatures in the `<summary>` on the class.
- **`MovementExtensions`** — vector flatten/lift (3D↔2D) and weighted-gravity helpers on `CharacterBody3D`. Signatures in the `<summary>` on the class.

## JmoRng

**Rule:** `JmoRng` is an **instance class** over a seeded `Godot.RandomNumberGenerator`. Every consumer holds its own instance; **never** a static singleton.

**Runtime requirement:** constructing a `JmoRng` SIGSEGVs a test host without engine bootstrap, so a test that constructs one carries `[RequireGodotRuntime]` (analyzer `GdUnit0501` enforces). A pure-Logic call site instead takes **(a)** the roll as a parameter or **(b)** a delegate that tests fill with `new Random(seed).Next`. **Never** field-initialize `JmoRng.NonDeterministic()` on a `Resource`-derived type; initialize to `null!` and assign in the entry method.

**Construction and lifetime:** use `RandomStreams.X.CreateRng(parentSeed)` (`Global/RandomStreamsExtensions.cs`); `new JmoRng(int seed)` is the explicit-seed form, and every `JmoRng.NonDeterministic()` call site is tracked migration debt. Cache one instance per owning scope; **never** allocate one per call inside a loop. Never call `FromRawStreamName` directly.

**Range asymmetry:** `GetRndInRange(float min, float max)` throws `ArgumentException` on min > max, while the `int` overload is inclusive with no validation, so the caller guards inspector-driven ranges with `Math.Min`/`Math.Max`.

Seam examples, crash mechanics, seed derivation, the lifetime rationale and the member list: `reference/rules/jmodot_utilities_examples.md` §JmoRng pure-Logic seams, §JmoRng runtime mechanics, §JmoRng construction and §JmoRng instance methods.

## Configuration Exceptions

**Rule:** Throw these for configuration errors. Pass the actual object as the second argument — the constructor extracts the name automatically.

- `new NodeConfigurationException("message", this)` — Node missing/misconfigured `[Export]`
- `new ResourceConfigurationException("message", this)` — Resource missing/misconfigured `[Export]`

**Do not** pass a string for the second arg. **Do not** use `JmoLogger.Error` for fail-fast configuration — throw the exception.

## Touchpoints

- [`reference/rules/jmodot_utilities_examples.md`](../reference/rules/jmodot_utilities_examples.md) — the snippets and member inventories this rule cites.
- [`csharp_patterns.md`](csharp_patterns.md) — `[RequiredExport]` mechanics, nullability, test helpers, signals vs events, comment discipline. The `<summary>` citations above point at **signatures**, a member's own contract and citable under the trust radius; a doc comment is never citable for an observation about other code.
- [`../skills/architecture_philosophy/SKILL.md`](../skills/architecture_philosophy/SKILL.md) — Component Initialization Paths, Resource Strategy Hierarchies, Marker Interface as Capability Query.

<!-- retire-when: review-by: 2027-03-15 -->
